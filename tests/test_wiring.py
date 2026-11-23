"""Every F2 command is reachable from the atta CLI."""

import contextlib
import io
import unittest

import support  # noqa: F401  (adds the repo root to sys.path)
from atta.spine import cli

F2_COMMANDS = ("init", "next", "save-artifact", "guard", "queue", "start", "refresh",
               "worktree", "publish", "comment", "label", "merge", "models",
               "settings", "doctor", "memory")


class WiringTests(unittest.TestCase):
    def test_help_lists_every_f2_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        for name in F2_COMMANDS:
            with self.subTest(command=name):
                self.assertIn(name, out.getvalue())

    def test_each_f2_command_has_help(self):
        for name in F2_COMMANDS:
            with self.subTest(command=name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main([name, "--help"])
                self.assertEqual(code, 0)
                self.assertIn(f"atta {name}", out.getvalue())


    def test_help_lists_model_failed(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("model-failed", out.getvalue())

    def test_memory_lists_lessons_through_the_cli(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "atta").mkdir()
            (Path(tmp) / "atta" / "config.json").write_text("{}\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--project", tmp, "memory"])
        self.assertEqual((code, out.getvalue()),
                         (0, "Todavía no hay lecciones en atta/memory/lessons.md.\n"))


if __name__ == "__main__":
    unittest.main()
