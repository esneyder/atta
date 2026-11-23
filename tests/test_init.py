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
    ["label", "create", "atta:needs-review", "--color", "1d76db",
     "--description", "Atta: PR listo para que lo revises y lo mergees", "--force"],
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
        # S13: new projects start with delivery.auto_merge_up_to = none («pr, más seguro»).
        written = json.loads((self.root / "atta" / "config.json").read_text(encoding="utf-8"))
        packaged = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        # F4 T3 (S8a): new projects start on the stage×risk matrix, so doctor stays quiet.
        self.assertEqual(written, {**packaged, "delivery": {"auto_merge_up_to": "none"},
                                   "routing_matrix": True})
        self.assertEqual(config.load_config(self.root / "atta" / "config.json")["delivery"],
                         {"auto_merge_up_to": "none"})
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
        self.assertEqual(len(dst), 12)
        self.assertIn("atta-refutador.md", dst)
        refutador = (self.root / ".claude" / "agents" / "atta-refutador.md").read_text(
            encoding="utf-8")
        self.assertIn("\nmodel: haiku\n", refutador)
        self.assertIn("atta-rastreador.md", dst)
        rastreador = (self.root / ".claude" / "agents" / "atta-rastreador.md").read_text(
            encoding="utf-8")
        self.assertIn("\nmodel: opus\n", rastreador)
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

    def test_existing_config_keeps_its_delivery_untouched(self):
        # S13: init only writes delivery=none when it creates config.json.
        cfg = self.root / "atta" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{"concurrency": 2}\n', encoding="utf-8")
        before = snapshot(cfg)
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertEqual(snapshot(cfg), before)
        self.assertIn("ya existe, la dejé igual: atta/config.json", out)
        self.assertEqual(config.load_config(cfg)["delivery"], {"auto_merge_up_to": "high"})


class ProjectModelsTest(InitBase):
    """S12: init resolves each agent's model with the project's `models` config."""

    def model_of(self, role):
        text = self.agent(f"atta-{role}")
        return [ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("model:")]

    def test_agents_follow_the_resolution_of_atta_models(self):
        code, _, err = self.run_init(templates=project.TEMPLATES_DIR)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.model_of("exploradora"), ["haiku"])
        self.assertEqual(self.model_of("guia"), ["sonnet"])
        self.assertEqual(self.model_of("vigia"), ["haiku"])  # never the Guía's model

    def test_force_keeps_the_project_overrides(self):
        cfg = self.root / "atta" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"models": {"claude-code": {
            "tiers": {"T2": "haiku"}, "roles": {"soldado": "opus"}}}}), encoding="utf-8")
        before = cfg.read_bytes()
        self.assertEqual(self.run_init(templates=project.TEMPLATES_DIR)[0], 0)
        (self.root / ".claude" / "agents" / "atta-guia.md").write_text("mío\n", encoding="utf-8")
        code, _, err = self.run_init(templates=project.TEMPLATES_DIR, force=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.model_of("guia"), ["haiku"])
        self.assertEqual(self.model_of("cortadora"), ["haiku"])
        self.assertEqual(self.model_of("soldado"), ["opus"])
        self.assertEqual(self.model_of("exploradora"), ["haiku"])
        self.assertEqual(cfg.read_bytes(), before)

    def driver_models(self):
        """`model:` lines in the frontmatter of the /atta command and skill (F4 S5)."""
        out = {}
        for rel in (".claude/commands/atta.md", ".claude/skills/atta/SKILL.md"):
            text = (self.root / rel).read_text(encoding="utf-8")
            front = text.split("---\n", 2)[1]
            self.assertNotIn("{{", text, rel)
            out[rel] = [ln.split(":", 1)[1].strip() for ln in front.splitlines()
                        if ln.startswith("model:")]
        return out

    def test_atta_command_and_skill_run_on_t0_haiku(self):
        # F4 S5 (G8): the /atta driver runs on the cheap tier T0, haiku by default.
        code, _, err = self.run_init(templates=project.TEMPLATES_DIR)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.driver_models(), {".claude/commands/atta.md": ["haiku"],
                                                ".claude/skills/atta/SKILL.md": ["haiku"]})

    def test_atta_command_follows_the_project_t0(self):
        cfg = self.root / "atta" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"models": {"claude-code": {"tiers": {"T0": "sonnet"}}}}),
                       encoding="utf-8")
        code, _, err = self.run_init(templates=project.TEMPLATES_DIR)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.driver_models(), {".claude/commands/atta.md": ["sonnet"],
                                                ".claude/skills/atta/SKILL.md": ["sonnet"]})

    def test_project_t0_leaves_the_agents_as_before(self):
        # PRESERVE: no agent defaults to T0, so moving T0 does not touch any agent file.
        self.assertEqual(self.run_init(templates=project.TEMPLATES_DIR)[0], 0)
        agents = {p.name: p.read_bytes()
                  for p in (self.root / ".claude" / "agents").glob("atta-*.md")}
        cfg = self.root / "atta" / "config.json"
        cfg.write_text(json.dumps({"models": {"claude-code": {"tiers": {"T0": "opus"}}}}),
                       encoding="utf-8")
        code, out, err = self.run_init(templates=project.TEMPLATES_DIR, force=True)
        self.assertEqual(code, 0, err)
        self.assertEqual({p.name: p.read_bytes()
                          for p in (self.root / ".claude" / "agents").glob("atta-*.md")},
                         agents)
        self.assertNotIn("agents/", out)
        self.assertIn("reemplacé: .claude/commands/atta.md", out)
        self.assertIn("reemplacé: .claude/skills/atta/SKILL.md", out)

    def test_invalid_models_config_exits_2_and_writes_nothing(self):
        cfg = self.root / "atta" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"models": {"claude-code": {"tiers": {"T2": "gpt-4"}}}}),
                       encoding="utf-8")
        before = tree(self.root)
        code, out, err = self.run_init(templates=project.TEMPLATES_DIR)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("config.json: models.claude-code.tiers.T2: «gpt-4» no es un modelo válido",
                      err)
        self.assertEqual(tree(self.root), before)


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


class CheckTest(InitBase):
    """`atta init --check` (F3 S3, G2): compares without writing; 0 al día, 1 otherwise."""

    def run_check(self, templates=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = initcmd.check(self.root, templates_dir=templates or self.tdir)
        return code, out.getvalue(), err.getvalue()

    def outdated_text(self, *entries):
        n = len(entries)
        head = (f"Atta revisó {self.root.resolve()}: {n} archivo"
                f"{' no coincide' if n == 1 else 's no coinciden'} con esta versión de Atta:")
        return "\n".join([head, *(f"  {e}" for e in entries),
                          "Ponlos al día en tu terminal con `atta init --force` (nunca toca "
                          "config.json, checks.json ni tus entradas de settings.json).",
                          "— Atta"]) + "\n"

    def test_up_to_date_exits_0_and_writes_nothing(self):
        self.assertEqual(self.run_init()[0], 0)
        before = tree(self.root)
        code, out, err = self.run_check()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"Atta revisó {self.root.resolve()}: al día (5 archivos).\n"
                              "— Atta\n")
        self.assertEqual(tree(self.root), before)

    def test_edited_agent_exits_1_and_writes_nothing(self):
        self.assertEqual(self.run_init()[0], 0)
        (self.root / ".claude" / "agents" / "atta-dos.md").write_text("mío\n", encoding="utf-8")
        before = tree(self.root)
        code, out, err = self.run_check()
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out, self.outdated_text("distinto: .claude/agents/atta-dos.md"))
        self.assertEqual(tree(self.root), before)

    def test_missing_skill_exits_1(self):
        self.assertEqual(self.run_init()[0], 0)
        (self.root / ".claude" / "skills" / "atta" / "SKILL.md").unlink()
        before = tree(self.root)
        code, out, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(out, self.outdated_text("falta: .claude/skills/atta/SKILL.md"))
        self.assertEqual(tree(self.root), before)

    def test_missing_hook_counts_as_outdated(self):
        self.assertEqual(self.run_init()[0], 0)
        (self.root / ".claude" / "settings.json").write_text('{"theme": "dark"}',
                                                             encoding="utf-8")
        code, out, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(out, self.outdated_text("distinto: .claude/settings.json"))

    def test_models_are_resolved_with_the_project_config(self):
        self.assertEqual(self.run_init()[0], 0)
        (self.root / "atta" / "config.json").write_text(json.dumps(
            {"models": {"claude-code": {"tiers": {"T2": "haiku"}}}}), encoding="utf-8")
        code, out, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(out, self.outdated_text("distinto: .claude/agents/atta-dos.md"))

    def test_empty_project_lists_everything_and_creates_nothing(self):
        code, out, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual(out, self.outdated_text(
            "falta: .claude/agents/atta-dos.md", "falta: .claude/agents/atta-tres.md",
            "falta: .claude/agents/atta-uno.md", "falta: .claude/skills/atta/SKILL.md",
            "falta: .claude/settings.json"))
        self.assertEqual(tree(self.root), {})

    def test_invalid_settings_exits_2(self):
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text("{no es json", encoding="utf-8")
        before = tree(self.root)
        code, out, err = self.run_check()
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no es JSON válido", err)
        self.assertEqual(tree(self.root), before)

    def test_driver_model_drift_is_reported(self):
        # F4 S5: a project T0 that the /atta command and skill do not follow is drift.
        packaged = project.TEMPLATES_DIR
        self.assertEqual(self.run_init(templates=packaged)[0], 0)
        (self.root / "atta" / "config.json").write_text(json.dumps(
            {"models": {"claude-code": {"tiers": {"T0": "sonnet"}}}}), encoding="utf-8")
        before = tree(self.root)
        code, out, err = self.run_check(templates=packaged)
        self.assertEqual((code, err), (1, ""))
        self.assertEqual(out, self.outdated_text("distinto: .claude/skills/atta/SKILL.md",
                                                 "distinto: .claude/commands/atta.md",
                                                 "distinto: .claude/commands/atta/config.md",
                                                 "distinto: .claude/commands/atta/models.md",
                                                 "distinto: .claude/commands/atta/profiles.md",
                                                 "distinto: .claude/commands/atta/delivery.md",
                                                 "distinto: .claude/commands/atta/rounds.md",
                                                 "distinto: .claude/commands/atta/risk.md",
                                                 "distinto: .claude/commands/atta/labels.md"))
        self.assertEqual(tree(self.root), before)

    def test_hand_edited_driver_model_is_reported(self):
        packaged = project.TEMPLATES_DIR
        self.assertEqual(self.run_init(templates=packaged)[0], 0)
        command = self.root / ".claude" / "commands" / "atta.md"
        command.write_text(command.read_text(encoding="utf-8").replace(
            "model: haiku", "model: opus"), encoding="utf-8")
        code, out, _ = self.run_check(templates=packaged)
        self.assertEqual(code, 1)
        self.assertEqual(out, self.outdated_text("distinto: .claude/commands/atta.md"))

    def test_cli_init_check_uses_packaged_templates_without_writing(self):
        from atta.spine import cli
        self.assertEqual(self.run_init(templates=project.TEMPLATES_DIR)[0], 0)
        before = tree(self.root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--project", str(self.root), "init", "--check"])
        self.assertEqual(code, 0, out.getvalue())
        # 12 agents + SKILL.md + commands/atta.md + 7 menus + settings.json hook
        self.assertIn(": al día (22 archivos).", out.getvalue())
        self.assertEqual(tree(self.root), before)


class MenuCommandsTest(InitBase):
    """F2.y T2: `/atta:config` and `/atta:models` live in .claude/commands/atta/."""

    MENUS = (".claude/commands/atta/config.md", ".claude/commands/atta/models.md",
             ".claude/commands/atta/profiles.md", ".claude/commands/atta/delivery.md",
             ".claude/commands/atta/rounds.md", ".claude/commands/atta/risk.md",
             ".claude/commands/atta/labels.md")

    def run_check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = initcmd.check(self.root, templates_dir=project.TEMPLATES_DIR)
        return code, out.getvalue(), err.getvalue()

    def init_packaged(self, **kw):
        return self.run_init(templates=project.TEMPLATES_DIR, **kw)

    def test_init_installs_the_menus_with_the_t0_model(self):
        code, out, err = self.init_packaged()
        self.assertEqual(code, 0, err)
        for rel in self.MENUS:
            path = self.root / rel
            self.assertTrue(path.is_file(), rel)
            self.assertIn(f"creé: {rel}", out)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("{{", text)
            front = "\n" + text.split("---\n", 2)[1]
            self.assertIn("\nmodel: haiku\n", front, rel)
            self.assertIn("\ndescription:", front)
        # /atta keeps working next to the namespace
        self.assertTrue((self.root / ".claude" / "commands" / "atta.md").is_file())

    def test_menus_follow_the_project_t0(self):
        cfg = self.root / "atta" / "config.json"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"models": {"claude-code": {"tiers": {"T0": "sonnet"}}}}),
                       encoding="utf-8")
        self.assertEqual(self.init_packaged()[0], 0)
        for rel in self.MENUS:
            self.assertIn("\nmodel: sonnet\n", (self.root / rel).read_text(encoding="utf-8"))

    def test_check_is_clean_after_init_and_flags_missing_and_outdated(self):
        self.assertEqual(self.init_packaged()[0], 0)
        self.assertEqual(self.run_check()[0], 0)
        (self.root / self.MENUS[0]).unlink()
        models_md = self.root / self.MENUS[1]
        models_md.write_text(models_md.read_text(encoding="utf-8") + "\nedición a mano\n",
                             encoding="utf-8")
        code, out, err = self.run_check()
        self.assertEqual((code, err), (1, ""))
        self.assertIn(f"  falta: {self.MENUS[0]}\n", out)
        self.assertIn(f"  distinto: {self.MENUS[1]}\n", out)

    def test_init_without_force_keeps_an_edited_menu_and_force_refreshes_it(self):
        self.assertEqual(self.init_packaged()[0], 0)
        path = self.root / self.MENUS[1]
        original = path.read_bytes()
        path.write_bytes(b"mio\n")
        code, out, _ = self.init_packaged()
        self.assertIn(f"ya existe, la dejé igual: {self.MENUS[1]}", out)
        self.assertEqual(path.read_bytes(), b"mio\n")
        code, out, err = self.init_packaged(force=True)
        self.assertEqual(code, 0, err)
        self.assertIn(f"reemplacé: {self.MENUS[1]}", out)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.run_check()[0], 0)


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
        self.assertFalse(args.check)
        self.assertEqual(args.runtime, "claude-code")

    def test_check_and_force_are_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                self.parser().parse_args(["init", "--check", "--force"])
        self.assertEqual(ctx.exception.code, 2)

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
