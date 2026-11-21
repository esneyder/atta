"""`atta init` (S2): prepares the resolved project for the colony.

What it does, on the project root:

- `.claude/agents/atta-*.md` from the packaged templates, with `{{model:Tn}}` resolved through
  `templates/claude/models.json` for the runtime (S5: T1 haiku, T2 sonnet, T3 opus), and
  `.claude/skills/atta/SKILL.md` when that template exists.
- `.claude/settings.json`: created from the template, or the template hooks are appended to the
  user's file when missing (idempotent; user entries are never removed or reordered).
- `atta/config.json` and `atta/checks.json` from the packaged defaults, only when missing.
- `/atta/jobs/` and `/.worktrees/` in `.gitignore` (appended, no duplicates).
- Unless `--no-github`: checks `origin/<integration_branch>` with `git ls-remote` and creates the
  config labels with `gh label create ... --force` (`gh` from $ATTA_GH, default `gh`). Never
  touches main.

Existing files that differ are kept (reported «ya existe, la dejé igual») unless `--force`, which
replaces only the agents and the skill. Every check runs before the first write: an invalid
`.claude/settings.json` exits 2 and changes nothing. Exit codes: 0 ok, 1 GitHub warning
(missing develop, label not created), 2 refused.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from . import config, project

OK, WARNING, REFUSED = 0, 1, 2
RUNTIMES = ("claude-code",)
GH_ENV = "ATTA_GH"
GITIGNORE_ENTRIES = ("/atta/jobs/", "/.worktrees/")
NET_TIMEOUT_S = 60
_MODEL_RE = re.compile(r"\{\{model:([A-Za-z0-9]+)\}\}")
# Label key in config["labels"] → (color, description) for `gh label create`.
LABEL_STYLE = {
    "queue": ("0e8a16", "Atta: issue en la cola de la colonia"),
    "needs_info": ("fbca04", "Atta: falta información del autor del issue"),
    "needs_human": ("d93f0b", "Atta: necesita una decisión humana"),
}


class InitError(Exception):
    """A refused init: message to stderr, exit 2, nothing written."""


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InitError(f"No se pudo leer {label}: {exc.strerror or exc}. No cambié nada.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InitError(f"{label} no es JSON válido ({exc}). Corrígelo y vuelve a correr "
                        "`atta init`; no cambié nada.") from exc


def _models(templates: Path, runtime: str) -> dict:
    path = templates / "claude" / "models.json"
    table = _read_json(path, "la plantilla models.json")
    models = table.get(runtime) if isinstance(table, dict) else None
    if not isinstance(models, dict):
        raise InitError(f"models.json no define el runtime «{runtime}». No cambié nada.")
    return models


def _resolve(text: str, models: dict, name: str) -> str:
    def sub(match: re.Match) -> str:
        tier = match.group(1)
        model = models.get(tier)
        if not isinstance(model, str) or not model:
            raise InitError(f"La plantilla {name} pide {{{{model:{tier}}}}}, que models.json no "
                            "define. No cambié nada.")
        return model
    return _MODEL_RE.sub(sub, text)


def _read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _hooks_of(data, label: str) -> dict:
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) for v in hooks.values()):
        raise InitError(f"{label}: «hooks» debe ser un objeto de listas. Corrígelo y vuelve a "
                        "correr `atta init`; no cambié nada.")
    return hooks


def _merge_settings(user, template) -> tuple[dict, bool]:
    """User settings plus every template hook entry it lacks (appended in template order)."""
    changed = False
    hooks = user.setdefault("hooks", {})
    for event, entries in _hooks_of(template, "La plantilla settings.json").items():
        current = hooks.get(event)
        if current is None:
            current = hooks[event] = []
        for entry in entries:
            if entry not in current:
                current.append(entry)
                changed = True
    return user, changed


def _gitignore(path: Path) -> tuple[bytes | None, bool]:
    """New .gitignore bytes and whether the file is new; (None, False) when nothing is missing."""
    if not path.exists():
        return ("\n".join(GITIGNORE_ENTRIES) + "\n").encode("utf-8"), True
    try:
        text = _read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise InitError(f"No se pudo leer .gitignore: {exc}. No cambié nada.") from exc
    present = {line.strip() for line in text.splitlines()}
    missing = [e for e in GITIGNORE_ENTRIES if e not in present]
    if not missing:
        return None, False
    nl = "\r\n" if "\r\n" in text else "\n"
    if text and not text.endswith(("\n", "\r")):
        text += nl
    return (text + nl.join(missing) + nl).encode("utf-8"), False


class _Plan:
    def __init__(self, root: Path, force: bool):
        self.root, self.force = root, force
        self.writes: list[tuple[Path, bytes]] = []
        self.lines: list[str] = []
        self.unchanged = 0

    def file(self, path: Path, data: bytes, *, replaceable: bool) -> None:
        rel = _rel(self.root, path)
        if not path.exists():
            self.writes.append((path, data))
            self.lines.append(f"creé: {rel}")
        elif path.read_bytes() == data:
            self.unchanged += 1
        elif replaceable and self.force:
            self.writes.append((path, data))
            self.lines.append(f"reemplacé: {rel}")
        else:
            self.lines.append(f"ya existe, la dejé igual: {rel}")

    def update(self, path: Path, data: bytes | None, created: bool) -> None:
        if data is None:
            self.unchanged += 1
            return
        self.writes.append((path, data))
        self.lines.append(f"{'creé' if created else 'actualicé'}: {_rel(self.root, path)}")


def _plan(root: Path, templates: Path, runtime: str, force: bool) -> _Plan:
    plan = _Plan(root, force)
    claude_t = templates / "claude"
    models = _models(templates, runtime)
    agents_dst = root / ".claude" / "agents"
    for src in sorted((claude_t / "agents").glob("atta-*.md")):
        text = _resolve(_read_text(src), models, src.name)
        plan.file(agents_dst / src.name, text.encode("utf-8"), replaceable=True)
    skill = claude_t / "skills" / "atta" / "SKILL.md"
    if skill.is_file():
        plan.file(root / ".claude" / "skills" / "atta" / "SKILL.md", skill.read_bytes(),
                  replaceable=True)

    settings_t = claude_t / "settings.json"
    if settings_t.is_file():
        template = _read_json(settings_t, "La plantilla settings.json")
        if not isinstance(template, dict):
            raise InitError("La plantilla settings.json debe ser un objeto JSON. No cambié nada.")
        _hooks_of(template, "La plantilla settings.json")
        dst = root / ".claude" / "settings.json"
        if not dst.exists():
            plan.update(dst, settings_t.read_bytes(), True)
        else:
            label = ".claude/settings.json"
            user = _read_json(dst, label)
            if not isinstance(user, dict):
                raise InitError(f"{label} debe ser un objeto JSON. Corrígelo y vuelve a correr "
                                "`atta init`; no cambié nada.")
            _hooks_of(user, label)
            merged, changed = _merge_settings(user, template)
            data = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            plan.update(dst, data if changed else None, False)

    plan.file(project.config_path(root), config.CONFIG_PATH.read_bytes(), replaceable=False)
    plan.file(project.checks_path(root), project.CHECKS_TEMPLATE.read_bytes(), replaceable=False)
    gi = root / ".gitignore"
    plan.update(gi, *_gitignore(gi))
    return plan


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".atta-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=NET_TIMEOUT_S)


def _last_line(proc: subprocess.CompletedProcess) -> str:
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"código {proc.returncode}"


def _github(root: Path, cfg: dict, environ: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """(summary lines, warnings). Checks origin/<integration branch> and creates the labels."""
    lines, warnings = [], []
    branch = cfg["integration_branch"]
    try:
        proc = _run(["git", "ls-remote", "--heads", "origin", branch], root)
        found = proc.returncode == 0 and any(
            line.split()[-1:] == [f"refs/heads/{branch}"] for line in proc.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        found = False
    if found:
        lines.append(f"origin/{branch} existe: ahí integra Atta.")
    else:
        warnings.append(
            f"Atta: no encontré la rama {branch} en origin. Atta integra solo en {branch} "
            f"(nunca en main): crea {branch} en origin y vuelve a correr `atta init`.")

    gh = (environ.get(GH_ENV) or "").strip() or "gh"
    for key, (color, description) in LABEL_STYLE.items():
        name = cfg["labels"][key]
        try:
            proc = _run([gh, "label", "create", name, "--color", color,
                         "--description", description, "--force"], root)
        except (OSError, subprocess.SubprocessError) as exc:
            warnings.append(f"Atta: no encontré gh ({gh}): instala GitHub CLI o define "
                            f"{GH_ENV}; no creé las labels ({exc}).")
            break
        if proc.returncode == 0:
            lines.append(f"label lista: {name}")
        else:
            warnings.append(f"Atta: no pude crear la label «{name}» con gh: {_last_line(proc)}")
    return lines, warnings


def run(root: str | Path, *, force: bool = False, no_github: bool = False,
        runtime: str = "claude-code", cfg: dict | None = None,
        templates_dir: str | Path | None = None,
        environ: Mapping[str, str] | None = None) -> int:
    """Prepare `root`; print the summary (stdout) and warnings/errors (stderr); return exit code."""
    root = Path(root).resolve()
    templates = Path(templates_dir) if templates_dir is not None else project.TEMPLATES_DIR
    environ = os.environ if environ is None else environ
    try:
        cfg = cfg if cfg is not None else project.load_config(root)
        plan = _plan(root, templates, runtime, force)
        for path, data in plan.writes:
            _write(path, data)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except config.ConfigError as exc:
        print(f"{exc} No cambié nada.", file=sys.stderr)
        return REFUSED
    except OSError as exc:
        print(f"No se pudo escribir en {root}: {exc.strerror or exc}.", file=sys.stderr)
        return REFUSED

    gh_lines, warnings = ([], []) if no_github else _github(root, cfg, environ)
    for w in warnings:
        print(w, file=sys.stderr)

    out = [f"Atta preparó {root}:"]
    out += [f"  {line}" for line in plan.lines]
    if plan.unchanged:
        out.append(f"  sin cambios: {plan.unchanged} archivo{'s' if plan.unchanged != 1 else ''}")
    if no_github:
        out.append("  GitHub: lo salté (--no-github).")
    else:
        out += [f"  {line}" for line in gh_lines]
        if warnings:
            out.append("  GitHub: hay avisos arriba; revísalos.")
    queue = cfg["labels"]["queue"]
    out += [
        "Siguientes pasos:",
        "  1. Llena atta/checks.json con los checks reales del proyecto "
        "(sin checks el Filtro siempre falla).",
        f"  2. Ponle la label «{queue}» a un issue.",
        "  3. En Claude Code, corre /atta run.",
        "— Atta",
    ]
    print("\n".join(out))
    return WARNING if warnings else OK


def cmd_init(args, cfg, jobs) -> int:
    """Entry point for cli.py (`atta init`); `jobs` is unused."""
    return run(args.root, force=args.force, no_github=args.no_github, runtime=args.runtime,
               cfg=cfg)


def register(subparsers) -> None:
    """Add `atta init` to the `atta` subparsers."""
    p = subparsers.add_parser(
        "init", help="Prepara el proyecto para Atta (agentes, skill, config, labels).",
        description="Prepara el proyecto: agentes y skill de Claude Code, hooks en "
                    ".claude/settings.json, atta/config.json y atta/checks.json si faltan, "
                    ".gitignore, y verifica develop en origin y las labels con gh. Nunca "
                    "sobrescribe archivos existentes sin --force.",
        epilog="Códigos de salida: 0 ok, 1 aviso de GitHub (falta develop o una label), "
               "2 rechazado (p. ej. .claude/settings.json inválido; no cambia nada).")
    p.add_argument("--force", action="store_true",
                   help="reemplaza los agentes y el skill existentes (nunca config.json, "
                        "checks.json ni settings.json)")
    p.add_argument("--no-github", action="store_true",
                   help="no verifica origin/develop ni crea labels (sin red)")
    p.add_argument("--runtime", choices=RUNTIMES, default="claude-code",
                   help="runtime de los agentes (por defecto: claude-code)")
    p.set_defaults(func=cmd_init)
