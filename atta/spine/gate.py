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
- `when` (S2, tiered checks): `always` (default) or `risk>=medium`. In a `checks` round whose
  effective risk (risk.evaluate: max of intake, rules on the diff and previous) is low, the
  `risk>=medium` checks are skipped and checks.md says so; when that would leave no check, all
  run. `merge-checks` always runs every check.

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

Failure kinds (T9, S2): every FAIL is `config` or `code`, written as the third line of the report
(`KIND: config|code`) and in `GateResult.kind`. `config` is what a person fixes in the main
checkout and the Cortadora never can: no checks in checks.json, checks.json unreadable or invalid,
a check's shell or the ast-grep binary missing, the first command of a check missing (T6: a bare
name not on the PATH, PATHEXT on Windows; a path like `./scripts/x.sh` in neither the main
checkout nor the worktree; builtins, `VAR=value` prefixes and words with expansions are never
flagged), ast-grep rules failing their own tests, or the main
checking out another branch than `integration_branch` (only checked when that branch exists in
the main checkout, locally or as origin/<branch>; message: «El checkout principal está en «main»;
Atta lee checks.json y las reglas desde develop. Corre: git switch develop»). Those are checked
first (`## Configuración`) and run nothing else. Everything else is `code`: protected paths,
dirty worktree, scope enforce, ast-grep error findings or bad output, a failing or timed-out
check (exit 127 of a command that exists included: nothing is reclassified after running).
A report without `KIND:` (older Atta) counts as `code` (`report_kind`). `atta next`
sends a `config` FAIL to needs-human without spending a round and retries the gate in the same
round once `config_problems` finds nothing (next.py). A `checks` run records the outcome in
job.json (`gate_failure: {kind: config, reason}`, removed by any other outcome) when
`<round_dir>/..` is a job folder.

For misconfiguration of Atta itself (bad base_ref, bad protected_paths, bad risk_rules) it raises
GateError and writes no report.

Then, in this order (checks.md gets one section each):
- `## Frescura` (K14, `checks` only): `git ls-remote` (never a fetch) tells whether the main
  checkout's integration branch is behind origin: a warning, never a failure.
- `## Alcance` (K8, G5, `checks` only; see scope.py): the diff against the spec's `FILES:`, the
  diff size against `scope.max_files`, and from round 2 on the round's own changes against the
  previous round's findings. Warnings; `scope.enforce: true` makes the first and third FAIL
  (no checks run).
- `## ast-grep` (K4, both kinds): without a sgconfig.yml (or with empty ruleDirs) in the MAIN
  checkout the step is skipped. With rules and no `ast-grep`/`sg` binary it FAILS. With
  `testConfigs`, `ast-grep test --skip-snapshot-tests` runs first and its failure FAILS. Then
  `ast-grep scan --config <main>/sgconfig.yml --json=stream -- <files>` over the diff's
  added/modified files only (rules always from the main checkout: a branch cannot weaken
  them); only `severity: error` matches block, warnings are listed; a non-JSON output or a
  non-zero exit without error matches also FAILS (the exit code alone is never trusted).
  A FAIL here runs no checks; `failed_check` is `ast-grep`.
Warnings of a passing gate are appended to the summary («Avisos en checks.md: alcance.»).

Riesgo por reglas (G3, see risk.py): when `checks` passes, the diff is classified with the
config's `risk_rules`; checks.md gets a `## Riesgo` section and, when `<round_dir>/..` is a job
folder, job.json gets `risk_rules` and `risk_effective` (never lower than before). An effective
risk above the routed one re-plans the review and is appended to the summary
(«El Filtro subió el riesgo a alto: migrations/0003.sql (datos).»). `merge-checks` and a FAIL
never touch the risk.

The report goes to `<round_dir>/<kind>.md` (first line `GATE: PASS|FAIL`, second `COMMIT: <sha>`,
on a FAIL a third `KIND: config|code`) and the combined output of the checks to
`<round_dir>/<kind>.log`.
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

from . import ids, risk as risk_rules, scope as scope_rules

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

# Tiered checks (S2, L4): `when` of a check; `risk>=medium` is skipped on an effective low risk.
WHEN_ALWAYS, WHEN_MEDIUM = "always", "risk>=medium"
WHENS = (WHEN_ALWAYS, WHEN_MEDIUM)

# ast-grep (K4): rules come only from the MAIN checkout's sgconfig.yml.
SGCONFIG_NAMES = ("sgconfig.yml", "sgconfig.yaml")
AST_GREP_BINARIES = ("ast-grep", "sg")
AST_GREP_STEP = "ast-grep"
AST_GREP_HINT = ("Instala ast-grep (npm i -g @ast-grep/cli, pip install ast-grep-cli o cargo "
                 "install ast-grep) o quita sgconfig.yml del checkout principal.")
AST_GREP_BATCH = 100
# cmd.exe re-parses the arguments of a .cmd/.bat launcher: these characters could run commands.
_CMD_UNSAFE = frozenset('&|<>^%!"\r\n')
_which = shutil.which  # patched in tests
_command_which = shutil.which  # the first command of a check (T6); patched in tests

# T6 (S6): the first command of every check must exist before anything runs. Words that are not
# programs (shell builtins, keywords, PowerShell aliases) are never looked up, and a first word
# that cannot be read with certainty (expansions, substitutions, grouping) is never flagged.
_POSIX_BUILTINS = frozenset(
    ": . ! [ alias bg bind break builtin caller case cd command compgen complete compopt "
    "continue coproc declare dirs disown do done echo elif else enable esac eval exec exit "
    "export false fc fg fi for function getopts hash help history if in jobs kill let local "
    "logout mapfile popd printf pushd pwd read readarray readonly return select set shift "
    "shopt source suspend test then time times trap true type typeset ulimit umask unalias "
    "unset until wait while".split())
_CMD_BUILTINS = frozenset(
    "assoc break call cd chdir cls color copy date del dir echo endlocal erase exit for ftype "
    "goto if md mkdir mklink move path pause popd prompt pushd rd rem ren rename rmdir set "
    "setlocal shift start time title type ver verify vol".split())
_PWSH_WORDS = frozenset(
    ". % ? begin break catch class continue data do dynamicparam else elseif end enum exit "
    "filter finally for foreach function if param process return switch throw trap try until "
    "using while ac cat cd chdir clc clear clhy cli clp cls clv cnsn compare copy cp cpi cpp "
    "curl cvpa dbp del diff dir dnsn ebp echo epal epcsv epsn erase etsn exsn fc fhx fl ft fw "
    "gal gbp gc gcb gci gcm gcs gdr ghy gi gin gjb gl gm gmo gp gps gpv group gsn gsnp gsv gtz "
    "gu gv gwmi h history icm iex ihy ii ipal ipcsv ipmo ipsn irm ise iwmi iwr kill lp ls man "
    "md measure mi mount move mp mv nal ndr ni nmo npssc nsn nv ogv oh popd ps pushd pwd r rbp "
    "rcjb rcsn rd rdr ren ri rjb rm rmdir rmo rni rnp rp rsn rsnp rujb rv rvpa rwmi sajb sal "
    "saps sasv sbp sc scb select set shcm si sl sleep sls sort sp spjb spps spsv start stz "
    "sujb sv swmi tee trcm type wget where wjb write".split())
# Characters that make a word an expansion, a pattern or shell grammar (never flagged).
_COMMAND_META = {"posix": frozenset("$`\\(){}[]<>|&;*?~#!"),
                 "cmd": frozenset('%!^&|<>()/'),
                 "pwsh": frozenset("$`(){}[]<>|&;@#,")}
# Characters that expand even inside double quotes.
_QUOTED_META = {"posix": frozenset("$`\\"), "cmd": frozenset("%!"), "pwsh": frozenset("$`")}
_QUOTES = {"posix": "'\"", "cmd": '"', "pwsh": "'\""}
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=")

# Freshness preflight (K14): `git ls-remote` never waits longer than this.
FRESHNESS_TIMEOUT_S = 15

# Failure kinds (T9): `config` is for a person (never spends a round), `code` for the Cortadora.
KIND_CONFIG, KIND_CODE = "config", "code"
FAILURE_KINDS = (KIND_CONFIG, KIND_CODE)
CONFIG_NOTE = ("Esto lo corrige una persona en el checkout principal, nunca la Cortadora: no "
               "gasta ronda.")
AST_GREP_MISSING = ("hay reglas de ast-grep (sgconfig.yml) y no encontré el binario ast-grep "
                    "(ni sg) en el PATH.")


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
    kind: str | None = None  # on a FAIL: `config` (for a person) or `code` (for the Cortadora)


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
        when = item.get("when", WHEN_ALWAYS)
        if when not in WHENS:
            raise GateError(f"checks.json: checks[{i}].when debe ser «{WHEN_ALWAYS}» o "
                            f"«{WHEN_MEDIUM}».")
        checks.append({
            "name": name,
            "run": run,
            "shell": select_shell(item.get("shell")),
            "timeout_s": _timeout(item.get("timeout_s", default_timeout), f"checks[{i}].timeout_s"),
            "when": when,
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
    return [(letter, paths) for letter, paths in changed_entries(worktree, f"{base}...{head}")
            if any(matches_protected(p, globs) for p in paths)]


def changed_entries(worktree: Path, span: str) -> list[tuple[str, tuple[str, ...]]]:
    """(status letter, paths) of every entry of `git diff --name-status -M <span>`; renames and
    copies carry (old, new). `span` is `<a>...<b>` or `<a>..<b>` of resolved shas."""
    raw = _git(Path(worktree), "diff", "--no-ext-diff", "--ignore-submodules=none",
               "--name-status", "-z", "-M", "--end-of-options", span, "--")
    return risk_rules._parse_name_status(raw)


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


# ---------------------------------------------------------------- ast-grep (K4)

def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def _sgconfig_keys(text: str) -> dict[str, list[str]]:
    """Top-level keys of a sgconfig.yml with their list items (the small YAML subset ast-grep
    configs use: `key:` + `- item` lines, or an inline `[a, b]`)."""
    out: dict[str, list[str]] = {}
    key = None
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        if not line[0].isspace():
            m = re.match(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
            key = m.group(1) if m else None
            if key is None:
                continue
            value = m.group(2).strip()
            if value.startswith("[") and value.endswith("]"):
                out[key] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
            else:
                out[key] = [_unquote(value)] if value else []
            continue
        if key is None:
            continue
        m = re.match(r"\s*-\s*(.*)$", line)
        out[key].append(_unquote(m.group(1)) if m else line.strip())
    return out


def ast_grep_rules(main_checkout: Path) -> dict:
    """{config, rule_dirs, has_rules, has_tests, reason} of the main checkout's sgconfig.yml."""
    main_checkout = Path(main_checkout)
    path = next((main_checkout / n for n in SGCONFIG_NAMES if (main_checkout / n).is_file()),
                None)
    if path is None:
        return {"config": None, "has_rules": False, "has_tests": False,
                "reason": "Salteado: no hay sgconfig.yml en el checkout principal."}
    try:
        keys = _sgconfig_keys(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"No se pudo leer {path}: {exc}.") from exc
    dirs = [d for d in keys.get("ruleDirs", []) if d]
    has_rules = any(f.is_file() and f.suffix.lower() in (".yml", ".yaml")
                    for d in dirs if (main_checkout / d).is_dir()
                    for f in (main_checkout / d).rglob("*"))
    has_tests = any("testDir" in item for item in keys.get("testConfigs", []))
    reason = None if has_rules else "Salteado: sgconfig.yml no tiene reglas (ruleDirs vacío)."
    return {"config": path, "has_rules": has_rules, "has_tests": has_tests, "reason": reason}


def ast_grep_binary() -> str | None:
    """`ast-grep` from the PATH, else `sg` when it really is ast-grep (on Linux `sg` is often
    the setgid tool of shadow-utils)."""
    exe = _which(AST_GREP_BINARIES[0])
    if exe:
        return exe
    exe = _which(AST_GREP_BINARIES[1])
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=FRESHNESS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if b"ast-grep" in (proc.stdout + proc.stderr).lower() else None


def _tool(argv: list[str], cwd: Path, timeout: float) -> tuple[int | None, str, str]:
    """(exit code or None on timeout/launch failure, stdout, stderr) of a tool run (no shell)."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, stdin=subprocess.DEVNULL,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", f"superó el tiempo límite ({_secs(timeout)} s)"
    except OSError as exc:
        return None, "", f"no se pudo lanzar: {exc}"
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def _plural_error(n: int) -> str:
    return f"{n} error" if n == 1 else f"{n} errores"


def run_ast_grep(main_checkout: Path, worktree: Path, files: list[str], timeout: float) -> dict:
    """The ast-grep step: {status: skipped|pass|fail, summary, lines, output}.

    Rules come from the main checkout's sgconfig.yml (`--config`), so a branch can never weaken
    them; only `files` (the diff's added/modified files, relative to `worktree`) are scanned.
    Only `severity: error` blocks; the JSON stream decides, never the exit code alone."""
    rules = ast_grep_rules(main_checkout)
    if not rules["has_rules"]:
        return {"status": "skipped", "summary": None, "lines": [rules["reason"]], "output": ""}
    exe = ast_grep_binary()
    if exe is None:
        summary = f"El Filtro no pasó: {AST_GREP_MISSING}"
        return {"status": "fail", "summary": summary, "lines": [summary, AST_GREP_HINT],
                "output": "", "kind": KIND_CONFIG}
    config_path = str(rules["config"].resolve())
    output = []
    if rules["has_tests"]:
        code, out, err = _tool([exe, "test", "--config", config_path, "--skip-snapshot-tests"],
                               Path(main_checkout), timeout)
        output.append(f"$ ast-grep test (exit {code})\n{out}{err}")
        if code != 0:
            summary = "El Filtro no pasó: las reglas de ast-grep no pasan sus propios tests."
            return {"status": "fail", "summary": summary,
                    "lines": ["Las reglas no pasan sus propios tests (ast-grep test):", "",
                              "```", _tail(out + err), "```"], "output": "\n".join(output),
                    "kind": KIND_CONFIG}
    if not files:
        return {"status": "pass", "summary": None, "output": "\n".join(output),
                "lines": ["El diff no agrega ni modifica archivos: nada que revisar."]}
    if exe.lower().endswith((".cmd", ".bat")):
        unsafe = [f for f in files if _CMD_UNSAFE & set(f)]
        if unsafe:
            summary = (f"El Filtro no pasó: ast-grep es un lanzador .cmd y estas rutas tienen "
                       f"caracteres que cmd interpreta: {', '.join(f'`{f}`' for f in unsafe)}.")
            return {"status": "fail", "summary": summary, "lines": [summary], "output": ""}
    errors, warnings, bad, failures = [], [], [], []
    for i in range(0, len(files), AST_GREP_BATCH):
        batch = files[i:i + AST_GREP_BATCH]
        code, out, err = _tool([exe, "scan", "--config", config_path, "--json=stream", "--",
                                *batch], Path(worktree), timeout)
        output.append(f"$ ast-grep scan (exit {code})\n{out}{err}")
        found_error = False
        for line in out.splitlines():
            if not line.strip():
                continue
            try:
                match = json.loads(line)
            except json.JSONDecodeError:
                bad.append(line)
                continue
            if not isinstance(match, dict):
                bad.append(line)
                continue
            if match.get("severity") == "error":
                errors.append(match)
                found_error = True
            else:
                warnings.append(match)
        if code is None or (code != 0 and not found_error):
            failures.append((code, err))
    lines = []
    for mark, matches in (("✗", errors), ("⚠", warnings)):
        lines += [f"- {mark} {_sg_where(m)} {m.get('ruleId', '?')} ({m.get('severity', '?')}): "
                  f"{_sg_message(m)}" for m in matches]
    if bad:
        summary = "El Filtro no pasó: ast-grep devolvió una salida que no es JSON."
        lines = [summary, "", "```", _tail("\n".join(bad)), "```"] + lines
    elif errors:
        first = errors[0]
        summary = (f"El Filtro no pasó: ast-grep encontró {_plural_error(len(errors))} "
                   f"({first.get('ruleId', '?')} en {_sg_where(first).strip('`')}).")
    elif failures:
        code, err = failures[0]
        summary = (f"El Filtro no pasó: ast-grep terminó con exit {code} sin reportar errores."
                   if code is not None else f"El Filtro no pasó: ast-grep {err}.")
        lines = [summary, "", "```", _tail(err), "```"] + lines
    else:
        summary = None
        if not lines:
            noun = "archivo" if len(files) == 1 else "archivos"
            lines = [f"Sin hallazgos en {len(files)} {noun} del diff."]
    return {"status": "fail" if summary else "pass", "summary": summary, "lines": lines,
            "output": "\n".join(output)}


def _sg_where(match: dict) -> str:
    path = str(match.get("file") or "?").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    start = ((match.get("range") or {}).get("start") or {}).get("line")
    line = start + 1 if isinstance(start, int) and not isinstance(start, bool) else "?"
    return f"`{path}:{line}`"


def _sg_message(match: dict) -> str:
    return " ".join(str(match.get("message") or "").split()) or "(sin mensaje)"


def _scan_files(entries) -> list[str]:
    """Added/modified (also renamed/copied) files of the diff, in diff order."""
    return [paths[-1] for letter, paths in entries if letter in "AMRC"]


# ---------------------------------------------------------------- freshness (K14)

def _git_quick(cwd: Path, *args: str) -> subprocess.CompletedProcess | None:
    env = dict(_git_env(), GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", "--no-replace-objects", "-C", str(cwd), *args],
                              capture_output=True, stdin=subprocess.DEVNULL, env=env,
                              timeout=FRESHNESS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None


def freshness(main_checkout: Path, branch: str) -> tuple[list[str], bool]:
    """(lines of `## Frescura`, warned): is the main checkout's `branch` behind origin?

    Read-only and never blocking: `git ls-remote` (no fetch) against the local ref."""
    lines = ["## Frescura", ""]
    remote = f"origin/{branch}"
    proc = _git_quick(main_checkout, "remote", "get-url", "origin")
    if proc is None or proc.returncode != 0:
        return lines + ["No hay remoto origin: no se revisó la frescura."], False
    proc = _git_quick(main_checkout, "ls-remote", "--heads", "origin", branch)
    if proc is None or proc.returncode != 0:
        return lines + [f"No pude consultar {remote}: no se revisó la frescura."], False
    ref = f"refs/heads/{branch}"
    sha = next((ln.split()[0] for ln in proc.stdout.decode("utf-8", "replace").splitlines()
                if ln.split()[-1:] == [ref]), None)
    if sha is None:
        return lines + [f"No existe {remote}: no se revisó la frescura."], False
    local = _git_quick(main_checkout, "rev-parse", "--verify", "-q", ref)
    local_sha = _sha(local.stdout) if local is not None and local.returncode == 0 else ""
    if not local_sha:
        return lines + [f"El checkout principal no tiene la rama {branch}: no se revisó la "
                        "frescura."], False
    if local_sha != sha:
        ahead = _git_quick(main_checkout, "merge-base", "--is-ancestor", sha, local_sha)
        if ahead is None or ahead.returncode != 0:
            return lines + [f"⚠ El checkout principal ({branch}) está atrás de {remote}: el "
                            "Filtro usa su atta/checks.json y sus reglas. Ponlo al día con "
                            f"git pull --ff-only origin {branch}."], True
    return lines + [f"El checkout principal ({branch}) está al día con {remote}."], False


# ---------------------------------------------------------------- configuration (T9)

def branch_hint(branch: str) -> str:
    return f"Atta lee checks.json y las reglas desde {branch}. Corre: git switch {branch}"


def branch_problem(current: str, branch: str) -> str:
    """The message when the main checkout is on `current` instead of `branch`."""
    return f"El checkout principal está en «{current}»; {branch_hint(branch)}"


def main_branch_problem(main_checkout: Path, branch: str) -> str | None:
    """branch_problem when the main checkout is not on `branch`, else None.

    Only checked when `branch` exists in the main checkout (refs/heads/<branch> or
    refs/remotes/origin/<branch>): without it there is nothing to switch to (and outside a git
    repo nothing to check). A detached HEAD counts as «HEAD suelto»."""
    main_checkout = Path(main_checkout)
    known = False
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        proc = _git_quick(main_checkout, "rev-parse", "--verify", "-q", ref)
        if proc is not None and proc.returncode == 0:
            known = True
            break
    if not known:
        return None
    proc = _git_quick(main_checkout, "symbolic-ref", "--short", "-q", "HEAD")
    current = (proc.stdout.decode("utf-8", "replace").strip()
               if proc is not None and proc.returncode == 0 else "")
    return None if current == branch else branch_problem(current or "HEAD suelto", branch)


def _shell_family(shell: str) -> str:
    return "cmd" if shell == "cmd" else "pwsh" if shell in ("pwsh", "powershell") else "posix"


def _next_word(text: str, i: int, family: str) -> tuple[str | None, str, bool, int]:
    """(value without quotes or None at the end, raw text, uncertain, next index) of the word
    of `text` starting at `i` (after blanks). A newline ends the first command."""
    n = len(text)
    while i < n and text[i] in " \t":
        i += 1
    if i >= n or text[i] in "\r\n":
        return None, "", False, i
    start, value, uncertain = i, [], False
    while i < n and text[i] not in " \t\r\n":
        ch = text[i]
        if ch in _QUOTES[family]:
            end = text.find(ch, i + 1)
            if end < 0:
                return None, text[start:], True, n
            part = text[i + 1:end]
            if ch == '"' and _QUOTED_META[family] & set(part):
                uncertain = True
            value.append(part)
            i = end + 1
            continue
        if ch in _COMMAND_META[family]:
            uncertain = True
        value.append(ch)
        i += 1
    return "".join(value), text[start:i], uncertain, i


def first_command(shell: str, run: str) -> str | None:
    """The program the shell runs first for `run`, or None when it is not a program (a builtin,
    a keyword, a PowerShell cmdlet or alias) or cannot be told for sure (expansions,
    substitutions, grouping, a PATH assignment). POSIX `VAR=value` prefixes are skipped."""
    family = _shell_family(shell)
    text = run.strip()
    if family == "cmd":
        text = text.lstrip("@").lstrip()
    i = 0
    while True:
        word, raw, uncertain, i = _next_word(text, i, family)
        if not word or uncertain:
            return None
        assignment = _ASSIGNMENT.match(raw) if family == "posix" else None
        if assignment is None:
            break
        if assignment.group(1) == "PATH" or set(raw) & _QUOTED_META["posix"]:
            return None
    folded = word.casefold()
    if family == "posix" and word in _POSIX_BUILTINS:
        return None
    if family == "cmd" and folded in _CMD_BUILTINS:
        return None
    if family == "pwsh" and (folded in _PWSH_WORDS or
                             re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+", word)):
        return None
    return word


def _missing_command(check: dict, main_checkout: Path, worktree: Path | None) -> str | None:
    """The config problem when the first command of `check` does not exist, else None.

    A bare name is looked up on this process's PATH (the one the check inherits; PATHEXT on
    Windows, and the check's folder too under cmd). A path-like word (`./x.sh`, `scripts\\x.cmd`)
    is resolved against the main checkout and the worktree and is only missing when it is in
    neither: if the worktree alone lost it, running it fails as `code` (the Cortadora's diff)."""
    family = _shell_family(check["shell"])
    word = first_command(check["shell"], check["run"])
    if word is None:
        return None
    name = check["name"]
    dirs = [Path(main_checkout)] + ([Path(worktree)] if worktree is not None else [])
    seps = "/" if family == "posix" else "\\/"
    if any(s in word for s in seps) or (family != "posix" and re.match(r"[A-Za-z]:", word)):
        path = Path(word)
        candidates = [path] if path.is_absolute() else [d / path for d in dirs]
        if any(c.exists() or _command_which(str(c)) for c in candidates):
            return None
        where = "en este equipo" if path.is_absolute() else "en el checkout principal"
        hint = "Instálalo" if path.is_absolute() else "Agrégalo"
        return (f"No se encontró «{word}» (check «{name}») {where}. {hint} o corrige "
                "atta/checks.json en el checkout principal.")
    if _command_which(word):
        return None
    if family == "cmd" and any(_command_which(word, path=str(d)) for d in dirs):
        return None
    return (f"No se encontró el comando «{word}» en el PATH de este equipo (check «{name}»). "
            "Instálalo o corrige atta/checks.json en el checkout principal.")


def _preflight(main_checkout: Path, config: dict,
               worktree: Path | None = None) -> tuple[list[str], dict | None]:
    """(config problems found before running anything, loaded checks.json or None).

    Order: main checkout branch (a wrong branch explains every other problem), checks.json
    readable and valid, at least one check, the shell of every check on the PATH, the first
    command of every check (T6: on the PATH, or a script in the main checkout or `worktree`).
    A check whose command exists and still exits 127 is a `code` failure: nothing is
    reclassified after running."""
    problems = []
    branch = config.get("integration_branch") or "develop"
    wrong = main_branch_problem(main_checkout, branch)
    if wrong:
        problems.append(wrong)
    try:
        spec = load_checks(main_checkout)
    except GateError as exc:
        problems.append(str(exc))
        return problems, None
    if not spec["checks"]:
        problems.append(f"atta/checks.json no tiene checks. {PLACEHOLDER_HINT}")
    missing = []
    for check in spec["checks"]:
        if check["shell"] not in missing and not _find_shell(check["shell"]):
            missing.append(check["shell"])
            problems.append(f"No se encontró el shell «{check['shell']}» en el PATH de este "
                            f"equipo (check «{check['name']}»).")
    seen = set()
    for check in spec["checks"]:
        if check["shell"] in missing:
            continue  # without its shell the command cannot be judged
        problem = _missing_command(check, main_checkout, worktree)
        word = first_command(check["shell"], check["run"])
        if problem and word not in seen:
            seen.add(word)
            problems.append(problem)
    return problems, spec


def config_problems(main_checkout: Path, config: dict) -> list[str]:
    """What would make the gate FAIL with kind `config` right now (cheap, read-only, no diff):
    the preflight plus ast-grep rules without the binary. Empty when the config is ready.

    Without a worktree a check's script is looked up in the main checkout only; the gate never
    reports a missing script this would not (it needs it missing from the main checkout too)."""
    main_checkout = Path(main_checkout)
    problems, _spec = _preflight(main_checkout, config)
    try:
        rules = ast_grep_rules(main_checkout)
    except GateError as exc:
        return problems + [str(exc)]
    if rules["has_rules"] and ast_grep_binary() is None:
        problems.append(f"{_sentence(AST_GREP_MISSING)} {AST_GREP_HINT}")
    return problems


def report_kind(path: Path) -> str | None:
    """`config` or `code` for a FAIL report, None for a PASS or a missing file. A FAIL without
    a `KIND:` line (written by an older Atta) counts as `code`."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[:3]
    except OSError:
        return None
    if not lines or lines[0].strip() != "GATE: FAIL":
        return None
    for line in lines[1:]:
        m = re.fullmatch(r"KIND:\s*(\S+)\s*", line)
        if m and m.group(1) in FAILURE_KINDS:
            return m.group(1)
    return KIND_CODE


def _sentence(text: str) -> str:
    """`text` starting with a capital letter (for a standalone sentence)."""
    return text[:1].upper() + text[1:]


def _after_colon(text: str) -> str:
    """`text` after «El Filtro no pasó:»: a leading capital of a plain word is lowered."""
    if len(text) > 1 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _record_failure(job_dir: Path, jid: str, kind: str | None, reason: str | None) -> None:
    """job.json `gate_failure` = {kind: config, reason} of a config FAIL; any other outcome
    removes it (job.json is only written when it changes)."""
    from .lock import job_lock
    from .store import JobStore, StoreError
    if not (job_dir / "job.json").is_file():
        return
    jobs = JobStore(job_dir.parent)
    try:
        with job_lock(job_dir):
            job = jobs.load(jid)
            value = {"kind": kind, "reason": reason} if kind else None
            if job.get("gate_failure") == value:
                return
            if value is None:
                job.pop("gate_failure", None)
            else:
                job["gate_failure"] = value
            jobs.save(job)
    except StoreError as exc:
        raise GateError(str(exc)) from None


# ---------------------------------------------------------------- the gate

def _effective_risk(assessed: dict | None) -> str | None:
    return assessed["outcome"]["effective"] if assessed is not None else None


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
    commit = _sha(_git(worktree, "rev-parse", "--verify", "HEAD^{commit}"))
    base = resolve_base(worktree, base_ref, commit)
    # T9: configuration first; a config FAIL is for a person and runs nothing else.
    problems, spec = _preflight(main_checkout, config, worktree)
    spec = spec or {"checks": [], "timeout_s": DEFAULT_TIMEOUT_S}
    violations = protected_violations(worktree, base, globs, commit)
    dirty = dirty_entries(worktree)
    checks = spec["checks"]
    build = kind == "checks"
    blocked = bool(problems or violations or dirty)

    # Everything below reads the diff; nothing is written before the report.
    entries = changed_entries(worktree, f"{base}...{commit}") if not blocked else []
    assessed = None
    fresh_lines, fresh_warn = [], False
    scope = None
    if not blocked and build:
        assessed = _assess_risk(worktree, base, commit, job, round_dir, config)
        fresh_lines, fresh_warn = freshness(main_checkout, config.get("integration_branch")
                                            or "develop")
        scope = scope_rules.assess(worktree, commit, round_dir, config, entries,
                                   risk_rules.rules_of(config).get("tests") or [])

    # Tiered checks (S2): an effective low risk skips `risk>=medium` (never in merge-checks,
    # never all of them: a gate that runs nothing never passes).
    to_run, skipped = list(checks), []
    if build and _effective_risk(assessed) == "low":
        quick = [c for c in checks if c["when"] != WHEN_MEDIUM]
        if quick:
            to_run, skipped = quick, [c for c in checks if c["when"] == WHEN_MEDIUM]

    start = time.monotonic()
    runs: list[_CheckRun] = []
    failed: _CheckRun | None = None
    sg = None
    scope_fail = scope["fail"] if scope is not None else None
    if not blocked and not scope_fail:
        sg = run_ast_grep(main_checkout, worktree, _scan_files(entries), spec["timeout_s"])
        if sg["status"] != "fail":
            for check in to_run:
                run = _run_check(check, worktree)
                runs.append(run)
                if not run.ok:
                    failed = run
                    break
    duration = round(time.monotonic() - start, 3)
    sg_fail = sg["summary"] if sg is not None and sg["status"] == "fail" else None

    failed_name = None
    if problems:
        summary = f"El Filtro no pasó: {_after_colon(problems[0])}"
    elif violations:
        listed = ", ".join(_entry_text(paths) for _, paths in violations)
        summary = f"El Filtro no pasó: el diff toca rutas protegidas ({listed})."
    elif dirty:
        summary = DIRTY_SUMMARY
    elif scope_fail:
        summary = scope_fail
    elif sg_fail:
        summary = sg_fail
        failed_name = AST_GREP_STEP
    elif failed is not None and failed.timed_out:
        limit = next(c["timeout_s"] for c in checks if c["name"] == failed.name)
        summary = f"El Filtro no pasó: `{failed.name}` superó el tiempo límite ({_secs(limit)} s)."
        failed_name = failed.name
    elif failed is not None:
        summary = f"El Filtro no pasó: falló `{failed.name}` (exit {failed.exit_code})."
        failed_name = failed.name
    else:
        noun = "check" if len(to_run) == 1 else "checks"
        summary = f"El Filtro pasó: {len(to_run)} {noun} en {_secs(duration)} s"
        if skipped:
            plural = "" if len(skipped) == 1 else "s"
            summary += f" ({len(skipped)} salteado{plural} por riesgo bajo)"
        summary += "."
    passed = not blocked and not scope_fail and not sg_fail and failed is None
    failure_kind = None
    if not passed:
        config_fail = bool(problems) or (sg_fail is not None and sg.get("kind") == KIND_CONFIG)
        failure_kind = KIND_CONFIG if config_fail else KIND_CODE

    warned = (scope["warnings"] if scope is not None else []) + (["frescura"] if fresh_warn
                                                                  else [])
    if passed and warned:
        summary = f"{summary} Avisos en checks.md: {', '.join(warned)}."
    if not passed:
        assessed = None
    elif assessed is not None and assessed["outcome"]["message"]:
        summary = f"{summary} {assessed['outcome']['message']}"

    round_dir.mkdir(parents=True, exist_ok=True)
    log_path = round_dir / f"{kind}.log"
    log = [f"# {kind} · job {job_id} · commit {commit}\n"]
    if sg is not None and sg["output"]:
        log.append(f"\n== {AST_GREP_STEP} ==\n{sg['output']}")
    for run in runs:
        status = "timeout" if run.timed_out else f"exit {run.exit_code}"
        log.append(f"\n== {run.name} ({status}, {_secs(run.duration_s)} s) ==\n{run.output}")
    log_path.write_text("".join(log), encoding="utf-8")

    md = [f"GATE: {'PASS' if passed else 'FAIL'}", f"COMMIT: {commit}"]
    if failure_kind:
        md.append(f"KIND: {failure_kind}")
    md += ["", "## Summary", "", summary, ""]
    if problems:
        md += ["## Configuración", "", *[f"- {p}" for p in problems], "", CONFIG_NOTE, ""]
    md += ["## Protected paths", "", f"Base: `{base}`", ""]
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
    if fresh_lines:
        md += ["", *fresh_lines]
    if scope is not None:
        md += ["", *scope["lines"]]
    if sg is not None:
        md += ["", f"## {AST_GREP_STEP}", "", *sg["lines"]]
    md += ["", "## Checks", ""]
    if problems:
        empty = any(p.startswith("atta/checks.json no tiene checks") for p in problems)
        md.append(f"No hay checks configurados. {PLACEHOLDER_HINT}" if empty
                  else "No se corrieron: la configuración del Filtro no está lista.")
    elif violations:
        md.append("No se corrieron: el diff toca rutas protegidas.")
    elif dirty:
        md.append("No se corrieron: el worktree tiene cambios sin commitear.")
    elif scope_fail:
        md.append("No se corrieron: el diff sale del alcance.")
    elif sg_fail:
        md.append("No se corrieron: ast-grep no pasó.")
    else:
        if skipped:
            md += ["Riesgo efectivo bajo: corren solo los checks sin «when: risk>=medium».", ""]
        ran = {r.name: r for r in runs}
        for check in checks:
            if check in skipped:
                md.append(f"- `{check['name']}`: salteado (riesgo bajo: corre con riesgo medio "
                          "o alto).")
                continue
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
    if build:
        reason = None
        if failure_kind == KIND_CONFIG:
            reason = (problems[0] if problems else
                      _sentence(summary.removeprefix("El Filtro no pasó: ")))
        _record_failure(round_dir.parent, job_id, KIND_CONFIG if reason else None, reason)

    return GateResult(passed, commit, failed_name, duration, summary,
                      round_dir / f"{kind}.md", log_path,
                      assessed["found"] if assessed is not None else None, failure_kind)


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
