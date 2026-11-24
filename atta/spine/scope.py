"""Alcance del diff (K8) y corrección acotada (G5): what a build round may touch.

El Filtro calls `assess` on a `checks` round (never on `merge-checks`) and writes the result in
checks.md under `## Alcance`. Three rules, all from data the spine already has:

- FILES (K8): the spec's `FILES:` (job folder `spec.md`; globs, comma or newline separated)
  names what the change may touch. A changed path outside it is a warning; with config
  `scope.enforce: true` it fails the gate. Test files (risk_rules `tests` globs) and lockfiles
  never count: the tests are part of every change and lockfiles follow the manifests.
- Size: more than `scope.max_files` (default 12) changed files is a warning, never a failure.
- Corrección acotada (G5): from round 2 on, the paths changed in this round (the previous
  round's checks.md `COMMIT:` .. HEAD) must be named by the previous round's findings
  (`path:line` in its review-*.md; an absolute path inside the project or its worktree counts
  as the project-relative one, S2) or by FILES; outside → «la corrección salió del alcance de
  los hallazgos» (warning, failure with `scope.enforce`). A previous round without findings
  (e.g. it failed the Filtro) does not bound the correction.
"""

from __future__ import annotations

import re
from pathlib import Path

from .paths import WORKTREES_DIR, is_absolute, project_rel

SPEC_FILE = "spec.md"
DEFAULT_MAX_FILES = 12
LOCKFILES = ("**/package-lock.json", "**/npm-shrinkwrap.json", "**/yarn.lock",
             "**/pnpm-lock.yaml", "**/bun.lockb", "**/bun.lock", "**/poetry.lock",
             "**/Pipfile.lock", "**/uv.lock", "**/pdm.lock", "**/Cargo.lock", "**/Gemfile.lock",
             "**/composer.lock", "**/go.sum", "**/mix.lock", "**/packages.lock.json")
OUT_OF_FILES = "el diff sale del alcance de FILES:"
OUT_OF_FINDINGS = "la corrección salió del alcance de los hallazgos"
MAX_LISTED = 10

_FILES_LINE = re.compile(r"^\s*(?:[-*]\s*)?\**FILES:\**\s*(.*)$")
_ROUND = re.compile(r"round-([1-9][0-9]*)\Z")
# `path:line` as the reviewers cite it (`src/orders/import.py:42`, `` `a/b.py:7` ``), also
# absolute (`/…/proj/.worktrees/7-x/calc.py:12`, `C:\…\calc.py:12`).
_FINDING = re.compile(r"(?<![\w./\\-])((?:[A-Za-z]:[/\\]|[/\\])?(?:[\w.-]+[/\\])*[\w-][\w.-]*)"
                      r":[0-9]+")


def _clean(entry: str) -> str:
    return entry.strip().strip("`'\"").strip()


def parse_files(text: str) -> list[str] | None:
    """The entries of the spec's `FILES:` (None when the spec has no `FILES:` line).

    The value is comma separated and may continue on the following lines (one entry or a
    `- entry` list item per line) until a blank line, a heading or another `KEY:` line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _FILES_LINE.match(line)
        if not m:
            continue
        chunks = [m.group(1)]
        for nxt in lines[i + 1:]:
            stripped = nxt.strip()
            if not stripped or stripped.startswith("#") or re.match(r"[A-Z][A-Z_ ]*:", stripped):
                break
            chunks.append(re.sub(r"^[-*]\s+", "", stripped))
        out = []
        for chunk in chunks:
            out += [e for e in (_clean(part) for part in chunk.split(",")) if e]
        return out
    return None


def _globs(entries) -> list[str]:
    # `src/orders/` (a folder) covers everything under it.
    return [e + "**" if e.endswith(("/", "\\")) else e for e in entries]


def matches(path: str, entries) -> bool:
    """True when `path` is named by an entry of FILES (exact path, glob or `folder/`)."""
    from .gate import matches_protected
    globs = _globs(entries)
    return matches_protected(path, globs) or matches_protected(path, [g + "/**" for g in globs
                                                                     if "*" not in g])


def exempt(path: str, tests) -> bool:
    """Test files and lockfiles never count against the scope."""
    from .gate import matches_protected
    return matches_protected(path, list(tests)) or matches_protected(path, LOCKFILES)


def finding_paths(texts, root=None, worktree=None) -> list[str]:
    """Paths cited as `path:line` in review texts, in first-seen order, without duplicates.

    An absolute path inside `root`, its job worktree `<root>/.worktrees/<id>/` or `worktree`
    becomes project-relative (S2); any other absolute path is left out."""
    out: list[str] = []
    for text in texts:
        for m in _FINDING.finditer(text):
            path = project_rel(m.group(1), root, worktree=worktree)
            if is_absolute(path):
                continue
            if path and path not in out:
                out.append(path)
    return out


def round_number(round_dir) -> int | None:
    m = _ROUND.match(Path(round_dir).name)
    return int(m.group(1)) if m else None


def previous_commit(prev_dir) -> str | None:
    """The `COMMIT:` sha of the previous round's checks.md, or None."""
    try:
        lines = (Path(prev_dir) / "checks.md").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines[:3]:
        m = re.fullmatch(r"COMMIT:\s*([0-9a-f]{40}|[0-9a-f]{64})\s*", line)
        if m:
            return m.group(1)
    return None


def settings(cfg: dict) -> tuple[bool, int]:
    """(scope.enforce, scope.max_files) with their defaults."""
    section = (cfg or {}).get("scope") or {}
    enforce = section.get("enforce", False) is True
    max_files = section.get("max_files", DEFAULT_MAX_FILES)
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1:
        max_files = DEFAULT_MAX_FILES
    return enforce, max_files


def _listed(paths) -> str:
    items = [f"`{p}`" for p in paths[:MAX_LISTED]]
    more = len(paths) - MAX_LISTED
    return ", ".join(items) + (f" y {more} más" if more > 0 else "")


def _live_paths(entries) -> list[str]:
    """Paths a diff leaves changed: the new path (the old one too for a rename)."""
    out: list[str] = []
    for letter, paths in entries:
        for p in (paths if letter == "R" else paths[-1:]):
            if p not in out:
                out.append(p)
    return out


def assess(worktree, commit: str, round_dir, cfg: dict, entries, tests) -> dict:
    """{lines, fail, warnings} of the scope rules for a `checks` round.

    `entries` are the (status, paths) of the diff `<base>...HEAD`; `tests` the risk_rules
    `tests` globs. `fail` is the gate summary when `scope.enforce` turns a warning into a
    failure; `warnings` names what warned («alcance», «tamaño», «corrección»)."""
    from . import gate
    enforce, max_files = settings(cfg)
    round_dir = Path(round_dir)
    lines = ["## Alcance", ""]
    warnings: list[str] = []
    fail = None
    changed = _live_paths(entries)

    spec = round_dir.parent / SPEC_FILE
    files = None
    if spec.is_file():
        try:
            files = parse_files(spec.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            files = None
    if files is None:
        lines.append("spec.md no tiene `FILES:`: no se comparó el alcance del diff.")
    elif not files:
        lines.append("`FILES:` de spec.md está vacío: no se comparó el alcance del diff.")
    else:
        lines.append(f"FILES: {', '.join(f'`{f}`' for f in files)}")
        outside = [p for p in changed if not exempt(p, tests) and not matches(p, files)]
        if outside:
            warnings.append("alcance")
            mark = "✗" if enforce else "⚠"
            lines.append(f"{mark} El diff toca archivos fuera de `FILES:`: {_listed(outside)}.")
            if enforce:
                fail = f"El Filtro no pasó: {OUT_OF_FILES} ({_listed(outside)})."
        else:
            lines.append("El diff queda dentro de `FILES:` (sin contar tests ni lockfiles).")

    if len(changed) > max_files:
        warnings.append("tamaño")
        lines.append(f"⚠ El diff cambia {len(changed)} archivos (más de {max_files}, "
                     "scope.max_files): conviene partirlo en cambios más chicos.")
    else:
        lines.append(f"Tamaño: {len(changed)} de {max_files} archivos como máximo.")

    n = round_number(round_dir)
    if n is not None and n >= 2:
        prev_dir = round_dir.parent / f"round-{n - 1}"
        texts = []
        for review in sorted(prev_dir.glob("review-*.md")):
            try:
                texts.append(review.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
        wt = Path(worktree)
        root = wt.parent.parent if wt.parent.name == WORKTREES_DIR else None
        cited = finding_paths(texts, root=root, worktree=wt)
        prev = previous_commit(prev_dir)
        label = f"Corrección acotada (ronda {n} contra los hallazgos de la ronda {n - 1})"
        if not cited:
            lines.append(f"{label}: la ronda anterior no tiene hallazgos con `path:line`; "
                         "no se acota la corrección.")
        elif prev is None or not _is_ancestor(worktree, prev, commit):
            lines.append(f"{label}: no encontré el commit de la ronda anterior en esta rama; "
                         "no se acota la corrección.")
        else:
            span = gate.changed_entries(worktree, f"{prev}..{commit}")
            allowed = cited + list(files or [])
            outside = [p for p in _live_paths(span)
                       if not exempt(p, tests) and not matches(p, allowed)]
            if outside:
                warnings.append("corrección")
                mark = "✗" if enforce else "⚠"
                lines.append(f"{mark} {label}: {OUT_OF_FINDINGS}: {_listed(outside)}.")
                if enforce and fail is None:
                    fail = f"El Filtro no pasó: {OUT_OF_FINDINGS} ({_listed(outside)})."
            else:
                lines.append(f"{label}: la corrección queda dentro de los hallazgos.")
    return {"lines": lines, "fail": fail, "warnings": warnings}


def _is_ancestor(worktree, ancestor: str, head: str) -> bool:
    from . import gate
    proc = gate._git_run(Path(worktree), "merge-base", "--is-ancestor", "--end-of-options",
                         ancestor, head)
    return proc.returncode == 0
