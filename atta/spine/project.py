"""Where Atta works: the project in the current directory, and the packaged assets.

The project root is, in order: `--project <dir>`, the `ATTA_PROJECT` environment variable,
`git rev-parse --show-toplevel` from the current directory, or the current directory itself.
Inside a linked worktree (a job's `.worktrees/<id>`) the git step resolves to the main checkout
(the parent of `git rev-parse --git-common-dir`), where `atta/jobs/` lives; `--project` and
`ATTA_PROJECT` are always used as given.
Per-project files live under `<root>/atta/`: `config.json` (missing: built-in defaults),
`checks.json` (the Filtro reads it from the main checkout, i.e. the project root) and `jobs/`.

Packaged assets (board static files, `atta init` templates) are always resolved relative to the
installed package, never to the project.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from . import config
from .gate import CHECKS_RELPATH

ENV_VAR = "ATTA_PROJECT"
GIT_TIMEOUT_S = 10

PACKAGE_DIR = Path(__file__).resolve().parents[1]
BOARD_DIR = PACKAGE_DIR / "board"
TEMPLATES_DIR = PACKAGE_DIR / "templates"
CHECKS_TEMPLATE = PACKAGE_DIR / "checks.json"

PROJECT_SUBDIR = Path("atta")


class ProjectError(ValueError):
    """The requested project directory does not exist."""


def git_toplevel(start: str | Path) -> Path | None:
    """Top-level directory of the git work tree containing `start`, or None (no git, no repo)."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(start),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    if proc.returncode != 0 or not out:
        return None
    return Path(out).resolve()


def main_checkout(start: str | Path) -> Path | None:
    """Main checkout of the linked worktree containing `start`; None when `start` is not inside a
    linked worktree (a normal checkout, no git, no repo)."""
    try:
        proc = subprocess.run(["git", "rev-parse", "--git-dir", "--git-common-dir"],
                              cwd=str(start), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or len(lines) != 2:
        return None
    # Both may be relative to `start`.
    git_dir, common = ((Path(start) / line.strip()).resolve() for line in lines)
    if os.path.normcase(str(git_dir)) == os.path.normcase(str(common)):
        return None
    return common.parent


def find_root(explicit: str | Path | None = None, *, cwd: str | Path | None = None,
              environ: Mapping[str, str] | None = None) -> Path:
    """Project root: explicit dir, then $ATTA_PROJECT, then the git top level (the main checkout
    when inside a linked worktree), then cwd."""
    environ = os.environ if environ is None else environ
    base = Path(cwd) if cwd is not None else Path.cwd()
    chosen = str(explicit) if explicit else (environ.get(ENV_VAR) or "").strip()
    if chosen:
        path = (base / Path(chosen).expanduser()).resolve()
        if not path.is_dir():
            raise ProjectError(f"La carpeta del proyecto no existe: {chosen}.")
        return path
    top = git_toplevel(base)
    if top is None:
        return base.resolve()
    return main_checkout(top) or top


def config_path(root: str | Path) -> Path:
    return Path(root) / PROJECT_SUBDIR / "config.json"


def checks_path(root: str | Path) -> Path:
    return Path(root) / CHECKS_RELPATH


def jobs_dir(root: str | Path) -> Path:
    return Path(root) / PROJECT_SUBDIR / "jobs"


def load_config(root: str | Path) -> dict:
    """The project's validated atta/config.json, or the built-in defaults when it is missing."""
    path = config_path(root)
    if not path.exists():
        return config.with_defaults({})
    return config.load_config(path)


def utf8_streams() -> None:
    """Pipes and files get UTF-8 on every OS (Windows would default to the ANSI code page and
    print «m?quina» or fail on «→»). Interactive consoles keep their own encoding but never
    crash on a glyph they cannot show; term.py falls back to ASCII there."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
