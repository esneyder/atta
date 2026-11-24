"""Riesgo por reglas (G3, F3 T2: S4, S2, S10): the diff decides a floor for the job's risk."""

import copy
import json
import subprocess
import sys
import unittest

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import config, events, gate, risk, store
from atta.spine import next as nxt

GIT_ID = ["-c", "user.name=Atta Test", "-c", "user.email=atta@example.invalid",
          "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
PY = f'"{sys.executable}"'
OK = f'{PY} -c "import sys; sys.exit(0)"'
FAIL = f'{PY} -c "import sys; sys.exit(1)"'
MODELS = {"T0": "haiku", "T1": "haiku", "T2": "sonnet", "T3": "opus"}

APP = ("def export_csv(rows):\n    return rows\n\n\ndef _helper():\n    return 1\n")


def git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), *GIT_ID, *args], capture_output=True,
                         text=True, check=True)
    return out.stdout.strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class RepoBase(TempDirMixin, unittest.TestCase):
    """A repo with a base commit; each test commits a change and classifies base...HEAD."""

    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        write(self.repo / "README.md", "# Proyecto\n")
        write(self.repo / "src" / "app.py", APP)
        write(self.repo / "src" / "cli.py", "parser.add_argument('--format')\n")
        write(self.repo / "tests" / "test_app.py", "def test_app():\n    assert True\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def change(self, files: dict, deleted=()):
        for rel, text in files.items():
            write(self.repo / rel, text)
        for rel in deleted:
            (self.repo / rel).unlink()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "cambio")

    def classify(self, rules=None):
        return risk.classify(self.repo, self.base, rules or risk.DEFAULT_RULES)

    def with_test(self, files: dict) -> dict:
        return {**files, "tests/test_app.py": "def test_app():\n    assert 1 == 1\n"}


class CategoryTest(RepoBase):
    def assertCategory(self, found, category, level, path):
        self.assertIn(category, found["categories"])
        self.assertEqual(found["risk"], level)
        label = risk.CATEGORY_ES[category]
        self.assertTrue(any(r.startswith(f"{path}: {label}") for r in found["reasons"]),
                        found["reasons"])

    def test_docs_only_diff_triggers_nothing(self):
        self.change({"README.md": "# Proyecto\n\nasyncio, password, DROP TABLE.\n",
                     "docs/guia.md": "Usa threading y migrations.\n"})
        self.assertEqual(self.classify(), {"risk": "low", "categories": [], "reasons": []})

    def test_data_migration_path(self):
        self.change({"migrations/0003.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
        found = self.classify()
        self.assertEqual(found["categories"], ["data"])
        self.assertCategory(found, "data", "high", "migrations/0003.sql")

    def test_data_seed_that_deletes(self):
        self.change(self.with_test({"src/seed.py": "db.run('DELETE FROM users')\n"}))
        self.assertCategory(self.classify(), "data", "high", "src/seed.py")

    def test_security_path_and_content(self):
        self.change(self.with_test({"src/auth/login.py": "def login():\n    return 1\n"}))
        self.assertCategory(self.classify(), "security", "high", "src/auth/login.py")
        self.change(self.with_test({"src/app.py": APP + "import hashlib\n"}))
        self.assertCategory(self.classify(), "security", "high", "src/app.py")

    def test_security_pem_file(self):
        self.change({"certs/server.pem": "-----BEGIN-----\n"})
        self.assertCategory(self.classify(), "security", "high", "certs/server.pem")

    def test_contracts_removed_or_renamed_public_function(self):
        self.change(self.with_test({"src/app.py": APP.replace("export_csv", "export_rows")}))
        found = self.classify()
        self.assertEqual(found["categories"], ["contracts"])
        self.assertCategory(found, "contracts", "high", "src/app.py")
        self.assertIn("export_csv", " ".join(found["reasons"]))

    def test_contracts_not_for_a_changed_signature_or_private_function(self):
        text = APP.replace("export_csv(rows)", "export_csv(rows, sep)").replace("_helper",
                                                                                 "_aux")
        self.change(self.with_test({"src/app.py": text}))
        self.assertEqual(self.classify()["categories"], [])

    def test_contracts_removed_cli_flag(self):
        self.change(self.with_test({"src/cli.py": "parser.add_argument('--output')\n"}))
        found = self.classify()
        self.assertCategory(found, "contracts", "high", "src/cli.py")
        self.assertIn("--format", " ".join(found["reasons"]))

    def test_contracts_public_api_route_file(self):
        self.change(self.with_test({"src/api/users.py": "ROUTES = []\n"}))
        self.assertCategory(self.classify(), "contracts", "high", "src/api/users.py")

    def test_concurrency_content(self):
        self.change(self.with_test({"src/app.py": APP + "import threading\nlock = threading.Lock()\n"}))
        found = self.classify()
        self.assertEqual(found["categories"], ["concurrency"])
        self.assertCategory(found, "concurrency", "high", "src/app.py")

    def test_concurrency_scheduler_path(self):
        self.change(self.with_test({"src/scheduler.py": "JOBS = []\n"}))
        self.assertCategory(self.classify(), "concurrency", "high", "src/scheduler.py")

    def test_delivery_is_medium(self):
        for rel in ("Dockerfile", "requirements.txt", ".github/workflows/ci.yml",
                    "package-lock.json"):
            with self.subTest(rel=rel):
                self.change({rel: f"cambio {rel}\n"})
                found = self.classify()
                self.assertEqual(found["categories"], ["delivery"])
                self.assertCategory(found, "delivery", "medium", rel)

    def test_untested_source_without_tests(self):
        self.change({"src/app.py": APP + "X = 1\n"})
        found = self.classify()
        self.assertEqual(found["categories"], ["untested"])
        self.assertCategory(found, "untested", "medium", "src/app.py")

    def test_untested_not_when_a_test_changes(self):
        self.change(self.with_test({"src/app.py": APP + "X = 1\n"}))
        self.assertEqual(self.classify(), {"risk": "low", "categories": [], "reasons": []})

    # ---- T1 (S1): `untested` ignores blank lines, comments and Python docstrings

    QUIET = {"risk": "low", "categories": [], "reasons": []}

    def base_with(self, files: dict):
        """Move the base to a commit that also holds `files`."""
        for rel, text in files.items():
            write(self.repo / rel, text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

    def write_bytes(self, rel, data: bytes):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def assertUntested(self, found, path):
        self.assertEqual(found["categories"], ["untested"])
        self.assertEqual(found["reasons"],
                         [f"{path}: sin test — cambia código y el diff no cambia ningún test"])

    def test_untested_not_for_a_docstring_only_python_change(self):
        self.base_with({"src/doc.py": '"""Viejo."""\n\n\nclass A:\n    """Clase."""\n\n'
                                       '    def m(self):\n        return 1\n'})
        text = APP.replace("def export_csv(rows):\n",
                           'def export_csv(rows):\n    """Exporta\n\n    filas."""\n')
        self.change({"src/app.py": '"""Módulo de la app."""\n' + text,
                     "src/doc.py": '"""Nuevo."""\n\n\nclass A:\n    """Otra clase.\n    """\n'
                                   '\n    def m(self):\n        """Método."""\n        return 1\n'})
        self.assertEqual(self.classify(), self.QUIET)

    def test_untested_not_for_a_comment_only_change(self):
        base = {
            "src/app.py": APP,
            "src/web.js": "const a = 1;\nfunction f() {\n  return a;\n}\n",
            "src/main.go": "package main\n\nfunc main() {}\n",
            "src/run.sh": "#!/bin/sh\necho hola\n",
            "src/lib.c": "#include <stdio.h>\nint x = 1;\n",
            "src/app.rb": "puts 1\n",
            "src/App.java": "class App {\n  int x = 1;\n}\n",
        }
        self.base_with(base)
        changed = {
            "src/app.py": "# Cabecera.\n" + APP.replace("    return rows\n",
                                                      "    # devuelve\n    return rows\n"),
            "src/web.js": "// cabecera\nconst a = 1;\n/*\n * bloque\n */\nfunction f() {\n"
                          "  /* corto */\n  return a;\n}\n",
            "src/main.go": "// Package main hace cosas.\npackage main\n\nfunc main() {}\n",
            "src/run.sh": "#!/bin/sh\n# saluda\necho hola\n",
            "src/lib.c": "/* licencia */\n#include <stdio.h>\n// x\nint x = 1;\n",
            "src/app.rb": "# imprime\nputs 1\n",
            "src/App.java": "/**\n * App.\n */\nclass App {\n  // x\n  int x = 1;\n}\n",
        }
        for rel, text in changed.items():
            with self.subTest(rel=rel):
                self.base_with(base)
                self.change({rel: text})
                self.assertEqual(self.classify(), self.QUIET)

    def test_untested_not_for_a_blank_line_only_change(self):
        self.base_with({"src/web.ts": "const a = 1;\nconst b = 2;\n"})
        self.change({"src/app.py": APP + "\n\n", "src/web.ts": "const a = 1;\n\n\nconst b = 2;\n"})
        self.assertEqual(self.classify(), self.QUIET)

    def test_untested_not_for_a_comment_only_change_with_crlf(self):
        self.write_bytes("src/win.ts", b"const a = 1;\r\nconst b = 2;\r\n")
        self.write_bytes("src/win.py", b"X = 1\r\n")
        self.base_with({})
        self.write_bytes("src/win.ts", b"const a = 1;\r\n// nota\r\n\r\nconst b = 2;\r\n")
        self.write_bytes("src/win.py", b'"""Doc."""\r\n# nota\r\nX = 1\r\n')
        self.change({})
        self.assertEqual(self.classify(), self.QUIET)

    def test_untested_still_for_a_real_code_change(self):
        self.base_with({"src/web.js": "const a = 1;\n"})
        cases = {
            "src/app.py": '"""Doc."""\n# nota\n' + APP + "X = 1\n",
            "src/web.js": "// nota\nconst a = 2;\n",
        }
        for rel, text in cases.items():
            with self.subTest(rel=rel):
                self.base_with({"src/app.py": APP, "src/web.js": "const a = 1;\n"})
                self.change({rel: text})
                self.assertUntested(self.classify(), rel)

    def test_untested_still_for_an_unparseable_python_file(self):
        self.base_with({"src/roto.py": "def f(:\n    return 1\n"})
        self.change({"src/roto.py": "# nota\ndef f(:\n    return 1\n"})
        self.assertUntested(self.classify(), "src/roto.py")

    def test_untested_still_for_meaningful_comments_and_unknown_languages(self):
        base = {"src/main.go": "package main\n", "src/run.sh": "#!/bin/sh\necho 1\n",
                "src/app.rb": "puts 'a'\n", "src/tool.py": "X = 1\n",
                "src/web.ts": "const a = 1;\n", "src/page.php": "<?php\nclass A {}\n",
                "src/a.zig": "const a = 1;\n", "src/b.sh": "echo 1\r\n"}
        cases = {
            "src/main.go": "//go:build linux\n\npackage main\n",
            "src/run.sh": "#!/bin/bash\necho 1\n",
            "src/app.rb": "# frozen_string_literal: true\nputs 'a'\n",
            "src/tool.py": "#!/usr/bin/env python3\nX = 1\n",
            "src/web.ts": "// @ts-nocheck\nconst a = 1;\n",
            "src/page.php": "<?php\n/** @ORM\\Entity */\nclass A {}\n",
            "src/a.zig": "// nota\nconst a = 1;\n",
            "src/b.sh": "echo 1\n",
        }
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        rules["source"].append("**/*.zig")
        for rel, text in cases.items():
            with self.subTest(rel=rel):
                self.base_with(base)
                self.change({rel: text})
                self.assertUntested(self.classify(rules), rel)

    def test_untested_still_for_a_mode_only_change(self):
        self.base_with({"src/run.sh": "echo 1\n"})
        git(self.repo, "update-index", "--chmod=+x", "src/run.sh")
        git(self.repo, "commit", "-q", "-m", "modo")
        self.assertUntested(self.classify(), "src/run.sh")

    def test_comment_only_change_keeps_other_categories_and_real_files(self):  # preserve
        self.change({"src/app.py": APP + "# usa hashlib\n", "src/other.py": "Y = 2\n"})
        found = self.classify()
        self.assertEqual(found["categories"], ["security", "untested"])
        self.assertEqual(found["risk"], "high")
        self.assertEqual(found["reasons"], [
            "src/app.py: seguridad — agrega «# usa hashlib»",
            "src/other.py: sin test — cambia código y el diff no cambia ningún test"])

    def test_categories_combine_and_take_the_highest(self):
        self.change({"migrations/0004.sql": "DROP TABLE x;\n", "Dockerfile": "FROM x\n",
                     "src/app.py": APP + "Y = 2\n"})
        found = self.classify()
        self.assertEqual(found["categories"], ["data", "delivery", "untested"])
        self.assertEqual(found["risk"], "high")

    def test_custom_rules_replace_the_defaults(self):
        rules = {"source": ["**/*.py"], "tests": ["tests/**"],
                 "categories": {"delivery": {"risk": "high", "paths": ["docs/**"]}}}
        self.change({"docs/guia.md": "x\n", "migrations/0003.sql": "DROP TABLE x;\n"})
        found = self.classify(rules)
        self.assertEqual(found["categories"], ["delivery"])
        self.assertEqual(found["risk"], "high")
        self.assertEqual(found["reasons"],
                         ["docs/guia.md: entrega — la ruta coincide con «docs/**»"])


class RulesConfigTest(TempDirMixin, unittest.TestCase):
    def load(self, data):
        path = self.tmp / "config.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return config.load_config(path)

    def refused(self, rules, message):
        with self.assertRaises(config.ConfigError) as ctx:
            self.load({"risk_rules": rules})
        self.assertEqual(str(ctx.exception), f"config.json: {message}")

    def test_defaults_when_missing(self):
        self.assertEqual(self.load({})["risk_rules"], risk.DEFAULT_RULES)

    def test_custom_rules_replace_whole_table(self):
        rules = {"source": [], "tests": [], "categories": {"untested": {"risk": "low"}}}
        self.assertEqual(self.load({"risk_rules": rules})["risk_rules"], rules)

    def test_malformed_rules_are_spanish_config_errors(self):
        cases = [
            ([], "risk_rules debe ser un objeto con source, tests y categories."),
            ({"tests": [], "categories": {}},
             "risk_rules.source debe ser una lista de textos."),
            ({"source": [], "tests": [], "categories": []},
             "risk_rules.categories debe ser un objeto."),
            ({"source": [], "tests": [], "categories": {"magia": {"risk": "high"}}},
             "risk_rules.categories.magia: categoría desconocida; usa data, security, "
             "contracts, concurrency, delivery o untested."),
            ({"source": [], "tests": [], "categories": {"data": {"risk": "altísimo"}}},
             "risk_rules.categories.data.risk «altísimo» no es válido; usa low, medium o high."),
            ({"source": [], "tests": [], "categories": {"data": {"risk": "high", "paths": "x"}}},
             "risk_rules.categories.data.paths debe ser una lista de textos."),
            ({"source": [], "tests": [], "categories": {"data": {"risk": "high",
                                                                 "added": ["("]}}},
             "risk_rules.categories.data.added: la expresión «(» no es válida."),
            ({"source": [], "tests": [], "categories": {"data": {"risk": "high", "foo": []}}},
             "risk_rules.categories.data: clave desconocida «foo»; usa risk, paths, added o "
             "removed."),
        ]
        for rules, message in cases:
            with self.subTest(message=message):
                self.refused(rules, message)


class EffectiveTest(unittest.TestCase):
    def job(self, intake=None, routed=None, previous=None):
        job = {"id": "12-x", "stage": "gate",
               "intake": {"risk": intake, "surfaces": [], "decision": "ACCEPT"},
               "agents": {}}
        if routed:
            job["agents"] = {"risk": routed, "surfaces": [], "spec_review": False,
                             "reviewers": []}
        if previous:
            job["risk_effective"] = previous
        return job

    def found(self, level, categories=(), reasons=()):
        return {"risk": level, "categories": list(categories), "reasons": list(reasons)}

    def test_effective_is_the_max_and_never_lowers(self):
        cfg = config.with_defaults({})
        cases = [("low", "low", None, "low"), ("low", "high", None, "high"),
                 ("high", "low", None, "high"), ("medium", "low", None, "medium"),
                 ("low", "low", "high", "high"), ("low", "medium", "high", "high")]
        for intake, rules, previous, expected in cases:
            with self.subTest(intake=intake, rules=rules, previous=previous):
                job = self.job(intake, intake, previous)
                out = risk.evaluate(job, self.found(rules), [], cfg)
                self.assertEqual(out["effective"], expected)

    def test_reroute_adds_security_reviewer(self):
        cfg = config.with_defaults({})
        job = self.job("low", "low")
        hits = [("migrations/0003.sql", "data", "la ruta coincide con «**/migrations/**»")]
        out = risk.evaluate(job, self.found("high", ["data"]), hits, cfg)
        self.assertTrue(out["escalated"])
        names = [r["reviewer"] for r in out["agents"]["reviewers"]]
        self.assertEqual(names, ["code", "security", "resilience", "rastreador"])
        self.assertEqual(out["agents"]["risk"], "high")
        self.assertEqual(out["message"],
                         "El Filtro subió el riesgo a alto: migrations/0003.sql (datos).")

    def test_no_reroute_when_not_higher(self):
        cfg = config.with_defaults({})
        out = risk.evaluate(self.job("high", "high"), self.found("medium", ["delivery"]),
                            [("Dockerfile", "delivery", "x")], cfg)
        self.assertEqual((out["effective"], out["escalated"], out["agents"], out["message"]),
                         ("high", False, None, None))


class GateRiskTest(TempDirMixin, unittest.TestCase):
    """The Filtro computes the effective risk on PASS and stores it in job.json (S4)."""

    def setUp(self):
        super().setUp()
        self.main = (self.tmp / "main").resolve()
        self.main.mkdir()
        git(self.main, "init", "-q")
        write(self.main / "atta" / "checks.json", json.dumps(
            {"checks": [{"name": "ok", "run": OK,
                         "shell": {"windows": "cmd", "default": "bash"}}]}))
        write(self.main / "src" / "app.py", APP)
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "base")
        self.base = git(self.main, "rev-parse", "HEAD")
        self.wt = self.tmp / "wt"
        git(self.main, "worktree", "add", "-q", "-b", "atta/12-x", str(self.wt), self.base)
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "low"}})
        self.jobs = store.JobStore(self.tmp / "jobs")
        job = self.jobs.create(12, "x", None, self.cfg)
        self.jid = job["id"]
        job.update(stage="gate", worktree=str(self.wt), pr=7,
                   intake={"risk": "low", "surfaces": [], "decision": "ACCEPT"},
                   agents={"risk": "low", "surfaces": [], "spec_review": False,
                           "reviewers": [{"reviewer": "code", "lenses": ["reliability"],
                                          "tier": "T1"}]})
        self.jobs.save(job)
        self.dir = self.jobs.job_dir(self.jid)
        self.round = self.dir / "round-1"

    def tearDown(self):
        subprocess.run(["git", "-C", str(self.main), "worktree", "remove", "--force",
                        str(self.wt)], capture_output=True)
        super().tearDown()

    def commit_wt(self, files):
        for rel, text in files.items():
            write(self.wt / rel, text)
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "cambio")

    def gate(self, kind="checks"):
        return gate.run_gate(self.main, self.wt, self.jobs.load(self.jid), self.round,
                             base_ref=self.base, config=self.cfg, kind=kind)

    def report(self, kind="checks"):
        return (self.round / f"{kind}.md").read_text(encoding="utf-8")

    def test_migration_escalates_reroutes_and_hands_off(self):
        self.commit_wt({"migrations/0003.sql": "ALTER TABLE users ADD COLUMN age int;\n"})
        result = self.gate()
        line = "El Filtro subió el riesgo a alto: migrations/0003.sql (datos)."
        self.assertTrue(result.passed)
        self.assertTrue(result.summary.endswith(" " + line), result.summary)
        text = self.report()
        self.assertTrue(text.startswith("GATE: PASS\n"))
        section = text[text.index("## Riesgo"):]
        for needle in ("Riesgo de la Exploradora: bajo.", "Riesgo por reglas: alto (datos).",
                       "Riesgo efectivo: alto.", line,
                       "- migrations/0003.sql: datos — la ruta coincide con «**/migrations/**»"):
            self.assertIn(needle, section)
        job = self.jobs.load(self.jid)
        self.assertEqual(job["risk_effective"], "high")
        self.assertEqual(job["risk_rules"]["categories"], ["data"])
        self.assertEqual(job["risk_rules"]["risk"], "high")
        self.assertEqual(job["intake"]["risk"], "low")  # the Exploradora's call is kept
        self.assertEqual(job["agents"]["risk"], "high")
        self.assertIn("security", [r["reviewer"] for r in job["agents"]["reviewers"]])
        evs = [e for e in events.read_events(self.dir) if e.get("event") == "risk"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["risk_from"], evs[0]["risk_to"], evs[0]["message"]),
                         ("low", "high", line))
        self.assertNotIn("from", evs[0])  # not a transition: durations stay untouched
        # `atta next` at review spawns the grown group (security joins).
        job.update(stage="review")
        self.jobs.save(job)
        action = nxt.decide(job, self.dir, self.main, self.cfg, MODELS)
        agents = [a["agent"] for a in action["agents"]]
        self.assertIn("atta-soldado", agents)
        # Delivery uses the effective risk: low intake + migration → handoff at low.
        job.update(stage="approve")
        self.jobs.save(job)
        (self.dir / "decision.md").write_text("APPROVE\n\n## Summary\n\nOK.\n",
                                              encoding="utf-8")
        action = nxt.decide(job, self.dir, self.main, self.cfg, MODELS)
        self.assertEqual(action, {"action": "run", "argv": ["atta", "handoff", self.jid]})

    def test_untested_raises_to_medium(self):
        self.commit_wt({"src/app.py": APP + "X = 1\n"})
        result = self.gate()
        self.assertTrue(result.summary.endswith(
            "El Filtro subió el riesgo a medio: src/app.py (sin test)."), result.summary)
        self.assertEqual(self.jobs.load(self.jid)["risk_effective"], "medium")

    def test_quiet_diff_keeps_the_plan(self):
        self.commit_wt({"docs/guia.md": "x\n"})
        before = self.jobs.load(self.jid)
        result = self.gate()
        self.assertRegex(result.summary, r"^El Filtro pasó: 1 check en [0-9.]+ s\.$")
        job = self.jobs.load(self.jid)
        self.assertEqual(job["agents"], before["agents"])
        self.assertEqual(job["risk_effective"], "low")
        self.assertIn("Riesgo efectivo: bajo.", self.report())
        self.assertNotIn("subió el riesgo", self.report())
        self.assertEqual([e for e in events.read_events(self.dir) if e.get("event")], [])

    def test_failing_gate_leaves_risk_untouched(self):
        write(self.main / "atta" / "checks.json", json.dumps(
            {"checks": [{"name": "rojo", "run": FAIL,
                         "shell": {"windows": "cmd", "default": "bash"}}]}))
        self.commit_wt({"migrations/0003.sql": "DROP TABLE x;\n"})
        before = self.jobs.load(self.jid)
        result = self.gate()
        self.assertFalse(result.passed)
        self.assertEqual(self.jobs.load(self.jid), before)
        self.assertNotIn("## Riesgo", self.report())

    def test_merge_checks_does_not_assess_risk(self):
        self.commit_wt({"migrations/0003.sql": "DROP TABLE x;\n"})
        before = self.jobs.load(self.jid)
        self.assertTrue(self.gate("merge-checks").passed)
        self.assertEqual(self.jobs.load(self.jid), before)
        self.assertNotIn("## Riesgo", self.report("merge-checks"))

    def test_malformed_rules_refuse_the_gate_without_report(self):
        self.commit_wt({"docs/guia.md": "x\n"})
        self.cfg = dict(self.cfg, risk_rules={"source": "x"})
        before = self.jobs.load(self.jid)
        with self.assertRaises(gate.GateError) as ctx:
            self.gate()
        self.assertEqual(str(ctx.exception),
                         "config.json: risk_rules.source debe ser una lista de textos.")
        self.assertFalse((self.round / "checks.md").exists())
        self.assertEqual(self.jobs.load(self.jid), before)

    def test_second_pass_never_lowers(self):
        self.commit_wt({"migrations/0003.sql": "DROP TABLE x;\n"})
        self.gate()
        git(self.wt, "rm", "-q", "migrations/0003.sql")
        self.commit_wt({"docs/guia.md": "x\n"})
        self.gate()
        job = self.jobs.load(self.jid)
        self.assertEqual(job["risk_effective"], "high")
        self.assertEqual(job["agents"]["risk"], "high")
        evs = [e for e in events.read_events(self.dir) if e.get("event") == "risk"]
        self.assertEqual(len(evs), 1)


if __name__ == "__main__":
    unittest.main()
