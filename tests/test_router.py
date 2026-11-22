import copy
import unittest

import support  # noqa: F401

from atta.spine import config, router


def R(reviewer, lenses, tier):
    return {"reviewer": reviewer, "lenses": lenses, "tier": tier}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config()

    def test_low_plan(self):
        self.assertEqual(router.plan("low", [], self.cfg), {
            "risk": "low", "surfaces": [], "spec_review": False,
            "reviewers": [R("code", ["reliability"], "T1")],
        })

    def test_medium_plan(self):
        self.assertEqual(router.plan("medium", [], self.cfg), {
            "risk": "medium", "surfaces": [], "spec_review": True,
            "reviewers": [R("code", ["reliability", "readability"], "T2")],
        })

    def test_high_plan_is_4r_plus_soldado_plus_rastreador(self):
        self.assertEqual(router.plan("high", [], self.cfg), {
            "risk": "high", "surfaces": [], "spec_review": True,
            "reviewers": [
                R("code", ["reliability", "readability"], "T2"),
                R("security", ["security"], "T3"),
                R("resilience", ["resilience"], "T2"),
                R("rastreador", ["adversarial"], "T3"),
            ],
        })

    def test_rastreador_only_on_high_risk(self):
        for risk in ("low", "medium"):
            for surfaces in ([], ["ui"], ["security"], ["ui", "security"]):
                with self.subTest(risk=risk, surfaces=surfaces):
                    names = [r["reviewer"] for r in
                             router.plan(risk, surfaces, self.cfg)["reviewers"]]
                    self.assertNotIn("rastreador", names)
        for surfaces in ([], ["ui"], ["security"]):
            with self.subTest(risk="high", surfaces=surfaces):
                found = [r for r in router.plan("high", surfaces, self.cfg)["reviewers"]
                         if r["reviewer"] == "rastreador"]
                self.assertEqual(found, [R("rastreador", ["adversarial"], "T3")])

    def test_default_table_matches_the_packaged_config(self):
        self.assertEqual(self.cfg["routing"], config.DEFAULT_ROUTING)

    def test_ui_surface_adds_ux_and_ui(self):
        p = router.plan("low", ["ui"], self.cfg)
        self.assertFalse(p["spec_review"])
        self.assertEqual(p["reviewers"], [
            R("code", ["reliability"], "T1"), R("ux", ["ux"], "T1"), R("ui", ["ui"], "T0"),
        ])

    def test_security_surface_adds_soldado_on_medium(self):
        p = router.plan("medium", ["security"], self.cfg)
        self.assertEqual(p["reviewers"], [
            R("code", ["reliability", "readability"], "T2"), R("security", ["security"], "T2"),
        ])

    def test_ui_and_security_surfaces_on_medium(self):
        p = router.plan("medium", ["ui", "security"], self.cfg)
        self.assertEqual([r["reviewer"] for r in p["reviewers"]], ["code", "ux", "ui", "security"])
        self.assertEqual(p["surfaces"], ["ui", "security"])

    def test_security_surface_on_high_does_not_duplicate_soldado(self):
        p = router.plan("high", ["security", "ui"], self.cfg)
        self.assertEqual([r["reviewer"] for r in p["reviewers"]],
                         ["code", "security", "resilience", "rastreador", "ux", "ui"])
        self.assertEqual(p["reviewers"][1]["tier"], "T3")

    def test_plan_from_intake_header(self):
        header = {"decision": "ACCEPT", "risk": "medium", "surfaces": ["ui"],
                  "blocked_by": [], "migration": False}
        self.assertEqual(router.plan_for_intake(header, self.cfg),
                         router.plan("medium", ["ui"], self.cfg))

    def test_plan_does_not_alias_config(self):
        p = router.plan("low", [], self.cfg)
        p["reviewers"][0]["lenses"].append("x")
        self.assertEqual(router.plan("low", [], self.cfg)["reviewers"][0]["lenses"], ["reliability"])

    def test_unknown_risk(self):
        with self.assertRaises(router.RoutingError) as ctx:
            router.plan("extreme", [], self.cfg)
        self.assertEqual(str(ctx.exception), "Riesgo desconocido: «extreme». Usa low, medium o high.")

    def test_unknown_surface(self):
        with self.assertRaises(router.RoutingError) as ctx:
            router.plan("low", ["db"], self.cfg)
        self.assertEqual(str(ctx.exception), "Superficie desconocida: «db». Usa ui o security.")

    def test_routing_changes_without_code(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["routing"]["risk"]["low"]["reviewers"] = [R("code", ["reliability", "readability"], "T2")]
        cfg["routing"]["risk"]["low"]["spec_review"] = True
        p = router.plan("low", [], cfg)
        self.assertTrue(p["spec_review"])
        self.assertEqual(p["reviewers"], [R("code", ["reliability", "readability"], "T2")])

    def test_missing_routing_raises(self):
        cfg = config.with_defaults({})
        cfg.pop("routing", None)
        with self.assertRaises(router.RoutingError) as ctx:
            router.plan("low", [], cfg)
        self.assertEqual(str(ctx.exception), "config.json: falta la tabla routing.")

    def test_invalid_tier_in_config_raises(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["routing"]["risk"]["low"]["reviewers"] = [R("code", ["reliability"], "T9")]
        with self.assertRaises(router.RoutingError) as ctx:
            router.plan("low", [], cfg)
        self.assertEqual(str(ctx.exception),
                         "config.json: routing.risk.low: tier «T9» no es válido; usa T0, T1, T2 o T3.")


if __name__ == "__main__":
    unittest.main()
