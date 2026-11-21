import unittest

import support  # noqa: F401
from atta.spine import voice


class VoiceTests(unittest.TestCase):
    def test_exploradora_accepts(self):
        self.assertEqual(voice.intake_accepted(12, "low"),
                         "La Exploradora aceptó el issue #12 (riesgo bajo).")
        self.assertEqual(voice.intake_accepted(5, "high"),
                         "La Exploradora aceptó el issue #5 (riesgo alto).")

    def test_rounds_exhausted(self):
        self.assertEqual(voice.rounds_exhausted(3),
                         "Tres rondas sin pasar el filtro. Te lo dejamos a ti.")
        self.assertEqual(voice.rounds_exhausted(12),
                         "12 rondas sin pasar el filtro. Te lo dejamos a ti.")

    def test_gate_failed(self):
        self.assertEqual(voice.gate_failed(2), "El Filtro no pasó: 2 tests fallan.")
        self.assertEqual(voice.gate_failed(1), "El Filtro no pasó: 1 test falla.")

    def test_reina_escalates(self):
        self.assertEqual(voice.reina_escalates("toca la base de datos."),
                         "La Reina lo deja en tus manos: toca la base de datos.")

    def test_contract_tokens_not_translated(self):
        self.assertEqual(voice.intake_rejected(7), "La Exploradora descartó el issue #7 (REJECT).")
        self.assertEqual(voice.intake_needs_info(7),
                         "La Exploradora necesita más información en el issue #7 (NEEDS-INFO).")
        self.assertEqual(voice.gate_passed(), "El Filtro pasó (PASS).")
        self.assertEqual(voice.reina_approved(34), "La Reina aprobó el PR #34 (APPROVE).")
        for token in ("ACCEPT", "REJECT", "NEEDS-INFO", "PASS", "FAIL", "CHANGES", "APPROVE",
                      "ESCALATE", "READY", "REVISE", "DONE", "BLOCKED"):
            self.assertIn(token, voice.CONTRACT_TOKENS)

    def test_unknown_risk_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            voice.intake_accepted(1, "extremo")
        self.assertEqual(str(ctx.exception), "Riesgo desconocido: extremo.")

    def test_for_transition(self):
        job = {"issue": 12, "intake": {"risk": "low"}, "pr": 34, "gate_rounds": 3}
        self.assertEqual(voice.for_transition(job, "intake", "spec"),
                         "La Exploradora aceptó el issue #12 (riesgo bajo).")
        self.assertEqual(voice.for_transition(job, "gate", "needs-human"),
                         "Tres rondas sin pasar el filtro. Te lo dejamos a ti.")
        self.assertEqual(voice.for_transition(job, "merging", "merged"),
                         "La Reina integró el PR #34 en develop.")
        self.assertIsNone(voice.for_transition(job, "spec", "build"))


if __name__ == "__main__":
    unittest.main()
