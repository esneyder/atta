"""El hormiguero: a read-only HTTP board over the job store (arquitectura §4.1).

Run it as the `atta-board` console script or `python atta/bin/board.py`. Jobs and config come
from the project (spine/project.py: `--project`, $ATTA_PROJECT, git top level or cwd); the static
view always comes from the installed package.

Serves the static view in `atta/board/` and a JSON API built from `job.json`,
`events.jsonl` and the first line / `## Summary` of each Markdown artifact:

    GET /                                   index.html
    GET /<asset>                            files directly inside atta/board/ (allowlist)
    GET /api/jobs                           {jobs, stats, warnings, ...}
    GET /api/jobs/<id>/files                [{path, first_line, summary}]
    GET /api/jobs/<id>/file?path=<rel.md>   text of one .md artifact of that job

No write endpoints. Every response carries `Cache-Control: no-store`. Paths are
matched against fixed patterns and allowlists, never joined from raw input.
Every job dir, round dir and file must resolve to exactly where it was found
(no symlink, NTFS junction or hardlink leads outside the job); anything else is
skipped with a warning and never read.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import stat
import statistics
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from . import project
from .ids import InvalidId, sanitize_id
from .timeutil import from_iso, to_iso, utcnow

ATTA_DIR = project.PACKAGE_DIR
STATIC_DIR = project.BOARD_DIR
DEFAULT_JOBS_DIR = ATTA_DIR / "jobs"
CONFIG_FILE = ATTA_DIR / "config.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

MAX_MD_BYTES = 1_000_000
REQUEST_TIMEOUT_S = 10
# Host header values accepted on a loopback server (plus the literal address it listens on).
LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "[::1]"})
_HOST = re.compile(r"(\[[0-9a-f:.]+\]|[0-9a-z.-]+)(?::[0-9]{1,5})?\Z", re.IGNORECASE | re.ASCII)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}

# Board column for each pipeline stage. needs-human lives in the alert banner.
COLUMNS = {
    "intake": "intake", "needs-info": "intake",
    "spec": "spec", "spec-review": "spec",
    "build": "build", "gate": "gate", "review": "review",
    "approve": "approve", "merging": "approve",
    "merged": "closed", "rejected": "closed",
    "needs-human": "human",
}
P50_STAGES = ("intake", "spec", "spec-review", "build", "gate", "review", "approve", "merging")
ROOT_ORDER = ("issue.md", "intake.md", "spec.md", "spec-review.md")

_ROUND_DIR = re.compile(r"round-([1-9][0-9]{0,3})\Z")
_REVIEW_FILE = re.compile(r"review-([0-9a-z][0-9a-z-]*)\.md\Z")
_TOKEN = re.compile(r"(?:(GATE|VERDICT|STATUS|DECISION):\s*)?([A-Z][A-Z-]*)\b")
_COMMIT = re.compile(r"COMMIT:\s*([0-9A-Za-z]{1,64})\s*\Z")
_SAFE_MD = re.compile(r"(?:round-[1-9][0-9]{0,3}/)?[0-9A-Za-z][0-9A-Za-z._-]*\.md\Z")
_ID = r"([0-9a-z][0-9a-z-]*)"
_ROUTE_FILES = re.compile(rf"/api/jobs/{_ID}/files\Z")
_ROUTE_FILE = re.compile(rf"/api/jobs/{_ID}/file\Z")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


# --- confinement -------------------------------------------------------------

def _inside(path: Path, directory: Path) -> bool:
    """True when `path` really is the entry `path.name` of `directory`.

    Resolving catches symlinks and NTFS junctions alike (`is_symlink()` misses junctions on
    Python 3.10/3.11), including links to a sibling inside the same directory.
    """
    try:
        real = path.resolve(strict=True)
        expected = directory.resolve(strict=True) / path.name
    except (OSError, RuntimeError):
        return False
    return os.path.normcase(str(real)) == os.path.normcase(str(expected))


def _warn(warnings: list | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)


def _entry_ok(path: Path, label: str, warnings: list | None = None, *, is_dir: bool) -> bool:
    """True for a real directory / regular file of its own parent; links are skipped with a
    warning that names only `label`, never the target or its content."""
    try:
        path.lstat()
    except OSError:
        return False
    if not _inside(path, path.parent):
        kind = "Carpeta ignorada" if is_dir else "Archivo ignorado"
        _warn(warnings, f"{kind}: {label} es un enlace.")
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    if is_dir:
        return stat.S_ISDIR(info.st_mode)
    if not stat.S_ISREG(info.st_mode):
        return False
    if info.st_nlink > 1:
        _warn(warnings, f"Archivo ignorado: {label} tiene enlaces duros.")
        return False
    return True


# --- reading artifacts -------------------------------------------------------

def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_MD_BYTES:
            return None
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def summary(text: str | None) -> str | None:
    """Text of the `## Summary` section (up to the next heading), joined into one paragraph."""
    if not text:
        return None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "## summary":
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("#"):
                    break
                body.append(nxt.strip())
            joined = " ".join(" ".join(body).split())
            return joined or None
    return None


def verdict(text: str | None) -> str | None:
    """Contract token of an artifact's first line: `GATE: PASS` -> PASS, `ESCALATE` -> ESCALATE."""
    line = first_line(text)
    if not line:
        return None
    match = _TOKEN.match(line)
    return match.group(2) if match else None


def _commit(text: str | None) -> str | None:
    for line in (text or "").splitlines()[:3]:
        match = _COMMIT.match(line.strip())
        if match:
            return match.group(1)
    return None


def _round_dirs(job_dir: Path, warnings: list | None = None) -> list[tuple[int, Path]]:
    found = []
    try:
        children = list(job_dir.iterdir())
    except OSError:
        return []
    for child in children:
        match = _ROUND_DIR.match(child.name)
        if match and _entry_ok(child, child.name, warnings, is_dir=True):
            found.append((int(match.group(1)), child))
    return sorted(found)


def _md_files(directory: Path, warnings: list | None = None, prefix: str = "") -> list[Path]:
    try:
        children = sorted(p for p in directory.iterdir() if p.suffix == ".md")
    except OSError:
        return []
    return [p for p in children if _entry_ok(p, prefix + p.name, warnings, is_dir=False)]


def read_rounds(job_dir: Path, warnings: list | None = None) -> list[dict]:
    rounds = []
    for n, rdir in _round_dirs(job_dir, warnings):
        files = _md_files(rdir, warnings, rdir.name + "/")
        by_name = {p.name: p for p in files}
        checks = _read_text(by_name["checks.md"]) if "checks.md" in by_name else None
        build = _read_text(by_name["build.md"]) if "build.md" in by_name else None
        reviews = []
        for path in files:
            match = _REVIEW_FILE.match(path.name)
            if match:
                reviews.append({"lens": match.group(1), "verdict": verdict(_read_text(path))})
        rounds.append({
            "n": n,
            "build": verdict(build),
            "gate": {"verdict": verdict(checks), "commit": _commit(checks)} if checks else None,
            "reviews": reviews,
        })
    return rounds


def list_artifacts(job_dir: Path, warnings: list | None = None) -> list[str]:
    """Relative POSIX paths of the job's .md artifacts: root and `round-N/` only."""
    def key(rel: str):
        if "/" in rel:
            n = int(_ROUND_DIR.match(rel.split("/")[0]).group(1))
            name = rel.split("/")[1]
            order = {"build.md": 0, "checks.md": 1}.get(name, 2)
            return (1, n, order, name)
        if rel in ROOT_ORDER:
            return (0, ROOT_ORDER.index(rel), 0, rel)
        if rel == "decision.md":
            return (2, 0, 0, rel)
        return (0, len(ROOT_ORDER), 0, rel)

    rels = [p.name for p in _md_files(job_dir, warnings)]
    for n, rdir in _round_dirs(job_dir, warnings):
        rels += [f"{rdir.name}/{p.name}" for p in _md_files(rdir, warnings, rdir.name + "/")]
    return sorted((r for r in rels if _SAFE_MD.match(r)), key=key)


# --- events ------------------------------------------------------------------

def read_events(job_dir: Path) -> tuple[list[dict], list[str]]:
    path = job_dir / "events.jsonl"
    events, warnings = [], []
    if not _entry_ok(path, "events.jsonl", warnings, is_dir=False):
        return events, warnings
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return events, ["No pude leer events.jsonl."]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"events.jsonl: línea {number} no es JSON; la salto.")
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            warnings.append(f"events.jsonl: línea {number} no es un objeto; la salto.")
    return events, warnings


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _clean(value: float) -> float | int:
    value = round(value, 3)
    return int(value) if value == int(value) else value


def durations(events: list[dict]) -> dict:
    totals: dict[str, float] = {}
    for event in events:
        stage, secs = event.get("from"), _number(event.get("duration_s"))
        if isinstance(stage, str) and secs is not None:
            totals[stage] = totals.get(stage, 0) + secs
    return {stage: _clean(secs) for stage, secs in totals.items()}


# --- jobs --------------------------------------------------------------------

def _str(value) -> str | None:
    return value if isinstance(value, str) else None


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _state(stage: str, rounds: list[dict]) -> str:
    if stage == "merged":
        return "ok"
    if stage == "rejected":
        return "off"
    if stage == "needs-human":
        return "human"
    if stage == "needs-info":
        return "wait"
    if stage == "build" and rounds:
        last = rounds[-1]
        gate = (last["gate"] or {}).get("verdict")
        if gate == "FAIL" or any(r["verdict"] == "CHANGES" for r in last["reviews"]):
            return "retry"
    return "run"


def _elapsed(job: dict, now: datetime) -> float | int | None:
    try:
        start = from_iso(job["started"])
        end = from_iso(job["ended"]) if job.get("ended") else now
    except (KeyError, TypeError, ValueError):
        return None
    return _clean(max((end - start).total_seconds(), 0.0))


def build_job(jid: str, job_dir: Path, raw: dict, now: datetime) -> dict:
    stage = _str(raw.get("stage")) or "intake"
    intake = raw.get("intake") if isinstance(raw.get("intake"), dict) else {}
    warnings: list[str] = []
    root_md = {p.name: p for p in _md_files(job_dir, warnings)}
    intake_text = _read_text(root_md["intake.md"]) if "intake.md" in root_md else None
    decision_text = _read_text(root_md["decision.md"]) if "decision.md" in root_md else None
    rounds = read_rounds(job_dir, warnings)
    events, event_warnings = read_events(job_dir)
    warnings += event_warnings
    models = [{"stage": e.get("from"), "model": e["model"], "tier": _str(e.get("tier"))}
              for e in events if _str(e.get("model"))]
    tiers = [e["tier"] for e in events if _str(e.get("tier"))]
    decision = verdict(decision_text)
    cost = _number(raw.get("cost_usd"))
    surfaces = intake.get("surfaces")
    return {
        "id": jid,
        "issue": _int(raw.get("issue")),
        "title": _str(raw.get("title")) or jid,
        "pr": _int(raw.get("pr")),
        "branch": _str(raw.get("branch")),
        "stage": stage,
        "stage_since": _str(raw.get("stage_since")),
        "started": _str(raw.get("started")),
        "ended": _str(raw.get("ended")),
        "column": COLUMNS.get(stage, "intake"),
        "state": _state(stage, rounds),
        "risk": _str(intake.get("risk")),
        "surfaces": [s for s in surfaces if isinstance(s, str)] if isinstance(surfaces, list) else [],
        "intake_decision": _str(intake.get("decision")) or verdict(intake_text),
        "decision": decision,
        "human_reason": summary(decision_text) if decision == "ESCALATE" else None,
        "counters": {k: _int(raw.get(k)) for k in ("review_rounds", "gate_rounds",
                                                   "sync_attempts", "total_rounds",
                                                   "max_total_rounds")},
        "round": rounds[-1]["n"] if rounds else 0,
        "rounds": rounds,
        "latest": rounds[-1] if rounds else None,
        "durations": durations(events),
        "elapsed_s": _elapsed(raw, now),
        "cost_usd": cost,
        "tier": tiers[-1] if tiers else None,
        "models": models,
        "warnings": warnings,
    }


def _job_dir(root: Path, jid: str) -> Path | None:
    """The job directory for a sanitized id, or None when it is not a job."""
    try:
        jid = sanitize_id(jid)
    except InvalidId:
        return None
    directory = Path(root) / jid
    if not _entry_ok(directory, jid, is_dir=True):
        return None
    if not _entry_ok(directory / "job.json", f"{jid}/job.json", is_dir=False):
        return None
    return directory


def collect(root: Path, now: datetime) -> tuple[list[dict], list[str]]:
    jobs, warnings = [], []
    try:
        children = sorted(p for p in Path(root).iterdir() if p.is_dir())
    except OSError:
        return jobs, warnings
    for child in children:
        if not (child / "job.json").is_file():
            continue
        try:
            jid = sanitize_id(child.name)
        except InvalidId:
            warnings.append(f"Carpeta ignorada: «{child.name}» no es un id de job válido.")
            continue
        if not _entry_ok(child, jid, warnings, is_dir=True):
            continue
        if not _entry_ok(child / "job.json", f"{jid}/job.json", warnings, is_dir=False):
            continue
        try:
            raw = json.loads((child / "job.json").read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            warnings.append(f"Job ignorado: no pude leer {jid}/job.json.")
            continue
        if not isinstance(raw, dict):
            warnings.append(f"Job ignorado: {jid}/job.json no es un objeto.")
            continue
        jobs.append(build_job(jid, child, raw, now))
    jobs.sort(key=lambda j: (j["issue"] is None, j["issue"] or 0, j["id"]))
    return jobs, warnings


def stats(jobs: list[dict]) -> dict:
    merged = sum(1 for j in jobs if j["stage"] == "merged")
    closed = sum(1 for j in jobs if j["stage"] in ("merged", "rejected"))
    human = sum(1 for j in jobs if j["stage"] == "needs-human")
    costs = [j["cost_usd"] for j in jobs if j["cost_usd"] is not None]
    p50 = {}
    for stage in P50_STAGES:
        values = [j["durations"][stage] for j in jobs if stage in j["durations"]]
        if values:
            p50[stage] = _clean(statistics.median(values))
    return {
        "active": len(jobs) - closed - human,
        "needs_human": human,
        "merged": merged,
        "closed": closed,
        "merge_rate": round(100 * merged / closed) if closed else None,
        "cost_usd": round(sum(costs), 6) if costs else None,
        "p50": p50,
    }


def _config(path: str | Path | None = None) -> dict:
    """Display-only reads of atta/config.json; the spine validates it elsewhere."""
    try:
        cfg = json.loads(Path(path or CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def jobs_payload(root: Path, now: datetime, config_file: str | Path | None = None) -> dict:
    jobs, warnings = collect(root, now)
    cfg = _config(config_file)
    branch = cfg.get("integration_branch")
    max_rounds = _int(cfg.get("max_rounds"))
    return {
        "generated": to_iso(now),
        "integration_branch": branch if isinstance(branch, str) and branch else "develop",
        "max_rounds": max_rounds if max_rounds and max_rounds > 0 else 3,
        "jobs": jobs,
        "stats": stats(jobs),
        "warnings": warnings,
    }


def files_payload(job_dir: Path) -> dict:
    files, warnings = [], []
    for rel in list_artifacts(job_dir, warnings):
        text = _read_text(job_dir / rel)
        files.append({"path": rel, "first_line": first_line(text), "summary": summary(text)})
    return {"job": job_dir.name, "files": files, "warnings": warnings}


def resolve_artifact(job_dir: Path, requested: str) -> Path | None:
    """The artifact for `requested` only if it is one of the listed .md files of this job."""
    if requested not in list_artifacts(job_dir):
        return None
    path = job_dir / requested
    # Checked again right before opening: still a regular, single-link file of its own dir.
    return path if _entry_ok(path, requested, is_dir=False) else None


# --- HTTP --------------------------------------------------------------------

def static_files(directory: Path = STATIC_DIR) -> dict[str, Path]:
    try:
        return {p.name: p for p in directory.iterdir()
                if p.is_file() and p.suffix in CONTENT_TYPES and not p.name.startswith(".")}
    except OSError:
        return {}


def is_loopback(host: str) -> bool:
    """`localhost`, any 127.0.0.0/8 address or ::1 (bracketed or not)."""
    if not isinstance(host, str) or not host:
        return False
    if host.lower() == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host_names(host: str) -> frozenset:
    """Host header names accepted by a server listening on `host`."""
    own = f"[{host.lower()}]" if ":" in host else host.lower()
    return LOOPBACK_NAMES | {own}


class BoardHandler(BaseHTTPRequestHandler):
    server_version = "AttaBoard"
    sys_version = ""
    # A connection that never sends its headers is closed instead of holding a thread.
    timeout = REQUEST_TIMEOUT_S

    # Set by make_server.
    jobs_dir: Path = DEFAULT_JOBS_DIR
    config_file: Path | None = None
    static: dict = {}
    now: Callable[[], datetime] = staticmethod(utcnow)
    quiet = False
    check_host = True
    host_names = LOOPBACK_NAMES

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        if not self.quiet:
            sys.stderr.write(f"hormiguero: {self.address_string()} {format % args}\n")

    # --- responses ---

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str, extra: dict | None = None) -> None:
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    # --- routing ---

    def _host_ok(self) -> bool:
        if not self.check_host:
            return True
        values = self.headers.get_all("Host") or []
        if len(values) != 1:
            return False
        match = _HOST.match(values[0])
        return bool(match) and match.group(1).lower() in self.host_names

    def do_GET(self):  # noqa: N802 - stdlib naming
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # never leak a traceback to the client
            self.log_message("error interno en %s", self.command)
            self._error(500, "Error interno del hormiguero.")

    do_HEAD = do_GET

    def _refuse_write(self):
        self._error(405, "El hormiguero es de solo lectura.", {"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse_write

    def _route(self) -> None:
        if not self._host_ok():
            self._error(400, "Host no permitido.")
            return
        parts = urlsplit(self.path)
        raw = parts.path
        if raw in ("/", "/index.html"):
            self._static("index.html")
        elif raw == "/api/jobs":
            self._json(200, jobs_payload(self.jobs_dir, self.now(), self.config_file))
        elif raw.startswith("/api/"):
            self._api(raw, parts.query)
        elif raw.startswith("/") and raw[1:] in self.static:
            self._static(raw[1:])
        else:
            self._error(404, "No existe.")

    def _static(self, name: str) -> None:
        path = self.static.get(name)
        if path is None:
            self._error(404, "No existe.")
            return
        self._send(200, path.read_bytes(), CONTENT_TYPES[path.suffix])

    def _api(self, raw: str, query: str) -> None:
        match = _ROUTE_FILES.match(raw) or _ROUTE_FILE.match(raw)
        job_dir = _job_dir(self.jobs_dir, match.group(1)) if match else None
        if job_dir is None:
            self._error(404, "No existe ese job.")
            return
        if raw.endswith("/files"):
            self._json(200, files_payload(job_dir))
            return
        values = parse_qs(query, keep_blank_values=True).get("path", [])
        if len(values) != 1 or not values[0]:
            self._error(400, "Falta el parámetro path.")
            return
        requested = values[0]
        if not requested.lower().endswith(".md"):
            self._error(400, "Solo se leen artefactos .md.")
            return
        path = resolve_artifact(job_dir, requested)
        if path is None:
            self._error(404, "No existe ese artefacto.")
            return
        self._send(200, path.read_bytes(), "text/plain; charset=utf-8")


class _BoardServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets another process bind the same port; never use it there.
    allow_reuse_address = sys.platform != "win32"


class _BoardServer6(_BoardServer):
    address_family = socket.AF_INET6


def make_server(jobs_dir: str | Path = DEFAULT_JOBS_DIR, host: str = DEFAULT_HOST,
                port: int = DEFAULT_PORT, *, now: Callable[[], datetime] | None = None,
                quiet: bool = False, static_dir: str | Path = STATIC_DIR,
                request_timeout: float = REQUEST_TIMEOUT_S,
                config_file: str | Path | None = None) -> ThreadingHTTPServer:
    handler = type("Handler", (BoardHandler,), {
        "jobs_dir": Path(jobs_dir),
        "config_file": Path(config_file) if config_file else None,
        "static": static_files(Path(static_dir)),
        "now": staticmethod(now or utcnow),
        "quiet": quiet,
        "timeout": request_timeout,
        # A foreign Host header on a loopback server means DNS rebinding: refuse it.
        "check_host": is_loopback(host),
        "host_names": _host_names(host),
    })
    server_cls = _BoardServer6 if ":" in host else _BoardServer
    return server_cls((host, port), handler)


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="atta-board",
        description="El hormiguero: tablero de solo lectura de los jobs de Atta.",
    )
    parser.add_argument("--project", metavar="<carpeta>",
                        help=f"raíz del proyecto (por defecto ${project.ENV_VAR}, la raíz git de "
                             "la carpeta actual o la carpeta actual)")
    parser.add_argument("--jobs-dir",
                        help="carpeta de jobs (por defecto atta/jobs del proyecto)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"puerto (por defecto {DEFAULT_PORT})")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"interfaz (por defecto {DEFAULT_HOST}, solo esta máquina)")
    parser.add_argument("--quiet", action="store_true", help="no registrar cada petición")
    return parser


def resolve_paths(args) -> tuple[Path, Path]:
    """(jobs dir, config file) of the project; `--jobs-dir` overrides the jobs dir."""
    root = project.find_root(args.project)
    jobs_dir = Path(args.jobs_dir) if args.jobs_dir else project.jobs_dir(root)
    return jobs_dir, project.config_path(root)


def main(argv: list[str] | None = None) -> int:
    # Pipes get UTF-8 (Windows would print «m?quina»); consoles never crash on «·» or tildes.
    project.utf8_streams()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        jobs_dir, config_file = resolve_paths(args)
    except project.ProjectError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        server = make_server(jobs_dir, args.host, args.port, quiet=args.quiet,
                             config_file=config_file)
    except OSError as exc:
        print(f"El hormiguero no pudo abrir {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    host, port = server.server_address[:2]
    shown = f"[{host}]" if ":" in str(host) else host
    print(f"El hormiguero está abierto en http://{shown}:{port}/ · Ctrl+C para cerrarlo.",
          flush=True)
    if not is_loopback(args.host):
        print("Aviso: escucha fuera de esta máquina; el tablero no tiene autenticación.",
              file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("El hormiguero se cerró.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
