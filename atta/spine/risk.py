"""Riesgo por reglas (G3, F3 T2): the diff `<base>...HEAD` sets a floor for the job's risk.

The Exploradora judges the risk before any code exists; El Filtro, when a build round passes
(`checks` with PASS), classifies the real diff into the high-risk categories and the job's
effective risk becomes `max(intake RISK, rules risk, previous effective risk)`: it never goes
down. Categories (labels in Spanish in every message):

    data         datos irreversibles: migrations, schema, *.sql, code that drops/alters/deletes
    security     seguridad: auth, permissions, crypto, secrets, *.pem, guard-like code
    contracts    contratos consumidos: public functions/exports or CLI flags removed or renamed,
                 public API route files
    concurrency  threads, locks, async primitives, queues, cron/schedulers
    delivery     entrega/CI: workflows, Dockerfile, deploy/infra, dependency manifests, lockfiles
    untested     source files changed with no test file changed in the diff; a file whose
                 change only touches blank lines or comments (known languages, see
                 COMMENT_STYLES) or, in .py, only docstrings/comments (same AST) is not
                 counted. An unknown extension or an unparseable .py keeps counting.

The rules are data: config key `risk_rules` (DEFAULT_RULES when absent; a project's table
replaces the whole default) with this shape::

    {"source": [glob, ...],     # code files: content rules and `untested` look only at these
     "tests":  [glob, ...],     # test files: never scanned by content rules; one changed
                                #   test file satisfies `untested`
     "categories": {<category>: {"risk": "low|medium|high",
                                 "paths":   [glob, ...],    # any changed path (old or new)
                                 "added":   [regex, ...],   # added lines of source files
                                 "removed": [regex, ...]}}} # removed lines of source files

Globs follow the Filtro's protected paths (`**`, case-insensitive). A `removed` regex with a
`(?P<name>...)` group only counts when no added line of the diff matches it with the same name
(a removed or renamed symbol; a changed signature keeps its name). `untested` takes only `risk`.

When the effective risk is above the risk the job was routed with, the router plans the review
again with the effective risk and the same surfaces (security joins on high), job.json records
`risk_rules`, `risk_effective` and `risk_escalation`, events.jsonl gets a `risk` event (not a
transition: it has no `from`/`to`), and the Filtro's summary and checks.md say so:
«El Filtro subió el riesgo a alto: migrations/0003.sql (datos).»
"""

from __future__ import annotations

import ast
import copy
import functools
import re
import warnings
from pathlib import Path

RISKS = ("low", "medium", "high")
RISK_ES = {"low": "bajo", "medium": "medio", "high": "alto"}
CATEGORIES = ("data", "security", "contracts", "concurrency", "delivery", "untested")
CATEGORY_ES = {"data": "datos", "security": "seguridad", "contracts": "contratos",
               "concurrency": "concurrencia", "delivery": "entrega", "untested": "sin test"}
CATEGORY_KEYS = ("risk", "paths", "added", "removed")
MESSAGE_ITEMS = 3

_DIR = "/**"
DEFAULT_RULES = {
    "source": ["**/*.py", "**/*.js", "**/*.jsx", "**/*.ts", "**/*.tsx", "**/*.mjs",
               "**/*.cjs", "**/*.go", "**/*.rs", "**/*.java", "**/*.kt", "**/*.rb",
               "**/*.php", "**/*.cs", "**/*.c", "**/*.h", "**/*.cpp", "**/*.hpp",
               "**/*.swift", "**/*.scala", "**/*.sh", "**/*.ps1"],
    "tests": ["**/test_*.py", "**/*_test.py", "**/*_test.go", "**/tests/**", "**/test/**",
              "**/__tests__/**", "**/spec/**", "**/*.test.*", "**/*.spec.*",
              "**/*Test.java", "**/*Tests.cs"],
    "categories": {
        "data": {
            "risk": "high",
            "paths": ["**/migrations/**", "**/migration/**", "**/alembic/**", "**/*.sql",
                      "**/schema.*", "**/schema/**", "**/*.prisma"],
            "added": [r"(?i)\b(?:drop\s+(?:table|column|index|database|schema)|alter\s+table"
                      r"|truncate\s+table|delete\s+from)\b"],
        },
        "security": {
            "risk": "high",
            "paths": ["**/auth/**", "**/auth.*", "**/*_auth.*", "**/auth_*", "**/*oauth*",
                      "**/*security*", "**/*security*" + _DIR, "**/*permission*",
                      "**/*permission*" + _DIR, "**/*crypto*", "**/*crypto*" + _DIR,
                      "**/*secret*", "**/*secret*" + _DIR, "**/*guard*", "**/*.pem",
                      "**/*.key", "**/*.crt"],
            "added": [r"(?i)\b(?:password|passwd|api[_-]?key|private[_-]?key|secret[_-]?key"
                      r"|access[_-]?token|jwt|bcrypt|hmac|hashlib|csrf|authenticate"
                      r"|authorize|is_admin|has_permission|login_required"
                      r"|permission_required)\b",
                      r"\bshell\s*=\s*True\b", r"\beval\("],
        },
        "contracts": {
            "risk": "high",
            "paths": ["**/api/**", "**/routes/**", "**/*routes.*", "**/openapi*",
                      "**/swagger*", "**/*.proto", "**/*.graphql", "**/*.schema.json"],
            "removed": [
                r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)\s*\(",
                r"^\s*class\s+(?P<name>[A-Za-z][A-Za-z0-9_]*)",
                r"^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|var"
                r"|interface|type|enum)\s+(?P<name>[A-Za-z$][\w$]*)",
                r"^\s*pub\s+(?:async\s+)?fn\s+(?P<name>[A-Za-z]\w*)",
                r"^func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Z]\w*)",
                r"add_argument\(\s*[\"'](?P<name>--?[\w-]+)",
            ],
        },
        "concurrency": {
            "risk": "high",
            "paths": ["**/*cron*", "**/*schedul*", "**/celery*"],
            "added": [r"\b(?:threading|multiprocessing|asyncio|concurrent\.futures|Thread\("
                      r"|Lock\(|RLock\(|Semaphore\(|Condition\(|Queue\(|async\s+def"
                      r"|await\s|Mutex|RwLock|sync\.WaitGroup|go\s+func|setInterval\("
                      r"|crontab|apscheduler|celery)"],
        },
        "delivery": {
            "risk": "medium",
            "paths": [".github/workflows/**", ".gitlab-ci.yml", ".circleci/**",
                      "azure-pipelines.yml", "**/Jenkinsfile", "**/Dockerfile*",
                      "**/docker-compose*", "**/*.tf", "deploy/**", "infra/**", "k8s/**",
                      "**/helm/**", "**/Procfile", "**/package.json", "**/package-lock.json",
                      "**/yarn.lock", "**/pnpm-lock.yaml", "**/requirements*.txt",
                      "**/pyproject.toml", "**/poetry.lock", "**/Pipfile", "**/Pipfile.lock",
                      "**/setup.py", "**/setup.cfg", "**/go.mod", "**/go.sum",
                      "**/Cargo.toml", "**/Cargo.lock", "**/Gemfile", "**/Gemfile.lock",
                      "**/composer.json", "**/composer.lock"],
        },
        "untested": {"risk": "medium"},
    },
}


# ---------------------------------------------------------------- rules

def rank(level) -> int:
    """0/1/2 for low/medium/high; -1 for anything else (an unmeasured risk)."""
    return RISKS.index(level) if level in RISKS else -1


def highest(*levels) -> str | None:
    known = [lv for lv in levels if lv in RISKS]
    return max(known, key=rank) if known else None


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


@functools.lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def rules_error(rules) -> str | None:
    """The first problem of a `risk_rules` table (Spanish, without the `config.json:` prefix)."""
    if not isinstance(rules, dict):
        return "risk_rules debe ser un objeto con source, tests y categories."
    for key in ("source", "tests"):
        if not _is_str_list(rules.get(key)):
            return f"risk_rules.{key} debe ser una lista de textos."
    categories = rules.get("categories")
    if not isinstance(categories, dict):
        return "risk_rules.categories debe ser un objeto."
    known = ", ".join(CATEGORIES[:-1]) + " o " + CATEGORIES[-1]
    for name, spec in categories.items():
        where = f"risk_rules.categories.{name}"
        if name not in CATEGORIES:
            return f"{where}: categoría desconocida; usa {known}."
        if not isinstance(spec, dict):
            return f"{where} debe ser un objeto."
        if spec.get("risk") not in RISKS:
            return f"{where}.risk «{spec.get('risk')}» no es válido; usa low, medium o high."
        allowed = ("risk",) if name == "untested" else CATEGORY_KEYS
        for key in spec:
            if key not in allowed:
                usa = "risk" if name == "untested" else "risk, paths, added o removed"
                return f"{where}: clave desconocida «{key}»; usa {usa}."
        for key in ("paths", "added", "removed"):
            if key in spec and not _is_str_list(spec[key]):
                return f"{where}.{key} debe ser una lista de textos."
        for key in ("added", "removed"):
            for pattern in spec.get(key, []):
                try:
                    _regex(pattern)
                except re.error:
                    return f"{where}.{key}: la expresión «{pattern}» no es válida."
    return None


def rules_of(cfg: dict) -> dict:
    """The project's `risk_rules`, or DEFAULT_RULES when the key is absent."""
    rules = (cfg or {}).get("risk_rules")
    return DEFAULT_RULES if rules is None else rules


# ---------------------------------------------------------------- the diff

def _parse_name_status(raw: bytes) -> list[tuple[str, tuple[str, ...]]]:
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    out, i = [], 0
    while i < len(fields) and fields[i]:
        letter = fields[i][0]
        npaths = 2 if letter in "RC" else 1
        out.append((letter, tuple(fields[i + 1:i + 1 + npaths])))
        i += 1 + npaths
    return out


def _patch_path(line: str, prefix: str) -> str | None:
    rest = line[len(prefix):].rstrip("\r")
    if rest == "/dev/null":
        return None
    if rest.startswith('"') and rest.endswith('"'):
        rest = rest[1:-1]
    return rest[2:] if rest[:2] in ("a/", "b/") else rest


def _parse_patch(raw: bytes) -> dict[str, dict[str, list[str]]]:
    """{path: {"added": [...], "removed": [...]}} from a `-U0` patch (new path; old if deleted)."""
    out: dict[str, dict[str, list[str]]] = {}
    current, old, in_hunk = None, None, False
    for line in raw.decode("utf-8", "replace").split("\n"):
        line = line.rstrip("\r")
        if line.startswith("diff --git "):
            current, old, in_hunk = None, None, False
        elif not in_hunk and line.startswith("--- "):
            old = _patch_path(line, "--- ")
        elif not in_hunk and line.startswith("+++ "):
            current = _patch_path(line, "+++ ") or old
            out.setdefault(current or "", {"added": [], "removed": []})
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and current is not None:
            if line.startswith("+"):
                out[current]["added"].append(line[1:])
            elif line.startswith("-"):
                out[current]["removed"].append(line[1:])
    return out


def changes(worktree, base: str, head: str = "HEAD") -> list[dict]:
    """[{status, paths, added, removed, cosmetic}] of the diff `<base>...<head>` (renames
    detected). `cosmetic` is True only for a modified file whose change touches nothing but
    blank lines, comments or (in .py) docstrings; see cosmetic()."""
    from . import gate  # lazy: gate imports this module
    span = f"{base}...{head}"
    entries = _parse_name_status(gate._git(
        Path(worktree), "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
        "--name-status", "-z", "-M", "--end-of-options", span, "--"))
    patch = _parse_patch(gate._git(
        Path(worktree), "-c", "core.quotepath=off", "diff", "--no-ext-diff", "--no-textconv",
        "--no-color", "--ignore-submodules=none", "-U0", "-M", "--end-of-options", span, "--"))
    out = []
    for letter, paths in entries:
        lines = patch.get(paths[-1]) or patch.get(paths[0]) or {"added": [], "removed": []}
        out.append({"status": letter, "paths": list(paths), "added": list(lines["added"]),
                    "removed": list(lines["removed"]), "cosmetic": False})
    candidates = [ch for ch in out if ch["status"] == "M" and _style(ch["paths"][-1])]
    if candidates:
        blobs = _parse_raw(gate._git(
            Path(worktree), "diff", "--no-ext-diff", "--no-textconv",
            "--ignore-submodules=none", "--raw", "-z", "--no-abbrev", "-M",
            "--end-of-options", span, "--"))
        for ch in candidates:
            ch["cosmetic"] = _cosmetic_blobs(Path(worktree), ch["paths"][-1],
                                             blobs.get(ch["paths"][-1]))
    return out


def _parse_raw(raw: bytes) -> dict[str, tuple[str, str, str, str]]:
    """{new path: (old mode, new mode, old blob, new blob)} from `diff --raw -z --no-abbrev`."""
    fields = raw.decode("utf-8", "surrogateescape").split("\0")
    out, i = {}, 0
    while i < len(fields) and fields[i].startswith(":"):
        meta = fields[i][1:].split()
        if len(meta) < 5 or not meta[4]:
            break
        npaths = 2 if meta[4][0] in "RC" else 1
        paths = fields[i + 1:i + 1 + npaths]
        if paths:
            out[paths[-1]] = (meta[0], meta[1], meta[2], meta[3])
        i += 1 + npaths
    return out


def _cosmetic_blobs(worktree: Path, path: str, entry) -> bool:
    from . import gate  # lazy: gate imports this module
    if entry is None:
        return False
    old_mode, new_mode, old_sha, new_sha = entry
    if old_mode != new_mode or old_mode not in ("100644", "100755"):
        return False  # a mode change, symlink or submodule is never cosmetic
    try:
        old = gate._git(worktree, "cat-file", "blob", old_sha)
        new = gate._git(worktree, "cat-file", "blob", new_sha)
    except (gate.GateError, OSError):
        return False
    return cosmetic(path, old, new)


# ---------------------------------------------------------------- cosmetic changes (S1)

# Comment syntax per extension. A false negative lowers the review depth (G3) and a false
# positive only asks for a test, so every doubt counts as code:
#   line    prefixes that start a comment line;  block  /* ... */ comments allowed
#   code    comment lines that still mean something (shebangs, build tags, cgo, magic
#           comments, pragmas, docblock annotations): they count as code
#   reject  regexes searched in the whole old and new text: a match (a multi-line string
#           opener, a heredoc, JSX, cgo, text outside <?php...) makes the file not cosmetic
#   bare    for languages whose plain quotes may span lines: a code line holding any of
#           these characters makes the file not cosmetic (no string can open, so no
#           comment-looking line can be string content)
# Any line ending in `\` (a continuation) and a /* that never closes are never cosmetic.
_SHEBANG = r"#!"
_JS_CODE = (_SHEBANG, r"///\s*<", r"@ts-", r"@jsx", r"@flow", r"__PURE__", r"webpack",
            r"sourceMappingURL")
_SLASH = {"line": ("//",), "block": True, "code": (_SHEBANG,), "reject": (), "bare": ""}
_HASH = {"line": ("#",), "block": False, "code": (_SHEBANG,), "reject": (),
         "bare": "\"'`"}
_TRIPLE = r'"""'
COMMENT_STYLES = {
    **{ext: {**_SLASH, "reject": (r'R"',)}  # C++ raw strings R"(...)"
       for ext in ("c", "h", "cc", "cpp", "cxx", "hh", "hpp", "hxx")},
    **{ext: {**_SLASH, "reject": (_TRIPLE,)} for ext in ("java", "kt", "kts")},
    "scala": {**_SLASH, "reject": (_TRIPLE, "</", "/>")},  # Scala 2 XML literals
    "swift": {**_SLASH, "reject": (_TRIPLE, r'#+"')},
    "cs": {**_SLASH, "reject": (_TRIPLE, r'@\$?"', r'\$@"')},
    "rs": {**_SLASH, "bare": '"'},  # plain Rust strings span lines; r"..." holds a quote
    **{ext: {**_SLASH, "code": _JS_CODE, "reject": ("`", "</", "/>")}  # template, JSX
       for ext in ("js", "jsx", "ts", "tsx", "mjs", "cjs")},
    "php": {**_SLASH, "code": (_SHEBANG, r"@"),  # `#` is an attribute; @ are annotations
            "reject": (r"\A(?!<\?php)", r"\?>", "<<"), "bare": "\"'`"},
    "go": {**_SLASH, "block": False,  # a /* */ before `import "C"` is cgo code
           "code": (_SHEBANG, r"^//go:", r"^//\s*\+build", r"^//line ", r"^//export ",
                    r"^//\s*#"),
           "reject": ("`", r'"C"')},  # raw strings; any cgo file
    **{ext: {**_HASH, "reject": ("<<",)} for ext in ("sh", "bash", "zsh")},
    **{ext: {**_HASH, "bare": "\"'`|>"} for ext in ("yaml", "yml")},  # block scalars
    "toml": {**_HASH, "reject": (_TRIPLE, "'''"), "bare": ""},
    "rb": {**_HASH, "code": (_SHEBANG, r"#\{",
                             r"(?i)^#.*\b(?:frozen_string_literal|encoding|coding"
                             r"|warn_indent|shareable_constant_value)\s*:"),
           "reject": ("<<", r"(?m)^__END__\s*$"), "bare": "\"'`%/"},  # %q(), /regex/
    "ps1": {**_HASH, "code": (_SHEBANG, r"(?i)^#\s*requires\b")},  # here-strings: quotes
}


def _style(path: str):
    """"py", a COMMENT_STYLES entry, or None for an unknown extension (Windows-safe)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name.lstrip(".") else ""
    return "py" if ext == "py" else COMMENT_STYLES.get(ext)


def cosmetic(path: str, old: bytes, new: bytes) -> bool:
    """True when `old` → `new` only touches blank lines or comments (or, for .py, the code
    keeps the same AST once docstrings are stripped). False for an unknown extension, an
    unparseable .py, or a changed shebang. Line endings count: CRLF ↔ LF is a change, except
    in .py where the AST decides."""
    style = _style(path)
    if style is None or old == new:
        return False
    if _first_line(old).startswith(b"#!") or _first_line(new).startswith(b"#!"):
        if _first_line(old) != _first_line(new):
            return False
    if style == "py":
        before, after = _py_skeleton(old), _py_skeleton(new)
        return before is not None and before == after
    before = _code_lines(old.decode("utf-8", "surrogateescape"), style)
    after = _code_lines(new.decode("utf-8", "surrogateescape"), style)
    return before is not None and before == after


def _first_line(blob: bytes) -> bytes:
    return blob.split(b"\n", 1)[0]


def _py_skeleton(blob: bytes) -> str | None:
    """ast.dump of a Python source without docstrings; None when it does not parse."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(blob)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:]
        return ast.dump(tree)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def _code_lines(text: str, style: dict) -> list[str] | None:
    """The lines of `text` that are not blank nor comment-only, verbatim (indentation and
    line endings kept). A line with code next to a comment is code. None when a comment-
    looking line could be code or string content (see COMMENT_STYLES)."""
    if any(_regex(p).search(text) for p in style["reject"]):
        return None
    code = [_regex(p) for p in style["code"]]
    out, in_block = [], False
    for line in text.split("\n"):
        if line.rstrip().endswith("\\"):
            return None
        s = line.strip()
        meaningful = any(rx.search(s) for rx in code)
        if in_block:
            end = s.find("*/")
            if end >= 0:
                in_block = False
            if meaningful or (end >= 0 and s[end + 2:].strip()):
                out.append(line)
            continue
        if not s:
            continue
        if meaningful:
            out.append(line)
            if style["block"] and s.startswith("/*") and s.find("*/", 2) < 0:
                in_block = True
            continue
        if s.startswith(style["line"]):
            continue
        if style["block"] and s.startswith("/*"):
            end = s.find("*/", 2)
            if end < 0:
                in_block = True
            elif s[end + 2:].strip():
                out.append(line)
            continue
        out.append(line)
    if in_block:
        return None
    if style["bare"] and any(c in line for line in out for c in style["bare"]):
        return None
    return out


# ---------------------------------------------------------------- classification

def _match(path: str, globs) -> str | None:
    from .gate import matches_protected
    return next((g for g in globs if matches_protected(path, [g])), None)


def _snippet(text: str, limit: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def assess(diff: list[dict], rules: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """({risk, categories, reasons}, hits) for the changes of a diff; hits are
    (path, category, why). Deterministic: categories in CATEGORIES order, paths in diff order."""
    source, tests = rules.get("source") or [], rules.get("tests") or []
    cats = rules.get("categories") or {}
    hits: list[tuple[str, str, str]] = []

    def scanned(path: str) -> bool:
        return _match(path, source) is not None and _match(path, tests) is None

    added_all = [line for ch in diff for line in ch["added"]]
    for cat in CATEGORIES:
        spec = cats.get(cat)
        if spec is None or cat == "untested":
            continue
        for ch in diff:
            path = ch["paths"][-1]
            why = None
            for p in ch["paths"]:
                glob = _match(p, spec.get("paths") or [])
                if glob:
                    path, why = p, f"la ruta coincide con «{glob}»"
                    break
            if why is None and scanned(path):
                why = _content(ch, spec, added_all)
            if why:
                hits.append((path, cat, why))
    if "untested" in cats:
        live = [ch["paths"][-1] for ch in diff if ch["status"] != "D"]
        if not any(_match(p, tests) for p in live):
            hits += [(ch["paths"][-1], "untested",
                      "cambia código y el diff no cambia ningún test")
                     for ch in diff if ch["status"] != "D" and scanned(ch["paths"][-1])
                     and not ch.get("cosmetic")]
    found = sorted({c for _, c, _ in hits}, key=CATEGORIES.index)
    level = highest(*(cats[c]["risk"] for c in found)) or "low"
    reasons = [f"{p}: {CATEGORY_ES[c]} — {why}" for p, c, why in hits]
    return {"risk": level, "categories": found, "reasons": reasons}, hits


def _content(ch: dict, spec: dict, added_all: list[str]) -> str | None:
    for pattern in spec.get("added") or []:
        rx = _regex(pattern)
        for line in ch["added"]:
            if rx.search(line):
                return f"agrega «{_snippet(line)}»"
    for pattern in spec.get("removed") or []:
        rx = _regex(pattern)
        named = "name" in rx.groupindex
        kept = {m.group("name") for line in added_all if (m := rx.search(line))} if named \
            else set()
        for line in ch["removed"]:
            m = rx.search(line)
            if m and not named:
                return f"quita «{_snippet(line)}»"
            if m and m.group("name") not in kept:
                return f"quita o renombra «{m.group('name')}»"
    return None


def classify(worktree, base: str, rules: dict, head: str = "HEAD") -> dict:
    """{risk, categories, reasons} of the diff `<base>...<head>` in `worktree`."""
    return assess(changes(worktree, base, head), rules)[0]


# ---------------------------------------------------------------- effective risk

def escalation_message(level: str, hits, rules: dict) -> str:
    """«El Filtro subió el riesgo a alto: migrations/0003.sql (datos).»"""
    cats = (rules or {}).get("categories") or {}
    cause = [h for h in hits if (cats.get(h[1]) or {}).get("risk") == level] or list(hits)
    items = [f"{p} ({CATEGORY_ES[c]})" for p, c, _ in cause[:MESSAGE_ITEMS]]
    more = len(cause) - MESSAGE_ITEMS
    text = ", ".join(items) + (f" y {more} más" if more > 0 else "")
    return f"El Filtro subió el riesgo a {RISK_ES[level]}: {text}." if text else \
        f"El Filtro subió el riesgo a {RISK_ES[level]}."


def evaluate(job: dict, found: dict, hits, cfg: dict) -> dict:
    """Pure: {effective, base, escalated, agents, message} for `job` after the diff `found`.

    effective = max(intake RISK, rules risk, previous risk_effective). `base` is the risk the
    job was routed with (else the intake's); when effective is above it the review is planned
    again with the effective risk and the job's surfaces (`agents`, else None)."""
    from . import router  # lazy: keeps config → risk free of cycles
    intake = job.get("intake") or {}
    agents = job.get("agents") or {}
    effective = highest(intake.get("risk"), found["risk"], job.get("risk_effective"))
    base = highest(agents.get("risk")) or highest(intake.get("risk"))
    escalated = base is not None and rank(effective) > rank(base)
    plan = None
    if escalated and agents:
        surfaces = agents.get("surfaces")
        if surfaces is None:
            surfaces = intake.get("surfaces") or []
        plan = router.plan(effective, surfaces, cfg)
    message = escalation_message(effective, hits, rules_of(cfg)) if escalated else None
    return {"effective": effective, "base": base, "escalated": escalated, "agents": plan,
            "message": message}


def report_lines(job: dict, found: dict, outcome: dict) -> list[str]:
    """The `## Riesgo` section of checks.md (Spanish)."""
    intake = (job.get("intake") or {}).get("risk")
    cats = ", ".join(CATEGORY_ES[c] for c in found["categories"])
    rules_line = (f"Riesgo por reglas: {RISK_ES[found['risk']]} ({cats})." if cats else
                  f"Riesgo por reglas: {RISK_ES[found['risk']]} (ninguna categoría de alto "
                  "riesgo).")
    effective = outcome["effective"]
    lines = ["## Riesgo", "",
             f"Riesgo de la Exploradora: {RISK_ES.get(intake, 'sin medir')}.", rules_line,
             f"Riesgo efectivo: {RISK_ES.get(effective, 'sin medir')}."]
    if outcome["message"]:
        lines.append(outcome["message"])
    if found["reasons"]:
        lines.append("")
        lines += [f"- {r}" for r in found["reasons"]]
    return lines


def record(job_dir, jid: str, found: dict, outcome: dict) -> dict | None:
    """Store the assessment in job.json under the job lock and, on escalation, the `risk`
    event. Returns the saved job, or None when `job_dir` holds no job.json."""
    from .events import append_event
    from .lock import job_lock
    from .store import JobStore
    from .timeutil import to_iso, utcnow
    job_dir = Path(job_dir)
    if not (job_dir / "job.json").is_file():
        return None
    jobs = JobStore(job_dir.parent)
    with job_lock(job_dir):
        job = jobs.load(jid)
        job["risk_rules"] = copy.deepcopy(found)
        if outcome["effective"] is not None:
            job["risk_effective"] = highest(outcome["effective"], job.get("risk_effective"))
        if outcome["escalated"]:
            if outcome["agents"] is not None:
                job["agents"] = copy.deepcopy(outcome["agents"])
            job["risk_escalation"] = outcome["message"]
            append_event(job_dir, {"ts": to_iso(utcnow()), "job": jid, "event": "risk",
                                   "stage": job.get("stage"), "risk_from": outcome["base"],
                                   "risk_to": outcome["effective"],
                                   "categories": list(found["categories"]),
                                   "reasons": list(found["reasons"]),
                                   "message": outcome["message"]})
        jobs.save(job)
        return job
