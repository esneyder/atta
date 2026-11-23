"""The `/atta:*` menu templates (F2.y T2): they only instruct single plain `atta` commands."""

import re
import unittest
from pathlib import Path

import support  # noqa: F401

from atta.spine import guard, project, settings_registry

COMMANDS = project.TEMPLATES_DIR / "claude" / "commands"
NEW_MENUS = ("delivery", "rounds", "risk", "labels")  # F2.y T4/T5
MENUS = ("config", "models", "profiles") + NEW_MENUS
FENCE = re.compile(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", re.M | re.S)


def commands_in(text: str) -> list[str]:
    """Every non-empty line of every fenced code block of `text`."""
    return [line.strip() for block in FENCE.findall(text) for line in block.splitlines()
            if line.strip()]


class MenuTemplatesTest(unittest.TestCase):
    def read(self, name):
        return (COMMANDS / "atta" / f"{name}.md").read_text(encoding="utf-8")

    def test_frontmatter_has_description_and_the_driver_model(self):
        for name in MENUS:
            with self.subTest(name=name):
                text = self.read(name)
                self.assertTrue(text.startswith("---\n"))
                front = "\n" + text.split("---\n", 2)[1]
                self.assertIn("\ndescription: ", front)
                self.assertIn("\nmodel: {{model:T0}}\n", front)

    def test_every_menu_makes_the_selector_mandatory(self):  # sandbox: «no me deja interactuar»
        for name in MENUS:
            with self.subTest(menu=name):
                text = self.read(name)
                head = " ".join(text.split("## ", 2)[1].split())  # first section, one line
                self.assertTrue(head.startswith("Interacción obligatoria"), head[:60])
                for phrase in ("herramienta `AskUserQuestion`",
                               "Nunca termines tu respuesta con una pregunta escrita",
                               "Cada vez que te invoquen empiezas de cero"):
                    self.assertIn(phrase, head)

    def test_atta_command_still_exists_next_to_the_namespace(self):
        self.assertTrue((COMMANDS / "atta.md").is_file())

    def test_every_instructed_command_is_a_single_plain_atta_invocation(self):
        root = Path(project.TEMPLATES_DIR).resolve()
        for name in MENUS:
            lines = commands_in(self.read(name))
            if name == "models":
                self.assertGreaterEqual(len(lines), 6)
            for line in lines:
                with self.subTest(name=name, line=line):
                    self.assertRegex(line, r"^atta ")
                    hook = {"session_id": "s", "hook_event_name": "PreToolUse",
                            "cwd": str(root), "tool_name": "Bash",
                            "tool_input": {"command": line}, "permission_mode": "default"}
                    got = guard.decide(hook, root=root)
                    # allowed (read-only) or asked of the person; never denied for its shape
                    self.assertIn(got[0] if got else None, (None, "ask"), got)

    def test_models_menu_runs_the_json_and_every_change_form(self):
        lines = commands_in(self.read("models"))
        self.assertIn("atta models --json", lines)
        for form in ("atta models set T2 haiku", "atta models set --role soldado opus",
                     "atta models unset --role soldado", "atta models fallback T3 sonnet haiku",
                     "atta models fallback T3 --clear", "atta models reset"):
            self.assertIn(form, lines)

    def test_menus_never_edit_config_or_chain_commands(self):
        text = self.read("models")
        self.assertIn("AskUserQuestion", text)
        self.assertIn("atta/config.json", text)
        self.assertIn("Nunca edites `atta/config.json`", text)
        self.assertIn("UN comando", text)

    def test_hub_names_the_areas_and_points_every_one_to_its_menu(self):
        text = self.read("config")
        for area in ("Modelos", "Perfiles", "Entrega", "Rondas", "Riesgo", "Labels"):
            self.assertIn(area, text)
        for menu in ("models", "profiles") + NEW_MENUS:
            self.assertIn(f"`/atta:{menu}`", text)
        self.assertNotIn("aún no está listo", text)
        self.assertNotIn("todavía no está construido", text)
        # AskUserQuestion takes 4 options: the hub offers four and leaves the rest to «Other»
        self.assertIn("hasta 4 opciones", text)
        self.assertIn("Other", text)

    def test_profiles_menu_runs_the_json_and_every_change_form(self):
        lines = commands_in(self.read("profiles"))
        self.assertEqual(lines[0], "atta profiles --json")
        for form in ("atta profiles use equilibrado", "atta profiles use economico",
                     "atta profiles use calidad", "atta profiles clear"):
            self.assertIn(form, lines)
        self.assertEqual(len(lines), 5)

    def test_profiles_menu_rules_preview_and_delivery_warning(self):
        text = self.read("profiles")
        self.assertIn("AskUserQuestion", text)
        self.assertIn("Nunca edites `atta/config.json`", text)
        self.assertIn("UN comando", text)
        self.assertIn("(actual)", text)
        self.assertIn("efecto", text)
        self.assertIn("delivery.auto_merge_up_to", text)
        self.assertIn("ganan sobre el perfil", text)

    def test_profiles_menu_is_installed_with_the_driver_files(self):
        from atta.spine import models
        self.assertIn(".claude/commands/atta/profiles.md", models.DRIVER_FILES)

    # ---- T4/T5: /atta:delivery, /atta:rounds, /atta:risk, /atta:labels

    EXPECTED = {
        "delivery": ("atta settings set delivery.auto_merge_up_to low",
                     "atta settings reset delivery.auto_merge_up_to"),
        "rounds": ("atta settings set max_rounds 4", "atta settings set max_total_rounds 8",
                   "atta settings set concurrency 2", "atta settings reset max_rounds"),
        "risk": ("atta settings set scope.enforce true", "atta settings set scope.max_files 20",
                 "atta settings reset scope.max_files"),
        "labels": ("atta settings set labels.queue atta", 'atta settings set labels.queue "mi cola"',
                   "atta settings set labels.needs_info atta:needs-info",
                   "atta settings set labels.needs_human atta:needs-human",
                   "atta settings set labels.needs_review atta:needs-review",
                   "atta settings reset labels.queue"),
    }

    def test_new_menus_read_settings_json_and_show_each_change_form(self):
        for name, forms in self.EXPECTED.items():
            with self.subTest(menu=name):
                lines = commands_in(self.read(name))
                self.assertEqual(lines[0], "atta settings --json")
                for form in forms:
                    self.assertIn(form, lines)

    def test_new_menus_only_use_editable_registry_keys(self):
        for name in NEW_MENUS:
            used = set()
            for line in commands_in(self.read(name)):
                m = re.match(r"^atta settings (?:set|reset) (\S+)", line)
                if m:
                    used.add(m.group(1))
            with self.subTest(menu=name):
                self.assertTrue(used)
                for key in used:
                    entry = settings_registry.find(key)
                    self.assertIsNotNone(entry, key)
                    self.assertTrue(entry.editable, key)

    def test_new_menus_state_the_rules_and_the_preview(self):
        for name in NEW_MENUS:
            with self.subTest(menu=name):
                text = self.read(name)
                for phrase in ("Nunca edites `atta/config.json`", "UN comando", "(actual)",
                               "efecto", "AskUserQuestion", "atta settings --json"):
                    self.assertIn(phrase, text)

    def test_delivery_menu_has_the_merge_table_and_every_threshold(self):
        text = self.read("delivery")
        for word in ("none", "low", "medium", "high", "mergea sola", "una persona"):
            self.assertIn(word, text)
        self.assertIn("riesgo desconocido cuenta como alto", text)

    def test_risk_menu_says_risk_rules_is_not_editable_through_settings(self):
        text = self.read("risk")
        self.assertFalse(settings_registry.find("risk_rules").editable)
        self.assertIn("risk_rules", text)
        self.assertIn("solo lectura", text)
        self.assertIn("scope.enforce", text)
        self.assertIn("scope.max_files", text)

    def test_labels_menu_tells_the_truth_about_renaming_and_quoting(self):
        text = self.read("labels")
        self.assertIn("no renombra", text)
        self.assertIn("atta init", text)
        self.assertIn("terminal", text)
        self.assertIn('"mi cola"', text)

    def test_rounds_menu_names_counters_cap_concurrency_and_defaults(self):
        text = self.read("rounds")
        for word in ("gate_rounds", "review_rounds", "max_total_rounds", "concurrency",
                     "por defecto"):
            self.assertIn(word, text)

    def test_every_new_menu_is_installed_with_the_driver_files(self):
        from atta.spine import models
        for name in NEW_MENUS:
            self.assertIn(f".claude/commands/atta/{name}.md", models.DRIVER_FILES)

    def test_quoted_label_passes_the_guard_and_a_dollar_label_does_not(self):
        root = Path(project.TEMPLATES_DIR).resolve()

        def decide(command):
            hook = {"session_id": "s", "hook_event_name": "PreToolUse", "cwd": str(root),
                    "tool_name": "Bash", "tool_input": {"command": command},
                    "permission_mode": "default"}
            return guard.decide(hook, root=root)

        got = decide('atta settings set labels.queue "mi cola"')
        self.assertEqual(got[0], "ask", got)
        got = decide('atta settings set labels.queue "cola $HOME"')
        self.assertEqual(got[0], "deny", got)


if __name__ == "__main__":
    unittest.main()
