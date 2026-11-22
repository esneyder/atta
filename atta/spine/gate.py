"""El Filtro: deterministic gate run on a job worktree.

`atta/checks.json` (always read from the MAIN checkout, never from the worktree) has this shape::

    {
      "checks": [
        {"name": "tests", "run": "python -m unittest", "shell": {"windows": "pwsh", "default": "bash"}},
        {"name": "lint", "run": "ruff check .", "shell": "bash", "timeout_s": 120}
      ],
      "timeout_s": 900
    }

- `checks`: ordered list; they run in this order with the worktree as cwd and the gate stops at the
  first failure (fail-fast). An empty list makes the gate FAIL: a project without real checks never
  passes silently.
- `name`: non-empty text shown in the report. `run`: the command, passed as ONE argument to the
  shell's command flag (`bash -c`, `sh -c`, `pwsh -Command`, `powershell -Command`, `cmd /c`).
- `shell`: a shell name or a map by OS with keys `windows`, `macos`, `linux` and `default`.
  Optional; when missing the first shell found on PATH is used: `pwsh`, `powershell`, `cmd` on
  Windows; `bash`, `sh` elsewhere. An explicit shell is never swapped silently.
- `timeout_s`: per-check limit in seconds (global value, overridable per check). Default 900.
  It holds even if a background child keeps the output open: output goes to a temp file, the
  whole process tree is killed on timeout.

Before the checks the gate validates `base_ref`: it must not be empty nor start with `-`, it must
resolve to a commit different from HEAD, share history with HEAD (a merge-base exists) and not
already contain HEAD. Otherwise GateError: a base can never hide the diff. Every git command runs
with `--no-replace-objects` (and `GIT_NO_REPLACE_OBJECTS=1`), so `git replace` cannot disguise it.

The gate FAILS without running checks when:
- the diff `<base>...HEAD` adds, modifies, deletes, renames, copies or retypes ANY file whose path
  (original or new) matches `protected_paths` from config.json (globs with `**`, compared without
  case, backslashes and a leading `./` normalised);
- the worktree has uncommitted changes (`git status --porcelain` not empty, or index entries
  hidden with skip-worktree / assume-unchanged).

For misconfiguration (bad checks.json, bad base_ref, bad risk_rules) it raises GateError and
writes no report.

Riesgo por reglas (G3, see risk.py): when `checks` passes, the diff is classified with the
config's `risk_rules`; checks.md gets a `## Riesgo` section and, when `<round_dir>/..` is a job
folder, job.json gets `risk_rules` and `risk_effective` (never lower than before). An effective
risk above the routed one re-plans the review and is appended to the summary
(«El Filtro subió el riesgo a alto: migrations/0003.sql (datos).»). `merge-checks` and a FAIL
never touch the risk.

The report goes to `<round_dir>/<kind>.md` (first line `GATE: PASS|FAIL`, second `COMMIT: <sha>`)
and the combined output of the checks to `<round_dir>/<kind>.log`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import ids, risk as risk_rules

CHECKS_RELPATH = Path("atta") / "checks.json"
KINDS = ("checks", "merge-checks")
DEFAULT_TIMEOUT_S = 900
# Default shell candidates by platform, in order of preference (first one found on PATH wins).
DEFAULT_SHELLS = {"windows": ("pwsh", "powershell", "cmd"), "default": ("bash", "sh")}
KILL_GRACE_S = 2
TAIL_LINES = 80
DIRTY_SUMMARY = "El Filtro no pasó: el worktree tiene cambios sin commitear."
# Environment that could point git at another repository/index; dropped for gate git calls.
_GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
                 "GIT_REPLACE_REF_BASE", "GIT_GRAFT_FILE")
PLATFORM_KEYS = ("windows", "macos", "linux")

# Shell name -> flags placed between the executable and the `run` string.
SHELLS = {
    "bash": ["-c"],
    "sh": ["-c"],
    "pwsh": ["-NoProfile", "-NonInteractive", "-Command"],
    "powershell": ["-NoProfile", "-NonInteractive", "-Command"],
    "cmd": ["/d", "/s", "/c"],
}

PLACEHOLDER_HINT = "Agrega los checks reales del proyecto en atta/checks.json."


class GateError(RuntimeError):
    """The gate is misconfigured (never raised for a failing check)."""


@dataclass
class GateResult:
    passed: bool
    commit: str
    failed_check: str | None
    duration_s: float
    summary: str
    report: Path
    log: Path
    risk: dict | None = None  # {risk, categories, reasons} when `checks` passed


@dataclass
class _CheckRun:
    name: str
    exit_code: int | None
    duration_s: float
    timed_out: bool
    output: str

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


# ---------------------------------------------------------------- platform and shells

def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _find_shell(name: str, which=shutil.which) -> str | None:
    exe = which(name)
    if exe is None and name == "cmd":
        exe = os.environ.get("COMSPEC")
    return exe or None


def default_shell(key: str | None = None, which=shutil.which) -> str:
    """First shell of DEFAULT_SHELLS[key] found on PATH (the last candidate if none is found)."""
    key = key or platform_key()
    candidates = DEFAULT_SHELLS.get(key, DEFAULT_SHELLS["default"])
    for name in candidates:
        if _find_shell(name, which):
            return name
    return candidates[-1]


def select_shell(spec, key: str | None = None, *, which=shutil.which) -> str:
    """Shell name for platform `key` from a name or a {windows|macos|linux|default: name} map.

    With no `spec` the platform default is picked from what is installed (see DEFAULT_SHELLS).
    """
    key = key or platform_key()
    if spec is None:
        return default_shell(key, which)
    if isinstance(spec, str):
        name = spec
    elif isinstance(spec, dict):
        name = spec.get(key, spec.get("darwin") if key == "macos" else None) or spec.get("default")
        if not name:
            raise GateError(f"checks.json: shell no define «{key}» ni «default».")
    else:
        raise GateError("checks.json: shell debe ser un texto o un objeto por sistema operativo.")
    if name not in SHELLS:
        raise GateError(f"checks.json: shell «{name}» no soportado (usa: {', '.join(SHELLS)}).")
    return name


def shell_command(shell: str, run: str, *, platform_key: str | None = None,
                  which=shutil.which) -> list[str]:
    """Argument list that runs `run` as a single argument of the shell's command flag."""
    if shell not in SHELLS:
        raise GateError(f"checks.json: shell «{shell}» no soportado.")
    exe = _find_shell(shell, which)
    if not exe:
        raise GateError(f"No se encontró el shell «{shell}» en el PATH de este equipo.")
    return [exe, *SHELLS[shell], run]


def _popen_args(shell: str, argv: list[str]):
    # cmd.exe does not follow the MS C argv quoting rules: build its command line verbatim.
    # `/s /c "<run>"` strips only the outer quotes, so `run` reaches cmd unchanged.
    if shell == "cmd" and os.name == "nt":
        return f'"{argv[0]}" {" ".join(SHELLS["cmd"])} "{argv[-1]}"'
    return argv


# ---------------------------------------------------------------- checks.json

def load_checks(main_checkout: Path) -> dict:
    path = Path(main_checkout) / CHECKS_RELPATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError(f"No se pudo leer {path}: {exc.strerror or exc}.") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(f"checks.json no es JSON válido: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        raise GateError("checks.json debe ser un objeto con una lista «checks».")
    default_timeout = _timeout(data.get("timeout_s", DEFAULT_TIMEOUT_S), "timeout_s")
    checks = []
    for i, item in enumerate(data["checks"]):
        if not isinstance(item, dict):
            raise GateError(f"checks.json: checks[{i}] debe ser un objeto.")
        name, run = item.get("name"), item.get("run")
        if not isinstance(name, str) or not name.strip():
            raise GateError(f"checks.json: checks[{i}].name debe ser un texto no vacío.")
        if not isinstance(run, str) or not run.strip():
            raise GateError(f"checks.json: checks[{i}].run debe ser un texto no vacío.")
        checks.append({
            "name": name,
            "run": run,
            "shell": select_shell(item.get("shell")),
            "timeout_s": _timeout(item.get("timeout_s", default_timeout), f"checks[{i}].timeout_s"),
        })
    return {"checks": checks, "timeout_s": default_timeout}


def _timeout(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise GateError(f"checks.json: {label} debe ser un número mayor que 0.")
    return value


# ---------------------------------------------------------------- protected paths

def _glob_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _norm_path(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.casefold()


def matches_protected(path: str, globs) -> bool:
    """True when repo-relative `path` matches any glob, ignoring case (`ATTA/x` matches `atta/**`).

    Both sides are case-folded and normalised (backslashes -> `/`, leading `./` removed): on
    Windows/macOS a case variant lands on the same file when merged.
    """
    path = _norm_path(path)
    return any(_glob_regex(_norm_path(g)).match(path) for g in globs)


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_DROP}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git_run(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    # --no-replace-objects: `git replace` must never change what the gate sees.
    # core.fsmonitor=false: a configured fsmonitor hook could make `status` lie.
    argv = ["git", "--no-replace-objects", "-c", "core.fsmonitor=false", "-C", str(worktree),
            *args]
    try:
        return subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, env=_git_env())
    except OSError as exc:
        raise GateError(f"No se pudo ejecutar git: {exc}.") from exc


def _git(worktree: Path, *args: str) -> bytes:
    proc = _git_run(worktree, *args)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GateError(f"git {' '.join(args)} falló en {worktree}: {err}")
    return proc.stdout


def _sha(raw: bytes) -> str:
    return raw.decode("ascii", "replace").strip()


def resolve_base(worktree: Path, base_ref, head: str) -> str:
    """Validate `base_ref` and return its commit sha; GateError when it could hide the diff."""
    if not isinstance(base_ref, str) or not base_ref.strip():
        raise GateError("base_ref vacío: el Filtro necesita la rama base para comparar el diff.")
    if base_ref.lstrip().startswith("-"):
        raise GateError(f"base_ref «{base_ref}» no es válido: no puede empezar con «-».")
    proc = _git_run(worktree, "rev-parse", "--verify", "--quiet", "--end-of-options",
                    f"{base_ref}^{{commit}}")
    base = _sha(proc.stdout)
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base):
        raise GateError(f"base_ref «{base_ref}» no existe como commit en {worktree}.")
    if base == head:
        raise GateError(f"base_ref «{base_ref}» apunta al mismo commit que HEAD: "
                        "el diff quedaría vacío.")
    proc = _git_run(worktree, "merge-base", "--end-of-options", base, head)
    merge_base = _sha(proc.stdout)
    if proc.returncode != 0 or not merge_base:
        raise GateError(f"base_ref «{base_ref}» no comparte historia con HEAD (no hay merge-base).")
    if merge_base == head:
        raise GateError(f"base_ref «{base_ref}» ya contiene HEAD: el diff quedaría vacío.")
    return base


def protected_violations(worktree: Path, base: str, globs,
                         head: str = "HEAD") -> list[tuple[str, tuple[str, ...]]]:
    """(status letter, paths) of every diff entry `<base>...<head>` touching a protected path.

    Any status counts (A, M, D, R, C, T, U, ...); renames and copies are checked on BOTH paths.
    `base` and `head` should be resolved shas (see resolve_base).
    """
    raw = _git(worktree, "diff", "--no-ext-diff", "--ignore-submodules=none", "--name-status",
               "-z", "-M", "--end-of-options", f"{base}...{head}", "--")
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        letter = fields[i][0]
        npaths = 2 if letter in "RC" else 1
        paths = tuple(fields[i + 1:i + 1 + npaths])
        i += 1 + npaths
        if any(matches_protected(p, globs) for p in paths):
            out.append((letter, paths))
    return out


def dirty_entries(worktree: Path) -> list[str]:
    """Uncommitted changes in the worktree, including index entries hidden from `status`."""
    status = _git(worktree, "status", "--porcelain=v1", "--untracked-files=normal",
                  "--ignore-submodules=none").decode("utf-8", "replace")
    out = [line for line in status.splitlines() if line.strip()]
    # `ls-files -v`: lowercase tag = assume-unchanged, `S` = skip-worktree; both hide edits.
    listing = _git(worktree, "ls-files", "-v", "-z").decode("utf-8", "replace")
    for entry in listing.split("\0"):
        if len(entry) > 2 and (entry[0].islower() or entry[0] == "S"):
            out.append(f"{entry[0]} {entry[2:]} (oculto del status)")
    return out


def _entry_text(paths) -> str:
    return " → ".join(f"`{p}`" for p in paths)


# ---------------------------------------------------------------- running checks

def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_check(check: dict, worktree: Path) -> _CheckRun:
    argv = shell_command(check["shell"], check["run"])
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    # Output goes to a temp file, not a pipe: a background child that keeps the handle open can
    # never make the gate wait past the timeout for an EOF.
    with tempfile.TemporaryFile() as sink:
        start = time.monotonic()
        try:
            proc = subprocess.Popen(_popen_args(check["shell"], argv), cwd=str(worktree),
                                    stdin=subprocess.DEVNULL, stdout=sink,
                                    stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            raise GateError(f"No se pudo lanzar el check «{check['name']}»: {exc}.") from exc
        timed_out = False
        try:
            proc.wait(timeout=check["timeout_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            try:
                proc.wait(timeout=KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                pass
        duration = round(time.monotonic() - start, 3)
        sink.seek(0)
        out = sink.read()
    return _CheckRun(check["name"], None if timed_out else proc.returncode, duration, timed_out,
                     out.decode("utf-8", "replace"))


# ---------------------------------------------------------------- report

def _secs(value: float) -> str:
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text or "0"


def _tail(text: str, n: int = TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _job_id(job) -> str:
    value = job.get("id") if isinstance(job, dict) else job
    return ids.sanitize_id(value)


def run_gate(main_checkout: Path, worktree: Path, job, round_dir: Path, *, base_ref: str,
             config: dict, kind: str = "checks") -> GateResult:
    """Run El Filtro on `worktree` and write `<round_dir>/<kind>.md` and `.log`.

    Returns a GateResult; raises GateError only for misconfiguration.
    """
    if kind not in KINDS:
        raise GateError(f"Tipo de filtro desconocido «{kind}» (usa: {', '.join(KINDS)}).")
    job_id = _job_id(job)
    main_checkout, worktree, round_dir = Path(main_checkout), Path(worktree), Path(round_dir)
    globs = config.get("protected_paths")
    if not isinstance(globs, list) or not all(isinstance(g, str) and g for g in globs):
        raise GateError("config.json: protected_paths debe ser una lista de textos.")
    spec = load_checks(main_checkout)
    commit = _sha(_git(worktree, "rev-parse", "--verify", "HEAD^{commit}"))
    base = resolve_base(worktree, base_ref, commit)
    violations = protected_violations(worktree, base, globs, commit)
    dirty = dirty_entries(worktree)

    start = time.monotonic()
    runs: list[_CheckRun] = []
    failed: _CheckRun | None = None
    if not violations and not dirty:
        for check in spec["checks"]:
            run = _run_check(check, worktree)
            runs.append(run)
            if not run.ok:
                failed = run
                break
    duration = round(time.monotonic() - start, 3)

    checks = spec["checks"]
    if violations:
        listed = ", ".join(_entry_text(paths) for _, paths in violations)
        summary = f"El Filtro no pasó: el diff toca rutas protegidas ({listed})."
        failed_name = None
    elif dirty:
        summary = DIRTY_SUMMARY
        failed_name = None
    elif not checks:
        summary = f"El Filtro no pasó: atta/checks.json no tiene checks. {PLACEHOLDER_HINT}"
        failed_name = None
    elif failed is not None and failed.timed_out:
        limit = next(c["timeout_s"] for c in checks if c["name"] == failed.name)
        summary = f"El Filtro no pasó: `{failed.name}` superó el tiempo límite ({_secs(limit)} s)."
        failed_name = failed.name
    elif failed is not None:
        summary = f"El Filtro no pasó: falló `{failed.name}` (exit {failed.exit_code})."
        failed_name = failed.name
    else:
        noun = "check" if len(checks) == 1 else "checks"
        summary = f"El Filtro pasó: {len(checks)} {noun} en {_secs(duration)} s."
        failed_name = None
    passed = not violations and not dirty and bool(checks) and failed is None

    assessed = None
    if passed and kind == "checks":
        assessed = _assess_risk(worktree, base, commit, job, round_dir, config)
        if assessed["outcome"]["message"]:
            summary = f"{summary} {assessed['outcome']['message']}"

    round_dir.mkdir(parents=True, exist_ok=True)
    log_path = round_dir / f"{kind}.log"
    log = [f"# {kind} · job {job_id} · commit {commit}\n"]
    for run in runs:
        status = "timeout" if run.timed_out else f"exit {run.exit_code}"
        log.append(f"\n== {run.name} ({status}, {_secs(run.duration_s)} s) ==\n{run.output}")
    log_path.write_text("".join(log), encoding="utf-8")

    md = [f"GATE: {'PASS' if passed else 'FAIL'}", f"COMMIT: {commit}", "",
          "## Summary", "", summary, "", "## Protected paths", "", f"Base: `{base}`", ""]
    if violations:
        md += [f"- {letter} {_entry_text(paths)}" for letter, paths in violations]
    else:
        md.append("Ninguna ruta protegida tocada.")
    md += ["", "## Worktree", ""]
    if dirty:
        md.append("Cambios sin commitear:")
        md += [f"- `{line}`" for line in dirty[:TAIL_LINES]]
    else:
        md.append("Limpio.")
    md += ["", "## Checks", ""]
    if violations:
        md.append("No se corrieron: el diff toca rutas protegidas.")
    elif dirty:
        md.append("No se corrieron: el worktree tiene cambios sin commitear.")
    elif not checks:
        md.append(f"No hay checks configurados. {PLACEHOLDER_HINT}")
    else:
        ran = {r.name: r for r in runs}
        for check in checks:
            run = ran.get(check["name"])
            if run is None:
                md.append(f"- `{check['name']}`: no se corrió (fail-fast).")
                continue
            status = "timeout" if run.timed_out else f"exit {run.exit_code}"
            md.append(f"- `{run.name}`: {status}, {_secs(run.duration_s)} s")
        if failed is not None:
            md += ["", f"Últimas {TAIL_LINES} líneas de `{failed.name}`:", "", "```",
                   _tail(failed.output), "```"]
    if assessed is not None:
        md += ["", *risk_rules.report_lines(assessed["job"], assessed["found"],
                                            assessed["outcome"])]
    (round_dir / f"{kind}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if assessed is not None:
        risk_rules.record(round_dir.parent, job_id, assessed["found"], assessed["outcome"])

    return GateResult(passed, commit, failed_name, duration, summary,
                      round_dir / f"{kind}.md", log_path,
                      assessed["found"] if assessed is not None else None)


def _assess_risk(worktree: Path, base: str, commit: str, job, round_dir: Path,
                 config: dict) -> dict:
    """Classify the diff and evaluate the effective risk (pure: nothing is written here).

    The job is read from `<round_dir>/../job.json` when it exists (the freshest copy), else
    the `job` dict given (or nothing: an unmeasured intake)."""
    from .router import RoutingError
    from .store import JobStore, StoreError
    rules = risk_rules.rules_of(config)
    problem = risk_rules.rules_error(rules)
    if problem:
        raise GateError(f"config.json: {problem}")
    current = job if isinstance(job, dict) else {}
    job_dir = round_dir.parent
    if (job_dir / "job.json").is_file():
        try:
            current = JobStore(job_dir.parent).load(_job_id(job))
        except StoreError as exc:
            raise GateError(str(exc)) from None
    found, hits = risk_rules.assess(risk_rules.changes(worktree, base, commit), rules)
    try:
        outcome = risk_rules.evaluate(current, found, hits, config)
    except RoutingError as exc:
        raise GateError(str(exc)) from None
    return {"job": current, "found": found, "outcome": outcome}
