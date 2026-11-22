"""Filtro (S11, K4): the ast-grep step with the REAL binary, not the fake of test_gate.py.

Skipped when neither `ast-grep` nor an ast-grep `sg` is on the PATH (gate.ast_grep_binary). With
ATTA_REQUIRE_AST_GREP=1 (the ast-grep job of the CI) a missing binary fails instead of skipping,
so the CI never passes this test by skipping it.
"""

import os
import unittest

import support  # noqa: F401
from test_gate import GateFixture, write

from atta.spine import gate

REQUIRED = os.environ.get("ATTA_REQUIRE_AST_GREP", "") not in ("", "0")
RULE = ("id: no-print\nlanguage: python\nseverity: error\nmessage: no uses print\n"
        "rule:\n  pattern: print($A)\n")


class RealAstGrepTests(GateFixture, unittest.TestCase):
    def setUp(self):
        exe = gate.ast_grep_binary()
        if exe is None:
            if REQUIRED:
                self.fail("ATTA_REQUIRE_AST_GREP=1 y no hay ast-grep (ni sg) en el PATH.")
            self.skipTest("ast-grep no está instalado")
        super().setUp()
        # One severity:error rule in the MAIN checkout (the Filtro never reads the worktree's).
        write(self.main / "sgconfig.yml", "ruleDirs:\n  - rules\n")
        write(self.main / "rules" / "no-print.yml", RULE)

    def test_violating_diff_fails(self):
        write(self.wt / "src" / "malo.py", "x = 1\nprint(x)\n")
        self.commit_wt("viola la regla")
        result = self.run_gate()
        self.assertFalse(result.passed, result.summary)
        self.assertEqual(result.failed_check, "ast-grep")
        self.assertEqual(result.kind, "code")
        self.assertEqual(result.summary, "El Filtro no pasó: ast-grep encontró 1 error "
                                         "(no-print en src/malo.py:2).")
        self.assertIn("- ✗ `src/malo.py:2` no-print (error)", self.section("ast-grep"))
        self.assertTrue(self.report().startswith("GATE: FAIL\n"))

    def test_clean_diff_passes(self):
        write(self.wt / "src" / "bueno.py", "def suma(a, b):\n    return a + b\n")
        self.commit_wt("sin print")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("Sin hallazgos en 2 archivos del diff.", self.section("ast-grep"))
        self.assertTrue(self.report().startswith("GATE: PASS\n"))


if __name__ == "__main__":
    unittest.main()
