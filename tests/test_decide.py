"""Reina determinista en riesgo bajo (F4 T1, S3): `atta next` approves by rule, without a model,
when every signal is clean; any missing, ambiguous or failing signal spawns the LLM Reina."""

import argparse
import contextlib
import copy
import io
import json
import os
import time
import unittest
from unittest import mock

import support  # noqa: F401
from test_next import (BUILD_OK, CHANGES, CHANGES_BLOCKER, CHANGES_MINOR, CODE, JID, PASS,
                       SECURITY, NextBase, _git, refute)

from atta.spine import config, events, flow, machine, risk
from atta.spine import next as nxt
from atta.spine.timeutil import from_iso

BRANCH = f"atta/{JID}"


class RuleBase(NextBase):
    """A low-risk job in approve with every signal clean: the Filtro passed on HEAD, the review
    passed after it, build.md keeps the contract, the diff stays in FILES: and hits no brake."""

    def setUp(self):
        super().setUp()
        self.routed(risk="low")
        self.wt.mkdir(parents=True)
        _git(self.wt, "init", "-q")
        _git(self.wt, "checkout", "-q", "-b", BRANCH)
        (self.wt / "README").write_text("x\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "base")
        _git(self.wt, "update-ref", "refs/remotes/origin/develop", "HEAD")
        self.commit({"src/a.py": "def export_csv(rows):\n    return rows\n",
                     "tests/test_a.py": "def test_x():\n    assert True\n"})
        self.job.update(stage="approve", pr=7, worktree=str(self.wt), branch=BRANCH)
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py\n")
        self.write("round-1/build.md", BUILD_OK)
        self.checks()
        self.review("code", PASS)
        self.jobs.save(self.job)

    def commit(self, files):
        for rel, text in files.items():
            path = self.wt / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "feature")
        self.sha = _git(self.wt, "rev-parse", "HEAD")

    def stamp(self, path, offset):
        t = 1_700_000_000 + offset
        os.utime(path, (t, t))
        return path

    def checks(self, first="GATE: PASS", commit=None):
        self.stamp(self.write("round-1/checks.md",
                              f"{first}\nCOMMIT: {commit or self.sha}\n\n## Summary\n\n"
                              "El Filtro pasó: 1 check en 0.1 s.\n"), 0)

    def review(self, name, text, offset=10):
        return self.stamp(self.write(f"round-1/review-{name}.md", text), offset)

    def call(self, *argv):
        self.jobs.save(self.job)
        parser = argparse.ArgumentParser(prog="atta")
        nxt.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        return code, out.getvalue(), err.getvalue()

    def next_action(self):
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(out.splitlines()), 1, out)
        return json.loads(out)

    def rule_events(self):
        return [e for e in events.read_events(self.dir) if e.get("event") == "reina_rule"]

    def decision(self):
        return self.dir / "decision.md"


class RuleApprovesTest(RuleBase):
    def test_docs_named_in_files_still_merge(self):  # allowlist (c)
        self.commit({"README.md": "# Exportar\n", "docs/guia.rst": "Guia\n"})
        self.checks()
        self.review("code", PASS)
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, README.md, docs/\n")
        self.assertEqual(self.next_action(), self.run_action("merge", JID))
        self.assertEqual(self.decision().read_text(encoding="utf-8").splitlines()[0], "APPROVE")

    def test_low_and_clean_writes_approve_by_rule_records_it_and_merges(self):
        action = self.next_action()
        self.assertEqual(action, self.run_action("merge", JID))
        text = self.decision().read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "APPROVE")
        self.assertIn("## Summary", text)
        self.assertIn("## Why", text)
        self.assertIn("La Reina aprueba por regla, sin modelo", text)
        self.assertIn("GATE: PASS", text)
        self.assertIn("code PASS", text)
        self.assertIn("FILES:", text)
        evs = self.rule_events()
        self.assertEqual(len(evs), 1, evs)
        ev = evs[0]
        self.assertEqual({k: ev[k] for k in ("job", "stage", "round", "outcome", "decision",
                                             "by", "model")},
                         {"job": JID, "stage": "approve", "round": 1, "outcome": "approve",
                          "decision": "APPROVE", "by": "rule", "model": None})
        self.assertTrue(ev["signals"])
        self.assertNotIn("to", ev)
        job = self.jobs.load(JID)
        self.assertIsNone(job.get("pending_agent"))
        self.assertEqual(job["stage"], "approve")
        # A second call reads the rule's decision.md like the LLM's: same action, no new event.
        before = self.decision().read_bytes()
        self.assertEqual(self.next_action(), self.run_action("merge", JID))
        self.assertEqual(self.decision().read_bytes(), before)
        self.assertEqual(len(self.rule_events()), 1)

    def test_delivery_follows_auto_merge_up_to(self):
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "none"}})
        self.assertEqual(self.next_action(), self.run_action("handoff", JID))
        self.assertEqual(self.decision().read_text(encoding="utf-8").splitlines()[0], "APPROVE")

    def test_changes_refuted_by_the_refutador_still_approves(self):
        self.routed(risk="low", reviewers=(CODE, SECURITY))
        self.review("security", CHANGES)
        self.stamp(self.write("round-1/refute.md",
                              refute("REFUTED", "REFUTED security src/a.py:3 — x")), 20)
        self.assertEqual(self.next_action(), self.run_action("merge", JID))
        self.assertIn("security CHANGES", self.decision().read_text(encoding="utf-8"))

    def test_decide_stays_pure(self):
        before = (self.dir / "job.json").read_bytes()
        self.assertEqual(self.decide(), self.run_action("merge", JID))
        self.assertFalse(self.decision().exists())
        self.assertFalse((self.dir / "events.jsonl").exists())
        self.assertEqual((self.dir / "job.json").read_bytes(), before)

    def test_merge_and_handoff_recovery_read_the_rule_decision(self):
        self.next_action()
        job = self.jobs.load(JID)
        flow._check_approved(self.jobs, job, "atta merge")  # raises when not a fresh APPROVE
        # A handoff escalated it: the archived rule decision lets `atta handoff` retake it.
        flow._retire_decision(self.dir)
        self.assertEqual((self.dir / "decision-1.md").read_text(encoding="utf-8")
                         .splitlines()[0], "APPROVE")
        self.write("round-1/merge-checks.md", f"GATE: PASS\nCOMMIT: {self.sha}\n")
        self.write("handoff.md", "HANDOFF: FAIL\n\n## Summary\n\ngh pr ready falló: boom\n")
        events.append_event(self.dir, events.make_event(
            JID, "review", "approve", 1, from_iso("2020-01-01T00:00:00Z")))
        machine.advance(self.jobs, JID, "needs-human")
        job = self.jobs.load(JID)
        self.assertEqual(nxt.handoff_recovery(job, self.dir, self.wt), (self.sha, None))


class RuleFallsBackTest(RuleBase):
    """S3 condition 1: any doubt goes to the LLM Reina, never to a person."""

    def assertReina(self, why):
        action = self.next_action()
        self.assertEqual((action["action"], action["agent"]), ("spawn", "atta-reina"), action)
        self.assertEqual(set(action), {"action", "agent", "model", "prompt"})
        self.assertFalse(self.decision().exists())
        self.assertEqual(self.jobs.load(JID)["pending_agent"]["agent"], "atta-reina")
        evs = self.rule_events()
        self.assertEqual(len(evs), 1, evs)
        self.assertEqual({k: evs[0][k] for k in ("stage", "round", "outcome", "agent")},
                         {"stage": "approve", "round": 1, "outcome": "fallback",
                          "agent": "atta-reina"})
        self.assertIn(why, evs[0]["reason"])
        # Asking again does not repeat the event within the same stage entry.
        self.assertEqual(self.next_action()["agent"], "atta-reina")
        self.assertEqual(len(self.rule_events()), 1)

    def test_no_checks(self):
        (self.dir / "round-1" / "checks.md").unlink()
        self.assertReina("checks.md")

    def test_checks_fail(self):
        self.checks(first="GATE: FAIL")
        self.assertReina("checks.md")

    def test_checks_on_another_commit(self):
        self.checks(commit="0" * 40)
        self.assertReina("HEAD")

    def test_review_changes_not_refuted(self):
        self.review("code", CHANGES)
        self.assertReina("refut")

    def test_review_changes_upheld(self):
        self.review("code", CHANGES)
        self.stamp(self.write("round-1/refute.md",
                              refute("UPHELD", "UPHELD code src/a.py:3 — x")), 20)
        self.assertReina("refut")

    def test_review_minor_changes_is_not_a_pass(self):
        self.review("code", CHANGES_MINOR)
        self.assertReina("code")

    def test_review_missing(self):
        (self.dir / "round-1" / "review-code.md").unlink()
        self.assertReina("code")

    def test_review_older_than_the_filtro(self):
        self.review("code", PASS, offset=-10)
        self.assertReina("code")

    def test_build_breaks_the_contract(self):
        self.write("round-1/build.md", BUILD_OK.replace("## Risks\n\n- ninguno\n", ""))
        self.assertReina("build.md")

    def test_build_with_an_open_criterion(self):
        self.write("round-1/build.md", BUILD_OK.replace("- ✓ «Exporta", "- ✗ «Exporta"))
        self.assertReina("build.md")

    def test_diff_out_of_files(self):
        self.write("spec.md", "STATUS: READY\n\nFILES: src/b.py\n")
        self.assertReina("FILES:")

    def test_spec_without_files(self):
        self.write("spec.md", "STATUS: READY\n")
        self.assertReina("FILES:")

    def test_brake_data_migration(self):
        self.commit({"migrations/0001.sql": "create table t (id int);\n"})
        self.checks()
        self.review("code", PASS)
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, migrations/\n")
        self.assertReina("frenos de la Reina: el diff toca migrations/0001.sql")

    def test_brake_ci(self):
        self.commit({".github/workflows/ci.yml": "on: push\n"})
        self.checks()
        self.review("code", PASS)
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, .github/\n")
        self.assertReina("frenos de la Reina: el diff toca .github/workflows/ci.yml")


    # ---- verifier regressions (B1-B5) and advisories (A1, A2, A6)

    def _regate(self):
        self.checks()
        self.review("code", PASS)

    def _brake_diff(self):
        self.commit({"migrations/0001.sql": "drop table t;\n", "Dockerfile": "FROM x\n"})
        self._regate()
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, migrations/, Dockerfile\n")

    def test_brakes_hold_when_project_rules_have_no_categories(self):  # B1
        self.cfg["risk_rules"] = {"source": ["**/*.py"], "tests": ["**/tests/**"],
                                  "categories": {}}
        self._brake_diff()
        self.assertReina("fren")

    def test_brakes_hold_when_project_rules_drop_data_and_delivery(self):  # B1
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        rules["categories"] = {"security": rules["categories"]["security"]}
        self.cfg["risk_rules"] = rules
        self._brake_diff()
        self.assertReina("fren")

    def test_brake_untested_holds_when_project_drops_it(self):  # B1
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        del rules["categories"]["untested"]
        self.cfg["risk_rules"] = rules
        _git(self.wt, "reset", "-q", "--hard", "origin/develop")
        self.commit({"src/a.py": "X = 1\n"})
        self._regate()
        self.assertReina("sin test")

    def test_project_tests_glob_cannot_exempt_everything(self):  # B1
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        rules["tests"] = ["**"]
        self.cfg["risk_rules"] = rules
        self.commit({"core/pay.py": "x = eval('1')\n"})
        self._regate()
        self.assertReina("frenos de la Reina: el diff toca seguridad (core/pay.py)")

    def test_brake_migration_and_build_files_outside_default_globs(self):  # B2
        for rel, text in (("db/migrate/20240101_drop.rb", "remove_column :o, :t\n"),
                          ("Makefile", "deploy:\n\ttrue\n"),
                          (".travis.yml", "script: x\n"),
                          ("bitbucket-pipelines.yml", "pipelines: {}\n"),
                          ("build.gradle", "dependencies {}\n"),
                          ("tox.ini", "[tox]\n"),
                          (".pre-commit-config.yaml", "repos: []\n")):
            with self.subTest(path=rel):
                self.commit({rel: text})
                self._regate()
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {rel}\n")
                action = self.next_action()
                self.assertEqual(action.get("agent"), "atta-reina", action)
                self.assertFalse(self.decision().exists())
                self.assertIn("fren", self.rule_events()[-1]["reason"])

    def test_code_in_an_exempt_path_outside_files(self):  # B3
        self.commit({"lib/test/core.py": "def run(c):\n    return eval(c)\n"})
        self._regate()
        self.assertReina("lib/test/core.py es un test fuera de `FILES:` y agrega código de "
                         "seguridad")

    def test_diff_made_only_of_exempt_paths(self):  # B3
        _git(self.wt, "reset", "-q", "--hard", "origin/develop")
        self.commit({"app/spec/pay.py": "def pay():\n    return eval('1')\n"})
        self._regate()
        self.assertReina("app/spec/pay.py es un test fuera de `FILES:`")

    def test_diff_of_tests_only_changes_no_files_path(self):  # B3
        _git(self.wt, "reset", "-q", "--hard", "origin/develop")
        self.commit({"tests/test_b.py": "def test_b():\n    assert True\n"})
        self._regate()
        self.assertReina("ninguna ruta de `FILES:`")

    def test_lockfile_outside_the_delivery_globs(self):  # B3
        for rel in ("uv.lock", "bun.lock"):
            with self.subTest(path=rel):
                self.commit({rel: "evil==6.6.6\n"})
                self._regate()
                self.assertEqual(self.next_action().get("agent"), "atta-reina")
                self.assertFalse(self.decision().exists())

    def test_unplanned_review_with_a_blocker(self):  # B4
        self.review("security", CHANGES_BLOCKER)
        self.assertReina("security")

    def test_unplanned_review_with_an_unrefuted_major(self):  # B4
        self.review("security", CHANGES)
        self.assertReina("de security que el Refutador no refutó")

    def test_project_rules_can_add_a_brake(self):
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        rules["categories"]["data"]["paths"] = ["**/legacy/**"]
        self.cfg["risk_rules"] = rules
        self.commit({"src/legacy/old.py": "X = 2\n",
                     "tests/test_old.py": "def test_o():\n    pass\n"})
        self._regate()
        self.write("spec.md", "STATUS: READY\n\nFILES: src/\n")
        self.assertReina("frenos de la Reina: el diff toca datos (src/legacy/old.py)")

    def test_retry_review_changes_after_a_primary_pass(self):  # B4
        self.stamp(self.write("round-1/review-code-retry.md", CHANGES_BLOCKER), 30)
        self.assertReina("code")

    def test_files_that_name_everything(self):  # B5
        for glob in ("**", "**/*", "*/**", "*"):
            with self.subTest(files=glob):
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {glob}\n")
                self.assertEqual(self.next_action().get("agent"), "atta-reina")
                self.assertFalse(self.decision().exists())
                self.assertIn("FILES:", self.rule_events()[-1]["reason"])

    def test_refuted_major_with_an_unrefuted_minor(self):  # A2
        self.routed(risk="low", reviewers=(CODE, SECURITY))
        self.review("security", CHANGES_BLOCKER)
        self.stamp(self.write("round-1/refute.md",
                              refute("REFUTED", "REFUTED security src/b.py:9 — x")), 20)
        self.assertReina("menor")

    # ---- allowlist (B2 class): only source, tests and plain docs may be approved by rule

    def test_brake_deploy_and_migration_paths_outside_the_list(self):  # B2
        for rel in ("charts/app/values.yaml", "charts/app/templates/deployment.yaml",
                    "kubernetes/deploy.yaml", "manifests/deploy.yaml", "serverless.yaml",
                    "azure-pipelines.yaml", "Taskfile.yaml", "skaffold.yaml", "render.yaml",
                    "wrangler.toml", "Tiltfile", "Vagrantfile", "ansible/site.yml",
                    "terraform/main.hcl", "nginx.conf", "config/production.yaml",
                    "liquibase/changelog/001.xml"):
            with self.subTest(path=rel):
                self.commit({rel: "x: 1\n"})
                self.checks()
                self.review("code", PASS)
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {rel}\n")
                action = self.next_action()
                self.assertEqual(action.get("agent"), "atta-reina", action)
                self.assertFalse(self.decision().exists())

    def test_manifests_build_configs_and_scripts_go_to_the_reina(self):
        for rel, text in (("src/App.csproj", "<Project/>\n"),
                          ("pubspec.yaml", "name: app\n"),
                          ("webpack.config.js", "module.exports = {};\n"),
                          ("scripts/release.sh", "echo release\n"),
                          ("src/config/settings.py", "DEBUG = False\n"),
                          ("requirements-dev.txt", "evil==6.6.6\n"),
                          ("tsconfig.json", "{}\n")):
            with self.subTest(path=rel):
                _git(self.wt, "reset", "-q", "--hard", "origin/develop")
                self.commit({"src/a.py": "def export_csv(rows):\n    return rows\n",
                             "tests/test_a.py": "def test_x():\n    assert True\n",
                             rel: text})
                self._regate()
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {rel}\n")
                (self.dir / "events.jsonl").unlink(missing_ok=True)
                action = self.next_action()
                self.assertEqual(action.get("agent"), "atta-reina", action)
                self.assertFalse(self.decision().exists())
                self.assertIn(rel, self.rule_events()[-1]["reason"])

    def test_project_source_cannot_widen_the_allowlist(self):
        rules = copy.deepcopy(risk.DEFAULT_RULES)
        rules["source"] = ["**"]
        self.cfg["risk_rules"] = rules
        self.commit({"data/catalogo.json": "{}\n"})
        self._regate()
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, data/\n")
        self.assertReina("data/catalogo.json")

    # ---- advisories: review names, future mtimes, invalid risk values

    def test_review_file_name_is_matched_case_insensitively(self):
        self.stamp(self.write("round-1/REVIEW-security.MD", CHANGES), 10)
        self.assertReina("security")

    def test_review_from_the_future_is_a_doubt(self):
        path = self.review("code", PASS)
        future = time.time() + 3600
        os.utime(path, (future, future))
        self.assertReina("futuro")

    def test_refute_from_the_future_is_a_doubt(self):
        self.routed(risk="low", reviewers=(CODE, SECURITY))
        self.review("security", CHANGES)
        path = self.write("round-1/refute.md",
                          refute("REFUTED", "REFUTED security src/a.py:3 — x"))
        future = time.time() + 3600
        os.utime(path, (future, future))
        self.assertReina("futuro")

    def test_malformed_reviewers_are_a_doubt(self):  # A6
        self.job["agents"]["reviewers"] = ["code"]
        self.assertReina("revisores")


    # ---- second verification (S3 conditions 1 and 4): false approvals B1-B5

    def assertNoRuleApproval(self):
        self.assertEqual(self.next_action().get("agent"), "atta-reina")
        self.assertFalse(self.decision().exists())

    def test_pass_review_listing_a_blocker_or_major_is_a_doubt(self):  # S3.1
        for sev in ("blocker", "major"):
            with self.subTest(sev=sev):
                self.review("code", PASS + f"\n## Findings\n\n- [{sev}] src/a.py:1 — rompe.\n")
                self.assertNoRuleApproval()

    def test_retry_pass_does_not_mask_a_valid_primary_changes(self):  # S3.1
        for primary, off in ((CHANGES, 10), (CHANGES_MINOR, 20)):
            with self.subTest(primary=primary[:40]):
                self.review("code", primary, offset=10)
                self.stamp(self.write("round-1/review-code-retry.md", PASS), off)
                self.assertNoRuleApproval()

    def test_unparsed_blocker_is_not_refuted_by_another_refute(self):  # S3.1
        self.routed(risk="low", reviewers=(CODE, SECURITY))
        self.review("code", "VERDICT: CHANGES\n\n## Findings\n\n"
                            "- [blocker]  src/a.py:1 — borra filas.\n\nPREVENTION: x\n")
        self.review("security", CHANGES)
        self.stamp(self.write("round-1/refute.md", refute(
            "REFUTED", "REFUTED security src/a.py:3 — no aplica")), 30)
        self.assertNoRuleApproval()

    def test_changes_without_findings_is_a_doubt(self):  # S3.1
        self.review("code", "VERDICT: CHANGES\n\n## Summary\n\nAlgo.\n\nPREVENTION: x\n")
        self.stamp(self.write("round-1/refute.md", refute("REFUTED")), 30)
        self.assertNoRuleApproval()

    def test_content_brake_holds_for_a_path_with_a_space(self):  # S3.4
        self.commit({"src/my mod.py": "def f(c):\n    password = 'x'\n    return eval(c)\n",
                     "tests/test_a.py": "def test_x():\n    assert 1\n"})
        self.write("spec.md", "STATUS: READY\n\nFILES: src/\n")
        self._regate()
        self.assertNoRuleApproval()

    def test_content_brake_holds_when_gitattributes_hides_the_patch(self):  # S3.4
        _git(self.wt, "reset", "-q", "--hard", "origin/develop")
        (self.wt / ".gitattributes").write_text("src/gen/** -diff\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "attrs")
        _git(self.wt, "update-ref", "refs/remotes/origin/develop", "HEAD")
        self.commit({"src/gen/x.py": "def f(c):\n    return eval(c)\n",
                     "tests/test_a.py": "def test_x():\n    assert 1\n"})
        self.write("spec.md", "STATUS: READY\n\nFILES: src/\n")
        self._regate()
        self.assertNoRuleApproval()

    def test_dependency_build_config_and_agent_files_are_not_allowed(self):  # S3.4
        for rel in ("requirements/prod.txt", "dev-requirements.txt", "build.rs",
                    "gulpfile.js", "fabfile.py", "ci/release.py", "app/settings.py",
                    "CLAUDE.md", "AGENTS.md"):
            with self.subTest(rel=rel):
                _git(self.wt, "reset", "-q", "--hard", "origin/develop")
                self.commit({"src/a.py": "def export_csv(rows):\n    return rows\n",
                             rel: "x = 1\n", "tests/test_a.py": f"# {rel}\n"})
                self._regate()
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {rel}\n")
                self.assertNoRuleApproval()

    def test_finding_in_other_markup_is_a_doubt(self):  # S3.1 (R1)
        for line in ("- **[blocker]** src/a.py:1 — rompe", "1. [blocker] src/a.py:1 — rompe",
                     "[blocker] src/a.py:1 — rompe", "> - [blocker] src/a.py:1 — rompe",
                     "- `[blocker]` src/a.py:1 — rompe", "- [x] [blocker] src/a.py:1",
                     "### [blocker] src/a.py:1", "- \\[blocker\\] src/a.py:1",
                     "• [Major] src/a.py:1"):
            with self.subTest(line=line):
                self.review("code", PASS + f"\n## Findings\n\n{line}\n")
                self.assertNoRuleApproval()

    def test_unjudged_blocker_in_other_markup_beside_a_refuted_major(self):  # S3.1 (R1)
        self.routed(risk="low", reviewers=(CODE, SECURITY))
        self.review("security", PASS)
        self.review("code", "VERDICT: CHANGES\n\n## Findings\n\n- [major] src/a.py:3 — falla.\n"
                            "- **[blocker]** src/a.py:9 — borra filas.\n\nPREVENTION: x\n")
        self.stamp(self.write("round-1/refute.md", refute(
            "REFUTED", "REFUTED code src/a.py:3 — no aplica")), 30)
        self.assertNoRuleApproval()

    def test_more_build_deploy_config_and_dependency_files(self):  # S3.4 (R2)
        for rel in ("Package.swift", "magefile.go", "project/Build.scala", "build.py",
                    "deploy.py", "tasks.py", "tools/release.py", "webpack.prod.js",
                    ".eslintrc.js", "config.py", "app/settings_prod.py", "docs/conf.py",
                    "reqs.txt", ".cursor/rules/x.md", "docker/entry.py", "manage.py",
                    "src/.hidden/x.py", "notes.txt"):
            with self.subTest(rel=rel):
                _git(self.wt, "reset", "-q", "--hard", "origin/develop")
                self.commit({"src/a.py": "def export_csv(rows):\n    return rows\n",
                             rel: "x = 1\n", "tests/test_a.py": f"# {rel}\n"})
                self._regate()
                self.write("spec.md", f"STATUS: READY\n\nFILES: src/a.py, {rel}\n")
                self.assertNoRuleApproval()

    def test_unexpected_error_in_evaluate_goes_to_the_reina(self):  # S3.1
        with mock.patch("atta.spine.risk.changes", side_effect=ValueError("boom")):
            self.assertNoRuleApproval()
        self.assertIn("boom", self.rule_events()[0]["reason"])

    def test_malformed_job_risk_fields_never_read_as_low(self):  # S3.1
        from atta.spine import decide
        for job in ({"intake": "x", "agents": {"risk": "low"}},
                    {"intake": {"risk": "low"}, "agents": [1]}):
            with self.subTest(job=job):
                self.assertIsNone(decide.rule_risk(job))

    def test_pass_with_minor_notes_still_approves(self):  # S3: minors on PASS are notes
        self.review("code", PASS + "\n## Findings\n\n- [minor] src/a.py:1 — nombre.\n")
        self.assertEqual(self.next_action()["action"], "run")
        self.assertTrue(self.decision().exists())

    def test_invalid_primary_with_valid_retry_pass_approves(self):  # mirrors next._review
        self.review("code", "VERDICT: maybe\n", offset=10)
        self.stamp(self.write("round-1/review-code-retry.md", PASS), 20)
        self.assertEqual(self.next_action()["action"], "run")
        self.assertTrue(self.decision().exists())


class RuleWriteOrderTest(RuleBase):
    def test_event_failure_leaves_no_approval(self):  # A7
        with mock.patch.object(nxt, "append_event", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                self.call("next", JID)
        self.assertFalse(self.decision().exists())

    def test_fallback_event_failure_still_prints_the_reina_spawn(self):  # review of a94111e
        self.review("code", PASS + "\n## Findings\n\n- [blocker] src/a.py:1 — rompe.\n")
        with mock.patch.object(nxt, "append_event", side_effect=OSError("disco lleno")):
            code, out, err = self.call("next", JID)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["agent"], "atta-reina")
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(err, "Atta: no pude registrar por qué la regla dejó la decisión a la "
                              "Reina (disco lleno); sigo con la Reina.\n")
        self.assertEqual(self.rule_events(), [])
        self.assertFalse(self.decision().exists())

    def test_fallback_event_is_still_written_when_the_disk_works(self):  # preserve
        self.review("code", PASS + "\n## Findings\n\n- [blocker] src/a.py:1 — rompe.\n")
        self.assertEqual(self.next_action()["agent"], "atta-reina")
        self.assertEqual([e["outcome"] for e in self.rule_events()], ["fallback"])

    def test_event_comes_before_decision(self):  # A7
        seen = []
        real = nxt.append_event

        def spy(job_dir, event):
            seen.append(self.decision().exists())
            real(job_dir, event)

        with mock.patch.object(nxt, "append_event", side_effect=spy):
            self.assertEqual(self.next_action(), self.run_action("merge", JID))
        self.assertEqual(seen, [False])
        self.assertTrue(self.decision().exists())


class OtherRisksTest(RuleBase):
    def test_invalid_risk_value_is_never_low(self):
        for field, value in (("intake", "bajo"), ("agents", "LOW"), ("risk_effective", "")):
            with self.subTest(field=field, value=value):
                self.routed(risk="low")
                self.job.pop("risk_effective", None)
                if field == "risk_effective":
                    self.job["risk_effective"] = value
                else:
                    self.job[field]["risk"] = value
                self.assertEqual(self.next_action()["agent"], "atta-reina")
                self.assertFalse(self.decision().exists())

    def test_highest_known_risk_decides_eligibility(self):  # A1
        for field in ("intake", "agents"):
            with self.subTest(field=field):
                self.routed(risk="low")
                self.job[field]["risk"] = "medium"
                self.job["risk_effective"] = "low"
                self.assertEqual(self.next_action()["agent"], "atta-reina")
                self.assertFalse(self.decision().exists())
                self.assertEqual(self.rule_events(), [])

    def test_medium_and_high_spawn_the_reina_as_before(self):
        for risk, model in (("medium", "sonnet"), ("high", "opus")):
            with self.subTest(risk=risk):
                self.routed(risk=risk)
                action = self.next_action()
                self.assertEqual((action["agent"], action["model"]), ("atta-reina", model))
                self.assertFalse(self.decision().exists())
                self.assertEqual(self.rule_events(), [])

    def test_effective_risk_above_low_spawns_the_reina(self):
        self.job["risk_effective"] = "medium"
        self.assertEqual(self.next_action()["agent"], "atta-reina")
        self.assertEqual(self.rule_events(), [])


if __name__ == "__main__":
    unittest.main()
