"""`atta doctor [--no-github]` (F3 S3, G1): is this machine and project ready to run the colony?

Read-only. One line per check, ✓ ok / ⚠ warning / ✗ failure (ASCII `[ok]` / `[!]` / `[x]` when
stdout cannot encode them or with ATTA_ASCII=1), a short Spanish fix hint under every ⚠ and ✗,
and a summary signed «— Atta». Exit 0 when nothing failed, 1 when any check is ✗ (⚠ never fails).

Checks, in order (each one catches a stumble of the real runs, odd/tasks/f2-colonia.md):

- `atta` and `atta-board` on the PATH (L13: Scripts folder of the Python missing from the PATH;
  without `atta` the guard hook blocks nothing) and the Python of Atta has pip (⚠, to update);
- the project root is a git repo, `atta/config.json` loads and validates (missing: ⚠, defaults),
  `atta/checks.json` has at least one check (without checks the Filtro always fails);
- with a `sgconfig.yml`, the `ast-grep` (or `sg`) binary on the PATH (⚠: the Filtro fails);
- remote `origin`, `origin/<integration_branch>` (`git ls-remote --heads`), the main checkout is
  on that branch (✗ otherwise, T9: on `main` the Filtro reads an empty checks.json; same hint as
  the Filtro: «Atta lee checks.json y las reglas desde develop. Corre: git switch develop») and
  not behind it (⚠; L31: the Filtro reads checks.json from this checkout);
- gh (`github.command()`, so $ATTA_GH works): installed, logged in, the active account sees the
  repository (`gh repo view --json name`), and every Atta label of the config exists
  (`gh label list`; L31: a missing `atta:needs-review`). `--no-github` skips them (⚠);
- Claude Code: the 11 agents, the skill, `.claude/commands/atta.md` (L16: «Unknown command:
  /atta»), the `atta guard` PreToolUse hook, `claude --version` (⚠ when missing), and the
  project files up to date with this Atta (the comparison of `atta init --check`, ⚠).

Doctor is the agent-safe entry point (L29): it never writes; every fix it suggests that changes
configuration is a command for the human in their terminal.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, gate, github, initcmd, models, project

OK, FAILED = 0, 1
TIMEOUT_S = 60
SIGN = "— Atta"
UNICODE = {"ok": "✓", "warn": "⚠", "fail": "✗", "arrow": "→", "sign": SIGN}
ASCII = {"ok": "[ok]", "warn": "[!]", "fail": "[x]", "arrow": "->", "sign": "-- Atta"}
RUN_INIT = "Corre atta init en tu terminal."
PATH_HINT = ("Agrega al PATH la carpeta Scripts del Python donde instalaste Atta "
             "(p. ej. …\\Python311\\Scripts) o corre pipx ensurepath, y abre una terminal nueva.")


# ---------------------------------------------------------------- probes (patched in tests)

def _which(name: str) -> str | None:
    # Absolute, so a match in the current folder (Windows searches it first, e.g. the `atta.cmd`
    # launcher of an Atta checkout) is visible as such.
    found = shutil.which(name)
    return os.path.abspath(found) if found else None


def _proc(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess | None:
    """Run argv (never a shell); None when it cannot start or times out."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", env=env,
                              stdin=subprocess.DEVNULL, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None


def _pip_version() -> str | None:
    proc = _proc([sys.executable, "-m", "pip", "--version"])
    if proc is None or proc.returncode != 0:
        return None
    parts = proc.stdout.split()
    return parts[1] if len(parts) > 1 else "?"


def _claude_version() -> str | None:
    exe = shutil.which("claude")
    proc = _proc([exe, "--version"]) if exe else None
    if proc is None or proc.returncode != 0:
        return None
    return (proc.stdout.strip().splitlines() or ["?"])[0]


def _last_line(proc: subprocess.CompletedProcess | None) -> str:
    if proc is None:
        return "no respondió"
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return lines[-1] if lines else f"código {proc.returncode}"


# ---------------------------------------------------------------- report

class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str | None]] = []

    def ok(self, text: str) -> None:
        self.rows.append(("ok", text, None))

    def warn(self, text: str, hint: str | None = None) -> None:
        self.rows.append(("warn", text, hint))

    def fail(self, text: str, hint: str) -> None:
        self.rows.append(("fail", text, hint))

    def skip(self, what: str, why: str) -> None:
        self.warn(f"{what}: salteado ({why})")

    def count(self, kind: str) -> int:
        return sum(1 for row in self.rows if row[0] == kind)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _marks(stream) -> dict:
    if os.environ.get("ATTA_ASCII", "") not in ("", "0"):
        return ASCII
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return UNICODE
    try:
        "".join(UNICODE.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return ASCII
    return UNICODE


def render(root: Path, report: Report, marks: dict) -> str:
    out = [f"Atta doctor: {root}"]
    for kind, text, hint in report.rows:
        out.append(f"{marks[kind]} {text}")
        if hint:
            out.append(f"    {marks['arrow']} {hint}")
    ok, warn, fail = report.count("ok"), report.count("warn"), report.count("fail")
    out.append(f"Resumen: {ok} bien, {_plural(warn, 'aviso')}, {_plural(fail, 'falla')}.")
    if fail:
        out.append(f"Corrige lo marcado con {marks['fail']} antes de correr /atta.")
    elif warn:
        out.append("Listo para correr /atta; revisa los avisos.")
    else:
        out.append("Todo listo para correr /atta.")
    out.append(marks["sign"])
    return "\n".join(out)


# ---------------------------------------------------------------- checks

def _tools(r: Report) -> None:
    for name in ("atta", "atta-board"):
        path = _which(name)
        if path:
            r.ok(f"{name} está en el PATH ({path})")
        else:
            hint = PATH_HINT
            if name == "atta":
                hint += " Sin atta en el PATH el hook del guard no bloquea nada."
            r.fail(f"{name} no está en el PATH", hint)
    pip = _pip_version()
    if pip:
        r.ok(f"Python {platform.python_version()} con pip {pip} ({sys.executable})")
    else:
        r.warn(f"el Python de Atta ({sys.executable}) no tiene pip",
               "Para actualizar Atta usa pipx o un Python con pip (en Windows: py -m pip "
               "install --force-reinstall git+https://github.com/esneyder/atta).")


def _project(r: Report, root: Path) -> tuple[bool, dict]:
    """(is a git repo, config to use: the project's, or the defaults when invalid/missing)."""
    top = project.git_toplevel(root)
    is_repo = top is not None
    if is_repo:
        r.ok(f"proyecto: {root} (repo git)")
    else:
        r.fail(f"{root} no es un repo git",
               "Corre atta doctor dentro del proyecto o pásale --project <carpeta>.")
    cfg = config.with_defaults({})
    if not project.config_path(root).exists():
        r.warn("no existe atta/config.json: uso los valores por defecto", RUN_INIT)
    else:
        try:
            cfg = project.load_config(root)
        except config.ConfigError as exc:
            r.fail(f"atta/config.json no es válido: {exc}",
                   "Corrígelo a mano en tu terminal (atta settings muestra cada clave).")
        else:
            r.ok("atta/config.json es válido")
    try:
        checks = gate.load_checks(root)["checks"]
    except gate.GateError as exc:
        r.fail(f"atta/checks.json: {exc}",
               "Corre atta init y llena atta/checks.json (README, sección 4.2).")
    else:
        if checks:
            r.ok(f"atta/checks.json tiene {_plural(len(checks), 'check')}")
        else:
            r.fail("atta/checks.json no tiene checks: el Filtro siempre falla",
                   "Agrega los checks reales del proyecto (README, sección 4.2).")
    _ast_grep(r, root)
    return is_repo, cfg


def _ast_grep(r: Report, root: Path) -> None:
    """With a sgconfig.yml the Filtro runs ast-grep and fails without the binary (K4)."""
    if not any((root / name).is_file() for name in gate.SGCONFIG_NAMES):
        return
    exe = next((p for p in (_which(n) for n in gate.AST_GREP_BINARIES) if p), None)
    if exe:
        r.ok(f"ast-grep para las reglas de sgconfig.yml ({exe})")
    else:
        r.warn("hay sgconfig.yml y no encontré ast-grep (ni sg) en el PATH: el Filtro va a "
               "fallar", gate.AST_GREP_HINT)


def _git(r: Report, root: Path, is_repo: bool, branch: str) -> None:
    remote = f"origin/{branch}"
    checkout = "checkout principal"
    if not is_repo:
        for what in ("remoto origin", remote, checkout):
            r.skip(what, "no es un repo git")
        return
    proc = _proc(["git", "remote", "get-url", "origin"], root)
    url = proc.stdout.strip() if proc is not None and proc.returncode == 0 else ""
    if not url:
        r.fail("no hay remoto origin", "Agrega el remoto de GitHub: git remote add origin <url>.")
        r.skip(remote, "no hay remoto origin")
        r.skip(checkout, "no hay remoto origin")
        return
    r.ok(f"remoto origin: {url}")
    proc = _proc(["git", "ls-remote", "--heads", "origin", branch], root)
    if proc is None or proc.returncode != 0:
        r.fail(f"no pude consultar origin: {_last_line(proc)}",
               "Revisa tu conexión y el acceso al remoto (git ls-remote origin).")
        r.skip(checkout, f"no pude consultar {remote}")
        return
    sha = next((line.split()[0] for line in proc.stdout.splitlines()
                if line.split()[-1:] == [f"refs/heads/{branch}"]), None)
    if sha is None:
        r.fail(f"no encontré la rama {branch} en origin",
               f"Atta integra solo en {branch} (nunca en main): créala en origin, "
               f"p. ej. git push origin HEAD:{branch}.")
        r.skip(checkout, f"falta {remote}")
        return
    r.ok(f"{remote} existe: ahí integra Atta")
    head = _proc(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    current = head.stdout.strip() if head is not None and head.returncode == 0 else "HEAD"
    if current != branch:
        shown = "HEAD suelto" if current == "HEAD" else current
        r.fail(f"el checkout principal está en «{shown}», no en {branch}",
               gate.branch_hint(branch))
        return
    known = _proc(["git", "cat-file", "-e", f"{sha}^{{commit}}"], root)
    ancestor = None
    if known is not None and known.returncode == 0:
        ancestor = _proc(["git", "merge-base", "--is-ancestor", sha, "HEAD"], root)
    if ancestor is not None and ancestor.returncode == 0:
        r.ok(f"el checkout principal ({current}) está al día con {remote}")
    else:
        r.warn(f"el checkout principal ({current}) está atrás de {remote}",
               f"Ponlo al día: git pull --ff-only origin {branch} (el Filtro lee "
               "atta/checks.json de este checkout).")


def _gh(r: Report, root: Path, cfg: dict) -> None:
    argv = github.command()
    repo, labels = "repo en gh", "labels de Atta"
    proc = _proc([*argv, "--version"], root)
    if proc is None:
        r.fail(f"no encontré gh ({argv[-1]})",
               f"Instala GitHub CLI (https://cli.github.com) o define {github.ENV_VAR}.")
        r.skip("sesión de gh", "gh no está listo")
        r.skip(repo, "gh no está listo")
        r.skip(labels, "gh no está listo")
        return
    if proc.returncode != 0:
        r.fail(f"gh no responde: {_last_line(proc)}",
               f"Reinstala GitHub CLI (https://cli.github.com) o revisa {github.ENV_VAR}.")
        r.skip("sesión de gh", "gh no está listo")
        r.skip(repo, "gh no está listo")
        r.skip(labels, "gh no está listo")
        return
    r.ok(f"gh instalado ({(proc.stdout.strip().splitlines() or ['gh'])[0]})")
    proc = _proc([*argv, "auth", "status"], root)
    if proc is None or proc.returncode != 0:
        r.fail("gh no tiene una sesión iniciada", "Corre gh auth login.")
        r.skip(repo, "gh no está listo")
        r.skip(labels, "gh no está listo")
        return
    r.ok("gh tiene una sesión iniciada")
    who = _proc([*argv, "api", "user", "--jq", ".login"], root)
    login = who.stdout.strip() if who is not None and who.returncode == 0 else ""
    login = login or "?"
    proc = _proc([*argv, "repo", "view", "--json", "name"], root)
    name = None
    if proc is not None and proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
            name = data.get("name") if isinstance(data, dict) else None
        except json.JSONDecodeError:
            name = None
    if not name:
        r.fail(f"la cuenta activa de gh ({login}) no ve el repo",
               "Cambia a una cuenta con acceso: gh auth switch -u <usuario> "
               "(gh auth status lista tus cuentas).")
        r.skip(labels, "gh no ve el repo")
        return
    r.ok(f"la cuenta {login} de gh ve el repo {name}")
    wanted = [config.label(cfg, key) for key in initcmd.LABEL_STYLE]
    proc = _proc([*argv, "label", "list", "--json", "name", "--limit", "500"], root)
    names = None
    if proc is not None and proc.returncode == 0:
        try:
            rows = json.loads(proc.stdout)
            names = {row.get("name") for row in rows if isinstance(row, dict)}
        except (json.JSONDecodeError, TypeError, AttributeError):
            names = None
    if names is None:
        r.fail(f"no pude listar las labels con gh: {_last_line(proc)}",
               "Revisa gh label list en el proyecto.")
        return
    missing = [w for w in wanted if w not in names]
    if missing:
        r.fail(f"faltan labels de Atta en el repo: {', '.join(missing)}",
               "Corre atta init en tu terminal: crea las labels que faltan.")
    else:
        r.ok(f"labels de Atta en el repo: {', '.join(wanted)}")


def _has_guard_hook(settings) -> bool:
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        for hook in (entry.get("hooks") or []) if isinstance(entry, dict) else ():
            command = hook.get("command") if isinstance(hook, dict) else None
            if isinstance(command, str) and command.split()[:2] == ["atta", "guard"]:
                return True
    return False


def _claude(r: Report, root: Path, cfg: dict) -> None:
    agents = root / ".claude" / "agents"
    missing = [f"atta-{role}.md" for role in models.ROLES
               if not (agents / f"atta-{role}.md").is_file()]
    if missing:
        r.fail(f"faltan agentes en .claude/agents: {', '.join(missing)}", RUN_INIT)
    else:
        r.ok(f".claude/agents: los {len(models.ROLES)} agentes de Atta")
    if (root / ".claude" / "skills" / "atta" / "SKILL.md").is_file():
        r.ok(".claude/skills/atta/SKILL.md existe")
    else:
        r.fail("falta .claude/skills/atta/SKILL.md", RUN_INIT)
    if (root / ".claude" / "commands" / "atta.md").is_file():
        r.ok("/atta registrado (.claude/commands/atta.md)")
    else:
        r.fail("falta .claude/commands/atta.md: /atta no aparece en Claude Code", RUN_INIT)
    path = root / ".claude" / "settings.json"
    no_hook = ".claude/settings.json no tiene el hook «atta guard»: el guard no bloquea nada"
    hook_hint = "Corre atta init en tu terminal (agrega el hook sin tocar lo tuyo)."
    if not path.is_file():
        r.fail("falta .claude/settings.json: el guard no bloquea nada", hook_hint)
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            r.fail(f".claude/settings.json no es JSON válido ({exc})",
                   "Corrígelo a mano y corre atta init en tu terminal.")
        else:
            if _has_guard_hook(data):
                r.ok("hook del guard instalado (.claude/settings.json)")
            else:
                r.fail(no_hook, hook_hint)
    version = _claude_version()
    if version:
        r.ok(f"Claude Code {version}")
    else:
        r.warn("no encontré claude en el PATH", "Instala Claude Code para correr /atta.")
    try:
        diff, _ = initcmd.outdated(root, cfg=cfg)
    except (initcmd.InitError, config.ConfigError) as exc:
        r.warn(f"no pude comparar los archivos de Atta: {exc}")
        return
    if diff:
        r.warn(f"archivos de Atta desactualizados: {', '.join(rel for rel, _ in diff)}",
               "Corre atta init --force en tu terminal (atta init --check muestra la lista).")
    else:
        r.ok("archivos de Claude Code al día con esta versión de Atta")


def collect(root: str | Path, *, no_github: bool = False) -> Report:
    root = Path(root).resolve()
    r = Report()
    _tools(r)
    is_repo, cfg = _project(r, root)
    _git(r, root, is_repo, cfg["integration_branch"])
    if no_github:
        r.skip("GitHub (gh y labels)", "--no-github")
    else:
        _gh(r, root, cfg)
    _claude(r, root, cfg)
    return r


def run(root: str | Path, *, no_github: bool = False) -> int:
    """Run every check on `root`, print the report and return 0 (no ✗) or 1."""
    root = Path(root).resolve()
    report = collect(root, no_github=no_github)
    print(render(root, report, _marks(sys.stdout)))
    return FAILED if report.count("fail") else OK


def cmd_doctor(args, cfg, jobs) -> int:
    """Entry point for cli.py; reads the config itself so an invalid one is a ✗, not exit 2."""
    return run(args.root, no_github=args.no_github)


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "doctor", help="Revisa si la máquina y el proyecto están listos para correr Atta.",
        description="Revisa, sin escribir nada: atta y atta-board en el PATH, Python con pip, "
                    "repo git, atta/config.json, atta/checks.json con checks, origin y "
                    "origin/develop, que el checkout principal esté al día, gh (instalado, con "
                    "sesión, con acceso al repo), las labels de Atta, los agentes, el skill, el "
                    "comando /atta, el hook del guard, Claude Code y que los archivos estén al "
                    "día (como atta init --check).",
        epilog="Códigos de salida: 0 sin fallas (los avisos ⚠ no fallan), 1 hay al menos una "
               "falla ✗.")
    p.add_argument("--no-github", action="store_true",
                   help="no consulta gh (cuenta, repo y labels)")
    p.set_defaults(func=cmd_doctor, tolerates_bad_config=True)
