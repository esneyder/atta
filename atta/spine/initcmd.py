"""`atta init` (S2): prepares the resolved project for the colony.

What it does, on the project root:

- `.claude/agents/atta-*.md` from the packaged templates, with `{{model:Tn}}` resolved per role by
  models.py with the project's config (S5 defaults T1 haiku, T2 sonnet, T3 opus; S12 overrides
  from `models` in atta/config.json survive `--force`; the Vigía avoids the Guía's model), and
  `.claude/skills/atta/SKILL.md`, `.claude/commands/atta.md` and the `/atta:*` menus
  (`.claude/commands/atta/config.md`, `models.md`: models.DRIVER_FILES) when those templates exist, with
  their `{{model:T0}}` resolved to the project's T0 model (F4 S5: the `/atta` orchestrator runs
  on the cheap tier; `atta models` keeps that line in sync).
- `.claude/settings.json`: created from the template, or the template hooks are appended to the
  user's file when missing (idempotent; user entries are never removed or reordered).
- `atta/config.json` and `atta/checks.json` from the packaged defaults, only when missing. A new
  config.json also gets `"delivery": {"auto_merge_up_to": "none"}` (S13: «pr, más seguro», Atta
  never merges by itself until you enable it); an existing one is never modified.
- `/atta/jobs/` and `/.worktrees/` in `.gitignore` (appended, no duplicates).
- Unless `--no-github`: checks `origin/<integration_branch>` with `git ls-remote` and creates the
  config labels with `gh label create ... --force` (`gh` is `github.command()`, like every other
  command: $ATTA_GH, where a `.py` runs with this Python, or `gh` from the PATH). Never touches
  main.

Existing files that differ are kept (reported «ya existe, la dejé igual») unless `--force`, which
replaces only the agents and the skill. Every check runs before the first write: an invalid
`.claude/settings.json` exits 2 and changes nothing. Exit codes: 0 ok, 1 GitHub warning
(missing develop, label not created), 2 refused.

`atta init --check` (F3 G2) writes nothing and uses no network: it plans exactly what init would
write (same `_plan`) and lists the Claude Code files that differ from it or are missing (agents
with their resolved models, skill, command, the settings hook). Exit 0 «al día», 1 when something
differs, 2 refused (e.g. invalid settings.json). `outdated()` is the pure comparison that
`atta doctor` reuses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from . import config, github, models, project

OK, WARNING, REFUSED = 0, 1, 2
RUNTIMES = models.RUNTIMES
GH_ENV = github.ENV_VAR
GITIGNORE_ENTRIES = ("/atta/jobs/", "/.worktrees/")
NET_TIMEOUT_S = 60
# Label key in config["labels"] → (color, description) for `gh label create`.
LABEL_STYLE = {
    "queue": ("0e8a16", "Atta: issue en la cola de la colonia"),
    "needs_info": ("fbca04", "Atta: falta información del autor del issue"),
    "needs_human": ("d93f0b", "Atta: necesita una decisión humana"),
    "needs_review": ("1d76db", "Atta: PR listo para que lo revises y lo mergees"),
}
# S13: what a new project's atta/config.json says about delivery (never merges by itself).
NEW_PROJECT_DELIVERY = {"auto_merge_up_to": "none"}


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


def _table(templates: Path, runtime: str, cfg: dict | None) -> "models.Table":
    try:
        return models.table(cfg, runtime, templates)
    except models.ModelsError as exc:
        raise InitError(f"{exc} No cambié nada.") from None


def _resolve(text: str, table: "models.Table", name: str) -> str:
    """`{{model:Tn}}` → the role's model (models.py) for a colony role, else the tier model."""
    role = name[len("atta-"):-len(".md")] if name.endswith(".md") else ""

    def sub(match) -> str:
        tier = match.group(1)
        model = table.tiers.get(tier)
        if not isinstance(model, str) or not model:
            raise InitError(f"La plantilla {name} pide {{{{model:{tier}}}}}, que models.json no "
                            "define. No cambié nada.")
        if role in models.ROLES and table.defaults.get(role) == tier:
            return table.agent_model(role)
        return model
    return models.PLACEHOLDER.sub(sub, text)


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


MISSING, DIFFERS = "falta", "distinto"


class _Plan:
    def __init__(self, root: Path, force: bool):
        self.root, self.force = root, force
        self.writes: list[tuple[Path, bytes]] = []
        self.lines: list[str] = []
        self.unchanged = 0
        # `--check`: (rel path, None | MISSING | DIFFERS) of every Claude Code file init owns
        # (agents, skill, command and the settings hook), in plan order.
        self.owned: list[tuple[str, str | None]] = []

    def file(self, path: Path, data: bytes, *, replaceable: bool) -> None:
        rel = _rel(self.root, path)
        if not path.exists():
            state = MISSING
            self.writes.append((path, data))
            self.lines.append(f"creé: {rel}")
        elif path.read_bytes() == data:
            state = None
            self.unchanged += 1
        elif replaceable and self.force:
            state = DIFFERS
            self.writes.append((path, data))
            self.lines.append(f"reemplacé: {rel}")
        else:
            state = DIFFERS
            self.lines.append(f"ya existe, la dejé igual: {rel}")
        if replaceable:
            self.owned.append((rel, state))

    def update(self, path: Path, data: bytes | None, created: bool, *,
               owned: bool = False) -> None:
        rel = _rel(self.root, path)
        if owned:
            self.owned.append((rel, None if data is None else MISSING if created else DIFFERS))
        if data is None:
            self.unchanged += 1
            return
        self.writes.append((path, data))
        self.lines.append(f"{'creé' if created else 'actualicé'}: {rel}")


def _plan(root: Path, templates: Path, runtime: str, force: bool,
          cfg: dict | None = None) -> _Plan:
    plan = _Plan(root, force)
    claude_t = templates / "claude"
    table = _table(templates, runtime, cfg)
    agents_dst = root / ".claude" / "agents"
    for src in sorted((claude_t / "agents").glob("atta-*.md")):
        text = _resolve(_read_text(src), table, src.name)
        plan.file(agents_dst / src.name, text.encode("utf-8"), replaceable=True)
    # The /atta driver: the skill, plus a slash command that points to it so `/atta` works
    # even on Claude Code versions where skills are not registered as slash commands. Both
    # carry `{{model:T0}}` (F4 S5), resolved like the agents' placeholders.
    for rel in models.DRIVER_FILES:
        src = claude_t / Path(rel).relative_to(".claude")
        if src.is_file():
            text = _resolve(_read_text(src), table, src.name)
            plan.file(root / rel, text.encode("utf-8"), replaceable=True)

    settings_t = claude_t / "settings.json"
    if settings_t.is_file():
        template = _read_json(settings_t, "La plantilla settings.json")
        if not isinstance(template, dict):
            raise InitError("La plantilla settings.json debe ser un objeto JSON. No cambié nada.")
        _hooks_of(template, "La plantilla settings.json")
        dst = root / ".claude" / "settings.json"
        if not dst.exists():
            plan.update(dst, settings_t.read_bytes(), True, owned=True)
        else:
            label = ".claude/settings.json"
            user = _read_json(dst, label)
            if not isinstance(user, dict):
                raise InitError(f"{label} debe ser un objeto JSON. Corrígelo y vuelve a correr "
                                "`atta init`; no cambié nada.")
            _hooks_of(user, label)
            merged, changed = _merge_settings(user, template)
            data = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            plan.update(dst, data if changed else None, False, owned=True)

    plan.file(project.config_path(root), new_config_bytes(), replaceable=False)
    plan.file(project.checks_path(root), project.CHECKS_TEMPLATE.read_bytes(), replaceable=False)
    gi = root / ".gitignore"
    plan.update(gi, *_gitignore(gi))
    return plan


def new_config_bytes() -> bytes:
    """The packaged config.json plus `delivery` and `routing_matrix` (F4 T3, S8a: new projects
    start on the stage×risk matrix) for new projects, keeping its formatting."""
    text = config.CONFIG_PATH.read_text(encoding="utf-8").rstrip()
    if not text.endswith("}"):
        raise InitError("La plantilla config.json no es un objeto JSON. No cambié nada.")
    entry = json.dumps(NEW_PROJECT_DELIVERY, ensure_ascii=False)
    text = (f'{text[:-1].rstrip()},\n  "delivery": {entry},\n  "routing_matrix": true\n'
            '}\n')
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise InitError(f"La plantilla config.json no es JSON válido ({exc}). No cambié "
                        "nada.") from None
    return text.encode("utf-8")


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


def _github(root: Path, cfg: dict) -> tuple[list[str], list[str]]:
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

    gh_argv = github.command()
    gh = gh_argv[-1]
    for key, (color, description) in LABEL_STYLE.items():
        name = config.label(cfg, key) if key in config.OPTIONAL_LABELS else cfg["labels"][key]
        try:
            proc = _run([*gh_argv, "label", "create", name, "--color", color,
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
        templates_dir: str | Path | None = None) -> int:
    """Prepare `root`; print the summary (stdout) and warnings/errors (stderr); return exit code."""
    root = Path(root).resolve()
    templates = Path(templates_dir) if templates_dir is not None else project.TEMPLATES_DIR
    try:
        cfg = cfg if cfg is not None else project.load_config(root)
        plan = _plan(root, templates, runtime, force, cfg)
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

    gh_lines, warnings = ([], []) if no_github else _github(root, cfg)
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


def outdated(root: str | Path, *, runtime: str = "claude-code", cfg: dict | None = None,
             templates_dir: str | Path | None = None) -> tuple[list[tuple[str, str]], int]:
    """([(rel path, MISSING | DIFFERS)], number of files compared) for the Claude Code files
    `atta init` owns, against what init would write now. Pure: reads only. Raises InitError
    (e.g. invalid settings.json, invalid models) or config.ConfigError."""
    root = Path(root).resolve()
    templates = Path(templates_dir) if templates_dir is not None else project.TEMPLATES_DIR
    cfg = cfg if cfg is not None else project.load_config(root)
    plan = _plan(root, templates, runtime, False, cfg)
    return [(rel, state) for rel, state in plan.owned if state], len(plan.owned)


def check(root: str | Path, *, runtime: str = "claude-code", cfg: dict | None = None,
          templates_dir: str | Path | None = None) -> int:
    """`atta init --check`: print «al día» (0) or the files that differ (1); 2 when refused."""
    root = Path(root).resolve()
    try:
        diff, total = outdated(root, runtime=runtime, cfg=cfg, templates_dir=templates_dir)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return REFUSED
    except config.ConfigError as exc:
        print(f"{exc} No cambié nada.", file=sys.stderr)
        return REFUSED
    if not diff:
        print(f"Atta revisó {root}: al día ({total} archivo{'' if total == 1 else 's'}).\n"
              "— Atta")
        return OK
    n = len(diff)
    out = [f"Atta revisó {root}: {n} archivo"
           f"{' no coincide' if n == 1 else 's no coinciden'} con esta versión de Atta:"]
    out += [f"  {state}: {rel}" for rel, state in diff]
    out += ["Ponlos al día en tu terminal con `atta init --force` (nunca toca config.json, "
            "checks.json ni tus entradas de settings.json).", "— Atta"]
    print("\n".join(out))
    return WARNING


def cmd_init(args, cfg, jobs) -> int:
    """Entry point for cli.py (`atta init`); `jobs` is unused."""
    if getattr(args, "check", False):
        return check(args.root, runtime=args.runtime, cfg=cfg)
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
               "2 rechazado (p. ej. .claude/settings.json inválido; no cambia nada). Con "
               "--check: 0 al día, 1 hay archivos distintos o faltantes, 2 rechazado.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true",
                      help="reemplaza los agentes y el skill existentes (nunca config.json, "
                           "checks.json ni settings.json)")
    mode.add_argument("--check", action="store_true",
                      help="no escribe nada ni usa la red: dice si los agentes, el skill, el "
                           "comando /atta y el hook están al día con esta versión de Atta")
    p.add_argument("--no-github", action="store_true",
                   help="no verifica origin/develop ni crea labels (sin red)")
    p.add_argument("--runtime", choices=RUNTIMES, default="claude-code",
                   help="runtime de los agentes (por defecto: claude-code)")
    p.set_defaults(func=cmd_init)
