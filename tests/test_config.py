import json
import unittest

import support  # noqa: F401  (adds atta/ to sys.path)
from support import TempDirMixin

from spine import config


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
        from spine import router
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


if __name__ == "__main__":
    unittest.main()
