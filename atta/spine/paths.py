"""Ruta relativa al proyecto (F4.x T2, S2).

Reviewers sometimes cite a finding with its absolute path (`/…/proj/.worktrees/7-x/calc.py:12`).
`project_rel(path, root)` turns such a path into the project-relative one the rest of the spine
uses (`calc.py`): it converts `\\` to `/`, strips a leading `./`, and removes the
`<root>/.worktrees/<job>/` prefix or else the `<root>/` prefix. Any other path (relative, or
absolute outside the project) comes back unchanged apart from the slashes. Drive paths
(`C:\\…`) always compare without case; POSIX paths compare without case on Windows and macOS.
Only stdlib, and the same result on every platform for the same input.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from pathlib import Path

WORKTREES_DIR = ".worktrees"  # flow.WORKTREES_DIR: the job worktree is <root>/.worktrees/<id>

_DRIVE = re.compile(r"[A-Za-z]:/")


def _slashes(path: str) -> str:
    path = str(path).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def is_absolute(path: str) -> bool:
    """True for `/x`, `//server/x` and `C:/x` (after `_slashes`), on any platform."""
    return path.startswith("/") or bool(_DRIVE.match(path))


def _absolute(path: str) -> str:
    """`path` normalized (no `..`, no trailing `/`); a relative root is made absolute here."""
    path = _slashes(path)
    if not is_absolute(path):
        path = _slashes(os.path.abspath(path))
    head = "//" if path.startswith("//") and not path.startswith("///") else ""
    out = posixpath.normpath(path)
    if head and not out.startswith("//"):
        out = "/" + out
    return out.rstrip("/") or "/"


def _roots(root) -> list[str]:
    """The root as given and, when it differs, its resolved form (macOS /var → /private/var)."""
    first = _absolute(str(root))
    out = [first]
    if not _DRIVE.match(first) or os.name == "nt":
        try:
            real = _absolute(os.path.realpath(str(root)))
        except (OSError, ValueError):
            real = first
        if real not in out:
            out.append(real)
    return out


def _strip(path: str, prefix: str, fold: bool) -> str | None:
    """The rest of `path` under directory `prefix`, or None when it is not under it."""
    base = prefix.rstrip("/") + "/"
    p, b = (path.casefold(), base.casefold()) if fold else (path, base)
    if p.startswith(b) and len(path) > len(base):
        return path[len(base):]
    return None


def project_rel(path: str, root=None, *, worktree=None, ignore_case: bool | None = None) -> str:
    """`path` relative to the project when it is an absolute path inside `root` (or inside its
    job worktree `<root>/.worktrees/<id>/`, or inside `worktree` when given); otherwise `path`
    with `/` slashes and without a leading `./` (an absolute one also without `..`)."""
    norm = _slashes(path)
    if not norm or not is_absolute(norm) or (root is None and worktree is None):
        return norm
    absolute = _absolute(norm)
    if ignore_case is None:
        ignore_case = sys.platform in ("win32", "darwin")
    fold = ignore_case or bool(_DRIVE.match(absolute))
    if worktree is not None:
        for wt in _roots(worktree):
            rest = _strip(absolute, wt, fold or bool(_DRIVE.match(wt)))
            if rest is not None:
                return rest
    if root is not None:
        for base in _roots(root):
            f = fold or bool(_DRIVE.match(base))
            rest = _strip(absolute, base, f)
            if rest is None:
                continue
            head, sep, tail = rest.partition("/")
            if sep and tail and (head.casefold() if f else head) == WORKTREES_DIR \
                    and "/" in tail:
                return tail.split("/", 1)[1]
            return rest
    return absolute


def root_of_job_dir(job_dir) -> Path | None:
    """`<root>` of a job folder `<root>/atta/jobs/<id>`, or None for any other layout."""
    job_dir = Path(job_dir)
    jobs = job_dir.parent
    if jobs.name == "jobs" and jobs.parent.name == "atta":
        return jobs.parent.parent
    return None
