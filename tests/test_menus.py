"""The `/atta:*` menu templates (F2.y T2): they only instruct single plain `atta` commands."""

import re
import unittest
from pathlib import Path

import support  # noqa: F401

from atta.spine import guard, project

COMMANDS = project.TEMPLATES_DIR / "claude" / "commands"
MENUS = ("config", "models")
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

    def test_hub_names_the_areas_and_points_to_models(self):
        text = self.read("config")
        for area in ("Modelos", "Perfiles", "Entrega", "Rondas", "Riesgo", "Labels"):
            self.assertIn(area, text)
        self.assertIn("/atta:models", text)
        self.assertIn("aún no está listo", text)


if __name__ == "__main__":
    unittest.main()
