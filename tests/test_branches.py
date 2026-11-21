import copy
import unittest

import support  # noqa: F401

from atta.spine import branches, config, ids


class BranchTestBase(unittest.TestCase):
    def setUp(self):
        self.cfg = config.with_defaults({})


class WorkBranchTests(BranchTestBase):
    def test_work_branch_uses_prefix(self):
        self.assertEqual(branches.work_branch("12-exportar-csv", self.cfg), "atta/12-exportar-csv")

    def test_work_branch_uses_configured_prefix(self):
        cfg = config.with_defaults({"branch_prefix": "bot/"})
        self.assertEqual(branches.work_branch("7", cfg), "bot/7")

    def test_work_branch_sanitizes_id(self):
        with self.assertRaises(ids.InvalidId):
            branches.work_branch("../main", self.cfg)

    def test_work_branch_refuses_integration_or_forbidden_result(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["forbidden_branches"] = ["main", "atta/7"]
        with self.assertRaises(branches.BranchRefused) as ctx:
            branches.work_branch("7", cfg)
        self.assertEqual(str(ctx.exception),
                         "La rama de trabajo «atta/7» coincide con una rama prohibida o de integración.")
        cfg = copy.deepcopy(self.cfg)
        cfg["integration_branch"] = "atta/7"
        with self.assertRaises(branches.BranchRefused):
            branches.work_branch("7", cfg)


class PrBaseTests(BranchTestBase):
    def assertRefusedBase(self, base, message, cfg=None):
        with self.assertRaises(branches.BranchRefused) as ctx:
            branches.check_pr_base(base, cfg or self.cfg)
        self.assertEqual(str(ctx.exception), message)

    def test_base_develop_accepted(self):
        self.assertIsNone(branches.check_pr_base("develop", self.cfg))

    def test_base_main_rejected_with_exact_message(self):
        self.assertRefusedBase("main", "La Reina no mergea a main: Atta solo integra en develop.")

    def test_base_master_rejected(self):
        self.assertRefusedBase("master", "La Reina no mergea a master: Atta solo integra en develop.")

    def test_base_other_feature_rejected(self):
        self.assertRefusedBase("feat/otra", "La Reina no mergea a feat/otra: Atta solo integra en develop.")

    def test_base_case_variant_rejected(self):
        self.assertRefusedBase("Main", "La Reina no mergea a Main: Atta solo integra en develop.")
        self.assertRefusedBase("Develop", "La Reina no mergea a Develop: Atta solo integra en develop.")

    def test_uses_configured_names(self):
        cfg = config.with_defaults({"integration_branch": "next", "forbidden_branches": ["prod"]})
        self.assertIsNone(branches.check_pr_base("next", cfg))
        self.assertRefusedBase("prod", "La Reina no mergea a prod: Atta solo integra en next.", cfg)

    def test_forbidden_integration_config_never_passes(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["integration_branch"] = "main"  # bypasses config.validate
        self.assertRefusedBase("main", "La Reina no mergea a main: Atta solo integra en main.", cfg)


class PushTargetTests(BranchTestBase):
    def assertRefusedPush(self, ref, message):
        with self.assertRaises(branches.BranchRefused) as ctx:
            branches.check_push_target(ref, self.cfg)
        self.assertEqual(str(ctx.exception), message)

    def test_push_to_work_branch_ok(self):
        self.assertIsNone(branches.check_push_target("atta/x", self.cfg))
        self.assertIsNone(branches.check_push_target("refs/heads/atta/12-exportar-csv", self.cfg))

    def test_push_to_develop_refused(self):
        self.assertRefusedPush("develop", "Atta no empuja a develop: solo se integra por PR.")
        self.assertRefusedPush("refs/heads/develop", "Atta no empuja a develop: solo se integra por PR.")

    def test_push_to_main_refused(self):
        self.assertRefusedPush("main", "Atta no empuja a main: es una rama prohibida.")
        self.assertRefusedPush("MASTER", "Atta no empuja a MASTER: es una rama prohibida.")

    def test_push_outside_prefix_refused(self):
        for ref in ("feat/x", "atta/", "atta/a/b", "atta/../main", "attax/1"):
            with self.subTest(ref=ref):
                self.assertRefusedPush(ref, f"Atta solo empuja a ramas atta/<job-id>; «{ref}» queda fuera.")


class RoundBudgetTests(BranchTestBase):
    def job(self, **counters):
        base = {"id": "12-x", "review_rounds": 0, "gate_rounds": 0, "sync_attempts": 0}
        base.update(counters)
        return base

    def test_below_max_ok(self):
        for counter in branches.ROUND_COUNTERS:
            with self.subTest(counter=counter):
                self.assertIsNone(branches.assert_round_budget(self.job(**{counter: 2}), counter, self.cfg))
                self.assertIsNone(branches.assert_round_budget(self.job(), counter, self.cfg))

    def test_at_max_refused(self):
        with self.assertRaises(branches.RoundBudgetExhausted) as ctx:
            branches.assert_round_budget(self.job(gate_rounds=3), "gate_rounds", self.cfg)
        self.assertEqual(str(ctx.exception),
                         "12-x: gate_rounds llegó a 3 de 3. Solo queda needs-human.")
        for counter in ("review_rounds", "sync_attempts"):
            with self.subTest(counter=counter):
                with self.assertRaises(branches.RoundBudgetExhausted):
                    branches.assert_round_budget(self.job(**{counter: 3}), counter, self.cfg)

    def test_counters_are_independent(self):
        job = self.job(gate_rounds=3)
        self.assertIsNone(branches.assert_round_budget(job, "review_rounds", self.cfg))

    def test_uses_configured_max(self):
        cfg = config.with_defaults({"max_rounds": 1})
        with self.assertRaises(branches.RoundBudgetExhausted):
            branches.assert_round_budget(self.job(review_rounds=1), "review_rounds", cfg)

    def test_unknown_counter(self):
        with self.assertRaises(ValueError) as ctx:
            branches.assert_round_budget(self.job(), "total_rounds", self.cfg)
        self.assertEqual(str(ctx.exception),
                         "Contador desconocido: total_rounds. Usa review_rounds, gate_rounds o sync_attempts.")


if __name__ == "__main__":
    unittest.main()
