"""Filtro (S7, S1): checks from the main checkout, fail-fast, timeout, protected paths, report."""

import json
import shutil
import subprocess
import sys
import time
import unittest

import support  # noqa: F401
from support import ATTA_DIR, TempDirMixin

from spine import config, gate

GIT_ID = ["-c", "user.name=Atta Test", "-c", "user.email=atta@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
SHELL = {"windows": "cmd", "default": "bash"}
PY = f'"{sys.executable}"'


def py(code):
    """A check command that runs `code` with this Python, valid in cmd and bash."""
    return f'{PY} -c "{code}"'


OK = py("import sys; sys.exit(0)")
FAIL = py("import sys; print('rojo'); sys.exit(1)")


def git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), *GIT_ID, *args], capture_output=True,
                         text=True, check=True)
    return out.stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def checks_json(checks, timeout_s=900):
    return json.dumps({"checks": checks, "timeout_s": timeout_s})


def check(name, run):
    return {"name": name, "run": run, "shell": SHELL}


class GateTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main = self.tmp / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        write(self.main / "atta" / "checks.json", checks_json([check("ok", OK)]))
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
        # The job branch always carries work: a base equal to HEAD is refused (S7).
        write(self.wt / "src" / "feature.py", "x = 1\n")
        self.commit_wt("feature")

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.main), "worktree", "remove", "--force",
                        str(self.wt)], capture_output=True)
        super().tearDown()

    # helpers
    def set_checks(self, checks, timeout_s=900):
        write(self.main / "atta" / "checks.json", checks_json(checks, timeout_s))

    def commit_wt(self, msg="cambio"):
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", msg)
        return git(self.wt, "rev-parse", "HEAD")

    def run_gate(self, **kw):
        kw.setdefault("base_ref", self.base)
        kw.setdefault("config", self.cfg)
        return gate.run_gate(self.main, self.wt, "12-job", self.round, **kw)

    def report(self, kind="checks"):
        return (self.round / f"{kind}.md").read_text(encoding="utf-8")

    # S7: pass
    def test_pass_writes_report_and_log(self):
        self.set_checks([check("lint", OK), check("tests", OK), check("build", OK)])
        write(self.wt / "src" / "app.py", "print('chao')\n")
        sha = self.commit_wt()
        result = self.run_gate()
        self.assertTrue(result.passed)
        self.assertEqual(result.commit, sha)
        self.assertIsNone(result.failed_check)
        self.assertGreaterEqual(result.duration_s, 0)
        lines = self.report().splitlines()
        self.assertEqual(lines[0], "GATE: PASS")
        self.assertEqual(lines[1], f"COMMIT: {sha}")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "## Summary")
        text = self.report()
        self.assertRegex(text, r"El Filtro pasó: 3 checks en [0-9.]+ s\.")
        self.assertIn("## Protected paths", text)
        self.assertIn("## Checks", text)
        for name in ("lint", "tests", "build"):
            self.assertIn(f"`{name}`", text)
        self.assertTrue((self.round / "checks.log").is_file())

    def test_first_two_lines_exact_on_fail(self):
        self.set_checks([check("tests", FAIL)])
        sha = git(self.wt, "rev-parse", "HEAD")
        result = self.run_gate()
        self.assertFalse(result.passed)
        lines = self.report().splitlines()
        self.assertEqual(lines[:3], ["GATE: FAIL", f"COMMIT: {sha}", ""])
        self.assertEqual(len(sha), 40)

    # S7: fail-fast
    def test_fail_fast_second_check_never_runs(self):
        marker = self.wt / "second.txt"
        self.set_checks([
            check("tests", FAIL),
            check("second", py("open('second.txt', 'w').write('x')")),
        ])
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "tests")
        self.assertFalse(marker.exists())
        text = self.report()
        self.assertIn("El Filtro no pasó: falló `tests` (exit 1).", text)
        self.assertIn("rojo", text)
        self.assertIn("rojo", (self.round / "checks.log").read_text(encoding="utf-8"))

    def test_checks_run_in_order_with_worktree_cwd(self):
        self.set_checks([
            check("uno", py("open('orden.txt', 'a').write('1')")),
            check("dos", py("open('orden.txt', 'a').write('2')")),
        ])
        result = self.run_gate()
        self.assertTrue(result.passed)
        self.assertEqual((self.wt / "orden.txt").read_text(), "12")
        self.assertFalse((self.main / "orden.txt").exists())

    # S7: timeout
    def test_timeout_fails_and_stops(self):
        self.set_checks([check("lento", py("import time; time.sleep(30)")),
                         check("second", py("open('second.txt', 'w').write('x')"))],
                        timeout_s=1)
        start = time.monotonic()
        result = self.run_gate()
        self.assertLess(time.monotonic() - start, 20)
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "lento")
        self.assertFalse((self.wt / "second.txt").exists())
        self.assertIn("El Filtro no pasó: `lento` superó el tiempo límite (1 s).", self.report())

    # S7: protected paths
    def test_modifying_protected_file_fails_even_if_checks_pass(self):
        write(self.wt / "atta" / "config.json", '{"max_rounds": 99}\n')
        self.commit_wt()
        result = self.run_gate()
        self.assertFalse(result.passed)
        text = self.report()
        self.assertTrue(text.startswith("GATE: FAIL\n"))
        self.assertIn("rutas protegidas", text)
        self.assertIn("M `atta/config.json`", text)

    def test_adding_file_under_protected_glob_fails(self):
        write(self.wt / "atta" / "nuevo.txt", "hola\n")
        write(self.wt / ".github" / "workflows" / "nuevo.yml", "on: push\n")
        self.commit_wt()
        result = self.run_gate()
        self.assertFalse(result.passed)
        text = self.report()
        self.assertTrue(text.startswith("GATE: FAIL\n"))
        self.assertIn("A `atta/nuevo.txt`", text)
        self.assertIn("A `.github/workflows/nuevo.yml`", text)

    def test_deleting_protected_file_fails(self):
        (self.wt / ".github" / "workflows" / "ci.yml").unlink()
        self.commit_wt()
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertIn("D `.github/workflows/ci.yml`", self.report())

    def test_renaming_protected_file_out_fails(self):
        git(self.wt, "mv", "atta/config.json", "src/config.json")
        self.commit_wt()
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertIn("`atta/config.json`", self.report())

    def test_protected_glob_semantics(self):
        globs = ["atta/**", ".github/workflows/**"]
        self.assertTrue(gate.matches_protected("atta/checks.json", globs))
        self.assertTrue(gate.matches_protected("atta/spine/gate.py", globs))
        self.assertTrue(gate.matches_protected(".github/workflows/ci.yml", globs))
        self.assertFalse(gate.matches_protected("attack/x.py", globs))
        self.assertTrue(gate.matches_protected("ATTA/x.py", globs))
        self.assertFalse(gate.matches_protected(".github/CODEOWNERS", globs))
        self.assertFalse(gate.matches_protected("src/atta/x.py", globs))
        self.assertTrue(gate.matches_protected("a/b/c.py", ["**/c.py"]))
        self.assertTrue(gate.matches_protected("c.py", ["**/c.py"]))

    # S7: checks.json from the main checkout
    def test_checks_read_from_main_checkout_not_worktree(self):
        # Main checkout copy (uncommitted) fails; the worktree's committed copy (`ok`) passes.
        self.set_checks([check("principal", FAIL)])
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "principal")
        self.assertNotIn("`ok`", self.report())

    def test_shipped_placeholder_fails(self):
        shutil.copyfile(ATTA_DIR / "checks.json", self.main / "atta" / "checks.json")
        result = self.run_gate()
        self.assertFalse(result.passed)
        text = self.report()
        self.assertTrue(text.startswith("GATE: FAIL\n"))
        self.assertIn("Agrega los checks reales del proyecto", text)

    def test_empty_checks_fail(self):
        self.set_checks([])
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertIn("no tiene checks", self.report())

    # kinds and misconfiguration
    def test_merge_checks_kind_writes_its_own_files(self):
        result = self.run_gate(kind="merge-checks")
        self.assertTrue(result.passed)
        self.assertTrue(self.report("merge-checks").startswith("GATE: PASS\n"))
        self.assertTrue((self.round / "merge-checks.log").is_file())
        self.assertFalse((self.round / "checks.md").exists())

    def test_unknown_kind_raises(self):
        with self.assertRaises(gate.GateError):
            self.run_gate(kind="otra")

    def test_missing_checks_json_raises_spanish(self):
        (self.main / "atta" / "checks.json").unlink()
        with self.assertRaises(gate.GateError) as ctx:
            self.run_gate()
        self.assertIn("No se pudo leer", str(ctx.exception))

    def test_invalid_checks_json_raises(self):
        write(self.main / "atta" / "checks.json", "{no es json")
        with self.assertRaises(gate.GateError):
            self.run_gate()
        write(self.main / "atta" / "checks.json",
              json.dumps({"checks": [{"name": "x", "run": "y", "shell": {"default": "zsh9"}}]}))
        with self.assertRaises(gate.GateError):
            self.run_gate()

    def test_bad_base_ref_raises(self):
        with self.assertRaises(gate.GateError):
            self.run_gate(base_ref="no-existe-esta-ref")


class ShellCommandTests(unittest.TestCase):
    def test_run_string_is_single_argument_for_posix_shells(self):
        argv = gate.shell_command("bash", "echo 'a b' && exit 1", platform_key="linux",
                                  which=lambda name: f"/bin/{name}")
        self.assertEqual(argv, ["/bin/bash", "-c", "echo 'a b' && exit 1"])

    def test_pwsh_uses_command_flag(self):
        argv = gate.shell_command("pwsh", "npm test", platform_key="windows",
                                  which=lambda name: f"C:/bin/{name}.exe")
        self.assertEqual(argv[0], "C:/bin/pwsh.exe")
        self.assertEqual(argv[-2:], ["-Command", "npm test"])

    def test_shell_selection_by_platform(self):
        spec = {"windows": "pwsh", "default": "bash"}
        self.assertEqual(gate.select_shell(spec, "windows"), "pwsh")
        self.assertEqual(gate.select_shell(spec, "linux"), "bash")
        self.assertEqual(gate.select_shell(spec, "macos"), "bash")
        self.assertEqual(gate.select_shell("cmd", "windows"), "cmd")

    def test_missing_shell_executable_raises(self):
        with self.assertRaises(gate.GateError):
            gate.shell_command("pwsh", "x", platform_key="linux", which=lambda name: None)


if __name__ == "__main__":
    unittest.main()
