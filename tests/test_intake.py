import unittest

import support  # noqa: F401
from support import TempDirMixin

from spine import intake


def doc(*header, body="## Summary\nAlgo.\n"):
    return "\n".join(header) + "\n\n" + body


class ParseHeaderTests(unittest.TestCase):
    def test_minimal_header(self):
        self.assertEqual(
            intake.parse_header(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none")),
            {"decision": "ACCEPT", "risk": "low", "surfaces": [],
             "blocked_by": [], "migration": False},
        )

    def test_full_header(self):
        text = doc("DECISION: ACCEPT", "RISK: high", "SURFACES: ui,security",
                   "BLOCKED-BY: #3, #12", "MIGRATION: yes")
        self.assertEqual(intake.parse_header(text), {
            "decision": "ACCEPT", "risk": "high", "surfaces": ["ui", "security"],
            "blocked_by": [3, 12], "migration": True,
        })

    def test_single_blocked_by_and_migration_no(self):
        h = intake.parse_header(doc("DECISION: NEEDS-INFO", "RISK: medium", "SURFACES: ui",
                                    "BLOCKED-BY: #7", "MIGRATION: no"))
        self.assertEqual(h["decision"], "NEEDS-INFO")
        self.assertEqual(h["surfaces"], ["ui"])
        self.assertEqual(h["blocked_by"], [7])
        self.assertFalse(h["migration"])

    def test_reject_decision_and_crlf(self):
        text = "DECISION: REJECT\r\nRISK: low\r\nSURFACES: security\r\n\r\nRISK: banana\r\n"
        h = intake.parse_header(text)
        self.assertEqual(h["decision"], "REJECT")
        self.assertEqual(h["surfaces"], ["security"])

    def test_header_ends_at_first_blank_line(self):
        h = intake.parse_header(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none",
                                    body="MIGRATION: maybe\nDECISION: lol\n"))
        self.assertFalse(h["migration"])

    def assertBad(self, text, message):
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.parse_header(text)
        self.assertEqual(str(ctx.exception), message)

    def test_first_line_must_be_decision(self):
        self.assertBad(doc("RISK: low", "DECISION: ACCEPT", "SURFACES: none"),
                       "intake.md: la primera línea debe ser «DECISION: ACCEPT|REJECT|NEEDS-INFO».")

    def test_empty_text(self):
        self.assertBad("", "intake.md: la primera línea debe ser «DECISION: ACCEPT|REJECT|NEEDS-INFO».")

    def test_unknown_decision(self):
        self.assertBad(doc("DECISION: MAYBE", "RISK: low", "SURFACES: none"),
                       "intake.md: línea 1: DECISION «MAYBE» no es válido; usa ACCEPT, REJECT o NEEDS-INFO.")

    def test_lowercase_decision_rejected(self):
        self.assertBad(doc("DECISION: accept", "RISK: low", "SURFACES: none"),
                       "intake.md: línea 1: DECISION «accept» no es válido; usa ACCEPT, REJECT o NEEDS-INFO.")

    def test_unknown_risk(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: extreme", "SURFACES: none"),
                       "intake.md: línea 2: RISK «extreme» no es válido; usa low, medium o high.")

    def test_unknown_surface(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: ui,db"),
                       "intake.md: línea 3: SURFACES «ui,db» no es válido; usa none, ui, security o ui,security.")

    def test_none_mixed_with_surface(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none,ui"),
                       "intake.md: línea 3: SURFACES «none,ui» no es válido; usa none, ui, security o ui,security.")

    def test_duplicate_surface(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: ui,ui"),
                       "intake.md: línea 3: SURFACES «ui,ui» no es válido; usa none, ui, security o ui,security.")

    def test_malformed_line(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK low", "SURFACES: none"),
                       "intake.md: línea 2: «RISK low» no tiene la forma «CLAVE: valor».")

    def test_unknown_key(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none", "OWNER: ana"),
                       "intake.md: línea 4: clave desconocida «OWNER».")

    def test_duplicate_key(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "RISK: high", "SURFACES: none"),
                       "intake.md: línea 3: RISK aparece dos veces.")

    def test_missing_risk(self):
        self.assertBad(doc("DECISION: ACCEPT", "SURFACES: none"),
                       "intake.md: falta RISK en la cabecera.")

    def test_missing_surfaces(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low"),
                       "intake.md: falta SURFACES en la cabecera.")

    def test_malformed_blocked_by(self):
        for value in ("3", "#3,", "#3 #4", "#0", "#x", "#3, #3"):
            with self.subTest(value=value):
                self.assertBad(
                    doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none", f"BLOCKED-BY: {value}"),
                    f"intake.md: línea 4: BLOCKED-BY «{value}» no es válido; usa #n o #n, #m.")

    def test_malformed_migration(self):
        self.assertBad(doc("DECISION: ACCEPT", "RISK: low", "SURFACES: none", "MIGRATION: si"),
                       "intake.md: línea 4: MIGRATION «si» no es válido; usa yes o no.")


class ParseFileTests(TempDirMixin, unittest.TestCase):
    def test_reads_file_as_utf8(self):
        path = self.tmp / "intake.md"
        path.write_text(doc("DECISION: ACCEPT", "RISK: medium", "SURFACES: ui",
                            body="Acotación: sí.\n"), encoding="utf-8")
        self.assertEqual(intake.parse_file(path)["risk"], "medium")

    def test_missing_file(self):
        with self.assertRaises(intake.IntakeError) as ctx:
            intake.parse_file(self.tmp / "nope.md")
        self.assertTrue(str(ctx.exception).startswith("No se pudo leer "))


if __name__ == "__main__":
    unittest.main()
