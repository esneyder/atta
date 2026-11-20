"""Entry point of the `atta` CLI: `python atta/bin/atta.py <comando>` (see spine/cli.py)."""

import sys
from pathlib import Path

ATTA_DIR = Path(__file__).resolve().parents[1]
if str(ATTA_DIR) not in sys.path:
    sys.path.insert(0, str(ATTA_DIR))

from spine import cli  # noqa: E402


def _utf8_when_redirected() -> None:
    # Pipes and files get UTF-8 on every OS (Windows would default to the ANSI code page and fail
    # on «→» or «◐»). Interactive consoles keep their own encoding; term.py falls back to ASCII.
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


if __name__ == "__main__":
    _utf8_when_redirected()
    sys.exit(cli.main(sys.argv[1:]))
