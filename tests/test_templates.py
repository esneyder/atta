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
        self.assertIn("OK: 12 plantillas válidas.", done.stdout)


class RastreadorRuleTests(TempDirMixin, unittest.TestCase):
    """The checker enforces the Rastreador's tools, T3 tier and adversarial contract (G6)."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "claude"
        (self.root / "agents").mkdir(parents=True)
        shutil.copy(TEMPLATES / "models.json", self.root / "models.json")
        for path in (TEMPLATES / "agents").glob("atta-*.md"):
            shutil.copy(path, self.root / "agents" / path.name)
        self.path = self.root / "agents" / "atta-rastreador.md"
        self.text = self.path.read_text(encoding="utf-8")

    def check(self, old, new):
        self.assertIn(old, self.text)
        self.path.write_text(self.text.replace(old, new), encoding="utf-8")
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        return done.stdout

    def test_rastreador_cannot_edit(self):
        out = self.check("tools: Read, Grep, Glob, Bash, Write",
                         "tools: Read, Grep, Glob, Bash, Write, Edit")
        self.assertIn("agents/atta-rastreador.md: este rol no puede tener la herramienta Edit.",
                      out)

    def test_rastreador_needs_bash_to_grep(self):
        out = self.check("tools: Read, Grep, Glob, Bash, Write", "tools: Read, Grep, Glob, Write")
        self.assertIn("agents/atta-rastreador.md: tools debe ser Bash, Glob, Grep, Read, Write; "
                      "es Read, Grep, Glob, Write.", out)

    def test_rastreador_runs_only_on_t3(self):
        for tier in ("T0", "T1", "T2"):
            with self.subTest(tier=tier):
                out = self.check("{{model:T3}}", "{{model:%s}}" % tier)
                self.assertIn(f"agents/atta-rastreador.md: model usa {tier}; para este rol "
                              "usa T3.", out)

    def test_rastreador_keeps_its_contract_tokens(self):
        for token in ("VERDICT: PASS|CHANGES", "review-rastreador.md", "PREVENTION:",
                      "[blocker|major|minor] path:line", "try to break the change",
                      "grep the call, not the loop you touched",
                      "every caller, writer and reader", "bypass", "partial failure",
                      "data loss", "concurrency", "Never edit code"):
            with self.subTest(token=token):
                out = self.check(token, "xyz")
                self.assertIn(f"agents/atta-rastreador.md: el prompt no contiene «{token}».",
                              out)

    def test_rastreador_scope_and_context_budget(self):
        for token in ("correctness and the stated requirements only; do not invent work",
                      "## Context budget", "do not read the whole repo", "## Summary"):
            with self.subTest(token=token):
                out = self.check(token, "xyz")
                self.assertIn(f"agents/atta-rastreador.md: el prompt no contiene «{token}».",
                              out)

    def test_rastreador_checklist_has_at_most_twelve_items(self):
        out = self.check("\n\n## Verdict rules", "\n13. One more item.\n\n## Verdict rules")
        self.assertIn("agents/atta-rastreador.md: «## Core checklist» debe tener entre 1 y "
                      "12 puntos; tiene 13.", out)

    def test_missing_rastreador_template_is_reported(self):
        self.path.unlink()
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("agents/atta-rastreador.md: falta la plantilla.", done.stdout)


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


class StructuredContractTests(TempDirMixin, unittest.TestCase):
    """The checker enforces the structured contracts of F3 (G10, G11, G12, K2, Vigía cap)."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "claude"
        (self.root / "agents").mkdir(parents=True)
        shutil.copy(TEMPLATES / "models.json", self.root / "models.json")
        for path in (TEMPLATES / "agents").glob("atta-*.md"):
            shutil.copy(path, self.root / "agents" / path.name)

    def check(self, name, old, new):
        path = self.root / "agents" / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        return done.stdout

    def test_cortadora_build_md_needs_every_section(self):
        out = self.check("atta-cortadora", "## Risks\n", "")
        self.assertIn("agents/atta-cortadora.md: las secciones del artefacto deben ser Summary, "
                      "Changes, Tests, Acceptance, Risks, Blocked en ese orden; son Summary, "
                      "Changes, Tests, Acceptance, Blocked.", out)

    def test_cortadora_build_md_sections_keep_their_order(self):
        out = self.check("atta-cortadora", "## Tests\n\n- `python", "## Testsx\n\n- `python")
        self.assertIn("agents/atta-cortadora.md: las secciones del artefacto deben ser Summary, "
                      "Changes, Tests, Acceptance, Risks, Blocked en ese orden; son Summary, "
                      "Changes, Testsx, Acceptance, Risks, Blocked.", out)

    def test_cortadora_rejects_claims_without_evidence(self):
        out = self.check("atta-cortadora", "is not evidence", "is fine")
        self.assertIn("agents/atta-cortadora.md: el prompt no contiene «is not evidence».", out)

    def test_guia_copies_criteria_verbatim(self):
        out = self.check("atta-guia", "verbatim", "loosely")
        self.assertIn("agents/atta-guia.md: el prompt no contiene «verbatim».", out)

    def test_guia_files_line_is_mandatory(self):
        out = self.check("atta-guia", "The `FILES:` line is mandatory", "The `FILES:` line")
        self.assertIn("agents/atta-guia.md: el prompt no contiene "
                      "«The `FILES:` line is mandatory».", out)

    def test_reviewer_scope_is_bounded(self):
        scope = "correctness and the stated requirements only; do not invent work"
        out = self.check("atta-soldado", scope, "everything")
        self.assertIn(f"agents/atta-soldado.md: el prompt no contiene «{scope}».", out)

    def test_reviewer_checklist_has_at_most_twelve_items(self):
        out = self.check("atta-jardinera-code", "\n\n## Verdict rules",
                         "\n13. One more item.\n\n## Verdict rules")
        self.assertIn("agents/atta-jardinera-code.md: «## Core checklist» debe tener entre 1 y "
                      "12 puntos; tiene 13.", out)

    def test_reviewer_checks_callers_before_claiming_safe(self):
        out = self.check("atta-jardinera-ux", "callers and writers", "code")
        self.assertIn("agents/atta-jardinera-ux.md: el prompt no contiene "
                      "«callers and writers».", out)

    def test_vigia_caps_revise_rounds(self):
        out = self.check("atta-vigia", "at most 2 `REVISE` rounds", "many rounds")
        self.assertIn("agents/atta-vigia.md: el prompt no contiene "
                      "«at most 2 `REVISE` rounds».", out)

    def test_every_agent_has_a_context_budget(self):
        out = self.check("atta-exploradora", "do not read the whole repo", "read anything")
        self.assertIn("agents/atta-exploradora.md: el prompt no contiene "
                      "«do not read the whole repo».", out)


class DriverModelTests(TempDirMixin, unittest.TestCase):
    """F4 S5 (G8): the /atta command and skill run on the cheap tier, `{{model:T0}}`."""

    FILES = ("commands/atta.md", "skills/atta/SKILL.md")

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "claude"
        shutil.copytree(TEMPLATES, self.root)

    def test_packaged_driver_runs_on_t0(self):
        for rel in self.FILES:
            front = (TEMPLATES / rel).read_text(encoding="utf-8").split("---\n", 2)[1]
            self.assertIn("\nmodel: {{model:T0}}\n", "\n" + front, rel)

    def test_driver_on_another_tier_is_reported(self):
        for rel in self.FILES:
            with self.subTest(rel=rel):
                path = self.root / rel
                original = path.read_text(encoding="utf-8")
                path.write_text(original.replace("{{model:T0}}", "{{model:T2}}"),
                                encoding="utf-8")
                done = run_checker(str(self.root))
                path.write_text(original, encoding="utf-8")
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertIn(f"{rel}: model debe ser {{{{model:T0}}}} (el orquestador /atta "
                              "corre en el tier barato); es {{model:T2}}.", done.stdout)

    def test_driver_without_model_is_reported(self):
        path = self.root / "commands" / "atta.md"
        path.write_text(path.read_text(encoding="utf-8").replace("model: {{model:T0}}\n", ""),
                        encoding="utf-8")
        done = run_checker(str(self.root))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("commands/atta.md: model debe ser {{model:T0}} (el orquestador /atta "
                      "corre en el tier barato); es nada.", done.stdout)


class PrBodyTemplateTests(unittest.TestCase):
    """atta/templates/pr-body.md (K16) has the fixed sections and documents its placeholders."""

    def setUp(self):
        self.text = (TEMPLATES.parent / "pr-body.md").read_text(encoding="utf-8")
        head, sep, self.body = self.text.partition("-->")
        self.assertTrue(self.text.startswith("<!--") and sep, "falta el comentario inicial")
        self.head = head

    def test_sections_in_order(self):
        headings = [line for line in self.body.splitlines() if line.startswith("## ")]
        self.assertEqual(headings, ["## Resumen", "## Cambios", "## Pruebas",
                                    "## Criterios de aceptación"])
        self.assertEqual(self.body.strip().splitlines()[-1], "Closes #{{issue}}")

    def test_every_placeholder_is_documented(self):
        import re
        used = set(re.findall(r"\{\{([a-z_]+)\}\}", self.body))
        documented = set(re.findall(r"\{\{([a-z_]+)\}\}", self.head))
        self.assertEqual(used, documented)
        self.assertIn("git diff --stat", self.head)


if __name__ == "__main__":
    unittest.main()
