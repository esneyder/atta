"""`atta guard`: the Claude Code PreToolUse hook (S8). Python stdlib only; Windows, macOS, Linux.

Claude Code runs `atta guard` before every Write/Edit/MultiEdit/NotebookEdit/Bash/PowerShell call
and passes the hook JSON on stdin (`tool_name`, `tool_input`, `cwd`, ...; extra fields ignored).
Allowed: prints nothing, exit 0. Denied: prints the PreToolUse decision JSON
(`permissionDecision: deny`, reason in Spanish signed «Atta»), exit 0. The only `ask` is for
Atta's configuration commands (S4, below). Malformed input, an unreadable project config or any
internal error: denied (fail closed).

Project root: `--project`, then `$ATTA_PROJECT` (both taken as given), else the hook `cwd`
resolved like every Atta command (git top level, then the folder itself); when that lands on a job
worktree (`<root>/.worktrees/<id>`) the guard climbs to the main checkout. Config is always read
from `<root>/atta/config.json` of the main checkout; jobs from `<root>/atta/jobs` (`--jobs-dir`
does not apply to the guard).

File writes (the target is normalised, `..` and symlinks/junctions resolved, compared with
`str.lower()` on Windows and macOS -- never `casefold()`, which maps the long s `ſ` to `s`) are
allowed only:
- inside `<root>/.worktrees/<id>/` of a job whose stage is active (not merged/rejected), outside
  `protected_paths` (relative to that worktree) and outside any `.git` folder; or
- for exactly one of the job artifacts inside `<root>/atta/jobs/<id>/` of an active job:
  `intake.md`, `spec.md`, `spec-review.md`, `decision.md`, `feedback-<x>.md`,
  `round-<N>/build.md`, `round-<N>/review-<x>.md`, `round-<N>/refute.md`, `round-<N>/refute-retry.md`.
- outside the project, inside the system temp folder (`tempfile.gettempdir()` plus `$TEMP`,
  `$TMP`, `$TMPDIR` when absolute; a folder that is a drive root, the user's home or an ancestor
  of it never counts), e.g. the orchestrator's scratchpad. Project rules win when the project
  itself lives under the temp folder.
Everything else is denied: config.json, checks.json, job.json, events.jsonl, `.claude/**`, the
main checkout, other projects, the user's home (`~/.bashrc`). On Windows, alternate data streams (`a:b`), trailing dots/spaces,
drive-relative paths (`C:src/a.py`) and device paths (`\\\\?\\`) are denied outright.

Heredoc bodies (`<<DELIM`, `<<'DELIM'`, `<<"DELIM"`, `<<-DELIM`, quoting-aware, terminated by a
line equal to DELIM) and PowerShell here-strings (`@'...'@`, `@"..."@`) are data: they are
removed before the command is tokenised (`$(...)`/backticks inside an unquoted heredoc and
`$(...)` inside `@"..."@` are still inspected). Exceptions, where the body stays in the command
and is inspected as today: the pipeline fed by the heredoc reaches a shell (`bash/sh/zsh/...`,
`pwsh/powershell`, `cmd`), `source`/`.`, `eval`, `xargs`, `at`/`batch`, `Invoke-Expression`, a
wrapper with no command (`exec 3<<EOF`, `sudo -s`) or a compound command (`done <<EOF`); the
opener's logical line (continuations joined) ends in `|`, `&&`, `||` or `\\`; the command text
before the end of the opener line (other heredoc bodies excluded) contains a construct whose
nested quoting the scanner does not model (Bash: `${`, `$(`, backticks, `$[`, `<(`, `>(`, `((`;
PowerShell: `$(`, `${`, backticks, `<(`, `<#`, typographic quotes), so the heredoc could be text
for the shell; or the command contains dynamic execution anywhere (a shell, `eval`, `source`,
`at`, `iex`, `Invoke-Command`, `[scriptblock]`, `Start-Process`, a `$var` as command word). An
interpreter fed code through a heredoc (`python`, `python3`, `py`, `node`, `perl`, `ruby` with
no script, or `-`/`/dev/stdin`) is denied, and so is any heredoc fed to a program that runs its
input as commands (`ssh`, `su`, `script`, `wsl`, `busybox`, `make`, `crontab`, `php`, `lua`,
`tclsh`, `expect`, `osascript`, `Rscript`, `nu`, `xonsh`). A `#` right after `<`/`>` starts a
comment for the scanner (as in Bash). An unterminated heredoc is not stripped.

Shell commands (Bash and PowerShell) first get their line continuations joined (`\\` + newline
in Bash, backtick + newline in PowerShell), then are tokenised with shlex (tolerant: unbalanced
quotes fall back to a plain split) and split on `;`, `&&`, `||`, `|`, `&`, newlines, parentheses
and braces. Leading shell keywords (`if then elif else fi do done while until !`) are skipped so
the real command word is checked; `for/select/case ... in ...` headers are data. `$(...)`,
backticks, here-strings (`<<<`), `bash/sh/zsh -c`, `pwsh/powershell -Command|-EncodedCommand`
(also `/c`, `/Command`, any prefix such as `-c`, `-Co`, `-e`, `-ec`; the base64 UTF-16LE payload
is decoded), `cmd /c|/k`, `eval`, `Invoke-Expression`, `Start-Process` and the command after
`find -exec|-execdir|-ok|-okdir` are inspected recursively, and `sudo`, `env`, `nice`, `nohup`,
`timeout`, `command`, `exec`, `time`, `npx`, `xargs` and `VAR=value` prefixes are skipped.
Denied:
- a shell (`bash/sh/zsh/dash/...`, `pwsh/powershell`, `cmd`) started with neither `-c`/`-Command`/
  `/c` nor a script file: it would read its commands from stdin (`... | bash`, `bash < f`, `sh -s`);
- defining `GIT_CONFIG*` or `GIT_ALIAS*` environment variables (prefix, `export`, `env`,
  `$env:`, `SetEnvironmentVariable`), which can inject aliases or push mappings;
- `git push` unless it names a remote and at least one refspec and every destination passes
  `branches.check_push_target` (only `atta/<id>`); `+refspec`, `:branch`, `HEAD` without a
  destination, `--force*`, `--mirror`, `--all`, `--tags`, `--delete`, `--prune`,
  `--receive-pack` and unknown options are denied; `git -c alias.*|remote.*|url.*|include.*|
  includeIf.*|core.hooksPath`, writing those keys with `git config` (the values of `-f/--file/
  --blob/--type/--default/--comment` are skipped when locating the key), `git send-pack`,
  `git update-ref -d|--delete|--stdin`, `git update-ref` of the integration or a forbidden branch,
  and branch deletion (`git branch -d|-D|--delete`);
- `gh pr merge`, `gh repo delete|archive`, `gh release ...`, `gh alias set|import`, `gh api` with
  DELETE, `gh api` with a write method (PUT/POST/PATCH, or `-f/-F/--input` implying POST) to
  `pulls/<n>/merge`, `git/refs`, `branches/*/protection`, `merges`, `merge-upstream` or
  `releases` (every non-flag argument is a candidate endpoint, cut at `?`/`#`), the GraphQL
  mutations `mergePullRequest`, `enablePullRequestAutoMerge`, `mergeBranch`, `deleteRef`,
  `updateRef`, and `gh pr create|edit` with a base other than the integration branch
  (`gh pr create` without `--base` too);
- `curl`, `wget`, `Invoke-RestMethod`, `Invoke-WebRequest`, `irm`, `iwr` against the GitHub API
  (`api.github.com`, or `/api/v3/`, `/api/graphql` of a GitHub Enterprise host) with a write
  method (`-X/--request/--method/-Method PUT|POST|PATCH|DELETE`, or `-d/--data*/--json/-F/-T/
  --post-data` implying one);
- Atta's configuration-mutating subcommands (L29), which change atta/config.json and .claude/
  through the CLI where the write rules cannot see them: `atta settings set|reset`,
  `atta models set|unset|reset|fallback`, `atta profiles use|clear` and `atta init` (any flags). Detected as `atta`, `atta.exe`,
  `atta.cmd`, `atta.bat`, `./atta.sh` (also `sh atta.sh`), `python|python3|python3.N|py [-3.N]
  -m atta.spine.cli` and `python .../atta.py`, with global options (`--project X`,
  `--jobs-dir Y`, `=` forms and argparse prefixes) and `models --runtime X` before the action.
  Read-only forms (`atta settings`, `atta settings get <k>`, `atta models [--runtime X]`, `atta profiles [--json]`, `atta profiles show X`) and
  every other subcommand stay allowed;
  the reason always carries the exact command (cut at 400 characters) so the human can run it in
  their terminal. F2.y (S4): the answer is `ask` (Claude Code asks the user to approve that exact
  command) instead of `deny` only when ALL hold: the hook `permission_mode` is `default`,
  `acceptEdits` or `plan` (missing, non-string or any other value, including `bypassPermissions`,
  `dontAsk` and `auto`, fails closed to `deny`); `agent_id` is absent or blank (a subagent, or a
  non-string `agent_id`, always gets `deny`); and the command is ONE plain Atta invocation: its
  first word is Atta itself and it has no `;`, `&`, `|`, `<`, `>`, parentheses, braces, backticks,
  `$`, backslash, newline (PowerShell: no `@` either), so no chaining, pipes, subshells,
  substitutions, heredocs, redirections, variables, `env`/`sudo`/`timeout`/`nohup`/`command`
  wrappers or `bash -c`/`eval`/`cmd /c`. Every other rule keeps `deny`; `ask` is never given for
  a non-config command. `decide()` returns `(kind, reason)`; `evaluate()` returns the reason for
  both kinds, so a non-None result is never an allow;
- orchestrator-only Atta commands (F4 T4): `atta model-failed` (any detected form, see above)
  when the hook payload carries a non-empty `agent_id`, which Claude Code sets only for tool
  calls inside a subagent. The /atta driver (main session, no `agent_id`) runs it; a payload
  without `agent_id` is treated as the main session;
- deploy commands: `guard.deny_commands` in config.json (a list of command prefixes; it REPLACES
  `DEFAULT_DENY_COMMANDS`). A rule matches when the command name is equal and the remaining words
  appear in order among the arguments (`kubectl apply` matches `kubectl -n prod apply -f x`);
- writing through `>`, `>>`, `>|`, `&>`, `2>`, `tee`, `Out-File`, `Set-Content`, `Add-Content`
  or `Tee-Object` to a target inside the project but outside the worktree of an active job (main
  checkout, job control files, artifacts, other worktrees), into protected paths or into a
  `.git` folder; outside the project, to anything but the system temp folder (same roots as for
  Write/Edit: `~/.bashrc`, `~/.profile`, the Windows Startup folder, other projects are denied).
  Null devices (`/dev/null`, `NUL`, `$null`, `/dev/stdout`, `/dev/stderr`, `CON`) stay allowed.
  A destination with variables (`$X`, `$HOME`, `$PROFILE`, `%X%`) cannot be checked and is
  denied. `~` is expanded. `cd` within the command is followed.

Known limits (best effort, not detected):
- writes by other programs: `cp`, `mv`, `sed -i`, `rm`, `install`, `git apply`, hard links;
- other interpreters (`python -c`, `node -e`, `perl -e`, `ruby -e`) and scripts run from files
  (`bash deploy.sh`, `pwsh -File x.ps1`, `python x.py`, `make`): their content is not read;
- commands built at run time: `X=push; git $X ...`, brace expansion (`git pu{sh,ll}`), globbing,
  aliases or functions defined earlier in the same session;
- other shells or launchers not modelled: `wsl`, `busybox sh`, `nu`, `xonsh`, `script -c`,
  `httpie` and other HTTP clients;
- git aliases or `remote.*.push` mappings already in the user's git config, gh aliases or
  extensions defined outside the session, GraphQL queries passed through files
  (`-F query=@q.graphql`, `--input`), `gh api -X PATCH pulls/<n>` changing a PR base;
- heavily obfuscated shell code.
- a heredoc stored in a variable and run later without a dynamic-execution word in the same
  command (`read X <<EOF` then, in a later call, `$X`); a heredoc read by a program not listed
  above that executes data (`awk '{system($0)}'`, `sed e`, `git am`, a function or alias defined
  in an earlier call); `bash 3<<EOF` is seen as a script named `3` (its body is still inspected,
  since a shell is present).
- programs that read commands from a pipe instead of a heredoc (`echo x | ssh host`).
- Atta's configuration commands run through launchers not modelled (`uv run atta`,
  `pipx run atta`, `python -c "...cli.main(...)"`, a copy of the CLI under another name).
The Filtro (gate) still rejects any diff that touches protected paths, and branch protection on
GitHub remains the last line. Text that merely mentions a denied command (quoted args, heredocs
that stay inspected per the exceptions above) may be denied.

Wiring: the hook command is `atta guard`. If `atta` is not on PATH the hook command itself fails
(non-zero exit, no JSON) and Claude Code treats that as a non-blocking hook error, i.e. the tool
call is NOT blocked. `atta init` and the install instructions must keep `atta` on PATH (pipx).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from . import branches, config, machine, project, store
from .gate import matches_protected
from .ids import InvalidId, sanitize_id

WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
               "NotebookEdit": "notebook_path"}
SHELL_TOOLS = ("Bash", "PowerShell")
SIGNATURE = "«Atta»"
WORKTREES_DIR = ".worktrees"
MAX_DEPTH = 8
IS_WINDOWS = sys.platform.startswith("win")
CASE_INSENSITIVE = IS_WINDOWS or sys.platform == "darwin"

DEFAULT_DENY_COMMANDS = (
    "kubectl apply", "kubectl create", "kubectl delete", "kubectl replace", "kubectl patch",
    "kubectl rollout", "kubectl scale", "kubectl set", "helm install", "helm upgrade",
    "helm uninstall", "helm rollback", "terraform apply", "terraform destroy", "tofu apply",
    "tofu destroy", "pulumi up", "pulumi destroy", "vercel --prod", "netlify deploy",
    "fly deploy", "flyctl deploy", "firebase deploy", "gcloud deploy", "aws deploy",
    "sam deploy", "cdk deploy", "serverless deploy", "sls deploy", "eb deploy",
    "heroku releases:rollback", "docker push", "npm publish", "pnpm publish", "yarn publish",
    "twine upload", "cargo publish", "gem push", "gh workflow run",
)

_ARTIFACT = re.compile(
    r"(?:intake|spec|spec-review|decision)\.md"
    r"|feedback-[0-9a-z][0-9a-z-]*\.md"
    r"|round-[1-9][0-9]{0,3}/(?:build|refute(?:-retry)?|review-[0-9a-z][0-9a-z-]*)\.md"
)
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SUBST_DOLLAR = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)")
_SUBST_BACKTICK = re.compile(r"`([^`]*)`")
_NUMERIC = re.compile(r"\d+(?:\.\d+)?[smhd]?\Z")
_PUNCT_BASH = "();<>|&{}`\n"
_PUNCT_PS = "();<>|&{}\n"
_SEPARATOR_MARKS = set("(){};`\n")
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
_NULL_TARGETS = {"/dev/null", "nul", "$null", "/dev/stdout", "/dev/stderr", "con"}
_CONTINUATION_BASH = re.compile(r"\\\r?\n")
_CONTINUATION_PS = re.compile(r"`\r?\n")
_GIT_ENV = (re.compile(r"(?i)(?<![\w.])GIT_(?:CONFIG|ALIAS)\w*['\"]?\s*[=,]"),
            re.compile(r"(?i)(?:\benv:|\bsetx\s+|\bexport\s+)GIT_(?:CONFIG|ALIAS)"))
# Leading shell keywords skipped to reach the real command word; the rest of a `for`/`case`/
# `select`/`function` header is data (substitutions inside it are still inspected).
_KEYWORDS = {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "{", "}"}
_HEADER_KEYWORDS = {"for", "select", "case", "esac", "function", "in"}
_FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
INTERPRETERS = {"python", "python3", "py", "pythonw", "node", "perl", "ruby"}
_INTERP_CODE_FLAGS = {"-c", "-m", "-e", "-E", "-p", "--eval", "--print"}
_INTERP_VALUED = {"-W", "-X", "-r", "--require"}
_STDIN_FILES = {"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
# Command words whose presence anywhere keeps heredoc bodies inspected as commands.
_DYNAMIC = {"eval", "source", ".", "iex", "invoke-expression", "invoke-command", "icm",
            "start-process", "saps", "start-job", "xargs", "at", "batch"}
# Programs that run their stdin as commands: a heredoc fed to them is denied.
STDIN_RUNNERS = {"ssh", "su", "script", "wsl", "busybox", "make", "gmake", "crontab", "php",
                 "lua", "luajit", "tclsh", "wish", "expect", "osascript", "rscript", "nu",
                 "xonsh"}
# Text before the end of a heredoc opener line that the quote scanner does not model; when
# present the heredoc body stays inspected (see module docstring).
_FRAGILE_BASH = ("${", "$(", "`", "$[", "<(", ">(", "((")
_FRAGILE_PS = ("$(", "${", "`", "<(", "<#") + tuple("\u2018\u2019\u201a\u201b\u201c\u201d\u201e")
_HERE_MARK = "ATTA_HEREDOC_DATA"

POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "fish", "mksh"}
_SH_VALUED = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
# PowerShell parameters that take a value (full names; any unambiguous prefix is accepted).
_PS_VALUED = ("-executionpolicy", "-windowstyle", "-outputformat", "-inputformat",
              "-configurationname", "-configurationfile", "-workingdirectory",
              "-psconsolefile", "-version", "-custompipename", "-settingsfile")
_PS_VALUED_ALIASES = {"-ep", "-ex", "-w", "-o", "-of", "-if", "-wd", "-v"}
HTTP_CLIENTS = {"curl", "wget", "invoke-restmethod", "invoke-webrequest", "irm", "iwr"}
_WRITE_METHODS = {"PUT", "POST", "PATCH", "DELETE"}
_GITHUB_API = re.compile(r"(?i)api\.github\.com|/api/v3(?:/|$)|/api/graphql")
_GH_API_VALUED = {"-X", "--method", "-H", "--header", "-f", "-F", "--field", "--raw-field",
                  "--input", "-q", "--jq", "-t", "--template", "--hostname", "-p", "--preview",
                  "--cache"}
_GH_API_WRITE_PATHS = re.compile(
    r"(?:^|/)(?:pulls/[^/]+/merge|git/refs?|branches/.+/protection|merges|merge-upstream"
    r"|releases)(?:/|$)")
_GH_MUTATIONS = ("mergepullrequest", "enablepullrequestautomerge", "mergebranch", "deleteref",
                 "updateref")
PS_SHELLS = {"pwsh", "powershell"}
WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "nice", "ionice", "stdbuf", "timeout",
            "npx", "bunx", "pnpx", "watch", "caffeinate", "chronic"}
PS_WRITERS = {"out-file", "set-content", "add-content", "tee-object", "sc", "ac"}
_PS_PATH_FLAGS = ("-filepath", "-path", "-literalpath", "-filename")

_PUSH_DENY = {"--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all",
              "--branches", "--tags", "--follow-tags", "--delete", "--prune", "--receive-pack",
              "--exec"}
_PUSH_SAFE = {"--set-upstream", "--quiet", "--verbose", "--progress", "--no-progress",
              "--dry-run", "--porcelain", "--atomic", "--no-atomic", "--no-verify", "--verify",
              "--signed", "--no-signed", "--thin", "--no-thin", "--ipv4", "--ipv6",
              "--no-force-with-lease", "--no-follow-tags", "--no-recurse-submodules"}
_PUSH_VALUED = {"--repo", "--push-option"}
_GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
               "--super-prefix", "--exec-path"}
_GIT_CONFIG_KEYS = ("alias.", "remote.", "url.", "include.", "includeif.", "core.hookspath")
_GIT_CONFIG_VALUED = {"-f", "--file", "--blob", "--type", "--default", "--comment"}

# Atta CLI (L29): names it runs under, global options that take a value, and the subcommand
# actions that change atta/config.json or .claude/ (None = the subcommand itself, any form).
ATTA_NAMES = {"atta", "atta.sh"}
ATTA_SCRIPT = "atta.py"
ATTA_MODULES = {"atta.spine.cli"}
_ATTA_GLOBAL_VALUED = ("--project", "--jobs-dir")
_ATTA_MUTATING = {"init": None, "settings": {"set", "reset"},
                  "models": {"set", "unset", "reset", "fallback"},
                  "profiles": {"use", "clear"}}
_ATTA_SUB_VALUED = {"models": ("--runtime",)}
# Orchestrator actions only the /atta driver runs; denied inside a subagent (F4 T4).
_ATTA_DRIVER_ONLY = {"model-failed"}
_PY_VALUED = {"-X", "-W"}


# F2.y (S4): `permission_mode` values where Claude Code prompts the user, so an `ask` is answered
# by a person. Anything else (bypassPermissions, dontAsk, auto, missing, unknown) fails closed.
ASK_MODES = ("default", "acceptEdits", "plan")
# A command with any of these is not a single plain invocation (chaining, pipes, subshells,
# substitutions, heredocs, redirections, backslash escapes, variables, newlines) nor what the
# person approves verbatim (globs `* ? [`, `~` and `!` expansion, `#` comments): the shell
# would run something else (review R3). PowerShell also expands `@` (splatting).
_NOT_PLAIN_BASH = re.compile(r"[;&|<>(){}`$\\\r\n*?\[~!#]")
_NOT_PLAIN_PS = re.compile(r"[;&|<>(){}`$@\r\n*?\[~!#]")
# The `ask` prompt shows the command whole; a longer one is shown cut, so it is denied.
MAX_SHOWN_COMMAND = 400


class Deny(Exception):
    """The tool call is refused; the message is the reason shown to the agent."""


class Ask(Deny):
    """Not refused outright: Claude Code must ask the user to approve this exact command."""


# ---------------------------------------------------------------- entry points

def decision(reason: str, kind: str = "deny") -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                   "permissionDecisionReason": f"{reason} — {SIGNATURE}"}}


def run(data, out, *, root=None, explicit=None, environ=None) -> int:
    """Decide on hook input `data` (str or bytes); write the decision JSON (deny or ask) to
    `out`. Always 0."""
    reason = None
    kind = "deny"
    try:
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise Deny("Entrada del hook ilegible: no es UTF-8.") from None
        try:
            hook = json.loads(data)
        except (TypeError, ValueError):
            raise Deny("Entrada del hook inválida: no es JSON. Atta bloquea por seguridad.") from None
        got = decide(hook, root=root, explicit=explicit, environ=environ)
        if got:
            kind, reason = got
    except Deny as exc:
        reason = str(exc)
    except Exception as exc:  # fail closed on any unexpected error
        reason = f"Error interno del guard ({type(exc).__name__}: {exc}). Atta bloquea por seguridad."
    if reason:
        out.write(json.dumps(decision(reason, kind)) + "\n")
        out.flush()
    return 0


def decide(hook, *, root=None, explicit=None, environ=None) -> tuple[str, str] | None:
    """`("deny" | "ask", reason)` for one PreToolUse hook payload, or None when it is allowed."""
    try:
        _evaluate(hook, root, explicit, environ)
    except Ask as exc:
        return "ask", str(exc)
    except Deny as exc:
        return "deny", str(exc)
    return None


def evaluate(hook, *, root=None, explicit=None, environ=None) -> str | None:
    """Reason for one PreToolUse hook payload that is denied OR must be asked (see `decide` for
    which), or None when the call is allowed."""
    got = decide(hook, root=root, explicit=explicit, environ=environ)
    return got[1] if got else None


# The only launchers that may get `ask` (S4): the installed command found on PATH, or a bare
# Python running the installed module. A launcher given by path (`./atta.sh`, `/x/atta`,
# `x/atta.py`) could be a file an agent wrote, so it is denied (F2.y T1, A1).
_PLAIN_LAUNCHERS = frozenset(("atta", "atta.exe", "atta.cmd"))
_PLAIN_PYTHON = re.compile(r"(?:python(?:3(?:\.[0-9]+)?)?|py)(?:\.exe)?", re.IGNORECASE)


def _is_plain_atta(command: str, powershell: bool) -> bool:
    """True for exactly one Atta invocation with nothing around it: no chaining, pipes,
    subshells, substitutions, heredocs, redirections, variables or wrappers (`env`, `timeout`,
    `nohup`, `sudo`, `bash -c`, ...), no longer than MAX_SHOWN_COMMAND (the prompt shows it
    whole), and the first word is a bare launcher: `atta`, `atta.exe`, `atta.cmd`, or a bare
    `python`/`python3`/`py` running `-m atta.spine.cli`. No path, no `VAR=value` prefix."""
    if len(command) > MAX_SHOWN_COMMAND:
        return False
    if (_NOT_PLAIN_PS if powershell else _NOT_PLAIN_BASH).search(command):
        return False
    words = _tokenize(command, powershell)
    if not words:
        return False
    first = words[0]
    if "/" in first or "\\" in first or _ASSIGN.match(first):
        return False
    if first.lower() in _PLAIN_LAUNCHERS:
        return True
    if not _PLAIN_PYTHON.fullmatch(first) or _atta_via_interpreter(words[1:]) is None:
        return False
    rest = words[1:]
    return any(rest[i] == "-m" and rest[i + 1] == "atta.spine.cli"
               for i in range(len(rest) - 1))


def _shown_command(command: str) -> str:
    command = command.strip()
    return command if len(command) <= MAX_SHOWN_COMMAND else command[:MAX_SHOWN_COMMAND] + "…"


def _evaluate(hook, root, explicit, environ) -> None:
    if not isinstance(hook, dict):
        raise Deny("Entrada del hook inválida: se esperaba un objeto JSON.")
    tool = hook.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise Deny("Entrada del hook inválida: falta tool_name.")
    if tool not in WRITE_TOOLS and tool not in SHELL_TOOLS:
        return
    tool_input = hook.get("tool_input")
    if not isinstance(tool_input, dict):
        raise Deny(f"Entrada del hook inválida: tool_input de {tool} no es un objeto.")
    cwd = hook.get("cwd")
    cwd = Path(cwd) if isinstance(cwd, str) and cwd.strip() and Path(cwd).is_dir() else None
    try:
        root = _real(root) if root is not None else resolve_root(cwd or Path.cwd(),
                                                                 explicit=explicit,
                                                                 environ=environ)
    except (project.ProjectError, OSError, ValueError) as exc:
        raise Deny(f"No se pudo resolver el proyecto: {exc}") from None
    try:
        cfg = project.load_config(root)
    except config.ConfigError as exc:
        raise Deny(f"atta/config.json no es válido ({exc}); Atta bloquea hasta corregirlo.") from None
    rules = deny_rules(cfg)
    base = _real(cwd) if cwd is not None else root
    if tool in WRITE_TOOLS:
        check_write(tool_input.get(WRITE_TOOLS[tool]), root, base, cfg, environ=environ)
        return
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        raise Deny(f"Entrada del hook inválida: falta el comando de {tool}.")
    agent_id = hook.get("agent_id")
    ctx = _Ctx(root, cfg, rules, base, environ,
               subagent=isinstance(agent_id, str) and bool(agent_id.strip()))
    ctx.command = _shown_command(command)
    # S4: `ask` only for a person at an interactive prompt: main session (agent_id absent or
    # blank; any other value fails closed) running a single plain Atta invocation.
    main = agent_id is None or (isinstance(agent_id, str) and not agent_id.strip())
    mode = hook.get("permission_mode")
    plain = main and _is_plain_atta(command.strip(), tool == "PowerShell")
    ctx.ask_ok = plain and isinstance(mode, str) and mode in ASK_MODES
    # Sandbox L13: a plain config command from the main session denied only by the permission
    # mode says so, so the person knows what to change to get the approval prompt.
    if plain and not ctx.ask_ok:
        if isinstance(mode, str) and mode.strip():
            ctx.mode_hint = (f" Esta sesión corre en modo «{mode}», que no te pide confirmación, "
                             "así que Atta no puede preguntarte: para aprobarlo desde Claude Code "
                             "usa el modo default o acceptEdits (por ejemplo \"defaultMode\": "
                             "\"acceptEdits\" en .claude/settings.local.json del proyecto).")

    check_command(command, ctx, powershell=tool == "PowerShell")


def resolve_root(start, *, explicit=None, environ=None) -> Path:
    """Main checkout for hook cwd `start` (see module docstring)."""
    env = os.environ if environ is None else environ
    given = bool(explicit) or bool((env.get(project.ENV_VAR) or "").strip())
    root = _real(project.find_root(explicit, cwd=start, environ=env))
    if not given and _key(root.parent.name) == _key(WORKTREES_DIR):
        root = root.parent.parent
    return root


def cmd_guard(args, cfg=None, jobs=None) -> int:
    """`atta guard` as wired by cli.py: reads stdin, ignores the cli-level config/job store."""
    return run(_read_stdin(), sys.stdout, explicit=getattr(args, "project", None))


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "guard", help="Hook PreToolUse de Claude Code: bloquea escrituras y comandos peligrosos.",
        description="Lee el JSON del hook PreToolUse por stdin. Si permite, no imprime nada; si "
                    "bloquea, imprime la decisión deny; para un comando de configuración de "
                    "Atta en una sesión interactiva imprime ask (pide tu aprobación). "
                    "Siempre sale 0.")
    p.set_defaults(func=cmd_guard)


def _read_stdin():
    buf = getattr(sys.stdin, "buffer", None)
    return buf.read() if buf is not None else sys.stdin.read()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m atta.spine.guard",
                                     description="Hook PreToolUse de Atta (lee JSON por stdin).")
    parser.add_argument("--project", metavar="<carpeta>", help="raíz del proyecto")
    args = parser.parse_args(argv)
    return run(_read_stdin(), sys.stdout, explicit=args.project)


# ---------------------------------------------------------------- paths

def _key(part: str) -> str:
    # lower(), not casefold(): casefold maps U+017F (long s) to «s», which the file system does not.
    return part.lower() if CASE_INSENSITIVE else part


def _real(path) -> Path:
    return Path(os.path.realpath(str(path)))


def _relparts(child: Path, parent: Path):
    c, p = child.parts, parent.parts
    if len(c) < len(p) or [_key(x) for x in c[:len(p)]] != [_key(x) for x in p]:
        return None
    return c[len(p):]


def _check_raw_path(raw: str) -> None:
    if "\0" in raw:
        raise Deny("Ruta inválida: contiene un carácter nulo.")
    if not IS_WINDOWS:
        return
    if raw.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./", "\\??\\")):
        raise Deny(f"Ruta de dispositivo no permitida: «{raw}».")
    drive, rest = os.path.splitdrive(raw)
    if drive.endswith(":") and not rest.startswith(("\\", "/")):
        raise Deny(f"Ruta relativa a una unidad no permitida: «{raw}»; usa una ruta absoluta o "
                   "relativa a la carpeta actual.")
    if ":" in rest:
        raise Deny(f"Ruta no permitida (flujo alterno de NTFS): «{raw}».")
    for part in re.split(r"[\\/]", rest):
        if part not in ("", ".", "..") and part != part.rstrip(". "):
            raise Deny(f"Ruta no permitida (termina en punto o espacio): «{raw}».")


def _target(raw: str, base: Path) -> Path:
    _check_raw_path(raw)
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    try:
        return _real(path)
    except (OSError, ValueError) as exc:
        raise Deny(f"No se pudo resolver la ruta «{raw}»: {exc}.") from None


def _job_from_part(part: str) -> str:
    candidate = part.lower() if CASE_INSENSITIVE else part
    try:
        return sanitize_id(candidate)
    except InvalidId:
        raise Deny(f"«{part}» no es un id de job válido.") from None


def _active_job(root: Path, jid: str) -> dict:
    try:
        job = store.JobStore(project.jobs_dir(root)).load(jid)
    except store.StoreError as exc:
        raise Deny(f"{exc} Atta solo deja escribir para jobs activos.") from None
    stage = job.get("stage") if isinstance(job, dict) else None
    if stage not in machine.STAGES:
        raise Deny(f"El job {jid} tiene una etapa desconocida; Atta bloquea por seguridad.")
    if stage in machine.TERMINAL:
        raise Deny(f"El job {jid} está en {stage}; ya no admite cambios.")
    return job


def temp_roots(environ=None) -> list[Path]:
    """System temp folders where writes outside the project are allowed (see module docstring)."""
    env = os.environ if environ is None else environ
    raw = [tempfile.gettempdir()] + [env.get(k) for k in ("TEMP", "TMP", "TMPDIR")]
    try:
        home = _real(Path.home())
    except (RuntimeError, OSError, KeyError):
        home = None
    roots: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip() or "\0" in item:
            continue
        path = Path(item.strip())
        if not path.is_absolute():
            continue
        try:
            path = _real(path)
        except (OSError, ValueError):
            continue
        if path.parent == path or (home is not None and _relparts(home, path) is not None):
            continue
        if all(_relparts(path, r) != () for r in roots):
            roots.append(path)
    return roots


def check_write(raw, root: Path, base: Path, cfg: dict, environ=None) -> None:
    """Raise Deny unless writing `raw` is allowed (see module docstring)."""
    if not isinstance(raw, str) or not raw.strip():
        raise Deny("Entrada del hook inválida: falta la ruta del archivo.")
    target = _target(raw, base)
    rel = _relparts(target, root)
    if rel is None:
        if any(_relparts(target, t) is not None for t in temp_roots(environ)):
            return
        raise Deny(f"Atta solo deja escribir dentro del worktree del job; «{raw}» queda fuera "
                   "del proyecto y de la carpeta temporal del sistema.")
    keys = [_key(p) for p in rel]
    if len(rel) >= 3 and keys[0] == _key(WORKTREES_DIR):
        _check_worktree_rel(rel, root, cfg)
        return
    jobs_rel = [_key(p) for p in project.PROJECT_SUBDIR.parts] + [_key("jobs")]
    if len(rel) >= len(jobs_rel) + 2 and keys[:len(jobs_rel)] == jobs_rel:
        jid = _job_from_part(rel[len(jobs_rel)])
        _active_job(root, jid)
        inner_s = "/".join(rel[len(jobs_rel) + 1:])
        if _ARTIFACT.fullmatch(inner_s.lower() if CASE_INSENSITIVE else inner_s):
            return
        raise Deny(f"En atta/jobs/{jid}/ solo se escriben los artefactos del job (intake.md, "
                   f"spec.md, round-N/build.md, ...); «{inner_s}» no es uno.")
    raise Deny(f"Atta solo deja escribir dentro del worktree de un job activo "
               f"(.worktrees/<id>/) o sus artefactos; «{'/'.join(rel) or raw}» queda fuera.")


def _check_worktree_rel(rel, root: Path, cfg: dict) -> None:
    """`rel` = (.worktrees, <id>, ...): deny unless it is a writable path of an active job."""
    jid = _job_from_part(rel[1])
    _active_job(root, jid)
    if not (root / WORKTREES_DIR / jid).is_dir():
        raise Deny(f"El worktree del job {jid} no existe.")
    inner = rel[2:]
    if any(_key(p) == _key(".git") for p in inner):
        raise Deny(f"Atta no deja escribir dentro de .git ({'/'.join(inner)}).")
    inner_s = "/".join(inner)
    if matches_protected(inner_s, cfg["protected_paths"]):
        raise Deny(f"«{inner_s}» es una ruta protegida (protected_paths); los agentes no la "
                   "tocan.")


# ---------------------------------------------------------------- shell commands

class _Ctx:
    def __init__(self, root: Path, cfg: dict, rules, cwd: Path | None, environ=None,
                 subagent: bool = False):
        self.root, self.cfg, self.rules, self.cwd = root, cfg, rules, cwd
        self.environ = environ
        self.subagent = subagent
        self.command = ""
        self.ask_ok = False
        self.mode_hint = ""


def deny_rules(cfg: dict):
    section = cfg.get("guard", {})
    if not isinstance(section, dict):
        raise Deny("config.json: guard debe ser un objeto; Atta bloquea hasta corregirlo.")
    raw = section.get("deny_commands", list(DEFAULT_DENY_COMMANDS))
    if not isinstance(raw, list) or not all(isinstance(r, str) and r.strip() for r in raw):
        raise Deny("config.json: guard.deny_commands debe ser una lista de textos; Atta bloquea "
                   "hasta corregirlo.")
    rules = []
    for text in raw:
        try:
            words = shlex.split(text)
        except ValueError:
            words = text.split()
        if words:
            rules.append((text, _cmd_name(words[0]), [w.lower() for w in words[1:]]))
    return rules


def _tokenize(command: str, powershell: bool) -> list[str]:
    lex = shlex.shlex(command, posix=True,
                      punctuation_chars=_PUNCT_PS if powershell else _PUNCT_BASH)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    if powershell:
        lex.escape = "`"
    try:
        return list(lex)
    except ValueError:
        # Unbalanced quotes: ignore quoting altogether (over-splitting only adds checks).
        punct = re.escape(_PUNCT_PS if powershell else _PUNCT_BASH)
        return [t.strip("'\"") for t in re.findall(rf"[{punct}]+|[^\s{punct}]+", command)
                if t.strip("'\"")]


def _is_punct(tok: str) -> bool:
    return bool(tok) and all(c in _PUNCT_BASH for c in tok)


def _segments(tokens: list[str]):
    seg: list[str] = []
    for tok in tokens:
        if tok != "{}" and _is_punct(tok) and (set(tok) & _SEPARATOR_MARKS or not set(tok) & set("<>")):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


# ---------------------------------------------------------------- heredocs and here-strings

class _Doc:
    """One heredoc/here-string: operator span, body span, terminator end, expansion."""

    def __init__(self, op_start, op_end, body_start, body_end, term_end, expand, line_nl):
        self.op_start, self.op_end = op_start, op_end
        self.body_start, self.body_end, self.term_end = body_start, body_end, term_end
        self.expand, self.line_nl = expand, line_nl


def _read_delim(s: str, j: int):
    """(delimiter, quoted, end) of the heredoc word at `j`; ('', False, j) when there is none."""
    out, quoted, n = [], False, len(s)
    while j < n:
        c = s[j]
        if c in " \t\r\n;&|<>()":
            break
        if c in "'\"":
            k = s.find(c, j + 1)
            if k == -1 or "\n" in s[j + 1:k]:
                return "", False, j
            out.append(s[j + 1:k])
            quoted, j = True, k + 1
        elif c == "\\" and j + 1 < n:
            out.append(s[j + 1])
            quoted, j = True, j + 2
        else:
            out.append(c)
            j += 1
    return "".join(out), quoted, j


def _find_heredocs(s: str) -> list[_Doc]:
    """Bash heredocs outside quotes and comments; stops at the first unterminated one."""
    docs: list[_Doc] = []
    pending = []
    state, i, n = None, 0, len(s)
    while i < n:
        c = s[i]
        if state == "'":
            state = None if c == "'" else state
            i += 1
            continue
        if state in ('"', "$'"):
            if c == "\\":
                i += 2
                continue
            if c == ("'" if state == "$'" else '"'):
                state = None
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c == "$" and s.startswith("$'", i):
            state, i = "$'", i + 2
            continue
        if c in "'\"":
            state, i = c, i + 1
            continue
        if c == "#" and (i == 0 or s[i - 1] in " \t\r\n;&|()<>"):
            nl = s.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if s.startswith("((", i) or s.startswith("$((", i):
            # Arithmetic: `<<` is a shift there, never a heredoc.
            j, level = i + (3 if c == "$" else 2), 2
            while j < n and level:
                level += {"(": 1, ")": -1}.get(s[j], 0)
                j += 1
            i = j
            continue
        if s.startswith("<<", i) and not s.startswith("<<<", i) and (i == 0 or s[i - 1] != "<"):
            j = i + 2
            dash = j < n and s[j] == "-"
            j += 1 if dash else 0
            while j < n and s[j] in " \t":
                j += 1
            delim, quoted, end = _read_delim(s, j)
            if delim:
                pending.append((i, end, delim, dash, not quoted))
                i = end
            else:
                i += 2
            continue
        if c == "\n" and pending:
            pos = i + 1
            for op_start, op_end, delim, dash, expand in pending:
                body_start, found = pos, False
                while pos <= n:
                    nl = s.find("\n", pos)
                    end = n if nl == -1 else nl
                    line = s[pos:end].rstrip("\r")
                    if (line.lstrip("\t") if dash else line) == delim:
                        docs.append(_Doc(op_start, op_end, body_start, pos, end, expand, i))
                        pos, found = (n if nl == -1 else nl), True
                        break
                    if nl == -1:
                        break
                    pos = nl + 1
                if not found:
                    return docs
            pending, i = [], pos
            continue
        i += 1
    return docs


def _find_herestrings(s: str) -> list[_Doc]:
    """PowerShell here-strings `@'`/`@"` outside quotes and comments."""
    docs: list[_Doc] = []
    state, i, n = None, 0, len(s)
    while i < n:
        c = s[i]
        if state == "'":
            if c == "'":
                if s.startswith("''", i):
                    i += 2
                    continue
                state = None
            i += 1
            continue
        if state == '"':
            if c == "`":
                i += 2
                continue
            if c == '"':
                if s.startswith('""', i):
                    i += 2
                    continue
                state = None
            i += 1
            continue
        if c == "`":
            i += 2
            continue
        if s.startswith("<#", i):  # block comment
            k = s.find("#>", i + 2)
            i = n if k == -1 else k + 2
            continue
        if c == "#":
            nl = s.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "@" and i + 1 < n and s[i + 1] in "'\"":
            quote = s[i + 1]
            nl = s.find("\n", i + 2)
            if nl != -1 and not s[i + 2:nl].strip():
                pos, found = nl + 1, False
                while pos <= n:
                    nxt = s.find("\n", pos)
                    end = n if nxt == -1 else nxt
                    if s.startswith(quote + "@", pos):
                        docs.append(_Doc(i, i + 2, nl + 1, pos, pos + 2, quote == '"', None))
                        found = True
                        break
                    if nxt == -1:
                        break
                    pos = nxt + 1
                if not found:
                    return docs
                i = docs[-1].term_end
                continue
        if c in "'\"":
            state = c
        elif c in "\u2018\u2019\u201a\u201b":
            state = "'"
        elif c in "\u201c\u201d\u201e":
            state = '"'
        i += 1
    return docs


def _pipelines(tokens: list[str]):
    """Statements as lists of pipeline stages; each stage is (words, has_heredoc_marker)."""
    stmts = [[[[], False]]]
    i = 0
    while i < len(tokens):
        t = tokens[i]
        stage = stmts[-1][-1]
        if t != "{}" and _is_punct(t):
            if set(t) & set("<>") and not set(t) & _SEPARATOR_MARKS:
                if t == "<<" or (i + 1 < len(tokens) and tokens[i + 1] == _HERE_MARK):
                    stage[1] = True
                i += 2
                continue
            if t in ("|", "|&"):
                stmts[-1].append([[], False])
            else:
                stmts.append([[[], False]])
            i += 1
            continue
        if t == _HERE_MARK:
            stage[1] = True
        stage[0].append(t)
        i += 1
    return stmts


def _resolve(words: list[str]):
    """(command name, args) after assignments, keywords and wrappers; ('', []) for compounds."""
    while words and _ASSIGN.match(words[0]):
        words = words[1:]
    if not words or words[0] in _KEYWORDS or words[0] in _HEADER_KEYWORDS:
        return "", []
    name, args = _cmd_name(words[0]), words[1:]
    if name in ("sudo", "doas"):
        return _resolve(_skip_flags(args, ("-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t")))
    if name == "env":
        if any(a in ("-S", "--split-string") for a in args):
            return "eval", args
        return _resolve(_skip_flags(args, ("-u", "--unset", "-C", "--chdir")))
    if name in WRAPPERS:
        return _resolve(_skip_flags(args, ("-n", "-s", "-k", "-c"), numeric=True))
    return name, args


def _is_dynamic(words: list[str], powershell: bool) -> bool:
    if not words:
        return False
    if any("scriptblock" in w.lower() for w in words):
        return True
    first = words[0]
    if powershell and first.startswith("$"):
        return len(words) == 1 or not re.fullmatch(r"[-+*/%?]?=", words[1])
    if not powershell and any(c in first for c in "$`") and not _ASSIGN.match(first):
        return True
    name, _args = _resolve(words)
    return name in _DYNAMIC or name in POSIX_SHELLS or name in PS_SHELLS or name == "cmd"


def _interpreter_fed(args: list[str]) -> bool:
    """True when an interpreter would read its program from stdin."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in _STDIN_FILES:
            return True
        if a in _INTERP_CODE_FLAGS or a.startswith(("--eval=", "--print=")):
            return False
        if a.startswith("-"):
            i += 2 if a in _INTERP_VALUED else 1
            continue
        return False
    return True


def _is_interpreter(name: str) -> bool:
    return name in INTERPRETERS or bool(re.fullmatch(r"python[0-9][0-9.]*w?", name))


def _feeding(line: str, powershell: bool) -> bool:
    """True when the heredoc on `line` feeds a shell/dynamic consumer (keep it inspected).
    Raises Deny when it feeds code to an interpreter or to a program that runs its input."""
    line = (_CONTINUATION_PS if powershell else _CONTINUATION_BASH).sub("", line)
    for stmt in _pipelines(_tokenize(line, powershell)):
        marked = [k for k, (_w, has) in enumerate(stmt) if has]
        if not marked:
            continue
        for words, _has in stmt[marked[0]:]:
            name, args = _resolve(words)
            if not name or _is_dynamic(words, powershell):
                return True
            if _is_interpreter(name) and _interpreter_fed(args):
                raise Deny(f"Atta no deja pasar código a {name} por un heredoc: no puede "
                           "verificarlo. Escribe el script en un archivo y ejecútalo.")
            if name in STDIN_RUNNERS:
                raise Deny(f"Atta no deja pasar un heredoc a {name}: ejecutaría su contenido "
                           "como comandos sin que Atta pueda verificarlos.")
    return False


def _line_start(s: str, pos: int, cont: str) -> int:
    """Start of the logical line holding `pos` (lines ending in `cont` + newline are joined)."""
    start = s.rfind("\n", 0, pos) + 1
    while start >= 2:
        k = start - 2
        if s[k] == "\r" and k >= 1:
            k -= 1
        if s[k] != cont:
            break
        start = s.rfind("\n", 0, k) + 1
    return start


def _skeleton(s: str, docs: list[_Doc], start: int, end: int, powershell: bool) -> str:
    """s[start:end] without the heredoc bodies (or whole here-strings) that lie inside it."""
    out, pos = [], start
    for d in docs:
        lo = d.op_start if powershell else d.body_start
        if lo >= start and d.term_end <= end:
            out.append(s[pos:lo])
            pos = d.term_end
    out.append(s[pos:end])
    return "".join(out)


def _strip_heredocs(command: str, ctx: "_Ctx", powershell: bool, depth: int) -> str:
    """`command` with data heredoc/here-string bodies removed (see module docstring)."""
    docs = _find_herestrings(command) if powershell else _find_heredocs(command)
    if not docs:
        return command
    keep: set[int] = set()
    groups: dict[int, list[int]] = {}
    for k, d in enumerate(docs):
        groups.setdefault(d.line_nl if d.line_nl is not None else -1 - k, []).append(k)
    for members in groups.values():
        first, last = docs[members[0]], docs[members[-1]]
        if powershell:
            start = _line_start(command, first.op_start, "`")
            nl = command.find("\n", last.term_end)
            rest = command[last.term_end:len(command) if nl == -1 else nl]
            line = command[start:first.op_start] + _HERE_MARK + " " + rest
            context = _skeleton(command, docs, 0, first.op_start, True) + " " + rest
            fragile = _FRAGILE_PS
        else:
            start = _line_start(command, first.op_start, "\\")
            line = command[start:first.line_nl]
            context = _skeleton(command, docs, 0, first.line_nl, False)
            fragile = _FRAGILE_BASH
        tail = line.rstrip()
        if (any(f in context for f in fragile)
                or tail.endswith(("|", "&&", "||", "\\", "`"))
                or _feeding(line, powershell)):
            keep.update(members)
    out, last_end = [], 0
    stripped = [d for k, d in enumerate(docs) if k not in keep]
    for d in stripped:
        if powershell:
            out.append(command[last_end:d.op_start] + _HERE_MARK + " ")
        else:
            out.append(command[last_end:d.body_start])
        last_end = d.term_end
    out.append(command[last_end:])
    result = "".join(out)
    tokens = _tokenize(result, powershell)
    if any(_is_dynamic(words, powershell) for stmt in _pipelines(tokens) for words, _h in stmt):
        keep, stripped, result = set(range(len(docs))), [], command
    for k in sorted(keep):
        # Kept bodies are code: inspect them as commands (a here-string is quoted in the text).
        d = docs[k]
        check_command(command[d.body_start:d.body_end], ctx, powershell=powershell,
                      depth=depth + 1)
    for d in stripped:
        if d.expand:
            body = command[d.body_start:d.body_end]
            for inner in _SUBST_DOLLAR.findall(body):
                check_command(inner, ctx, powershell=powershell, depth=depth + 1)
            if not powershell:
                for inner in _SUBST_BACKTICK.findall(body):
                    check_command(inner, ctx, powershell=False, depth=depth + 1)
    return result


def check_command(command: str, ctx: _Ctx, *, powershell: bool = False, depth: int = 0) -> None:
    """Raise Deny when any part of `command` is refused."""
    if depth > MAX_DEPTH:
        raise Deny("Comando demasiado anidado para verificarlo; Atta bloquea por seguridad.")
    command = _strip_heredocs(command, ctx, powershell, depth)
    command = (_CONTINUATION_PS if powershell else _CONTINUATION_BASH).sub("", command)
    if any(p.search(command) for p in _GIT_ENV):
        raise Deny("Atta no deja definir variables GIT_CONFIG_* / GIT_ALIAS*: podrían crear alias "
                   "o desviar un push.")
    for inner in _SUBST_DOLLAR.findall(command):
        check_command(inner, ctx, powershell=powershell, depth=depth + 1)
    if not powershell:
        for inner in _SUBST_BACKTICK.findall(command):
            check_command(inner, ctx, powershell=False, depth=depth + 1)
    for seg in _segments(_tokenize(command, powershell)):
        _check_segment(seg, ctx, powershell, depth)


def _check_segment(seg: list[str], ctx: _Ctx, powershell: bool, depth: int) -> None:
    words: list[str] = []
    i = 0
    while i < len(seg):
        tok = seg[i]
        if _is_punct(tok) and set(tok) & set("<>"):
            target = seg[i + 1] if i + 1 < len(seg) else None
            if tok == "<<<" and target is not None:
                check_command(target, ctx, powershell=powershell, depth=depth + 1)
            if target is not None and ">" in tok and not (
                    tok.endswith("&") and (target.isdigit() or target == "-")):
                _check_shell_target(target, ctx)
            i += 2
            continue
        words.append(tok)
        i += 1
    _check_words(words, ctx, powershell, depth)


def _cmd_name(tok: str) -> str:
    name = re.split(r"[\\/]", tok.strip("'\""))[-1].lower()
    for suffix in _EXE_SUFFIXES:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _skip_flags(args: list[str], valued=(), numeric=False) -> list[str]:
    i = 0
    while i < len(args):
        a = args[i]
        if _ASSIGN.match(a):
            i += 1
        elif a.startswith("-") and a != "-":
            i += 2 if a in valued else 1
        elif numeric and _NUMERIC.match(a):
            i += 1
        else:
            break
    return args[i:]


def _check_words(words: list[str], ctx: _Ctx, powershell: bool, depth: int) -> None:
    while words and (_ASSIGN.match(words[0]) or words[0] in _KEYWORDS):
        words = words[1:]
    if not words or words[0] in _HEADER_KEYWORDS:
        return
    name, args = _cmd_name(words[0]), words[1:]
    sub = lambda text, ps=powershell: check_command(text, ctx, powershell=ps, depth=depth + 1)

    if name == "find":
        for j, a in enumerate(args):
            if a in _FIND_EXEC:
                inner = []
                for b in args[j + 1:]:
                    if b in (";", "+", "\\;"):
                        break
                    inner.append(b)
                _check_words(inner, ctx, powershell, depth + 1)

    if name in ("sudo", "doas"):
        return _check_words(_skip_flags(args, ("-u", "-g", "-C", "-h", "-p", "-U", "-r", "-t")),
                            ctx, powershell, depth)
    if name == "env":
        rest = args
        for j, a in enumerate(args):
            if a in ("-S", "--split-string") and j + 1 < len(args):
                sub(" ".join(args[j + 1:]), False)
                return
        return _check_words(_skip_flags(rest, ("-u", "--unset", "-C", "--chdir")),
                            ctx, powershell, depth)
    if name == "xargs":
        return _check_words(_skip_flags(args, ("-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a")),
                            ctx, powershell, depth)
    if name in WRAPPERS:
        return _check_words(_skip_flags(args, ("-n", "-s", "-k", "-c"), numeric=True),
                            ctx, powershell, depth)
    if name in ("npm", "pnpm", "yarn") and args and args[0] in ("exec", "dlx", "x"):
        return _check_words(_skip_flags(args[1:]), ctx, powershell, depth)
    if name in POSIX_SHELLS:
        return _check_posix_shell_args(name, args, sub,
                                       on_script=lambda rest: _check_atta_script(rest, ctx))
    if name in PS_SHELLS:
        return _check_powershell_args(name, args, sub)
    if name == "cmd":
        for j, a in enumerate(args):
            if a.lower() in ("/c", "/k", "/r"):
                sub(" ".join(args[j + 1:]), False)
                return
        raise _stdin_shell(name)
    if name == "eval":
        return sub(" ".join(args), False)
    if name in ("invoke-expression", "iex"):
        return sub(" ".join(a for a in args if a.lower() != "-command"), True)
    if name in ("start-process", "saps", "start"):
        return sub(" ".join(a for a in args if not a.startswith("-")), powershell)

    if name in ("cd", "chdir", "pushd", "set-location", "sl"):
        dest = next((a for a in args if not a.startswith("-")), None)
        if dest is not None:
            if any(c in dest for c in "$`%~") or ctx.cwd is None:
                ctx.cwd = None
            else:
                ctx.cwd = _target(dest, ctx.cwd)
    elif name == "git":
        _check_git(args, ctx)
    elif name == "gh":
        _check_gh(args, ctx)
    elif name == "tee":
        for a in args:
            if not a.startswith("-"):
                _check_shell_target(a, ctx)
    elif name in PS_WRITERS:
        _check_ps_writer(args, ctx)
    elif name in HTTP_CLIENTS:
        _check_http(name, args)
    elif name in ATTA_NAMES:
        if ctx.ask_ok:  # A3: a project deny rule beats the `ask` of a config command
            _check_deny_rules(name, args, ctx)
        _check_atta(args, ctx)
    elif _is_interpreter(name):
        atta_args = _atta_via_interpreter(args)
        if atta_args is not None:
            if ctx.ask_ok:
                _check_deny_rules(name, args, ctx)
            _check_atta(atta_args, ctx)
    _check_deny_rules(name, args, ctx)


def _option_takes_value(arg: str, valued) -> bool:
    """True for `--name` (or an argparse prefix of it, no `=`) of an option in `valued`."""
    return arg.startswith("--") and len(arg) > 2 and "=" not in arg and any(
        full.startswith(arg) for full in valued)


def _first_positional(args: list[str], valued=()) -> tuple[str | None, list[str]]:
    i = 0
    while i < len(args):
        a = args[i]
        if _option_takes_value(a, valued):
            i += 2
        elif a.startswith("-") and a != "-":
            i += 1
        else:
            return a, args[i + 1:]
    return None, []


def _check_atta(args: list[str], ctx: "_Ctx | None" = None) -> None:
    """Deny Atta's configuration-mutating subcommands, and the driver-only ones inside a
    subagent (see module docstring)."""
    subcmd, rest = _first_positional(args, _ATTA_GLOBAL_VALUED)
    subcmd = (subcmd or "").lower()
    if subcmd in _ATTA_DRIVER_ONLY and ctx is not None and ctx.subagent:
        raise Deny(f"Solo el orquestador /atta registra la falla de modelo de un agente (atta "
                   f"{subcmd}); un subagente no puede. Termina tu trabajo o informa el error "
                   "en tu respuesta.")
    if subcmd not in _ATTA_MUTATING:
        return
    actions = _ATTA_MUTATING[subcmd]
    shown = f"atta {subcmd}"
    if actions is not None:
        action, _ = _first_positional(rest, _ATTA_SUB_VALUED.get(subcmd, ()))
        action = (action or "").lower()
        if action not in actions:
            return
        shown += f" {action}"
    command = ctx.command if ctx is not None else ""
    if ctx is not None and ctx.ask_ok:
        raise Ask(f"Atta pide tu aprobación para cambiar la configuración ({shown}). Comando "
                  f"exacto: {command}. Apruébalo solo si lo pediste tú; si no, recházalo y corre "
                  "ese comando en tu terminal.")
    hint = ctx.mode_hint if ctx is not None else ""
    raise Deny(f"Atta no deja que un agente cambie su propia configuración ({shown}). Corre ese "
               f"comando tú en tu terminal. Comando: {command}{hint}")


def _check_atta_script(args: list[str], ctx: "_Ctx | None" = None) -> None:
    """`sh atta.sh ...`: `args[0]` is the script a shell runs."""
    if args and _cmd_name(args[0]) == "atta.sh":
        _check_atta(args[1:], ctx)


def _atta_via_interpreter(args: list[str]) -> list[str] | None:
    """Atta CLI arguments when a Python interpreter runs Atta (`-m atta.spine.cli`, `atta.py`)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a.startswith("--"):
            i += 1
            continue
        if a.startswith("-") and a != "-":
            if a in _PY_VALUED:
                i += 2
                continue
            cluster = a[1:]
            if cluster[:1] in ("X", "W"):
                i += 1
                continue
            pos = [k for k in (cluster.find("m"), cluster.find("c")) if k != -1]
            if not pos:
                i += 1
                continue
            k = min(pos)
            if cluster[k] == "c":
                return None
            module = cluster[k + 1:]
            if module:
                rest = args[i + 1:]
            else:
                module = args[i + 1] if i + 1 < len(args) else ""
                rest = args[i + 2:]
            return rest if module in ATTA_MODULES else None
        break
    if i < len(args) and _cmd_name(args[i]) == ATTA_SCRIPT:
        return args[i + 1:]
    return None


def _stdin_shell(name: str) -> Deny:
    return Deny(f"{name} sin -c ni archivo de script lee comandos de la entrada estándar y Atta "
                "no puede verificarlos; pasa el comando con -c (o -Command, /c).")


def _check_posix_shell_args(name: str, args: list[str], sub, on_script=None) -> None:
    j = 0
    script = on_script or (lambda _rest: None)
    while j < len(args):
        a = args[j]
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", a) or a == "--command":
            if j + 1 < len(args):
                sub(args[j + 1], False)
                return
            raise _stdin_shell(name)
        if a.startswith("--command="):
            return sub(a.split("=", 1)[1], False)
        if a in _SH_VALUED:
            j += 2
            continue
        if a in ("-", "--"):
            if a == "--" and j + 1 < len(args) and args[j + 1] != "-":
                return script(args[j + 1:])  # script file (known limit)
            raise _stdin_shell(name)
        if re.fullmatch(r"[-+][A-Za-z]*s[A-Za-z]*", a):
            raise _stdin_shell(name)
        if a.startswith(("-", "+")):
            j += 1
            continue
        return script(args[j:])  # script file (known limit: its content is not inspected)
    raise _stdin_shell(name)


def _ps_param(arg: str):
    """(`-name` lowered, glued value or None) for a PowerShell parameter (`-x`, `/x`, `-x:v`)."""
    low = arg.lower()
    if re.fullmatch(r"/[a-z]+(?::.*)?", low, re.S):  # `/c`, `/Command:x`; not `/path/x.ps1`
        low = "-" + low[1:]
    if not low.startswith("-") or len(low) < 2:
        return None, None
    name, colon, value = low.partition(":")
    return name, (arg.split(":", 1)[1] if colon else None)


def _check_powershell_args(shell: str, args: list[str], sub) -> None:
    positional = []
    j = 0
    while j < len(args):
        a = args[j]
        name, glued = _ps_param(a)
        if name is None:
            if a == "-":
                raise _stdin_shell(shell)
            positional.append(a)
            j += 1
            continue
        rest = ([glued] if glued is not None else []) + args[j + 1:]
        if "-command".startswith(name):
            if not rest or rest == ["-"]:
                raise _stdin_shell(shell)
            return sub(" ".join(rest), True)
        if name in ("-e", "-ec") or (len(name) >= 3 and "-encodedcommand".startswith(name)):
            payload = rest[0] if rest else ""
            try:
                text = base64.b64decode(payload, validate=True).decode("utf-16-le")
            except (binascii.Error, ValueError):
                raise Deny("Comando codificado de PowerShell ilegible; Atta bloquea por "
                           "seguridad.") from None
            return sub(text, True)
        if "-file".startswith(name):
            if not rest or rest[0] == "-":
                raise _stdin_shell(shell)
            return  # script file (known limit: its content is not inspected)
        valued = name in _PS_VALUED_ALIASES or (
            len(name) >= 3 and any(full.startswith(name) for full in _PS_VALUED))
        j += 1 if (glued is not None or not valued) else 2
    if not positional:
        raise _stdin_shell(shell)
    # A bare argument is -Command in Windows PowerShell and -File in pwsh: check it as code.
    sub(" ".join(positional), True)


def _check_http(name: str, args: list[str]) -> None:
    """curl/wget/Invoke-RestMethod/Invoke-WebRequest writing to the GitHub API are denied."""
    if not any(_GITHUB_API.search(a) for a in args):
        return
    method, implied, get = None, False, False
    unix = name in ("curl", "wget")
    for j, a in enumerate(args):
        nxt = args[j + 1] if j + 1 < len(args) else ""
        low = a.lower()
        ps_name, glued = _ps_param(a)
        if ps_name and len(ps_name) >= 3 and "-method".startswith(ps_name):
            method = glued if glued is not None else nxt
        elif a in ("-X", "--request", "--method"):
            method = nxt
        elif low.startswith(("--request=", "--method=")):
            method = a.split("=", 1)[1]
        elif re.fullmatch(r"-[A-Za-z]*X\S*", a):
            tail = a.split("X", 1)[1]
            method = tail or nxt
        elif low.startswith(("--data", "--json", "--form", "--upload-file", "--post-data",
                             "--post-file", "--body-data", "--body-file")) \
                or (unix and re.fullmatch(r"-[A-Za-z]*[dFT]\S*", a)):
            implied = True
        elif a in ("-G", "--get"):
            get = True
    method = (method or "").strip("'\"").upper()
    if method in _WRITE_METHODS or (implied and not get and not method):
        raise Deny(f"{name} con escritura ({method or 'POST'}) contra la API de GitHub no está "
                   "permitido; usa los comandos de Atta (atta publish, atta comment, ...).")


def _check_ps_writer(args: list[str], ctx: _Ctx) -> None:
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        low = a.lower()
        flag, _, glued = low.partition(":")
        if flag in _PS_PATH_FLAGS:
            value = a.split(":", 1)[1] if glued else (args[i + 1] if i + 1 < len(args) else "")
            _check_shell_target(value, ctx)
            i += 1 if glued else 2
            continue
        if a.startswith("-"):
            i += 2 if low in ("-value", "-encoding", "-inputobject", "-width") else 1
            continue
        positional.append(a)
        i += 1
    if positional:
        _check_shell_target(positional[0], ctx)


def _check_shell_target(raw: str, ctx: _Ctx) -> None:
    """Shell write target: allowed in an active job worktree or the system temp folder."""
    text = raw.strip()
    if not text or text.lower() in _NULL_TARGETS:
        return
    if any(c in text for c in "$`%"):
        raise Deny(f"No se puede verificar el destino «{raw}» de la escritura; usa una ruta "
                   "literal.")
    if text.startswith("~"):
        text = os.path.expanduser(text)
    if ctx.cwd is None and not Path(text).is_absolute():
        raise Deny(f"No se puede verificar el destino «{raw}»: la carpeta actual es desconocida.")
    target = _target(text, ctx.cwd or ctx.root)
    rel = _relparts(target, ctx.root)
    if rel is None:
        if any(_relparts(target, t) is not None for t in temp_roots(ctx.environ)):
            return
        raise Deny(f"Atta solo deja escribir desde la terminal dentro del worktree de un job "
                   f"activo o en la carpeta temporal del sistema; «{raw}» queda fuera del "
                   "proyecto y de la carpeta temporal.")
    if any(_key(p) == _key(".git") for p in rel):
        raise Deny(f"Atta no deja escribir dentro de .git desde la terminal («{raw}»).")
    in_worktree = len(rel) >= 3 and _key(rel[0]) == _key(WORKTREES_DIR)
    inner = rel[2:] if in_worktree else rel
    if inner and matches_protected("/".join(inner), ctx.cfg["protected_paths"]):
        raise Deny(f"Atta no deja escribir en la ruta protegida «{raw}» desde la terminal.")
    if not in_worktree:
        raise Deny(f"Atta solo deja escribir desde la terminal dentro del worktree de un job "
                   f"activo (.worktrees/<id>/) o en la carpeta temporal del sistema; «{raw}» "
                   "queda en el proyecto.")
    _check_worktree_rel(rel, ctx.root, ctx.cfg)


def _check_deny_rules(name: str, args: list[str], ctx: _Ctx) -> None:
    lowered = [a.lower() for a in args]
    for text, first, rest in ctx.rules:
        if name != first:
            continue
        it = iter(lowered)
        if all(word in it for word in rest):
            raise Deny(f"«{text}» es un comando de despliegue o publicación; los agentes de Atta "
                       "no despliegan (guard.deny_commands).")


# ---------------------------------------------------------------- git and gh

def _check_git(args: list[str], ctx: _Ctx) -> None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in _GIT_VALUED:
            value = args[i + 1] if i + 1 < len(args) else ""
            if a == "-c":
                _check_git_key(value.split("=", 1)[0])
            i += 2
            continue
        if a.startswith("--config-env="):
            _check_git_key(a.split("=", 1)[1].split("=", 1)[0])
        i += 1
    if i >= len(args):
        return
    subcmd, rest = args[i].lower(), args[i + 1:]
    if subcmd == "push":
        _check_push(rest, ctx)
    elif subcmd == "branch":
        for a in rest:
            if a == "--delete" or (a.startswith("-") and not a.startswith("--")
                                   and set(a[1:]) & {"d", "D"}):
                raise Deny("Los agentes de Atta no borran ramas (git branch -d/-D).")
    elif subcmd in ("send-pack", "http-push"):
        raise Deny(f"git {subcmd} no está permitido; usa git push origin atta/<id>.")
    elif subcmd == "config":
        _check_git_config(rest)
    elif subcmd == "update-ref":
        _check_update_ref(rest, ctx)


def _check_update_ref(rest: list[str], ctx: _Ctx) -> None:
    positional = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-d", "--delete", "--stdin"):
            raise Deny(f"git update-ref {a} no está permitido: los agentes de Atta no borran ni "
                       "reescriben ramas.")
        if a == "-m":
            i += 2
            continue
        if not a.startswith("-"):
            positional.append(a)
        i += 1
    if not positional:
        return
    ref, cfg = positional[0], ctx.cfg
    integration = config._canonical_branch(cfg["integration_branch"])
    if config.is_forbidden_branch(ref, cfg) or integration in config.branch_aliases(ref):
        raise Deny(f"git update-ref {ref} no está permitido: es la rama de integración o una rama "
                   "prohibida.")


def _check_git_key(key: str) -> None:
    if key.strip().lower().startswith(_GIT_CONFIG_KEYS):
        raise Deny(f"git -c {key} no está permitido: podría desviar un push, crear alias o "
                   "ejecutar hooks ajenos.")


def _check_git_config(rest: list[str]) -> None:
    flags: list[str] = []
    positional: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a.startswith("-"):
            low = a.lower()
            flags.append(low.split("=", 1)[0])
            i += 2 if low in _GIT_CONFIG_VALUED else 1
            continue
        positional.append(a)
        i += 1
    if any(f in ("--edit", "-e") for f in flags) or (positional and positional[0] == "edit"):
        raise Deny("git config --edit no está permitido.")
    if positional and positional[0] in ("set", "unset", "rename-section", "remove-section"):
        positional = positional[1:]
        writing = True
    else:
        writing = len(positional) >= 2 or any(
            f in ("--add", "--replace-all", "--unset", "--unset-all", "--rename-section",
                  "--remove-section") for f in flags)
    if writing and positional and positional[0].lower().startswith(_GIT_CONFIG_KEYS):
        raise Deny(f"git config {positional[0]} no está permitido: podría desviar un push, crear "
                   "alias o ejecutar hooks ajenos.")


def _check_push(rest: list[str], ctx: _Ctx) -> None:
    positional: list[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--":
            positional += rest[i + 1:]
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in _PUSH_DENY:
                raise Deny(f"git push {name} no está permitido; Atta solo empuja a atta/<id> "
                           "sin forzar.")
            if name in _PUSH_VALUED:
                i += 1 if eq else 2
                continue
            if name == "--recurse-submodules":
                if value not in ("check", "no"):
                    raise Deny("git push --recurse-submodules solo con check o no.")
            elif name not in _PUSH_SAFE:
                raise Deny(f"git push {name}: opción no reconocida; Atta solo permite "
                           "git push origin atta/<id>.")
        elif a.startswith("-") and len(a) > 1:
            if a == "-o":
                i += 2
                continue
            if not set(a[1:]) <= set("uqvn46"):
                raise Deny(f"git push {a} no está permitido; Atta solo empuja a atta/<id> sin "
                           "forzar ni borrar.")
        else:
            positional.append(a)
        i += 1
    if len(positional) < 2:
        raise Deny("git push sin rama explícita no está permitido; usa "
                   "git push origin atta/<id>.")
    for spec in positional[1:]:
        _check_refspec(spec, ctx.cfg)


def _check_refspec(spec: str, cfg: dict) -> None:
    if spec.startswith("+"):
        raise Deny(f"«{spec}» fuerza el push; Atta nunca fuerza.")
    if ":" in spec:
        src, dst = spec.split(":", 1)
        if not src:
            raise Deny(f"«{spec}» borra una rama remota; los agentes de Atta no borran ramas.")
        if not dst:
            raise Deny(f"«{spec}» no tiene destino; usa HEAD:atta/<id>.")
    else:
        dst = spec
        if dst.upper() == "HEAD" or dst.startswith("@"):
            raise Deny(f"«{spec}» sin destino explícito; usa HEAD:atta/<id>.")
    try:
        branches.check_push_target(dst, cfg)
    except branches.BranchRefused as exc:
        raise Deny(str(exc)) from None


def _flag_value(args: list[str], names) -> str | None:
    for j, a in enumerate(args):
        for n in names:
            if a == n and j + 1 < len(args):
                return args[j + 1]
            if a.startswith(n + "=") and n.startswith("--"):
                return a.split("=", 1)[1]
            if not n.startswith("--") and a.startswith(n) and len(a) > len(n):
                return a[len(n):]
    return None


def _check_gh(args: list[str], ctx: _Ctx) -> None:
    positional: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-R", "--repo", "--hostname"):
            skip = True
            continue
        if a.startswith("-"):
            continue
        positional.append(a.lower())
        if len(positional) == 2:
            break
    if not positional:
        return
    group = positional[0]
    action = positional[1] if len(positional) > 1 else ""
    if group == "pr" and action == "merge":
        raise Deny("Los agentes no mergean PRs (gh pr merge); el merge lo hace `atta merge`, "
                   "solo contra la rama de integración.")
    if group == "pr" and action in ("create", "edit"):
        base = _flag_value(args, ("--base", "-B"))
        if base is None:
            if action == "create":
                raise Deny("gh pr create sin --base no está permitido; el PR va contra "
                           f"{ctx.cfg['integration_branch']} (o usa `atta publish`).")
            return
        try:
            branches.check_pr_base(base, ctx.cfg)
        except branches.BranchRefused as exc:
            raise Deny(str(exc)) from None
    if group == "repo" and action in ("delete", "archive"):
        raise Deny(f"gh repo {action} no está permitido para los agentes de Atta.")
    if group == "release":
        raise Deny("gh release no está permitido: los agentes de Atta no publican versiones.")
    if group == "alias" and action in ("set", "import"):
        raise Deny("gh alias no está permitido: podría esconder un comando bloqueado.")
    if group == "api":
        _check_gh_api(args)


def _check_gh_api(args: list[str]) -> None:
    text = " ".join(args).lower()
    for mutation in _GH_MUTATIONS:
        if mutation in text:
            raise Deny("gh api no puede mergear PRs, activar auto-merge ni crear, mover o borrar "
                       f"ramas ({mutation}).")
    method, fields, candidates = "", False, []
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else ""
        name, eq, value = a.partition("=")
        if a.startswith("--") and eq:
            if name == "--method":
                method = value
            fields = fields or name in ("--field", "--raw-field", "--input")
            i += 1
            continue
        if a in _GH_API_VALUED:
            if a in ("-X", "--method"):
                method = nxt
            fields = fields or a in ("-f", "-F", "--field", "--raw-field", "--input")
            i += 2
            continue
        if a.startswith("-") and not a.startswith("--") and len(a) > 2:
            if a[1] == "X":
                method = a[2:]
            fields = fields or a[1] in "fF"
            i += 1
            continue
        if not a.startswith("-"):
            candidates.append(a)
        i += 1
    method = (method.strip("'\"").upper() or ("POST" if fields else "GET"))
    if method == "DELETE":
        raise Deny("gh api con DELETE no está permitido para los agentes de Atta.")
    if method not in _WRITE_METHODS:
        return
    for raw in candidates:
        endpoint = re.split(r"[?#]", raw, maxsplit=1)[0].lower()
        endpoint = re.sub(r"^https?://[^/]+", "", endpoint)
        if _GH_API_WRITE_PATHS.search(endpoint):
            raise Deny(f"gh api {method} {endpoint} no está permitido: los agentes de Atta no "
                       "mergean, no tocan ramas ni su protección y no publican versiones.")


if __name__ == "__main__":
    sys.exit(main())
