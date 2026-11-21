"""El hormiguero: `python atta/bin/board.py [--jobs-dir D] [--port 8765] [--host 127.0.0.1]`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spine.board import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
