"""Filtro bypass probes (S7, S1, S2): ways a worker could sneak a change past El Filtro.

Each probe starts from a worktree whose branch carries a real change and asserts the gate never
passes it. Paths outside the worktree (helper scripts, pid files) live in the test temp dir.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TempDirMixin

from spine import config, gate

GIT_ID = ["-c", "user.name=Atta Test", "-c", "user.email=atta@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
SHELL = {"windows": "cmd", "default": "bash"}
PY = f'"{sys.executable}"'
OK = f'{PY} -c "import sys; sys.exit(0)"'
DIRTY_SUMMARY = "El Filtro no pasó: el worktree tiene cambios sin commitear."

# Background-child probe: the check spawns a daemon that leaves an orphan holding stdout open,
# then sleeps past the timeout. Killing the check's tree does not reach the orphan.
BG_SCRIPT = r'''
import os, subprocess, sys, tempfile, time
pidfile, role = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "check")
if role == "sleeper":
    time.sleep(20)
    sys.exit(0)
if role == "daemon":
    kw = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    p = subprocess.Popen([sys.executable, __file__, pidfile, "sleeper"], stdout=sys.stdout,
                         stderr=sys.stderr, cwd=tempfile.gettempdir(), **kw)
    with open(pidfile, "w") as fh:
        fh.write(str(p.pid))
    sys.exit(0)
subprocess.run([sys.executable, __file__, pidfile, "daemon"], stdout=sys.stdout, stderr=sys.stderr)
print("check sigue vivo", flush=True)
time.sleep(20)
'''


def git(cwd, *args, stdin=None):
    out = subprocess.run(["git", "-C", str(cwd), *GIT_ID, *args], capture_output=True,
                         text=True, check=True, input=stdin)
    return out.stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def check(name, run, shell=SHELL):
    item = {"name": name, "run": run}
    if shell is not None:
        item["shell"] = shell
    return item


class BypassFixture(TempDirMixin):
    def setUp(self):
        super().setUp()
        self.main = self.tmp / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        self.set_checks([check("ok", OK)])
        write(self.main / "atta" / "config.json", "{}\n")
        write(self.main / ".github" / "workflows" / "ci.yml", "on: push\n")
        write(self.main / "src" / "app.py", "print('hola')\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "base")
        self.base = git(self.main, "rev-parse", "HEAD")
        self.wt = self.tmp / "wt"
        git(self.main, "worktree", "add", "-q", "-b", "atta/12-job", str(self.wt), self.base)
        self.round = self.tmp / "jobs" / "12-job" / "round-1"
        self.cfg = config.with_defaults({})

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.main), "worktree", "remove", "--force",
                        str(self.wt)], capture_output=True)
        super().tearDown()

    def set_checks(self, checks, timeout_s=900):
        write(self.main / "atta" / "checks.json",
              json.dumps({"checks": checks, "timeout_s": timeout_s}))

    def commit_wt(self, msg="cambio"):
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", msg)
        return git(self.wt, "rev-parse", "HEAD")

    def run_gate(self, **kw):
        kw.setdefault("base_ref", self.base)
        kw.setdefault("config", self.cfg)
        return gate.run_gate(self.main, self.wt, "12-job", self.round, **kw)

    def report(self):
        return (self.round / "checks.md").read_text(encoding="utf-8")

    def assert_protected_fail(self, result, *paths):
        self.assertFalse(result.passed)
        text = self.report()
        self.assertTrue(text.startswith("GATE: FAIL\n"))
        self.assertIn("El Filtro no pasó: el diff toca rutas protegidas", result.summary)
        for path in paths:
            self.assertIn(f"`{path}`", text)
        self.assertIn("No se corrieron: el diff toca rutas protegidas.", text)


class ProtectedPathBypassTests(BypassFixture, unittest.TestCase):
    def test_case_variant_matches(self):
        globs = self.cfg["protected_paths"]
        for path in ("ATTA/checks.json", "Atta/Spine/gate.py", ".GitHub/Workflows/ci.yml",
                     "./atta/x", "atta\\checks.json", ".CLAUDE/settings.json", ".Pi/x"):
            self.assertTrue(gate.matches_protected(path, globs), path)
        self.assertTrue(gate.matches_protected("atta/x", ["ATTA/**"]))
        self.assertFalse(gate.matches_protected("attack/x.py", globs))

    def test_case_variant_added_path_fails(self):
        # Built with plumbing so the index keeps `ATTA/` even on case-insensitive filesystems.
        write(self.wt / "ATTA" / "nuevo.json", "{}\n")
        blob = git(self.wt, "hash-object", "-w", "--stdin", stdin="{}\n")
        git(self.wt, "update-index", "--add", "--cacheinfo", f"100644,{blob},ATTA/nuevo.json")
        git(self.wt, "commit", "-q", "-m", "mayusculas")
        result = self.run_gate()
        self.assert_protected_fail(result, "ATTA/nuevo.json")
        self.assertIn("- A `ATTA/nuevo.json`", self.report())

    def test_add_under_github_workflows_fails(self):
        write(self.wt / ".github" / "workflows" / "nuevo.yml", "on: push\n")
        self.commit_wt()
        result = self.run_gate()
        self.assert_protected_fail(result, ".github/workflows/nuevo.yml")
        self.assertIn("- A `.github/workflows/nuevo.yml`", self.report())

    def test_rename_out_of_protected_fails(self):
        git(self.wt, "mv", "atta/config.json", "src/config.json")
        self.commit_wt()
        result = self.run_gate()
        self.assert_protected_fail(result, "atta/config.json")
        self.assertIn("- R `atta/config.json` → `src/config.json`", self.report())

    def test_rename_into_protected_fails(self):
        (self.wt / ".pi").mkdir()
        git(self.wt, "mv", "src/app.py", ".pi/app.py")
        self.commit_wt()
        result = self.run_gate()
        self.assert_protected_fail(result, ".pi/app.py")
        self.assertIn("- R `src/app.py` → `.pi/app.py`", self.report())

    def test_worktree_checks_json_is_ignored(self):
        # Main checkout (uncommitted copy) says FAIL; the worktree's committed copy says OK.
        self.set_checks([check("principal", f'{PY} -c "import sys; sys.exit(3)"')])
        write(self.wt / "src" / "feature.py", "x = 1\n")
        self.commit_wt()
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "principal")
        self.assertNotIn("`ok`", self.report())


class BaseRefBypassTests(BypassFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # A protected change that a hidden diff would let through.
        write(self.wt / "atta" / "config.json", '{"max_rounds": 99}\n')
        self.head = self.commit_wt()
        self.cwd_before = set(os.listdir(os.getcwd()))

    def assert_refused(self, base_ref, message=None):
        with self.assertRaises(gate.GateError) as ctx:
            self.run_gate(base_ref=base_ref)
        if message is not None:
            self.assertEqual(str(ctx.exception), message)
        self.assertFalse((self.round / "checks.md").exists())
        for where in (self.wt, self.main, self.tmp):
            self.assertFalse((where / "pwned").exists())
        self.assertEqual(set(os.listdir(os.getcwd())), self.cwd_before)
        return str(ctx.exception)

    def test_empty_base_refused(self):
        msg = "base_ref vacío: el Filtro necesita la rama base para comparar el diff."
        self.assert_refused("", msg)
        self.assert_refused("   ", msg)

    def test_option_like_base_refused(self):
        self.assert_refused("--output=pwned",
                            "base_ref «--output=pwned» no es válido: no puede empezar con «-».")
        self.assert_refused("-", "base_ref «-» no es válido: no puede empezar con «-».")

    def test_head_base_refused(self):
        self.assert_refused("HEAD", "base_ref «HEAD» apunta al mismo commit que HEAD: "
                                    "el diff quedaría vacío.")

    def test_branch_at_head_refused(self):
        git(self.wt, "branch", "atta/1-x", "HEAD")
        self.assert_refused("atta/1-x", "base_ref «atta/1-x» apunta al mismo commit que HEAD: "
                                        "el diff quedaría vacío.")

    def test_descendant_base_refused(self):
        git(self.wt, "branch", "atta/2-y", "HEAD")
        other = self.tmp / "wt2"
        git(self.main, "worktree", "add", "-q", str(other), "atta/2-y")
        try:
            git(other, "commit", "-q", "--allow-empty", "-m", "adelante")
        finally:
            subprocess.run(["git", "-C", str(self.main), "worktree", "remove", "--force",
                            str(other)], capture_output=True)
        self.assert_refused("atta/2-y", "base_ref «atta/2-y» ya contiene HEAD: "
                                        "el diff quedaría vacío.")

    def test_unrelated_base_refused(self):
        tree = git(self.wt, "rev-parse", "HEAD^{tree}")
        orphan = git(self.wt, "commit-tree", tree, "-m", "huerfano")
        msg = self.assert_refused(orphan)
        self.assertIn("no comparte historia con HEAD", msg)

    def test_unknown_base_refused_spanish(self):
        msg = self.assert_refused("no-existe-esta-ref")
        self.assertIn("base_ref «no-existe-esta-ref» no existe como commit", msg)

    def test_valid_base_still_reports_protected_change(self):
        branch = git(self.main, "symbolic-ref", "--short", "HEAD")
        result = self.run_gate(base_ref=branch)
        self.assert_protected_fail(result, "atta/config.json")
        self.assertIn(f"Base: `{self.base}`", self.report())


class WorktreeStateBypassTests(BypassFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write(self.wt / "src" / "feature.py", "x = 1\n")
        self.commit_wt()
        self.marker = self.tmp / "ran.txt"
        self.set_checks([check("marca", f'{PY} -c "open(r\'{self.marker}\', \'w\').write(\'x\')"')])

    def assert_dirty_fail(self):
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, DIRTY_SUMMARY)
        text = self.report()
        self.assertTrue(text.startswith("GATE: FAIL\n"))
        self.assertIn(DIRTY_SUMMARY, text)
        self.assertFalse(self.marker.exists(), "los checks no deben correr")

    def test_clean_worktree_passes(self):
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertTrue(self.marker.exists())

    def test_modified_tracked_file_fails(self):
        write(self.wt / "src" / "app.py", "print('sin commit')\n")
        self.assert_dirty_fail()

    def test_untracked_file_fails(self):
        write(self.wt / "src" / "suelto.py", "y = 2\n")
        self.assert_dirty_fail()

    def test_untracked_hidden_by_config_still_fails(self):
        git(self.wt, "config", "status.showUntrackedFiles", "no")
        write(self.wt / "src" / "suelto.py", "y = 2\n")
        self.assert_dirty_fail()

    def test_skip_worktree_hidden_change_fails(self):
        git(self.wt, "update-index", "--skip-worktree", "src/app.py")
        write(self.wt / "src" / "app.py", "print('escondido')\n")
        self.assert_dirty_fail()

    def test_assume_unchanged_hidden_change_fails(self):
        git(self.wt, "update-index", "--assume-unchanged", "src/app.py")
        write(self.wt / "src" / "app.py", "print('escondido')\n")
        self.assert_dirty_fail()

    def test_replace_object_trick_fails(self):
        # Real HEAD changes a protected file; a replace ref makes HEAD look like the base tree.
        write(self.wt / "atta" / "config.json", '{"max_rounds": 99}\n')
        real = self.commit_wt()
        base_tree = git(self.wt, "rev-parse", f"{self.base}^{{tree}}")
        fake = git(self.wt, "commit-tree", base_tree, "-p", self.base, "-m", "disfraz")
        git(self.wt, "replace", real, fake)
        result = self.run_gate()
        self.assertEqual(result.commit, real)
        self.assert_protected_fail(result, "atta/config.json")


class ShellAndTimeoutBypassTests(BypassFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        write(self.wt / "src" / "feature.py", "x = 1\n")
        self.commit_wt()

    def test_default_shell_works_on_this_platform(self):
        self.set_checks([check("git", "git --version", shell=None)])
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("git version", (self.round / "checks.log").read_text(encoding="utf-8"))

    def test_default_shell_fallbacks(self):
        def which(found):
            return lambda name: f"/x/{name}" if name in found else None
        self.assertEqual(gate.select_shell(None, "windows", which=which({"pwsh", "powershell"})),
                         "pwsh")
        self.assertEqual(gate.select_shell(None, "windows", which=which({"powershell"})),
                         "powershell")
        self.assertEqual(gate.select_shell(None, "windows", which=which(set())), "cmd")
        self.assertEqual(gate.select_shell(None, "linux", which=which({"bash", "sh"})), "bash")
        self.assertEqual(gate.select_shell(None, "macos", which=which({"sh"})), "sh")
        # An explicit choice is respected, never silently swapped.
        self.assertEqual(gate.select_shell("pwsh", "windows", which=which(set())), "pwsh")

    def test_background_child_cannot_hold_timeout(self):
        script = self.tmp / "bg.py"
        script.write_text(BG_SCRIPT, encoding="utf-8")
        pidfile = self.tmp / "orphan.pid"
        self.set_checks([check("lento", f'{PY} "{script}" "{pidfile}"')], timeout_s=1)
        start = time.monotonic()
        try:
            result = self.run_gate()
            elapsed = time.monotonic() - start
        finally:
            self._kill_orphan(pidfile)
        self.assertLess(elapsed, 1 + 5)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "lento")
        self.assertIn("El Filtro no pasó: `lento` superó el tiempo límite (1 s).", self.report())

    @staticmethod
    def _kill_orphan(pidfile: Path):
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            pid = int(pidfile.read_text())
        except (OSError, ValueError):
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


class ConfigBypassTests(unittest.TestCase):
    def test_forbidden_integration_variants_rejected(self):
        for name in ("refs/heads/main", "heads/main", "origin/main", "refs/remotes/origin/main",
                     "Main", " main ", "MASTER", "origin/Master"):
            with self.subTest(name=name):
                with self.assertRaises(config.ConfigError) as ctx:
                    config.with_defaults({"integration_branch": name})
                self.assertIn("está en forbidden_branches", str(ctx.exception))

    def test_develop_variants_still_accepted(self):
        self.assertEqual(config.with_defaults({})["integration_branch"], "develop")
        config.with_defaults({"integration_branch": "release/2025"})

    def test_empty_policy_lists_and_ref_prefix_rejected(self):
        for data in ({"forbidden_branches": []}, {"protected_paths": []},
                     {"branch_prefix": "refs/heads/atta/"}, {"branch_prefix": "Refs/atta/"}):
            with self.subTest(data=data):
                with self.assertRaises(config.ConfigError):
                    config.with_defaults(data)


if __name__ == "__main__":
    unittest.main()
