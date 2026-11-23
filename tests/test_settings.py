"""`atta settings` (S14, L24): view, get, set and reset every key of atta/config.json."""

import contextlib
import io
import json
import re
import shutil
import unittest
from unittest import mock

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import cli, config, guard, settings_registry
from atta.spine.settings_registry import Setting

KEYS = ("integration_branch", "forbidden_branches", "branch_prefix", "max_rounds",
        "max_total_rounds", "concurrency", "merge_method", "labels.queue", "labels.needs_info",
        "labels.needs_human", "labels.needs_review", "protected_paths",
        "delivery.auto_merge_up_to", "scope.enforce", "scope.max_files",
        "guard.deny_commands", "prices_usd_per_mtok", "routing", "risk_rules", "models")
NOT_KEY = "La clave «foo» no existe; mira las claves con `atta settings`."
FLAG = Setting("experimental.flag", "bool", False, "Bandera de prueba.")


class SettingsBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = (self.tmp / "proj").resolve()
        (self.root / "atta").mkdir(parents=True)
        self.cfg_path = self.root / "atta" / "config.json"
        shutil.copyfile(config.CONFIG_PATH, self.cfg_path)

    def atta(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--project", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.atta(*argv)
        self.assertEqual((code, err), (0, ""), out)
        return out

    def stored(self):
        return json.loads(self.cfg_path.read_text(encoding="utf-8"))

    def write(self, data):
        self.cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class RegistryTest(unittest.TestCase):
    def test_every_key_in_order_with_its_default(self):
        reg = settings_registry.SETTINGS
        self.assertEqual(tuple(s.key for s in reg), KEYS)
        by = {s.key: s for s in reg}
        self.assertEqual(by["max_rounds"].default, 3)
        self.assertEqual(by["labels.queue"].default, "atta")
        self.assertEqual(by["merge_method"].choices, ("merge", "squash", "rebase"))
        self.assertEqual(by["merge_method"].default, "merge")
        self.assertEqual(by["delivery.auto_merge_up_to"].choices,
                         ("none", "low", "medium", "high"))
        self.assertEqual(by["delivery.auto_merge_up_to"].default, "high")
        # S13: the description explains the table none/low/medium/high.
        for word in ("none", "low", "medium", "high", "PR"):
            self.assertIn(word, by["delivery.auto_merge_up_to"].description)
        self.assertEqual(by["labels.needs_review"].default, "atta:needs-review")
        self.assertEqual(by["labels.needs_review"].type, "str")
        self.assertEqual(by["guard.deny_commands"].default, list(guard.DEFAULT_DENY_COMMANDS))
        self.assertEqual((by["scope.enforce"].type, by["scope.enforce"].default), ("bool", False))
        self.assertEqual((by["scope.max_files"].type, by["scope.max_files"].default), ("int", 12))
        self.assertIn("FILES:", by["scope.enforce"].description)
        for key in ("prices_usd_per_mtok", "routing", "risk_rules", "models"):
            self.assertFalse(by[key].editable, key)
        for s in reg:
            self.assertTrue(s.description, s.key)


class TableTest(SettingsBase):
    def test_lists_every_key_with_value_and_default(self):
        out = self.ok("settings")
        lines = out.splitlines()
        self.assertEqual(lines[0], f"Configuración de Atta en {self.root} (atta/config.json):")
        self.assertRegex(lines[1], r"^CLAVE\s+VALOR\s+POR DEFECTO\s+DESCRIPCIÓN$")
        rows = lines[2:2 + len(KEYS)]
        self.assertEqual([r.split()[0] for r in rows], list(KEYS))
        self.assertRegex(out, r"(?m)^max_rounds\s+3\s+3\s+Rondas")
        self.assertRegex(out, r"(?m)^merge_method\s+merge\s+merge\s+\S")
        self.assertRegex(out, r"(?m)^delivery\.auto_merge_up_to\s+high\s+high\s+\S")
        self.assertRegex(out, r"(?m)^labels\.queue\s+atta\s+atta\s+\S")
        self.assertRegex(out, r"(?m)^forbidden_branches\s+main, master\s+main, master\s+\S")
        self.assertRegex(out, r"(?m)^routing\s+de fábrica\s+de fábrica\s+.*edítalo en "
                              r"atta/config\.json")
        self.assertRegex(out, r"(?m)^risk_rules\s+de fábrica\s+de fábrica\s+.*edítalo en "
                              r"atta/config\.json")
        self.assertRegex(out, r"(?m)^models\s+.*usa atta models")
        self.assertNotRegex(out, r" \*( |$)")
        self.assertNotIn("distinto del valor por defecto", out)
        self.assertTrue(out.endswith("— Atta\n"), out)

    def test_marks_values_that_differ_from_the_default(self):
        self.ok("settings", "set", "max_rounds", "5")
        out = self.ok("settings")
        self.assertRegex(out, r"(?m)^max_rounds\s+5 \*\s+3\s+Rondas")
        self.assertRegex(out, r"(?m)^concurrency\s+3\s+3\s")
        self.assertIn("* distinto del valor por defecto.\n", out)


class GetTest(SettingsBase):
    def test_get_values_by_type(self):
        self.assertEqual(self.ok("settings", "get", "max_rounds"), "3\n")
        self.assertEqual(self.ok("settings", "get", "merge_method"), "merge\n")
        self.assertEqual(self.ok("settings", "get", "delivery.auto_merge_up_to"), "high\n")
        self.assertEqual(self.ok("settings", "get", "labels.queue"), "atta\n")
        self.assertEqual(self.ok("settings", "get", "forbidden_branches"),
                         '["main", "master"]\n')
        self.assertEqual(json.loads(self.ok("settings", "get", "guard.deny_commands")),
                         list(guard.DEFAULT_DENY_COMMANDS))
        self.assertEqual(json.loads(self.ok("settings", "get", "routing")),
                         config.DEFAULT_ROUTING)

    def test_get_unknown_key(self):
        self.assertEqual(self.atta("settings", "get", "foo"), (2, "", NOT_KEY + "\n"))


class SetTest(SettingsBase):
    def test_set_int(self):
        before = self.stored()
        out = self.ok("settings", "set", "max_rounds", "5")
        self.assertEqual(out, "Atta cambió max_rounds en atta/config.json: 3 → 5.\n— Atta\n")
        data = self.stored()
        self.assertEqual(data["max_rounds"], 5)
        self.assertEqual(list(data), list(before))
        self.assertEqual({k: v for k, v in data.items() if k != "max_rounds"},
                         {k: v for k, v in before.items() if k != "max_rounds"})
        self.assertEqual(self.ok("settings", "get", "max_rounds"), "5\n")

    def test_set_enum(self):
        out = self.ok("settings", "set", "merge_method", "squash")
        self.assertEqual(out, "Atta cambió merge_method en atta/config.json: merge → squash.\n"
                              "— Atta\n")
        self.assertEqual(self.stored()["merge_method"], "squash")

    def test_set_nested_enum_creates_the_section(self):
        out = self.ok("settings", "set", "delivery.auto_merge_up_to", "low")
        self.assertEqual(out, "Atta cambió delivery.auto_merge_up_to en atta/config.json: "
                              "high → low.\n— Atta\n")
        self.assertEqual(self.stored()["delivery"], {"auto_merge_up_to": "low"})
        self.assertEqual(self.ok("settings", "get", "delivery.auto_merge_up_to"), "low\n")

    def test_delivery_threshold_set_get_and_reset(self):
        for value in ("none", "medium"):
            self.ok("settings", "set", "delivery.auto_merge_up_to", value)
            self.assertEqual(self.ok("settings", "get", "delivery.auto_merge_up_to"),
                             value + "\n")
        self.ok("settings", "reset", "delivery.auto_merge_up_to")
        self.assertEqual(self.ok("settings", "get", "delivery.auto_merge_up_to"), "high\n")

    def test_needs_review_label_get_and_set(self):
        self.assertEqual(self.ok("settings", "get", "labels.needs_review"),
                         "atta:needs-review\n")
        out = self.ok("settings", "set", "labels.needs_review", "revisar")
        self.assertEqual(out, "Atta cambió labels.needs_review en atta/config.json: "
                              "atta:needs-review → revisar.\n— Atta\n")
        self.assertEqual(self.stored()["labels"]["needs_review"], "revisar")
        self.assertEqual(self.stored()["labels"]["queue"], "atta")
        self.assertEqual(self.ok("settings", "get", "labels.needs_review"), "revisar\n")

    def test_set_nested_label_keeps_the_other_labels(self):
        self.ok("settings", "set", "labels.queue", "colonia")
        self.assertEqual(self.stored()["labels"], {"queue": "colonia",
                                                   "needs_info": "atta:needs-info",
                                                   "needs_human": "atta:needs-human"})

    def test_set_list_comma_separated_and_json(self):
        out = self.ok("settings", "set", "forbidden_branches", "main, master ,production")
        self.assertEqual(out, 'Atta cambió forbidden_branches en atta/config.json: '
                              '["main", "master"] → ["main", "master", "production"].\n— Atta\n')
        self.assertEqual(self.stored()["forbidden_branches"], ["main", "master", "production"])
        self.ok("settings", "set", "forbidden_branches", '["main", "prod"]')
        self.assertEqual(self.stored()["forbidden_branches"], ["main", "prod"])

    def test_set_nested_list_under_guard(self):
        self.ok("settings", "set", "guard.deny_commands", "kubectl apply,helm install")
        self.assertEqual(self.stored()["guard"], {"deny_commands": ["kubectl apply",
                                                                   "helm install"]})

    def test_set_bool(self):
        with mock.patch.object(settings_registry, "SETTINGS",
                               settings_registry.SETTINGS + (FLAG,)):
            for text, value, shown in (("sí", True, "no → sí"), ("no", False, "sí → no"),
                                       ("true", True, "no → sí"), ("false", False, "sí → no")):
                with self.subTest(text=text):
                    out = self.ok("settings", "set", "experimental.flag", text)
                    self.assertEqual(out, f"Atta cambió experimental.flag en atta/config.json: "
                                          f"{shown}.\n— Atta\n")
                    self.assertEqual(self.stored()["experimental"], {"flag": value})
            self.assertEqual(self.ok("settings", "get", "experimental.flag"), "false\n")

    def test_set_scope_keys(self):
        out = self.ok("settings", "set", "scope.enforce", "sí")
        self.assertEqual(out, "Atta cambió scope.enforce en atta/config.json: no → sí.\n— Atta\n")
        self.ok("settings", "set", "scope.max_files", "20")
        self.assertEqual(self.stored()["scope"], {"enforce": True, "max_files": 20})
        self.assertEqual(self.ok("settings", "get", "scope.max_files"), "20\n")
        self.assertRegex(self.ok("settings"), r"(?m)^scope\.enforce\s+sí \*\s+no\s+\S")

    def test_set_keeps_unknown_keys_and_models(self):
        data = self.stored()
        data["mi_clave"] = {"x": 1}
        data["models"] = {"claude-code": {"roles": {"soldado": "opus"}}}
        self.write(data)
        self.ok("settings", "set", "concurrency", "1")
        new = self.stored()
        self.assertEqual(list(new), list(data))
        self.assertEqual(new, dict(data, concurrency=1))

    def test_same_value_changes_nothing(self):
        before = self.cfg_path.read_bytes()
        self.assertEqual(self.ok("settings", "set", "max_rounds", "3"),
                         "Atta: max_rounds ya vale 3; no cambié nada.\n— Atta\n")
        self.assertEqual(self.cfg_path.read_bytes(), before)

    def test_set_without_config_file_starts_from_the_packaged_one(self):
        self.cfg_path.unlink()
        self.ok("settings", "set", "max_rounds", "4")
        packaged = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(self.stored(), dict(packaged, max_rounds=4))


class RefusalTest(SettingsBase):
    def assertRefused(self, argv, message):
        before = self.cfg_path.read_bytes()
        self.assertEqual(self.atta(*argv), (2, "", message + " No cambié nada.\n"))
        self.assertEqual(self.cfg_path.read_bytes(), before)

    def test_invalid_int(self):
        for text in ("0", "x", "-1", "2.5"):
            with self.subTest(text=text):
                self.assertRefused(("settings", "set", "max_rounds", text),
                                   f"max_rounds debe ser un entero mayor que 0, no «{text}».")

    def test_enum_outside_the_set(self):
        self.assertRefused(("settings", "set", "merge_method", "fast"),
                           "merge_method debe ser merge, squash o rebase, no «fast».")
        self.assertRefused(("settings", "set", "delivery.auto_merge_up_to", "all"),
                           "delivery.auto_merge_up_to debe ser none, low, medium o high, "
                           "no «all».")

    def test_config_rules_still_apply(self):
        self.assertRefused(("settings", "set", "integration_branch", "main"),
                           "config.json: integration_branch «main» está en forbidden_branches; "
                           "Atta nunca integra en una rama prohibida.")
        self.assertRefused(("settings", "set", "branch_prefix", "atta"),
                           "config.json: branch_prefix debe terminar en «/».")
        self.assertRefused(("settings", "set", "forbidden_branches", ""),
                           "config.json: forbidden_branches no puede estar vacía; "
                           "Atta necesita al menos una rama prohibida (p. ej. main).")

    def test_invalid_list_and_text(self):
        self.assertRefused(("settings", "set", "forbidden_branches", "[1]"),
                           "forbidden_branches debe ser una lista de textos (a,b o "
                           "[\"a\", \"b\"]), no «[1]».")
        self.assertRefused(("settings", "set", "labels.queue", " "),
                           "labels.queue no puede estar vacío.")

    def test_invalid_bool(self):
        with mock.patch.object(settings_registry, "SETTINGS",
                               settings_registry.SETTINGS + (FLAG,)):
            self.assertRefused(("settings", "set", "experimental.flag", "quizá"),
                               "experimental.flag debe ser sí o no (o true/false), no «quizá».")

    def test_read_only_keys(self):
        hint = {"prices_usd_per_mtok": "edítalo en atta/config.json",
                "routing": "edítalo en atta/config.json",
                "risk_rules": "edítalo en atta/config.json", "models": "usa atta models"}
        for key, how in hint.items():
            with self.subTest(key=key):
                msg = f"La clave «{key}» es de solo lectura en atta settings: {how}."
                self.assertRefused(("settings", "set", key, "{}"), msg)
                self.assertRefused(("settings", "reset", key), msg)

    def test_unknown_key(self):
        self.assertRefused(("settings", "set", "foo", "1"), NOT_KEY)
        self.assertRefused(("settings", "reset", "foo"), NOT_KEY)


class ResetTest(SettingsBase):
    def test_reset_restores_the_default(self):
        self.ok("settings", "set", "max_rounds", "5")
        out = self.ok("settings", "reset", "max_rounds")
        self.assertEqual(out, "Atta restableció max_rounds en atta/config.json: 5 → 3 "
                              "(por defecto).\n— Atta\n")
        self.assertNotIn("max_rounds", self.stored())
        self.assertEqual(self.ok("settings", "get", "max_rounds"), "3\n")

    def test_reset_nested_label_keeps_the_others(self):
        self.ok("settings", "set", "labels.queue", "colonia")
        self.ok("settings", "reset", "labels.queue")
        self.assertEqual(self.stored()["labels"], {"needs_info": "atta:needs-info",
                                                   "needs_human": "atta:needs-human"})
        self.assertEqual(self.ok("settings", "get", "labels.queue"), "atta\n")

    def test_reset_prunes_an_empty_section(self):
        self.ok("settings", "set", "delivery.auto_merge_up_to", "none")
        out = self.ok("settings", "reset", "delivery.auto_merge_up_to")
        self.assertEqual(out, "Atta restableció delivery.auto_merge_up_to en atta/config.json: "
                              "none → high (por defecto).\n— Atta\n")
        self.assertNotIn("delivery", self.stored())
        self.ok("settings", "set", "guard.deny_commands", "kubectl apply")
        self.ok("settings", "reset", "guard.deny_commands")
        self.assertNotIn("guard", self.stored())

    def test_reset_of_a_default_key_changes_nothing(self):
        before = self.cfg_path.read_bytes()
        self.assertEqual(self.ok("settings", "reset", "delivery.auto_merge_up_to"),
                         "Atta: delivery.auto_merge_up_to ya usa el valor por defecto (high); "
                         "no cambié nada.\n— Atta\n")
        self.assertEqual(self.cfg_path.read_bytes(), before)


class HelpTest(SettingsBase):
    def test_help_mentions_actions(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["settings", "--help"]), 0)
        for word in ("get", "set", "reset"):
            self.assertTrue(re.search(rf"\b{word}\b", out.getvalue()), word)


class JsonStateTest(SettingsBase):
    """`atta settings --json` (F2.y T2): read-only state for the /atta:config menus."""

    def state(self):
        out = self.ok("settings", "--json")
        data = json.loads(out)
        self.assertEqual(out, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
                         + "\n")
        return data, {item["key"]: item for item in data["settings"]}

    def test_every_key_with_value_default_type_and_description(self):
        data, by = self.state()
        self.assertEqual(sorted(data), ["root", "settings"])
        self.assertEqual(data["root"], str(self.root))
        self.assertEqual([i["key"] for i in data["settings"]], list(KEYS))
        for item in data["settings"]:
            self.assertEqual(sorted(item), ["changed", "choices", "default", "description",
                                            "editable", "how", "key", "type", "value"])
            self.assertTrue(item["description"])
            self.assertIs(item["changed"], False)
        self.assertEqual(by["max_rounds"], {
            "changed": False, "choices": None, "default": 3, "value": 3, "editable": True,
            "how": None, "key": "max_rounds", "type": "int",
            "description": "Rondas máximas del Filtro y de revisión por job."})
        self.assertEqual(by["merge_method"]["choices"], ["merge", "squash", "rebase"])
        self.assertEqual(by["merge_method"]["type"], "enum")
        self.assertEqual(by["scope.enforce"]["type"], "bool")
        self.assertEqual(by["labels.queue"]["value"], "atta")
        self.assertEqual(by["models"]["editable"], False)
        self.assertEqual(by["models"]["how"], "usa atta models")
        self.assertEqual(by["routing"]["type"], "summary")

    def test_changed_values_are_marked(self):
        self.ok("settings", "set", "max_rounds", "5")
        self.ok("settings", "set", "scope.enforce", "sí")
        _, by = self.state()
        self.assertEqual((by["max_rounds"]["value"], by["max_rounds"]["default"],
                          by["max_rounds"]["changed"]), (5, 3, True))
        self.assertEqual((by["scope.enforce"]["value"], by["scope.enforce"]["changed"]),
                         (True, True))
        self.assertIs(by["concurrency"]["changed"], False)

    def test_is_read_only(self):
        before = self.cfg_path.read_bytes()
        self.state()
        self.assertEqual(self.cfg_path.read_bytes(), before)

    def test_json_with_an_action_is_refused_without_writing(self):
        before = self.cfg_path.read_bytes()
        code, out, err = self.atta("settings", "--json", "set", "max_rounds", "9")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "--json solo se usa sin acción (atta settings --json). "
                              "No cambié nada.\n")
        self.assertEqual(self.cfg_path.read_bytes(), before)

    def test_help_documents_json(self):
        code, out, err = self.atta("settings", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--json", out)
        self.assertIn("solo lectura", out)

    def test_text_output_is_unchanged(self):
        # PRESERVE: the table and `get` keep their text with the new flag defined.
        out = self.ok("settings")
        self.assertTrue(out.startswith(f"Configuración de Atta en {self.root} "
                                       "(atta/config.json):\nCLAVE"))
        self.assertTrue(out.endswith("Consulta con `atta settings get <clave>`; cambia con "
                                     "`atta settings set <clave> <valor>` o `atta settings "
                                     "reset <clave>`.\n— Atta\n"))
        self.assertNotIn('"key"', out)
        self.assertEqual(self.ok("settings", "get", "max_rounds"), "3\n")


if __name__ == "__main__":
    unittest.main()
