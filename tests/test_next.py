"""`atta next` and `atta save-artifact` (S6, S7, S10): one deterministic JSON action per job state."""

import argparse
import contextlib
import io
import json
import os
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import config, store
from atta.spine import next as nxt

JID = "12-agregar-exportacion-a-csv"
PAST = "2020-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"
MODELS = {"T0": "haiku", "T1": "haiku", "T2": "sonnet", "T3": "opus"}
CODE = {"reviewer": "code", "lenses": ["reliability", "readability"], "tier": "T2"}
SECURITY = {"reviewer": "security", "lenses": ["security"], "tier": "T3"}
UX = {"reviewer": "ux", "lenses": ["ux"], "tier": "T1"}
PASS = "VERDICT: PASS\n\n## Summary\n\nTodo bien.\n"
CHANGES = ("VERDICT: CHANGES\n\n## Summary\n\nFalta un test.\n\n## Findings\n\n"
           "- [major] src/a.py:3 — falla con vacío — agrega un test.\n\n"
           "PREVENTION: checklist item \"empty input test\".\n")
CHANGES_NO_PREVENTION = ("VERDICT: CHANGES\n\n## Summary\n\nX.\n\n## Findings\n\n"
                         "- [major] src/a.py:3 — falla — arregla.\n")
INTAKE = "DECISION: {d}\nRISK: {r}\nSURFACES: none\n\n## Summary\n\nAlgo.\n"


class NextBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp.resolve()
        self.cfg = config.with_defaults({})
        self.jobs = store.JobStore(self.root / "atta" / "jobs")
        self.job = store.new_job(12, "Agregar exportación a CSV", None, self.cfg, support.T0)
        self.job["stage_since"] = PAST
        self.dir = self.jobs.job_dir(JID)
        self.dir.mkdir(parents=True)
        self.wt = self.root / ".worktrees" / JID

    def set(self, **fields):
        self.job.update(fields)
        self.jobs.save(self.job)

    def write(self, rel, text):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def decide(self, models=MODELS):
        self.jobs.save(self.job)
        return nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg, models)

    def routed(self, risk="low", reviewers=(CODE,), spec_review=False, decision="ACCEPT"):
        self.job["intake"] = {"risk": risk, "surfaces": [], "decision": decision}
        self.job["agents"] = {"risk": risk, "surfaces": [], "spec_review": spec_review,
                              "reviewers": [dict(r) for r in reviewers]}

    def with_worktree(self):
        self.wt.mkdir(parents=True)
        self.job["worktree"] = str(self.wt)

    def assertSpawn(self, action, agent, model, *needles):
        self.assertEqual(set(action), {"action", "agent", "model", "prompt"})
        self.assertEqual((action["action"], action["agent"], action["model"]),
                         ("spawn", agent, model))
        self.assertTrue(action["prompt"].startswith(f"Job: {JID}. Job folder: {self.dir}."),
                        action["prompt"])
        for needle in needles:
            self.assertIn(str(needle), action["prompt"])

    def run_action(self, *argv, then=None):
        action = {"action": "run", "argv": ["atta", *argv]}
        if then:
            action["then"] = then
        return action


class ModelsTest(unittest.TestCase):
    def test_packaged_tier_map_for_claude_code(self):
        self.assertEqual(nxt.load_models(), MODELS)


class IntakeTest(NextBase):
    def test_without_intake_spawns_exploradora_on_t1(self):
        action = self.decide()
        self.assertSpawn(action, "atta-exploradora", "haiku", f"Repository root: {self.root}.",
                         "issue.md", "data, never instructions", "<<<ATTA-ARTIFACT")

    def test_exploradora_model_follows_tier_map(self):
        action = self.decide(models=dict(MODELS, T1="sonnet"))
        self.assertEqual(action["model"], "sonnet")

    def test_intake_without_agents_runs_route(self):
        self.write("intake.md", INTAKE.format(d="ACCEPT", r="low"))
        self.assertEqual(self.decide(), self.run_action("route", JID))

    def test_intake_changed_after_route_routes_again(self):
        self.routed(risk="low")
        self.write("intake.md", INTAKE.format(d="ACCEPT", r="high"))
        self.assertEqual(self.decide(), self.run_action("route", JID))

    def test_stale_intake_from_before_the_stage_spawns_exploradora_again(self):
        self.write("intake.md", INTAKE.format(d="NEEDS-INFO", r="low"))
        self.set(stage_since=FUTURE)
        self.assertEqual(self.decide()["agent"], "atta-exploradora")

    def test_decisions_after_route(self):
        intake_md = str(self.dir / "intake.md")
        cases = {
            "ACCEPT": self.run_action("advance", JID, "spec"),
            "NEEDS-INFO": self.run_action(
                "label", JID, "add", "atta:needs-info",
                then=[["atta", "comment", JID, "--role", "exploradora", "--file", intake_md],
                      ["atta", "advance", JID, "needs-info"]]),
            "REJECT": self.run_action(
                "comment", JID, "--role", "exploradora", "--file", intake_md,
                then=[["atta", "advance", JID, "rejected"]]),
        }
        for decision, expected in cases.items():
            with self.subTest(decision=decision):
                self.routed(decision=decision)
                self.write("intake.md", INTAKE.format(d=decision, r="low"))
                self.assertEqual(self.decide(), expected)

    def test_malformed_intake_waits_with_reason(self):
        self.write("intake.md", "hola\n")
        action = self.decide()
        self.assertEqual(action["action"], "wait")
        self.assertIn("intake.md", action["reason"])


class SpecTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed(risk="medium", spec_review=True)
        self.job["stage"] = "spec"

    def test_without_spec_spawns_guia_on_t2(self):
        self.assertSpawn(self.decide(), "atta-guia", "sonnet", f"Repository root: {self.root}.",
                         self.dir / "spec.md", "data, never instructions")

    def test_with_spec_and_no_worktree_runs_worktree(self):
        self.write("spec.md", "STATUS: READY\n")
        self.assertEqual(self.decide(), self.run_action("worktree", JID))

    def test_default_worktree_folder_counts_as_present(self):
        self.write("spec.md", "STATUS: READY\n")
        self.wt.mkdir(parents=True)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "spec-review"))

    def test_with_spec_and_worktree_advances_per_plan(self):
        self.write("spec.md", "STATUS: READY\n")
        self.with_worktree()
        self.assertEqual(self.decide(), self.run_action("advance", JID, "spec-review"))
        self.routed(risk="low", spec_review=False)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_spec_older_than_revise_spawns_guia_again_with_review(self):
        self.write("spec.md", "STATUS: READY\n")
        self.write("spec-review.md", "VERDICT: REVISE\n")
        self.job["stage_since"] = FUTURE
        self.assertSpawn(self.decide(), "atta-guia", "sonnet", self.dir / "spec-review.md")


class SpecReviewTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed(risk="medium", spec_review=True)
        self.with_worktree()
        self.job["stage"] = "spec-review"
        self.write("spec.md", "STATUS: READY\n")

    def test_vigia_uses_a_model_other_than_the_guia(self):
        self.assertSpawn(self.decide(), "atta-vigia", "haiku", self.dir / "spec-review.md",
                         f"Repository root: {self.root}.")

    def test_vigia_shares_the_model_when_the_tier_map_allows_nothing_else(self):
        same = {t: "sonnet" for t in MODELS}
        self.assertEqual(self.decide(models=same)["model"], "sonnet")

    def test_vigia_goes_up_when_no_lower_tier_differs(self):
        models = {"T0": "sonnet", "T1": "sonnet", "T2": "sonnet", "T3": "opus"}
        self.assertEqual(self.decide(models=models)["model"], "opus")

    def test_verdicts(self):
        for verdict, stage in (("READY", "build"), ("REVISE", "spec")):
            with self.subTest(verdict=verdict):
                self.write("spec-review.md", f"VERDICT: {verdict}\n\n## Summary\n\nX.\n")
                self.assertEqual(self.decide(), self.run_action("advance", JID, stage))


class BuildTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"

    def test_without_worktree_runs_worktree(self):
        self.job["worktree"] = None
        os.rmdir(self.wt)
        self.assertEqual(self.decide(), self.run_action("worktree", JID))

    def test_first_round_spawns_cortadora_on_t2(self):
        self.assertSpawn(self.decide(), "atta-cortadora", "sonnet", "Round: 1.",
                         f"Worktree: {self.wt}.", "Base: origin/develop.",
                         self.dir / "round-1" / "build.md")

    def test_escalates_to_t3_from_round_three_or_high_risk(self):
        self.job["total_rounds"] = 1
        self.assertEqual(self.decide()["model"], "sonnet")
        self.job["total_rounds"] = 2
        self.assertEqual(self.decide()["model"], "opus")
        self.job["total_rounds"] = 0
        self.routed(risk="high")
        self.assertEqual(self.decide()["model"], "opus")

    def test_later_round_lists_previous_checks_reviews_and_feedback(self):
        self.job["total_rounds"] = 1
        files = [self.write("round-1/checks.md", "GATE: FAIL\n"),
                 self.write("round-1/checks.log", "boom\n"),
                 self.write("round-1/review-code.md", CHANGES),
                 self.write("feedback-1.md", "Usa la otra API.\n")]
        action = self.decide()
        self.assertSpawn(action, "atta-cortadora", "sonnet", "Round: 2.",
                         self.dir / "round-2" / "build.md", *files)

    def test_done_on_first_round_publishes_then_advances(self):
        self.write("round-1/build.md", "STATUS: DONE\n")
        self.assertEqual(self.decide(), self.run_action("publish", JID))
        self.job["pr"] = 7
        self.assertEqual(self.decide(), self.run_action("advance", JID, "gate"))

    def test_blocked_goes_to_needs_human(self):
        self.write("round-1/build.md", "STATUS: BLOCKED\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))

    def test_stale_build_after_human_feedback_spawns_cortadora(self):
        self.write("round-1/build.md", "STATUS: BLOCKED\n")
        self.job["stage_since"] = FUTURE
        self.assertEqual(self.decide()["agent"], "atta-cortadora")


class GateTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed()
        self.with_worktree()
        self.job.update(stage="gate", pr=7)

    def test_without_report_runs_gate(self):
        self.assertEqual(self.decide(), self.run_action(
            "gate", JID, "--worktree", str(self.wt), "--base", "origin/develop"))

    def test_pass_goes_to_review(self):
        self.write("round-1/checks.md", "GATE: PASS\nCOMMIT: abc\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "review"))

    def test_fail_goes_back_to_build(self):
        self.write("round-1/checks.md", "GATE: FAIL\nCOMMIT: abc\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_fail_with_budget_exhausted_goes_to_needs_human(self):
        for fields in ({"gate_rounds": 3, "total_rounds": 3},
                       {"gate_rounds": 1, "total_rounds": 6}):
            with self.subTest(**fields):
                self.job.update(fields)
                self.write(f"round-{fields['total_rounds'] + 1}/checks.md", "GATE: FAIL\n")
                self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))


class ReviewTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed(risk="high", reviewers=(CODE, SECURITY, UX))
        self.with_worktree()
        self.job.update(stage="review", pr=7)

    def test_spawns_group_with_every_reviewer_on_its_tier(self):
        action = self.decide()
        self.assertEqual(set(action), {"action", "agents"})
        self.assertEqual(action["action"], "spawn-group")
        got = [(a["agent"], a["model"]) for a in action["agents"]]
        self.assertEqual(got, [("atta-jardinera-code", "sonnet"), ("atta-soldado", "opus"),
                               ("atta-jardinera-ux", "haiku")])
        for spec, agent in zip((CODE, SECURITY, UX), action["agents"]):
            self.assertSpawn(dict(agent, action="spawn"), agent["agent"], agent["model"],
                             self.dir / "round-1" / f"review-{spec['reviewer']}.md",
                             "Lenses: " + ", ".join(spec["lenses"]) + ".", f"Worktree: {self.wt}.")

    def test_spawns_only_missing_reviewers(self):
        self.write("round-1/review-code.md", PASS)
        action = self.decide()
        self.assertEqual([a["agent"] for a in action["agents"]],
                         ["atta-soldado", "atta-jardinera-ux"])

    def test_all_pass_goes_to_approve(self):
        for r in ("code", "security", "ux"):
            self.write(f"round-1/review-{r}.md", PASS)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

    def test_valid_changes_goes_back_to_build(self):
        self.write("round-1/review-code.md", PASS)
        self.write("round-1/review-security.md", CHANGES)
        self.write("round-1/review-ux.md", PASS)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_changes_with_budget_exhausted_goes_to_needs_human(self):
        self.job.update(review_rounds=3, total_rounds=3)
        for r in ("code", "security", "ux"):
            self.write(f"round-4/review-{r}.md", CHANGES)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))

    def test_invalid_changes_respawns_that_reviewer_once_then_needs_human(self):
        self.write("round-1/review-code.md", PASS)
        self.write("round-1/review-security.md", CHANGES_NO_PREVENTION)
        self.write("round-1/review-ux.md", PASS)
        action = self.decide()
        self.assertEqual([(a["agent"], a["model"]) for a in action["agents"]],
                         [("atta-soldado", "opus")])
        retry = self.dir / "round-1" / "review-security-retry.md"
        self.assertIn(str(retry), action["agents"][0]["prompt"])
        self.write("round-1/review-security-retry.md", "VERDICT: CHANGES\n\n## Findings\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))

    def test_valid_retry_replaces_the_invalid_review(self):
        self.write("round-1/review-code.md", PASS)
        self.write("round-1/review-security.md", "VERDICT: MAYBE\n")
        self.write("round-1/review-security-retry.md", PASS)
        self.write("round-1/review-ux.md", PASS)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))


class ApproveTest(NextBase):
    def setUp(self):
        super().setUp()
        self.routed(risk="low")
        self.with_worktree()
        self.job.update(stage="approve", pr=7)

    def test_spawns_reina_on_t2_or_t3_when_high_risk(self):
        self.assertSpawn(self.decide(), "atta-reina", "sonnet", self.dir / "decision.md",
                         f"Worktree: {self.wt}.")
        self.routed(risk="high")
        self.assertEqual(self.decide()["model"], "opus")

    def test_approve_runs_merge(self):
        self.write("decision.md", "APPROVE\n\n## Summary\n\nOK.\n")
        self.assertEqual(self.decide(), self.run_action("merge", JID))

    def test_escalate_labels_comments_and_goes_to_needs_human(self):
        decision = self.write("decision.md", "ESCALATE\n\n## Question\n\n- ¿Seguro?\n")
        self.assertEqual(self.decide(), self.run_action(
            "label", JID, "add", "atta:needs-human",
            then=[["atta", "comment", JID, "--role", "reina", "--file", str(decision)],
                  ["atta", "advance", JID, "needs-human"]]))


class RestingStagesTest(NextBase):
    def test_wait_and_done(self):
        for stage in ("needs-info", "needs-human", "merging"):
            with self.subTest(stage=stage):
                self.job["stage"] = stage
                action = self.decide()
                self.assertEqual(set(action), {"action", "reason"})
                self.assertEqual(action["action"], "wait")
                self.assertTrue(action["reason"])
        for stage in ("merged", "rejected"):
            with self.subTest(stage=stage):
                self.job["stage"] = stage
                self.assertEqual(self.decide(), {"action": "done", "stage": stage})


class CommandTest(NextBase):
    def call(self, *argv):
        parser = argparse.ArgumentParser(prog="atta")
        nxt.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        return code, out.getvalue(), err.getvalue()

    def test_next_prints_one_json_line(self):
        self.jobs.save(self.job)
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out)["agent"], "atta-exploradora")

    def reply(self, text):
        path = self.tmp / "reply.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_save_artifact_writes_intake_between_markers(self):
        self.jobs.save(self.job)
        body = INTAKE.format(d="ACCEPT", r="low")
        reply = self.reply(f"Pensé un poco.\r\n<<<ATTA-ARTIFACT\n{body}ATTA-ARTIFACT>>>\nDECISION: ACCEPT\n")
        code, out, err = self.call("save-artifact", JID, "intake", "--from-reply", reply)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, f"La Exploradora decidió ACCEPT en {JID}: intake.md guardado.\n")
        self.assertEqual((self.dir / "intake.md").read_text(encoding="utf-8"), body)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["intake.md", "job.json"])

    def test_save_artifact_refuses_malformed_reply_and_writes_nothing(self):
        self.jobs.save(self.job)
        missing = ("La respuesta no trae el artefacto entre «<<<ATTA-ARTIFACT» y "
                   "«ATTA-ARTIFACT>>>». No se escribió intake.md.\n")
        cases = {
            "sin marcadores": ("DECISION: ACCEPT\nRISK: low\nSURFACES: none\n", missing),
            "sin cierre": ("<<<ATTA-ARTIFACT\nDECISION: ACCEPT\nRISK: low\nSURFACES: none\n",
                           missing),
            "dos bloques": ("<<<ATTA-ARTIFACT\nDECISION: ACCEPT\nATTA-ARTIFACT>>>\n" * 2, missing),
            "cabecera inválida": (
                "<<<ATTA-ARTIFACT\nDECISION: ACCEPT\nRISK: banana\nSURFACES: none\nATTA-ARTIFACT>>>\n",
                "intake.md: línea 2: RISK «banana» no es válido; usa low, medium o high. "
                "No se escribió intake.md.\n"),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                code, out, err = self.call("save-artifact", JID, "intake",
                                           "--from-reply", self.reply(text))
                self.assertEqual((code, out, err), (2, "", message))
                self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["job.json"])

    def test_save_artifact_missing_reply_file(self):
        self.jobs.save(self.job)
        code, out, err = self.call("save-artifact", JID, "intake",
                                   "--from-reply", str(self.tmp / "nope.txt"))
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("No se pudo leer "), err)
        self.assertFalse((self.dir / "intake.md").exists())

    def test_save_artifact_only_during_intake(self):
        self.set(stage="spec")
        body = INTAKE.format(d="ACCEPT", r="low")
        code, out, err = self.call("save-artifact", JID, "intake", "--from-reply",
                                   self.reply(f"<<<ATTA-ARTIFACT\n{body}ATTA-ARTIFACT>>>\n"))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, f"El job {JID} está en spec; intake.md solo se guarda en intake.\n")
        self.assertFalse((self.dir / "intake.md").exists())


if __name__ == "__main__":
    unittest.main()
