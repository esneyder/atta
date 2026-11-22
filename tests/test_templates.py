"""The colony subagent templates pass the structural checker."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TempDirMixin

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "atta" / "templates" / "claude" / "check_templates.py"
TEMPLATES = CHECKER.parent


def run_checker(*args):
    return subprocess.run([sys.executable, str(CHECKER), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


class TemplateTests(unittest.TestCase):
    def test_templates_pass_checker(self):
        done = run_checker()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK: 11 plantillas válidas.", done.stdout)


class RefutadorRuleTests(TempDirMixin, unittest.TestCase):
    """The checker enforces the Refutador's tools, tier and contract tokens (G4)."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "claude"
        (self.root / "agents").mkdir(parents=True)
        shutil.copy(TEMPLATES / "models.json", self.root / "models.json")
        for path in (TEMPLATES / "agents").glob("atta-*.md"):
            shutil.copy(path, self.root / "agents" / path.name)
        self.path = self.root / "agents" / "atta-refutador.md"
        self.text = self.path.read_text(encoding="utf-8")

    def check(self, text):
        self.path.write_text(text, encoding="utf-8")
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        return done.stdout

    def test_refutador_cannot_edit(self):
        out = self.check(self.text.replace("tools: Read, Grep, Glob, Bash, Write",
                                           "tools: Read, Grep, Glob, Bash, Write, Edit"))
        self.assertIn("agents/atta-refutador.md: este rol no puede tener la herramienta Edit.",
                      out)

    def test_refutador_runs_on_a_cheap_tier(self):
        out = self.check(self.text.replace("{{model:T1}}", "{{model:T3}}"))
        self.assertIn("agents/atta-refutador.md: model usa T3; para este rol usa T0 o T1.", out)

    def test_refutador_keeps_its_contract(self):
        out = self.check(self.text.replace("REFUTE: UPHELD|REFUTED|PARTIAL", "REFUTE:"))
        self.assertIn("agents/atta-refutador.md: el prompt no contiene "
                      "«REFUTE: UPHELD|REFUTED|PARTIAL».", out)

    def test_missing_refutador_template_is_reported(self):
        self.path.unlink()
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("agents/atta-refutador.md: falta la plantilla.", done.stdout)


if __name__ == "__main__":
    unittest.main()
