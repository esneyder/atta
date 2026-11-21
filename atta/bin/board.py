"""El hormiguero from a checkout: `python atta/bin/board.py [--project P] [--jobs-dir D] [--port 8765]`.

Installed, the same board is the `atta-board` console script (atta.spine.board:main).
"""

import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != BIN_DIR]
sys.path.insert(0, str(BIN_DIR.parents[1]))

from atta.spine.board import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
