"""The colony subagent templates pass the structural checker."""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "atta" / "templates" / "claude" / "check_templates.py"


class TemplateTests(unittest.TestCase):
    def test_templates_pass_checker(self):
        done = subprocess.run([sys.executable, str(CHECKER)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK: 10 plantillas válidas.", done.stdout)


if __name__ == "__main__":
    unittest.main()
