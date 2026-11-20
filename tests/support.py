"""Shared helpers for spine tests: put atta/ on sys.path and build temp job stores."""

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ATTA_DIR = REPO_ROOT / "atta"
if str(ATTA_DIR) not in sys.path:
    sys.path.insert(0, str(ATTA_DIR))

T0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    """A fixed UTC instant `seconds` after T0."""
    return T0 + timedelta(seconds=seconds)


class TempDirMixin:
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()


def snapshot(path):
    """Bytes of a file, or None when it does not exist."""
    p = Path(path)
    return p.read_bytes() if p.exists() else None
