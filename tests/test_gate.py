"""Filtro (S7, S1): checks from the main checkout, fail-fast, timeout, protected paths, report."""

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock

import support  # noqa: F401
from support import ATTA_DIR, TempDirMixin

from atta.spine import config, gate

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


class GateFixture(TempDirMixin):
    def setUp(self):
        super().setUp()
        self.main = self.tmp / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        # T9: the main checkout is on the integration branch, as Atta expects.
        git(self.main, "checkout", "-q", "-b", "develop")
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

    def section(self, title, kind="checks"):
        text = self.report(kind)
        start = text.index(f"## {title}\n")
        end = text.find("\n## ", start + 1)
        return text[start:] if end < 0 else text[start:end]


class GateTests(GateFixture, unittest.TestCase):
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
        self.assertEqual(lines[:4], ["GATE: FAIL", f"COMMIT: {sha}", "KIND: code", ""])
        self.assertEqual(result.kind, "code")
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

    # T9: a missing or invalid checks.json is a config FAIL (for a person), never a GateError.
    def test_missing_checks_json_is_a_config_fail_in_spanish(self):
        (self.main / "atta" / "checks.json").unlink()
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.kind, "config")
        self.assertTrue(result.summary.startswith("El Filtro no pasó: no se pudo leer "),
                        result.summary)
        self.assertEqual(self.report().splitlines()[2], "KIND: config")

    def test_invalid_checks_json_is_a_config_fail(self):
        write(self.main / "atta" / "checks.json", "{no es json")
        result = self.run_gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertIn("checks.json no es JSON válido", result.summary)
        write(self.main / "atta" / "checks.json",
              json.dumps({"checks": [{"name": "x", "run": "y", "shell": {"default": "zsh9"}}]}))
        result = self.run_gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertIn("shell «zsh9» no soportado", result.summary)

    def test_bad_base_ref_raises(self):
        with self.assertRaises(gate.GateError):
            self.run_gate(base_ref="no-existe-esta-ref")


# A fake ast-grep: reads its behaviour from $FAKE_SG_STATE and records every call there.
FAKE_SG = r'''
import json, os, sys
path = os.environ["FAKE_SG_STATE"]
with open(path, encoding="utf-8") as fh:
    state = json.load(fh)
state.setdefault("calls", []).append({"argv": sys.argv[1:], "cwd": os.getcwd()})
with open(path, "w", encoding="utf-8") as fh:
    json.dump(state, fh)
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "--version":
    print("ast-grep 0.39.0")
    sys.exit(0)
if cmd == "test":
    print(state.get("test_out", "todas las reglas pasan"))
    sys.exit(state.get("test_exit", 0))
if cmd == "scan":
    for m in state.get("matches", []):
        print(json.dumps(m))
    sys.stdout.write(state.get("raw", ""))
    sys.stderr.write(state.get("stderr", ""))
    sys.exit(state.get("scan_exit", 0))
sys.exit(2)
'''

RULE = "id: no-print\nlanguage: python\nseverity: error\nrule:\n  pattern: print($A)\n"


def sg_match(path, line, rule="no-print", severity="error", message="no uses print"):
    return {"text": "print(x)", "file": path, "ruleId": rule, "severity": severity,
            "message": message, "range": {"start": {"line": line - 1, "column": 0},
                                          "end": {"line": line - 1, "column": 8}}}


class AstGrepFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        script = self.tmp / "fake_sg.py"
        script.write_text(FAKE_SG, encoding="utf-8")
        if os.name == "nt":
            (self.bin / "ast-grep.cmd").write_text(
                f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            exe = self.bin / "ast-grep"
            exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                           encoding="utf-8")
            exe.chmod(0o755)
        self.state = self.tmp / "sg-state.json"
        self.set_sg({})
        env = {"FAKE_SG_STATE": str(self.state),
               "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_sg(self, state):
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def sg_calls(self):
        return json.loads(self.state.read_text(encoding="utf-8")).get("calls", [])

    def add_rules(self, root=None, tests=False):
        root = root or self.main
        text = "ruleDirs:\n  - rules\n"
        if tests:
            text += "testConfigs:\n  - testDir: rule-tests\n"
        write(root / "sgconfig.yml", text)
        write(root / "rules" / "no-print.yml", RULE)


class AstGrepTests(AstGrepFixture, unittest.TestCase):
    """S7 (K4): ast-grep in the Filtro, rules only from the main checkout."""

    def test_without_sgconfig_the_step_is_skipped(self):
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("Salteado: no hay sgconfig.yml en el checkout principal.",
                      self.section("ast-grep"))
        self.assertEqual(self.sg_calls(), [])

    def test_sgconfig_with_empty_rule_dirs_is_skipped(self):
        write(self.main / "sgconfig.yml", "ruleDirs:\n  - rules\n")
        (self.main / "rules").mkdir()
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("Salteado: sgconfig.yml no tiene reglas", self.section("ast-grep"))
        self.assertEqual(self.sg_calls(), [])

    def test_rules_without_binary_fail_with_a_hint(self):
        self.add_rules()
        self.set_checks([check("marca", py("open('marca.txt', 'w').write('x')"))])
        with mock.patch.object(gate, "_which", return_value=None):
            result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "ast-grep")
        self.assertEqual(result.summary,
                         "El Filtro no pasó: hay reglas de ast-grep (sgconfig.yml) y no "
                         "encontré el binario ast-grep (ni sg) en el PATH.")
        self.assertIn("Instala ast-grep", self.section("ast-grep"))
        self.assertIn("No se corrieron: ast-grep no pasó.", self.section("Checks"))
        self.assertFalse((self.wt / "marca.txt").exists())
        self.assertTrue(self.report().startswith("GATE: FAIL\n"))
        # T9: a missing binary is for a person.
        self.assertEqual(result.kind, "config")
        self.assertEqual(self.report().splitlines()[2], "KIND: config")

    def test_rule_tests_run_first_and_their_failure_blocks(self):
        self.add_rules(tests=True)
        self.set_sg({"test_exit": 1, "test_out": "no-print: invalid case 2 no coincide"})
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "El Filtro no pasó: las reglas de ast-grep no pasan "
                                         "sus propios tests.")
        self.assertIn("no-print: invalid case 2 no coincide", self.section("ast-grep"))
        calls = self.sg_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"][0], "test")
        self.assertIn("--skip-snapshot-tests", calls[0]["argv"])
        self.assertTrue(os.path.samefile(calls[0]["cwd"], self.main))

    def test_rule_tests_pass_then_scan_runs(self):
        self.add_rules(tests=True)
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertEqual([c["argv"][0] for c in self.sg_calls()], ["test", "scan"])

    def test_no_test_configs_skips_rule_tests(self):
        self.add_rules()
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertEqual([c["argv"][0] for c in self.sg_calls()], ["scan"])

    def test_scan_only_diff_files_with_main_config_and_json_stream(self):
        self.add_rules()
        write(self.wt / "src" / "otro.py", "y = 2\n")
        (self.wt / "src" / "app.py").unlink()
        write(self.wt / "README.md", "hola\n")
        self.commit_wt()
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        (call,) = self.sg_calls()
        argv = call["argv"]
        self.assertEqual(argv[0], "scan")
        self.assertIn("--json=stream", argv)
        config_arg = argv[argv.index("--config") + 1]
        self.assertTrue(os.path.samefile(config_arg, self.main / "sgconfig.yml"))
        files = argv[argv.index("--") + 1:]
        self.assertEqual(sorted(files), ["README.md", "src/feature.py", "src/otro.py"])
        self.assertTrue(os.path.samefile(call["cwd"], self.wt))
        self.assertIn("Sin hallazgos en 3 archivos del diff.", self.section("ast-grep"))

    def test_error_match_blocks_and_is_listed(self):
        self.add_rules()
        self.set_checks([check("marca", py("open('marca.txt', 'w').write('x')"))])
        self.set_sg({"matches": [sg_match("src/feature.py", 1)], "scan_exit": 1})
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.failed_check, "ast-grep")
        self.assertEqual(result.summary, "El Filtro no pasó: ast-grep encontró 1 error "
                                         "(no-print en src/feature.py:1).")
        self.assertIn("- ✗ `src/feature.py:1` no-print (error): no uses print",
                      self.section("ast-grep"))
        self.assertFalse((self.wt / "marca.txt").exists())
        # T9: a finding is the Cortadora's to fix.
        self.assertEqual(result.kind, "code")

    def test_warnings_inform_and_never_block(self):
        self.add_rules()
        self.set_sg({"matches": [sg_match("src/feature.py", 1, rule="prefer-x",
                                          severity="warning", message="mejor x")]})
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("- ⚠ `src/feature.py:1` prefer-x (warning): mejor x",
                      self.section("ast-grep"))

    def test_exit_zero_with_an_error_match_still_blocks(self):
        self.add_rules()
        self.set_sg({"matches": [sg_match("src/feature.py", 1)], "scan_exit": 0})
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertIn("ast-grep encontró 1 error", result.summary)

    def test_nonzero_exit_without_errors_fails(self):
        self.add_rules()
        self.set_sg({"scan_exit": 2, "stderr": "Error: no pude leer la regla\n"})
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "El Filtro no pasó: ast-grep terminó con exit 2 sin "
                                         "reportar errores.")
        self.assertIn("Error: no pude leer la regla", self.section("ast-grep"))

    def test_output_that_is_not_json_fails(self):
        self.add_rules()
        self.set_sg({"raw": "esto no es json\n"})
        result = self.run_gate()
        self.assertFalse(result.passed)
        self.assertEqual(result.summary, "El Filtro no pasó: ast-grep devolvió una salida que "
                                         "no es JSON.")

    def test_branch_rules_are_ignored(self):
        # The branch brings its own sgconfig.yml and rules: the main checkout has none.
        self.add_rules(root=self.wt)
        self.commit_wt("reglas propias")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("Salteado: no hay sgconfig.yml en el checkout principal.",
                      self.section("ast-grep"))
        self.assertEqual(self.sg_calls(), [])

    def test_branch_cannot_weaken_main_rules(self):
        self.add_rules()
        write(self.wt / "sgconfig.yml", "ruleDirs: []\n")
        self.commit_wt("sin reglas")
        self.set_sg({"matches": [sg_match("src/feature.py", 1)], "scan_exit": 1})
        result = self.run_gate()
        self.assertFalse(result.passed)
        argv = self.sg_calls()[0]["argv"]
        self.assertTrue(os.path.samefile(argv[argv.index("--config") + 1],
                                         self.main / "sgconfig.yml"))

    def test_merge_checks_also_run_ast_grep(self):
        self.add_rules()
        self.set_sg({"matches": [sg_match("src/feature.py", 1)], "scan_exit": 1})
        result = self.run_gate(kind="merge-checks")
        self.assertFalse(result.passed)
        self.assertIn("ast-grep encontró 1 error", self.report("merge-checks"))


class TieredChecksTests(GateFixture, unittest.TestCase):
    """S2 (L4): `when: risk>=medium` checks are skipped when the effective risk is low."""

    def setUp(self):
        super().setUp()
        # A quiet diff: the source change comes with its test (no high-risk category).
        write(self.wt / "tests" / "test_feature.py", "def test_x():\n    assert True\n")
        self.commit_wt("test")
        self.set_checks([
            check("rapido", py("open('rapido.txt', 'w').write('x')")),
            {**check("lento", py("open('lento.txt', 'w').write('x')")), "when": "risk>=medium"},
        ])

    def job(self, risk):
        return {"id": "12-job", "intake": {"risk": risk, "surfaces": [], "decision": "ACCEPT"}}

    def gate(self, risk, kind="checks"):
        return gate.run_gate(self.main, self.wt, self.job(risk), self.round,
                             base_ref=self.base, config=self.cfg, kind=kind)

    def test_low_risk_skips_medium_checks_and_says_so(self):
        result = self.gate("low")
        self.assertTrue(result.passed, result.summary)
        self.assertTrue((self.wt / "rapido.txt").exists())
        self.assertFalse((self.wt / "lento.txt").exists())
        self.assertRegex(result.summary,
                         r"El Filtro pasó: 1 check en [0-9.]+ s \(1 salteado por riesgo bajo\)\.")
        checks = self.section("Checks")
        self.assertIn("- `lento`: salteado (riesgo bajo: corre con riesgo medio o alto).", checks)
        self.assertIn("Riesgo efectivo bajo", checks)

    def test_medium_risk_runs_everything(self):
        result = self.gate("medium")
        self.assertTrue(result.passed, result.summary)
        self.assertTrue((self.wt / "lento.txt").exists())
        self.assertRegex(result.summary, r"El Filtro pasó: 2 checks en [0-9.]+ s\.")
        self.assertNotIn("salteado", self.section("Checks"))

    def test_rules_risk_above_a_low_intake_runs_everything(self):
        write(self.wt / "migrations" / "0001.sql", "ALTER TABLE t ADD c int;\n")
        self.commit_wt("migracion")
        result = self.gate("low")
        self.assertTrue(result.passed, result.summary)
        self.assertTrue((self.wt / "lento.txt").exists())

    def test_merge_checks_always_run_everything(self):
        result = self.gate("low", kind="merge-checks")
        self.assertTrue(result.passed, result.summary)
        self.assertTrue((self.wt / "lento.txt").exists())
        self.assertRegex(result.summary, r"El Filtro pasó: 2 checks en [0-9.]+ s\.")

    def test_low_risk_with_only_medium_checks_runs_them_all(self):
        self.set_checks([{**check("lento", py("open('lento.txt', 'w').write('x')")),
                          "when": "risk>=medium"}])
        result = self.gate("low")
        self.assertTrue(result.passed, result.summary)
        self.assertTrue((self.wt / "lento.txt").exists())

    def test_invalid_when_is_a_config_error(self):
        self.set_checks([{**check("x", OK), "when": "risk>=alto"}])
        result = self.gate("low")
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertIn("checks[0].when debe ser «always» o «risk>=medium»", result.summary)
        self.assertEqual(self.report().splitlines()[2], "KIND: config")


class FreshnessTests(GateFixture, unittest.TestCase):
    """S7 (K14): the main checkout's integration branch vs origin; a warning, never a block."""

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True,
                       capture_output=True)
        git(self.main, "remote", "add", "origin", str(self.origin))
        git(self.main, "push", "-q", "origin", "develop")

    def test_up_to_date_is_noted(self):
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("El checkout principal (develop) está al día con origin/develop.",
                      self.section("Frescura"))

    def test_behind_origin_warns_and_still_passes(self):
        head = git(self.wt, "rev-parse", "HEAD")
        git(self.main, "push", "-q", "origin", f"{head}:refs/heads/develop")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("⚠ El checkout principal (develop) está atrás de origin/develop",
                      self.section("Frescura"))
        self.assertIn("Avisos en checks.md: frescura.", result.summary)

    def test_unknown_remote_commit_counts_as_behind(self):
        other = self.tmp / "other"
        subprocess.run(["git", "clone", "-q", "-b", "develop", str(self.origin), str(other)],
                       check=True, capture_output=True)
        write(other / "nuevo.txt", "x\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "otro")
        git(other, "push", "-q", "origin", "HEAD:develop")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("⚠ El checkout principal (develop) está atrás de origin/develop",
                      self.section("Frescura"))

    def test_local_ahead_is_up_to_date(self):
        head = git(self.wt, "rev-parse", "HEAD")
        git(self.main, "merge", "-q", "--ff-only", head)
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("está al día con origin/develop", self.section("Frescura"))

    def test_no_origin_is_only_noted(self):
        git(self.main, "remote", "remove", "origin")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIn("No hay remoto origin: no se revisó la frescura.", self.section("Frescura"))
        self.assertNotIn("Avisos", result.summary)

    def test_merge_checks_skip_freshness(self):
        head = git(self.wt, "rev-parse", "HEAD")
        git(self.main, "push", "-q", "origin", f"{head}:refs/heads/develop")
        result = self.run_gate(kind="merge-checks")
        self.assertTrue(result.passed, result.summary)
        self.assertNotIn("## Frescura", self.report("merge-checks"))
        self.assertNotIn("Avisos", result.summary)


BRANCH_MSG = ("El checkout principal está en «main»; Atta lee checks.json y las reglas desde "
              "develop. Corre: git switch develop")


class FailureKindTests(GateFixture, unittest.TestCase):
    """T9 (S2, S10): a FAIL is `config` (for a person) or `code` (for the Cortadora)."""

    def marker_check(self):
        return check("marca", py("open('marca.txt', 'w').write('x')"))

    def test_empty_checks_is_config_with_header_and_section(self):
        self.set_checks([])
        result = self.run_gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertEqual(result.summary, "El Filtro no pasó: atta/checks.json no tiene checks. "
                                         "Agrega los checks reales del proyecto en "
                                         "atta/checks.json.")
        lines = self.report().splitlines()
        self.assertEqual(lines[0], "GATE: FAIL")
        self.assertTrue(lines[1].startswith("COMMIT: "))
        self.assertEqual(lines[2:4], ["KIND: config", ""])
        section = self.section("Configuración")
        self.assertIn("- atta/checks.json no tiene checks.", section)
        self.assertIn(gate.CONFIG_NOTE, section)
        self.assertIn("No hay checks configurados.", self.section("Checks"))

    def test_main_checkout_on_another_branch_is_config_with_the_exact_message(self):
        git(self.main, "checkout", "-q", "-b", "main")
        self.set_checks([self.marker_check()])
        result = self.run_gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertEqual(result.summary,
                         "El Filtro no pasó: el checkout principal está en «main»; Atta lee "
                         "checks.json y las reglas desde develop. Corre: git switch develop")
        self.assertIn(f"- {BRANCH_MSG}", self.section("Configuración"))
        self.assertEqual(self.report().splitlines()[2], "KIND: config")
        self.assertIn("No se corrieron: la configuración del Filtro no está lista.",
                      self.section("Checks"))
        self.assertFalse((self.wt / "marca.txt").exists())

    def test_wrong_branch_is_listed_before_an_empty_checks_json(self):
        git(self.main, "checkout", "-q", "-b", "main")
        self.set_checks([])
        result = self.run_gate()
        self.assertIn("el checkout principal está en «main»", result.summary)
        section = self.section("Configuración")
        self.assertLess(section.index(BRANCH_MSG), section.index("atta/checks.json no tiene"))

    def test_detached_main_checkout_is_config(self):
        git(self.main, "checkout", "-q", "--detach")
        result = self.run_gate()
        self.assertEqual(result.kind, "config")
        self.assertIn("el checkout principal está en «HEAD suelto»", result.summary)

    def test_without_the_integration_branch_the_branch_is_not_checked(self):
        git(self.main, "checkout", "-q", "-b", "otra")
        git(self.main, "branch", "-q", "-D", "develop")
        result = self.run_gate()
        self.assertTrue(result.passed, result.summary)
        self.assertIsNone(result.kind)
        self.assertNotIn("KIND:", self.report())

    def test_origin_integration_branch_alone_is_enough_to_check(self):
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True,
                       capture_output=True)
        git(self.main, "remote", "add", "origin", str(origin))
        git(self.main, "push", "-q", "origin", "develop")
        git(self.main, "fetch", "-q", "origin")
        git(self.main, "checkout", "-q", "-b", "main")
        git(self.main, "branch", "-q", "-D", "develop")
        result = self.run_gate()
        self.assertEqual(result.kind, "config")
        self.assertIn(BRANCH_MSG, self.report())

    def test_missing_shell_is_config_and_runs_nothing(self):
        self.set_checks([self.marker_check()])
        shell = gate.select_shell(SHELL)
        with mock.patch.object(gate, "_find_shell", return_value=None):
            result = self.run_gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertEqual(result.summary, f"El Filtro no pasó: no se encontró el shell "
                                         f"«{shell}» en el PATH de este equipo (check «marca»).")
        self.assertFalse((self.wt / "marca.txt").exists())

    def test_config_comes_before_protected_paths(self):
        write(self.wt / "atta" / "config.json", '{"max_rounds": 99}\n')
        self.commit_wt()
        self.set_checks([])
        result = self.run_gate()
        self.assertEqual(result.kind, "config")
        self.assertIn("M `atta/config.json`", self.section("Protected paths"))

    def test_code_failures(self):
        self.set_checks([check("tests", FAIL)])
        self.assertEqual(self.run_gate().kind, "code")
        self.set_checks([check("ok", OK)])
        write(self.wt / "sucio.txt", "x\n")
        self.assertEqual(self.run_gate().kind, "code")
        self.assertEqual(self.report().splitlines()[2], "KIND: code")
        (self.wt / "sucio.txt").unlink()
        write(self.wt / "atta" / "config.json", '{"max_rounds": 99}\n')
        self.commit_wt()
        self.assertEqual(self.run_gate().kind, "code")

    def test_report_kind_reads_the_header_and_legacy_fail_is_code(self):
        path = self.tmp / "checks.md"
        cases = [("GATE: FAIL\nCOMMIT: abc\nKIND: config\n\n## Summary\n", "config"),
                 ("GATE: FAIL\nCOMMIT: abc\nKIND: code\n", "code"),
                 ("GATE: FAIL\nCOMMIT: abc\n\n## Summary\n", "code"),
                 ("GATE: FAIL\n", "code"),
                 ("GATE: PASS\nCOMMIT: abc\n", None)]
        for text, want in cases:
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                self.assertEqual(gate.report_kind(path), want)
        self.assertIsNone(gate.report_kind(self.tmp / "no-existe.md"))

    def test_config_problems_is_empty_when_ready_and_lists_what_breaks(self):
        self.assertEqual(gate.config_problems(self.main, self.cfg), [])
        self.set_checks([])
        self.assertEqual(gate.config_problems(self.main, self.cfg),
                         ["atta/checks.json no tiene checks. Agrega los checks reales del "
                          "proyecto en atta/checks.json."])
        self.set_checks([check("ok", OK)])
        git(self.main, "checkout", "-q", "-b", "main")
        self.assertEqual(gate.config_problems(self.main, self.cfg), [BRANCH_MSG])
        git(self.main, "checkout", "-q", "develop")
        write(self.main / "sgconfig.yml", "ruleDirs:\n  - rules\n")
        write(self.main / "rules" / "r.yml", "id: r\n")
        with mock.patch.object(gate, "_which", return_value=None):
            problems = gate.config_problems(self.main, self.cfg)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("Hay reglas de ast-grep (sgconfig.yml) y no "
                                               "encontré el binario"), problems)

    def test_job_json_records_a_config_stop_and_any_other_outcome_clears_it(self):
        from atta.spine import store
        jobs = store.JobStore(self.tmp / "jobs")
        job = jobs.create(12, "job", None, self.cfg)
        self.round = jobs.job_dir(job["id"]) / "round-1"
        self.set_checks([])
        result = gate.run_gate(self.main, self.wt, job["id"], self.round, base_ref=self.base,
                               config=self.cfg)
        self.assertEqual(result.kind, "config")
        self.assertEqual(jobs.load(job["id"])["gate_failure"], {
            "kind": "config", "reason": "atta/checks.json no tiene checks. Agrega los checks "
                                        "reales del proyecto en atta/checks.json."})
        self.set_checks([check("tests", FAIL)])
        gate.run_gate(self.main, self.wt, job["id"], self.round, base_ref=self.base,
                      config=self.cfg)
        self.assertNotIn("gate_failure", jobs.load(job["id"]))
        # merge-checks never touches it.
        self.set_checks([])
        gate.run_gate(self.main, self.wt, job["id"], self.round, base_ref=self.base,
                      config=self.cfg, kind="merge-checks")
        self.assertNotIn("gate_failure", jobs.load(job["id"]))


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
