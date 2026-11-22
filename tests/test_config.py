import json
import unittest

import support  # noqa: F401  (adds atta/ to sys.path)
from support import TempDirMixin

from atta.spine import config


class ConfigTests(TempDirMixin, unittest.TestCase):
    def write(self, data):
        path = self.tmp / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_repo_config_loads_with_defaults(self):
        cfg = config.load_config()
        self.assertEqual(cfg["integration_branch"], "develop")
        self.assertEqual(cfg["forbidden_branches"], ["main", "master"])
        self.assertEqual(cfg["max_rounds"], 3)
        self.assertEqual(cfg["max_total_rounds"], 6)
        self.assertEqual(cfg["concurrency"], 3)
        self.assertEqual(
            cfg["labels"],
            {"queue": "atta", "needs_info": "atta:needs-info", "needs_human": "atta:needs-human"},
        )
        self.assertEqual(
            cfg["protected_paths"],
            ["atta/**", ".claude/**", ".pi/**", ".github/workflows/**"],
        )
        self.assertEqual(cfg["branch_prefix"], "atta/")
        self.assertEqual(cfg["prices_usd_per_mtok"], {})

    def test_missing_keys_take_defaults(self):
        cfg = config.load_config(self.write({"concurrency": 1}))
        self.assertEqual(cfg["concurrency"], 1)
        self.assertEqual(cfg["integration_branch"], "develop")

    def test_missing_routing_takes_default_table(self):
        from atta.spine import router
        cfg = config.load_config(self.write({"concurrency": 1}))
        self.assertEqual(set(cfg["routing"]["risk"]), {"low", "medium", "high"})
        self.assertEqual(router.plan("low", [], cfg)["reviewers"][0]["reviewer"], "code")

    def test_rejects_forbidden_integration_branch(self):
        path = self.write({"integration_branch": "main"})
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(path)
        self.assertEqual(
            str(ctx.exception),
            "config.json: integration_branch «main» está en forbidden_branches; "
            "Atta nunca integra en una rama prohibida.",
        )

    def test_rejects_forbidden_branch_case_insensitively(self):
        path = self.write({"integration_branch": "Master"})
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(path)
        self.assertIn("«Master» está en forbidden_branches", str(ctx.exception))

    def test_rejects_forbidden_branch_behind_ref_or_remote_prefix(self):
        for name in ("refs/heads/main", "origin/main", "Main", " main "):
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config(self.write({"integration_branch": name}))
                self.assertEqual(
                    str(ctx.exception),
                    f"config.json: integration_branch «{name}» está en forbidden_branches; "
                    "Atta nunca integra en una rama prohibida.",
                )

    def test_rejects_empty_forbidden_branches(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"forbidden_branches": []}))
        self.assertEqual(
            str(ctx.exception),
            "config.json: forbidden_branches no puede estar vacía; "
            "Atta necesita al menos una rama prohibida (p. ej. main).",
        )

    def test_rejects_empty_protected_paths(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"protected_paths": []}))
        self.assertEqual(
            str(ctx.exception),
            "config.json: protected_paths no puede estar vacía; el Filtro necesita rutas protegidas.",
        )

    def test_rejects_branch_prefix_under_refs(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"branch_prefix": "refs/heads/atta/"}))
        self.assertEqual(
            str(ctx.exception),
            "config.json: branch_prefix no puede empezar con «refs/»; "
            "usa un prefijo de rama como «atta/».",
        )

    def test_rejects_invalid_json(self):
        path = self.tmp / "config.json"
        path.write_text("{no es json", encoding="utf-8")
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(path)
        self.assertTrue(str(ctx.exception).startswith("config.json no es JSON válido:"))

    def test_rejects_bad_types(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"max_total_rounds": 0}))
        self.assertEqual(
            str(ctx.exception),
            "config.json: max_total_rounds debe ser un entero mayor que 0.",
        )

    def test_rejects_branch_prefix_without_slash(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"branch_prefix": "atta"}))
        self.assertEqual(
            str(ctx.exception),
            "config.json: branch_prefix debe terminar en «/».",
        )

    def test_merge_method_and_delivery_defaults(self):
        cfg = config.load_config(self.write({}))
        self.assertEqual(cfg["merge_method"], "merge")
        self.assertEqual(cfg["delivery"], {"auto_merge_up_to": "high"})
        for method in ("merge", "squash", "rebase"):
            self.assertEqual(config.load_config(self.write({"merge_method": method}))
                             ["merge_method"], method)
        for level in ("none", "low", "medium", "high"):
            data = {"delivery": {"auto_merge_up_to": level}}
            self.assertEqual(config.load_config(self.write(data))["delivery"],
                             {"auto_merge_up_to": level})

    def test_rejects_invalid_merge_method_and_delivery(self):
        cases = [
            ({"merge_method": "fast"},
             "merge_method «fast» no es válido (usa: merge, squash, rebase)."),
            ({"delivery": {"auto_merge_up_to": "all"}},
             "delivery.auto_merge_up_to «all» no es válido (usa: none, low, medium, high)."),
            ({"delivery": "pr"}, "delivery debe ser un objeto."),
        ]
        for data, msg in cases:
            with self.subTest(data=data):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config(self.write(data))
                self.assertEqual(str(ctx.exception), f"config.json: {msg}")

    def test_partial_sections_take_the_missing_defaults(self):
        cfg = config.load_config(self.write({"labels": {"queue": "colonia"},
                                              "delivery": {"mode": "by-risk"}}))
        self.assertEqual(cfg["labels"], {"queue": "colonia", "needs_info": "atta:needs-info",
                                         "needs_human": "atta:needs-human"})
        self.assertEqual(cfg["delivery"], {"auto_merge_up_to": "high", "mode": "by-risk"})

    def test_labels_with_a_non_text_value_are_still_rejected(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.load_config(self.write({"labels": {"queue": 3}}))
        self.assertEqual(str(ctx.exception),
                         "config.json: labels debe tener queue, needs_info y needs_human como "
                         "textos.")

    def test_scope_defaults_and_partial_section(self):
        self.assertEqual(config.load_config(self.write({}))["scope"],
                         {"enforce": False, "max_files": 12})
        cfg = config.load_config(self.write({"scope": {"enforce": True}}))
        self.assertEqual(cfg["scope"], {"enforce": True, "max_files": 12})

    def test_rejects_invalid_scope(self):
        cases = [
            ({"scope": []}, "scope debe ser un objeto."),
            ({"scope": {"enforce": "sí"}}, "scope.enforce debe ser true o false."),
            ({"scope": {"max_files": 0}}, "scope.max_files debe ser un entero mayor que 0."),
            ({"scope": {"max_files": True}}, "scope.max_files debe ser un entero mayor que 0."),
        ]
        for data, msg in cases:
            with self.subTest(data=data):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config(self.write(data))
                self.assertEqual(str(ctx.exception), f"config.json: {msg}")

    def test_models_is_optional_and_accepts_tiers_and_roles(self):
        self.assertNotIn("models", config.load_config(self.write({})))
        models = {"claude-code": {"tiers": {"T2": "haiku", "T3": "claude-opus-4-1"},
                                   "roles": {"soldado": "opus"}}}
        self.assertEqual(config.load_config(self.write({"models": models}))["models"], models)

    def test_rejects_invalid_models(self):
        example = ('{"claude-code": {"tiers": {"T2": "haiku"}, "roles": {"soldado": "opus"}}}')
        cases = [
            ([], f"models debe ser un objeto por runtime, p. ej. {example}."),
            ({"otro": {}}, "models: el runtime «otro» no existe; usa claude-code."),
            ({"claude-code": {"tier": {}}},
             f"models.claude-code debe ser un objeto con «tiers» y/o «roles», p. ej. {example}."),
            ({"claude-code": {"tiers": []}}, "models.claude-code.tiers debe ser un objeto."),
            ({"claude-code": {"tiers": {"T9": "haiku"}}},
             "models.claude-code.tiers.T9: El tier «T9» no existe: usa T0, T1, T2 o T3."),
            ({"claude-code": {"roles": {"obrera": "haiku"}}},
             "models.claude-code.roles.obrera: El rol «obrera» no existe: usa exploradora, guia, "
             "vigia, cortadora, jardinera-code, jardinera-resilience, jardinera-ux, "
             "jardinera-ui, soldado, refutador o reina."),
            ({"claude-code": {"roles": {"soldado": "gpt-4"}}},
             "models.claude-code.roles.soldado: «gpt-4» no es un modelo válido para "
             "claude-code: usa haiku, sonnet u opus, o un id completo que cumpla "
             "claude-[a-z0-9.-]+ (p. ej. claude-sonnet-4-5)."),
            ({"claude-code": {"tiers": {"T2": 3}}},
             "models.claude-code.tiers.T2: «3» no es un modelo válido para claude-code: usa "
             "haiku, sonnet u opus, o un id completo que cumpla claude-[a-z0-9.-]+ "
             "(p. ej. claude-sonnet-4-5)."),
        ]
        for value, msg in cases:
            with self.subTest(value=value):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config(self.write({"models": value}))
                self.assertEqual(str(ctx.exception), f"config.json: {msg}")


if __name__ == "__main__":
    unittest.main()
