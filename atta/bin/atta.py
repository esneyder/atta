"""Entry point of the `atta` CLI from a checkout: `python atta/bin/atta.py <comando>`.

Installed, the same CLI is the `atta` console script (atta.spine.cli:main).
"""

import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = BIN_DIR.parents[1]
# This script is named atta.py: drop its own folder from sys.path so `import atta` finds the
# package at the repo root, not this file.
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != BIN_DIR]
sys.path.insert(0, str(REPO_ROOT))

from atta.spine import cli  # noqa: E402

if __name__ == "__main__":
    sys.exit(cli.main(sys.argv[1:]))
