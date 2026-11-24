"""Alcance del diff (S7, K8) y corrección acotada (S5, G5) en el Filtro."""

import unittest

import support  # noqa: F401
from test_gate import GateFixture, check, git, py, write

from atta.spine import config, gate, scope


class ParseFilesTests(unittest.TestCase):
    def test_one_comma_separated_line(self):
        text = "## Files\n\nFILES: src/a.py, src/orders/**, tests/test_a.py\n\n## Failure paths\n"
        self.assertEqual(scope.parse_files(text), ["src/a.py", "src/orders/**", "tests/test_a.py"])

    def test_newline_separated_list_until_a_blank_line(self):
        text = "FILES:\n- `src/a.py`\n- src/b/\nsrc/c.py, src/d.py\n\nnotas\n"
        self.assertEqual(scope.parse_files(text), ["src/a.py", "src/b/", "src/c.py", "src/d.py"])

    def test_stops_at_a_heading_or_another_key(self):
        self.assertEqual(scope.parse_files("FILES: a.py\n## Otra\nb.py\n"), ["a.py"])
        self.assertEqual(scope.parse_files("FILES: a.py\nRISK: low\n"), ["a.py"])

    def test_missing_files_line(self):
        self.assertIsNone(scope.parse_files("STATUS: READY\n## Goal\nalgo\n"))

    def test_matches_paths_globs_and_folders(self):
        files = ["src/a.py", "src/orders/**", "lib/", "pkg"]
        for path in ("src/a.py", "SRC/A.py", "src/orders/x/y.py", "lib/z.py", "pkg/m.py"):
            self.assertTrue(scope.matches(path, files), path)
        for path in ("src/b.py", "src/ordersx.py", "libx/z.py"):
            self.assertFalse(scope.matches(path, files), path)

    def test_finding_paths(self):
        text = ("- [major] src/orders/import.py:42 — vacío — salta la fila\n"
                "- [minor] `src/a.py:7` — x — y\n"
                "- [blocker] .\\src\\win.py:3 — z — w\n"
                "- [minor] src/a.py:9 — repetido\n")
        self.assertEqual(scope.finding_paths([text]),
                         ["src/orders/import.py", "src/a.py", "src/win.py"])

    def test_absolute_finding_paths_inside_the_project_are_relative(self):
        # S2: reviewers cite absolute paths of the worktree or of the project.
        text = ("- [minor] /srv/proj/.worktrees/7-x/calc.py:12 — x — y\n"
                "- [major] `/srv/proj/src/a.py:3` — x — y\n"
                "- [major] C:\\Users\\u\\proj\\.worktrees\\7-x\\src\\w.py:4 — x — y\n"
                "- [minor] /otro/sitio/b.py:1 — fuera del proyecto\n")
        self.assertEqual(scope.finding_paths([text], root="/srv/proj"),
                         ["calc.py", "src/a.py"])
        self.assertEqual(scope.finding_paths([text], root="C:\\Users\\u\\proj"),
                         ["src/w.py"])

    def test_absolute_finding_paths_without_root_are_ignored(self):
        self.assertEqual(scope.finding_paths(["- [minor] /srv/proj/a.py:1 — x\n"]), [])


class ScopeGateTests(GateFixture, unittest.TestCase):
    """The `## Alcance` section of checks.md for a `checks` round."""

    def spec(self, files_line):
        write(self.round.parent / "spec.md",
              f"STATUS: READY\n\n## Goal\n\nx\n\n## Files\n\n{files_line}\n\n## Out of scope\n")

    def enforce(self, max_files=None):
        data = {"scope": {"enforce": True}}
        if max_files is not None:
            data["scope"]["max_files"] = max_files
        self.cfg = config.with_defaults(data)

    def test_inside_files_is_quiet(self):
        self.spec("FILES: src/feature.py")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        section = self.section("Alcance")
        self.assertIn("FILES: `src/feature.py`", section)
        self.assertIn("El diff queda dentro de `FILES:`", section)
        self.assertNotIn("Avisos", result.summary)

    def test_outside_files_warns_by_default(self):
        self.spec("FILES: src/otro.py")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("⚠ El diff toca archivos fuera de `FILES:`: `src/feature.py`.",
                      self.section("Alcance"))
        self.assertIn("Avisos en checks.md: alcance.", result.summary)

    def test_outside_files_fails_with_enforce_and_runs_no_checks(self):
        self.enforce()
        self.spec("FILES: src/otro.py")
        self.set_checks([check("marca", py("open('marca.txt', 'w').write('x')"))])
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertIsNone(result.failed_check)
        self.assertEqual(result.summary,
                         "El Filtro no pasó: el diff sale del alcance de FILES: "
                         "(`src/feature.py`).")
        self.assertIn("✗ El diff toca archivos fuera de `FILES:`", self.section("Alcance"))
        self.assertIn("No se corrieron: el diff sale del alcance.", self.section("Checks"))
        self.assertFalse((self.wt / "marca.txt").exists())
        self.assertNotIn("## Riesgo", self.report())

    def test_tests_and_lockfiles_never_count(self):
        self.enforce()
        self.spec("FILES: src/feature.py")
        write(self.wt / "tests" / "test_feature.py", "def test_x():\n    pass\n")
        write(self.wt / "package-lock.json", "{}\n")
        write(self.wt / "web" / "yarn.lock", "x\n")
        self.commit_wt()
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)

    def test_without_files_line_only_notes(self):
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("spec.md no tiene `FILES:`: no se comparó el alcance del diff.",
                      self.section("Alcance"))

    def test_size_over_max_files_warns_even_with_enforce(self):
        self.enforce(max_files=2)
        self.spec("FILES: src/**")
        for n in range(3):
            write(self.wt / "src" / f"m{n}.py", "x = 1\n")
        self.commit_wt()
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("⚠ El diff cambia 4 archivos (más de 2, scope.max_files)",
                      self.section("Alcance"))
        self.assertIn("Avisos en checks.md: tamaño.", result.summary)

    def test_default_max_files_is_twelve(self):
        result = self.run_gate()
        self.assertIn("Tamaño: 1 de 12 archivos como máximo.", self.section("Alcance"))

    def test_merge_checks_have_no_scope(self):
        self.enforce()
        self.spec("FILES: src/otro.py")
        result = self.run_gate(kind="merge-checks")
        self.assertTrue(result.passed, result.summary)
        self.assertNotIn("## Alcance", self.report("merge-checks"))


class BoundedCorrectionTests(GateFixture, unittest.TestCase):
    """S5 (G5): from round 2 on, the round's own changes stay within the previous findings."""

    def setUp(self):
        super().setUp()
        self.round1 = self.round
        first = self.run_gate()
        self.assertTrue(first.passed, first.summary)
        write(self.round1 / "review-code.md",
              "VERDICT: CHANGES\n\n- [major] src/feature.py:1 — x mal — corrígelo\n")
        self.round = self.round1.parent / "round-2"

    def test_correction_within_findings_is_quiet(self):
        write(self.wt / "src" / "feature.py", "x = 2\n")
        self.commit_wt("corrige")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("Corrección acotada (ronda 2 contra los hallazgos de la ronda 1): la "
                      "corrección queda dentro de los hallazgos.", self.section("Alcance"))

    def test_absolute_worktree_finding_bounds_the_correction(self):
        # S2: an absolute in-project finding path bounds the round-2 correction.
        write(self.round1 / "review-code.md",
              "VERDICT: CHANGES\n\n- [major] src/feature.py:1 — x — y\n"
              f"- [minor] {self.wt.as_posix()}/src/app.py:1 — otro — z\n")
        write(self.wt / "src" / "app.py", "print('corregido')\n")
        self.commit_wt("corrige app")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("la corrección queda dentro de los hallazgos", self.section("Alcance"))
        self.assertNotIn("Avisos", result.summary)

    def test_correction_outside_findings_warns(self):
        write(self.wt / "src" / "app.py", "print('otro')\n")
        self.commit_wt("se va")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("⚠ Corrección acotada (ronda 2 contra los hallazgos de la ronda 1): la "
                      "corrección salió del alcance de los hallazgos: `src/app.py`.",
                      self.section("Alcance"))
        self.assertIn("Avisos en checks.md: corrección.", result.summary)

    def test_correction_outside_findings_fails_with_enforce(self):
        self.cfg = config.with_defaults({"scope": {"enforce": True}})
        write(self.wt / "src" / "app.py", "print('otro')\n")
        self.commit_wt("se va")
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "El Filtro no pasó: la corrección salió del alcance de "
                                         "los hallazgos (`src/app.py`).")

    def test_files_of_the_spec_also_bound_the_correction(self):
        write(self.round1.parent / "spec.md", "FILES: src/feature.py, src/app.py\n")
        write(self.wt / "src" / "app.py", "print('otro')\n")
        self.commit_wt("dentro de FILES")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("la corrección queda dentro de los hallazgos", self.section("Alcance"))

    def test_tests_never_count_against_the_correction(self):
        write(self.wt / "tests" / "test_feature.py", "def test_x():\n    pass\n")
        self.commit_wt("test")
        self.run_gate()
        self.assertIn("la corrección queda dentro de los hallazgos", self.section("Alcance"))

    def test_previous_round_without_findings_does_not_bound(self):
        (self.round1 / "review-code.md").unlink()
        write(self.wt / "src" / "app.py", "print('otro')\n")
        self.commit_wt("se va")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("la ronda anterior no tiene hallazgos con `path:line`; no se acota",
                      self.section("Alcance"))

    def test_previous_commit_missing_does_not_bound(self):
        (self.round1 / "checks.md").write_text("GATE: PASS\n", encoding="utf-8")
        write(self.wt / "src" / "app.py", "print('otro')\n")
        self.commit_wt("se va")
        self.run_gate()
        self.assertIn("no encontré el commit de la ronda anterior", self.section("Alcance"))


if __name__ == "__main__":
    unittest.main()
