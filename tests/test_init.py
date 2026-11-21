"""`atta init` (S2, S5, S10): prepares a project without clobbering what the user already has."""

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from support import TempDirMixin, snapshot

from atta.spine import config, initcmd, project

HAS_GIT = shutil.which("git") is not None
FAKEGH = Path(__file__).resolve().parent / "fakegh" / "fakegh.py"

ATTA_HOOK = {"matcher": "Write|Edit|Bash", "hooks": [{"type": "command", "command": "atta guard"}]}
USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mio"}]}
LABEL_ARGS = [
    ["label", "create", "atta", "--color", "0e8a16",
     "--description", "Atta: issue en la cola de la colonia", "--force"],
    ["label", "create", "atta:needs-info", "--color", "fbca04",
     "--description", "Atta: falta información del autor del issue", "--force"],
    ["label", "create", "atta:needs-human", "--color", "d93f0b",
     "--description", "Atta: necesita una decisión humana", "--force"],
]


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args], cwd=str(cwd), check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)


def make_templates(base: Path, *, skill=True, settings=True) -> Path:
    tdir = base / "templates"
    agents = tdir / "claude" / "agents"
    agents.mkdir(parents=True)
    for name, tier in (("atta-uno", "T1"), ("atta-dos", "T2"), ("atta-tres", "T3")):
        (agents / f"{name}.md").write_text(
            f"---\nname: {name}\nmodel: {{{{model:{tier}}}}}\n---\n\nCuerpo de {name}.\n",
            encoding="utf-8", newline="\n")
    (agents / "otro.md").write_text("no es un agente de Atta\n", encoding="utf-8")
    (tdir / "claude" / "models.json").write_text(json.dumps(
        {"claude-code": {"T0": "haiku", "T1": "haiku", "T2": "sonnet", "T3": "opus"}}),
        encoding="utf-8")
    if skill:
        sdir = tdir / "claude" / "skills" / "atta"
        sdir.mkdir(parents=True)
        (sdir / "SKILL.md").write_text("---\nname: atta\n---\nSkill /atta.\n", encoding="utf-8")
    if settings:
        (tdir / "claude" / "settings.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [ATTA_HOOK]}}, indent=2) + "\n", encoding="utf-8")
    return tdir


def make_fake_gh(base: Path, *, fail=False) -> tuple[Path, Path]:
    """A tiny `gh` that logs its argv (one JSON list per line) and exits 0 (or 1 if `fail`)."""
    log = base / "gh-calls.jsonl"
    script = base / "fake_gh.py"
    script.write_text(
        "import json, sys\n"
        f"with open({str(log)!r}, 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')\n"
        f"sys.exit({1 if fail else 0})\n", encoding="utf-8")
    if os.name == "nt":
        exe = base / "gh.cmd"
        exe.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = base / "gh"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return exe, log


def gh_calls(log: Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def tree(root: Path) -> dict:
    """Every file under root (except .git) → bytes."""
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts or not p.is_file():
            continue
        out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


class InitBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.tdir = make_templates(self.tmp)

    def run_init(self, *, force=False, no_github=True, templates=None, environ=None):
        """Run init with exactly `environ`'s ATTA_GH/FAKEGH_STATE in os.environ (gh reads it)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {}), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            for key in ("ATTA_GH", "FAKEGH_STATE"):
                os.environ.pop(key, None)
            os.environ.update(environ or {})
            code = initcmd.run(self.root, force=force, no_github=no_github,
                               templates_dir=templates or self.tdir)
        return code, out.getvalue(), err.getvalue()

    def agent(self, name):
        return (self.root / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")


class FreshInitTest(InitBase):
    def test_fresh_init_creates_everything_with_models_resolved(self):
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertIn("model: haiku\n", self.agent("atta-uno"))
        self.assertIn("model: sonnet\n", self.agent("atta-dos"))
        self.assertIn("model: opus\n", self.agent("atta-tres"))
        for name in ("atta-uno", "atta-dos", "atta-tres"):
            self.assertNotIn("{{", self.agent(name))
        self.assertFalse((self.root / ".claude" / "agents" / "otro.md").exists())
        self.assertEqual((self.root / ".claude" / "skills" / "atta" / "SKILL.md").read_bytes(),
                         (self.tdir / "claude" / "skills" / "atta" / "SKILL.md").read_bytes())
        settings = json.loads((self.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, {"hooks": {"PreToolUse": [ATTA_HOOK]}})
        self.assertEqual((self.root / "atta" / "config.json").read_bytes(),
                         config.CONFIG_PATH.read_bytes())
        self.assertEqual((self.root / "atta" / "checks.json").read_bytes(),
                         project.CHECKS_TEMPLATE.read_bytes())
        self.assertEqual(json.loads((self.root / "atta" / "checks.json").read_text(
            encoding="utf-8"))["checks"], [])
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"),
                         "/atta/jobs/\n/.worktrees/\n")
        self.assertIn("creé: .claude/agents/atta-uno.md", out)
        self.assertIn("atta/checks.json", out)
        self.assertIn("/atta run", out)
        self.assertIn("label", out)
        self.assertTrue(out.rstrip().endswith("Atta"), out)

    def test_packaged_templates_resolve_every_agent(self):
        code, _, err = self.run_init(templates=project.TEMPLATES_DIR)
        self.assertEqual(code, 0, err)
        src = sorted(p.name for p in (project.TEMPLATES_DIR / "claude" / "agents").glob("atta-*.md"))
        dst = sorted(p.name for p in (self.root / ".claude" / "agents").glob("atta-*.md"))
        self.assertEqual(src, dst)
        self.assertEqual(len(dst), 10)
        models = set()
        for name in dst:
            text = (self.root / ".claude" / "agents" / name).read_text(encoding="utf-8")
            self.assertNotIn("{{model:", text)
            models.update(line.split(":", 1)[1].strip() for line in text.splitlines()
                          if line.startswith("model:"))
        self.assertLessEqual(models, {"haiku", "sonnet", "opus"})
        skill = project.TEMPLATES_DIR / "claude" / "skills" / "atta" / "SKILL.md"
        self.assertEqual((self.root / ".claude" / "skills" / "atta" / "SKILL.md").exists(),
                         skill.exists())
        # /atta must be a slash command even where skills are not slash-invocable
        command = self.root / ".claude" / "commands" / "atta.md"
        self.assertTrue(command.is_file())
        text = command.read_text(encoding="utf-8")
        self.assertIn("$ARGUMENTS", text)
        self.assertIn(".claude/skills/atta/SKILL.md", text)

    def test_missing_optional_templates_are_skipped(self):
        tdir = make_templates(self.tmp / "bare", skill=False, settings=False)
        code, _, err = self.run_init(templates=tdir)
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / ".claude" / "skills").exists())
        self.assertFalse((self.root / ".claude" / "settings.json").exists())
        self.assertTrue((self.root / ".claude" / "agents" / "atta-uno.md").exists())

    def test_second_run_changes_nothing(self):
        self.assertEqual(self.run_init()[0], 0)
        before = tree(self.root)
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertEqual(tree(self.root), before)
        self.assertNotIn("creé:", out)
        self.assertNotIn("ya existe", out)


class ExistingFilesTest(InitBase):
    def test_existing_agent_kept_without_force(self):
        target = self.root / ".claude" / "agents" / "atta-uno.md"
        target.parent.mkdir(parents=True)
        target.write_text("mi versión\n", encoding="utf-8")
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertEqual(target.read_text(encoding="utf-8"), "mi versión\n")
        self.assertIn("ya existe, la dejé igual: .claude/agents/atta-uno.md", out)
        self.assertIn("model: sonnet", self.agent("atta-dos"))

    def test_force_overwrites_agents_and_skill_only(self):
        agent = self.root / ".claude" / "agents" / "atta-uno.md"
        skill = self.root / ".claude" / "skills" / "atta" / "SKILL.md"
        cfg = self.root / "atta" / "config.json"
        checks = self.root / "atta" / "checks.json"
        settings = self.root / ".claude" / "settings.json"
        for p, text in ((agent, "mío\n"), (skill, "mío\n"), (cfg, '{"concurrency": 1}\n'),
                        (checks, '{"checks": [{"name": "t", "run": "x"}]}\n')):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        user_settings = {"hooks": {"PreToolUse": [ATTA_HOOK]}, "theme": "dark"}
        settings.write_text(json.dumps(user_settings), encoding="utf-8")
        before = {p: snapshot(p) for p in (cfg, checks, settings)}
        code, out, err = self.run_init(force=True)
        self.assertEqual(code, 0, err)
        self.assertIn("model: haiku", agent.read_text(encoding="utf-8"))
        self.assertEqual(skill.read_text(encoding="utf-8"), "---\nname: atta\n---\nSkill /atta.\n")
        for p, data in before.items():
            self.assertEqual(snapshot(p), data, p)
        self.assertIn("reemplacé: .claude/agents/atta-uno.md", out)
        self.assertIn("ya existe, la dejé igual: atta/config.json", out)


class SettingsMergeTest(InitBase):
    def settings(self):
        return self.root / ".claude" / "settings.json"

    def test_merge_preserves_user_hooks_and_is_idempotent(self):
        self.settings().parent.mkdir(parents=True)
        user = {"model": "sonnet",
                "hooks": {"PostToolUse": [USER_HOOK], "PreToolUse": [USER_HOOK]}}
        self.settings().write_text(json.dumps(user), encoding="utf-8")
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        merged = json.loads(self.settings().read_text(encoding="utf-8"))
        self.assertEqual(merged, {"model": "sonnet",
                                  "hooks": {"PostToolUse": [USER_HOOK],
                                            "PreToolUse": [USER_HOOK, ATTA_HOOK]}})
        self.assertEqual(list(merged), ["model", "hooks"])
        self.assertIn("actualicé: .claude/settings.json", out)
        first = snapshot(self.settings())
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(self.settings()), first)

    def test_settings_without_hooks_gets_them(self):
        self.settings().parent.mkdir(parents=True)
        self.settings().write_text('{"theme": "dark"}', encoding="utf-8")
        self.assertEqual(self.run_init()[0], 0)
        self.assertEqual(json.loads(self.settings().read_text(encoding="utf-8")),
                         {"theme": "dark", "hooks": {"PreToolUse": [ATTA_HOOK]}})

    def test_invalid_settings_exits_2_and_changes_nothing(self):
        self.settings().parent.mkdir(parents=True)
        self.settings().write_text("{no es json", encoding="utf-8")
        before = tree(self.root)
        code, out, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(".claude/settings.json", err)
        self.assertIn("no es JSON válido", err)
        self.assertIn("no cambié nada", err)
        self.assertEqual(tree(self.root), before)

    def test_settings_with_wrong_hooks_shape_exits_2(self):
        self.settings().parent.mkdir(parents=True)
        self.settings().write_text('{"hooks": {"PreToolUse": "x"}}', encoding="utf-8")
        before = tree(self.root)
        code, _, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertIn("no cambié nada", err)
        self.assertEqual(tree(self.root), before)


class GitignoreTest(InitBase):
    def test_append_keeps_content_without_duplicates(self):
        gi = self.root / ".gitignore"
        gi.write_text("node_modules/\n/atta/jobs/\n*.log", encoding="utf-8")
        self.assertEqual(self.run_init()[0], 0)
        self.assertEqual(gi.read_text(encoding="utf-8"),
                         "node_modules/\n/atta/jobs/\n*.log\n/.worktrees/\n")
        self.assertEqual(self.run_init()[0], 0)
        self.assertEqual(gi.read_text(encoding="utf-8"),
                         "node_modules/\n/atta/jobs/\n*.log\n/.worktrees/\n")

    def test_crlf_gitignore_keeps_its_line_endings(self):
        gi = self.root / ".gitignore"
        gi.write_bytes(b"dist/\r\n")
        self.assertEqual(self.run_init()[0], 0)
        self.assertEqual(gi.read_bytes(), b"dist/\r\n/atta/jobs/\r\n/.worktrees/\r\n")


@unittest.skipUnless(HAS_GIT, "git no disponible")
class GithubTest(InitBase):
    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        git("init", "-q", "--bare", str(self.origin), cwd=self.tmp)
        git("init", "-q", cwd=self.root)
        git("commit", "-q", "--allow-empty", "-m", "inicio", cwd=self.root)
        git("remote", "add", "origin", str(self.origin), cwd=self.root)
        self.gh, self.log = make_fake_gh(self.tmp)

    def env(self):
        return {"ATTA_GH": str(self.gh)}

    def test_develop_present_creates_labels_with_exact_args(self):
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        code, out, err = self.run_init(no_github=False, environ=self.env())
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(gh_calls(self.log), LABEL_ARGS)
        self.assertIn("develop", out)

    def test_missing_develop_exits_1_with_message(self):
        git("push", "-q", "origin", "HEAD:main", cwd=self.root)
        code, out, err = self.run_init(no_github=False, environ=self.env())
        self.assertEqual(code, 1)
        self.assertIn("no encontré la rama develop en origin", err)
        self.assertIn("Atta integra solo en develop", err)
        self.assertEqual(gh_calls(self.log), LABEL_ARGS)
        self.assertTrue((self.root / ".claude" / "agents" / "atta-uno.md").exists())
        self.assertTrue(out.rstrip().endswith("Atta"))

    def test_gh_failure_exits_1(self):
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        failing = self.tmp / "failing"
        failing.mkdir()
        gh, _ = make_fake_gh(failing, fail=True)
        code, _, err = self.run_init(no_github=False, environ={"ATTA_GH": str(gh)})
        self.assertEqual(code, 1)
        self.assertIn("no pude crear la label «atta»", err)

    def test_missing_gh_exits_1(self):
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        code, _, err = self.run_init(no_github=False,
                                     environ={"ATTA_GH": str(self.tmp / "no-existe-gh")})
        self.assertEqual(code, 1)
        self.assertIn("no encontré gh", err)

    def test_python_atta_gh_runs_with_this_interpreter(self):
        # L14 (1): ATTA_GH ending in .py runs like every other command (github.command()).
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        script = self.gh.parent / "fake_gh.py"
        code, out, err = self.run_init(no_github=False, environ={"ATTA_GH": str(script)})
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(gh_calls(self.log), LABEL_ARGS)
        self.assertIn("label lista: atta:needs-human", out)

    def test_atta_gh_fakegh_py_receives_the_label_calls(self):
        git("push", "-q", "origin", "HEAD:develop", cwd=self.root)
        state = self.tmp / "fakegh.json"
        state.write_text(json.dumps({"user": "operador"}), encoding="utf-8")
        code, _, err = self.run_init(no_github=False, environ={
            "ATTA_GH": str(FAKEGH), "FAKEGH_STATE": str(state)})
        calls = [c["argv"] for c in json.loads(state.read_text(encoding="utf-8"))["calls"]]
        self.assertEqual(calls, LABEL_ARGS)
        # The fake gh has no `label create`: the warning is its stderr, not «no encontré gh».
        self.assertEqual(code, 1)
        self.assertNotIn("no encontré gh", err)
        self.assertIn("Atta: no pude crear la label «atta» con gh: fakegh: comando no "
                      "soportado: label create atta", err)

    def test_no_github_skips_network(self):
        code, out, err = self.run_init(no_github=True, environ=self.env())
        self.assertEqual(code, 0, err)
        self.assertEqual(gh_calls(self.log), [])
        self.assertIn("--no-github", out)


class RegisterTest(TempDirMixin, unittest.TestCase):
    def parser(self):
        p = argparse.ArgumentParser(prog="atta")
        sub = p.add_subparsers(dest="command", required=True)
        initcmd.register(sub)
        return p

    def test_register_defaults(self):
        args = self.parser().parse_args(["init"])
        self.assertIs(args.func, initcmd.cmd_init)
        self.assertFalse(args.force)
        self.assertFalse(args.no_github)
        self.assertEqual(args.runtime, "claude-code")

    def test_register_flags(self):
        args = self.parser().parse_args(["init", "--force", "--no-github",
                                         "--runtime", "claude-code"])
        self.assertTrue(args.force)
        self.assertTrue(args.no_github)

    def test_unknown_runtime_is_a_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                self.parser().parse_args(["init", "--runtime", "otro"])
        self.assertEqual(ctx.exception.code, 2)

    def test_cmd_init_uses_args_root(self):
        root = self.tmp / "p"
        root.mkdir()
        args = self.parser().parse_args(["init", "--no-github"])
        args.root = root
        with contextlib.redirect_stdout(io.StringIO()):
            code = initcmd.cmd_init(args, config.with_defaults({}), None)
        self.assertEqual(code, 0)
        self.assertTrue((root / "atta" / "config.json").is_file())
        self.assertTrue((root / ".claude" / "agents" / "atta-reina.md").is_file())


if __name__ == "__main__":
    unittest.main()
