"""Portable per-job file lock (msvcrt on Windows, fcntl elsewhere). Never a global lock."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = ".lock"

if os.name == "nt":
    import msvcrt

    def _try_lock(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class LockTimeout(TimeoutError):
    """The job is locked by someone else."""


@contextmanager
def job_lock(job_dir: str | Path, timeout: float = 30.0, poll: float = 0.05):
    """Hold an exclusive lock on `<job_dir>/.lock` for the duration of the block."""
    path = Path(job_dir) / LOCK_NAME
    fh = open(path, "a+b")
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fh):
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"No se pudo bloquear el job en {Path(job_dir)} (ocupado por otro proceso)."
                )
            time.sleep(poll)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()
