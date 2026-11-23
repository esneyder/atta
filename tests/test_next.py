"""`atta next` and `atta save-artifact` (S6, S7, S10): one deterministic JSON action per job state."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import config, events, gate, machine, memory, store
from atta.spine import next as nxt
from atta.spine.timeutil import from_iso

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
CHANGES_MINOR = ("VERDICT: CHANGES\n\n## Summary\n\nDetalles.\n\n## Findings\n\n"
                 "- [minor] src/a.py:7 — nombre confuso — renómbralo.\n\n"
                 "PREVENTION: checklist item \"clear names\".\n")
CHANGES_BLOCKER = ("VERDICT: CHANGES\n\n## Summary\n\nRompe datos.\n\n## Findings\n\n"
                   "- [blocker] src/b.py:9 — borra filas — valida antes.\n"
                   "- [minor] src/b.py:12 — comentario viejo — bórralo.\n\n"
                   "PREVENTION: test \"no delete without check\".\n")


BUILD_OK = ("STATUS: DONE\n\n## Summary\n\nLa Cortadora agregó la exportación.\n\n"
            "## Changes\n\n- src/a.py — nueva export_csv().\n\n"
            "## Tests\n\n- `python -m pytest` → `6 passed in 0.41s`\n\n"
            "## Acceptance\n\n- ✓ «Exporta los pedidos filtrados.» — tests/test_a.py::test_x\n"
            "  (continuación del criterio)\n\n"
            "## Risks\n\n- ninguno\n")


def refute(header, *lines):
    body = "".join(f"- {line}\n" for line in lines)
    return f"REFUTE: {header}\n\n{body}\n## Summary\n\nEl Refutador revisó los hallazgos.\n"


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
            # T11: the Exploradora's acceptance is announced on the issue before advancing.
            "ACCEPT": self.run_action(
                "comment", JID, "--role", "exploradora", "--on", "issue",
                "--voice", "intake-accepted", then=[["atta", "advance", JID, "spec"]]),
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

    # S9: the third REVISE of a job goes to a person instead of back to spec.
    def test_third_revise_goes_to_needs_human(self):
        self.write("spec-review.md", "VERDICT: REVISE\n\n## Summary\n\nX.\n")
        for done, stage in ((0, "spec"), (1, "spec"), (2, "needs-human"), (5, "needs-human")):
            with self.subTest(spec_reviews=done):
                self.job["spec_reviews"] = done
                self.assertEqual(self.decide(), self.run_action("advance", JID, stage))
        # READY still builds at the cap.
        self.write("spec-review.md", "VERDICT: READY\n\n## Summary\n\nX.\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_second_review_prompt_says_so(self):
        self.assertNotIn("This is review", self.decide()["prompt"])
        self.job["spec_reviews"] = 1
        self.assertIn("This is review 2 of this spec (your previous review asked for changes 1 "
                      "time): REVISE only for a blocker", self.decide()["prompt"])

    def test_revise_cap_end_to_end_through_advance(self):
        # Three REVISE verdicts in a row: two go back to spec, the third to needs-human.
        self.jobs.save(self.job)
        for expected in ("spec", "spec", "needs-human"):
            job = self.jobs.load(JID)
            self.assertEqual(job["stage"], "spec-review")
            path = self.write("spec-review.md", "VERDICT: REVISE\n\n## Summary\n\nX.\n")
            os.utime(path, (from_iso(job["stage_since"]).timestamp() + 1,) * 2)
            action = nxt.decide(job, self.dir, self.root, self.cfg, MODELS)
            self.assertEqual(action, self.run_action("advance", JID, expected))
            machine.advance(self.jobs, JID, expected)
            if expected == "spec":
                machine.advance(self.jobs, JID, "spec-review")
        job = self.jobs.load(JID)
        self.assertEqual((job["stage"], job["spec_reviews"], job["total_rounds"]),
                         ("needs-human", 3, 0))


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

    def test_escalates_to_t3_from_round_two_or_high_risk(self):
        # S8: `Round: N` is total_rounds + 1; the Cortadora climbs to T3 from round 2 (F3 L9),
        # i.e. when one round already ended (total_rounds == 1). Round 1 stays on T2.
        self.assertEqual(self.decide()["model"], "sonnet")
        self.job["total_rounds"] = 1
        action = self.decide()
        self.assertEqual(action["model"], "opus")
        self.assertIn("Round: 2.", action["prompt"])
        self.job["total_rounds"] = 2
        self.assertEqual(self.decide()["model"], "opus")
        self.job["total_rounds"] = 0
        self.routed(risk="high")
        self.assertEqual(self.decide()["model"], "opus")

    def test_medium_risk_keeps_round_one_on_t2(self):
        self.routed(risk="medium")
        self.assertEqual(self.decide()["model"], "sonnet")

    def test_unknown_risk_counts_as_high_for_the_cortadora(self):
        self.routed(risk="low")
        self.job["intake"]["risk"] = self.job["agents"]["risk"] = None
        self.assertEqual(self.decide()["model"], "opus")

    def test_later_round_lists_previous_checks_reviews_and_feedback(self):
        self.job["total_rounds"] = 1
        self.routed(risk="medium")
        files = [self.write("round-1/checks.md", "GATE: FAIL\n"),
                 self.write("round-1/checks.log", "boom\n"),
                 self.write("round-1/review-code.md", CHANGES),
                 self.write("feedback-1.md", "Usa la otra API.\n")]
        action = self.decide()
        self.assertSpawn(action, "atta-cortadora", "opus", "Round: 2.",
                         self.dir / "round-2" / "build.md", *files)

    def test_done_on_first_round_publishes_then_advances(self):
        self.write("round-1/build.md", BUILD_OK)
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


class BuildContractTest(NextBase):
    """G10: a STATUS: DONE build.md keeps the section contract and every criterion ✓."""

    def setUp(self):
        super().setUp()
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        self.jobs.save(self.job)
        self.build = self.dir / "round-1" / "build.md"

    def call_next(self):
        parser = argparse.ArgumentParser(prog="atta")
        nxt.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["next", JID])
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.job = self.jobs.load(JID)
        return json.loads(out.getvalue())

    def rewrite(self, text, offset):
        """The Cortadora's retry: the same build.md, written later (another mtime)."""
        path = self.write("round-1/build.md", text)
        stamp = path.stat().st_mtime + offset
        os.utime(path, (stamp, stamp))

    def counters(self):
        job = self.jobs.load(JID)
        return (job["stage"], job["total_rounds"], job["gate_rounds"], job["review_rounds"])

    def test_valid_report_publishes(self):
        self.write("round-1/build.md", BUILD_OK)
        self.assertEqual(self.call_next(), self.run_action("publish", JID))
        self.assertNotIn("build_retry", self.job)

    def test_malformed_reports_respawn_once_naming_what_is_missing(self):
        cases = {
            "only the status line": ("STATUS: DONE\n", "missing `## Summary`, `## Changes`, "
                                     "`## Tests`, `## Acceptance`, `## Risks`"),
            "no Risks": (BUILD_OK.split("## Risks")[0], "missing `## Risks`"),
            "out of order": (BUILD_OK.replace("## Summary", "## Tmp").replace(
                "## Risks", "## Summary").replace("## Tmp", "## Risks"),
                "sections out of order (expected `## Summary`, `## Changes`, `## Tests`, "
                "`## Acceptance`, `## Risks`)"),
            "empty Tests": (BUILD_OK.replace("- `python -m pytest` → `6 passed in 0.41s`\n", ""),
                            "`## Tests` is empty"),
            "empty Acceptance": (BUILD_OK.replace(
                "- ✓ «Exporta los pedidos filtrados.» — tests/test_a.py::test_x\n"
                "  (continuación del criterio)\n", ""), "`## Acceptance` is empty"),
            "unmarked criterion": (BUILD_OK.replace("- ✓ «Exporta", "- «Exporta"),
                                   "acceptance lines without ✓ or ✗: «- «Exporta"),
        }
        for name, (text, note) in cases.items():
            with self.subTest(name):
                job = self.jobs.load(JID)
                job.pop("build_retry", None)
                self.jobs.save(job)
                self.write("round-1/build.md", text)
                before = self.counters()
                action = self.call_next()
                self.assertSpawn(action, "atta-cortadora", "sonnet", "Round: 1.",
                                 f"Your previous {self.build} says STATUS: DONE but breaks the "
                                 "build.md contract: ", note,
                                 f"Write the full {self.build} again")
                self.assertEqual(self.job["build_retry"],
                                 {"round": 1, "since": PAST, "mtime": self.build.stat().st_mtime})
                self.assertEqual(self.counters(), before)
                # Asking again before the rewrite repeats the same retry, no needs-human.
                self.assertEqual(self.call_next()["agent"], "atta-cortadora")
                # The rewrite is still malformed: a person decides.
                self.rewrite(text, 5)
                self.assertEqual(self.call_next(), self.run_action("advance", JID, "needs-human"))
                self.assertEqual(self.counters(), before)

    def test_open_criterion_respawns_asking_to_finish_then_needs_human(self):
        text = BUILD_OK.replace("— tests/test_a.py::test_x\n",
                                "— tests/test_a.py::test_x\n- ✗ «Pagina.» — not covered\n")
        self.write("round-1/build.md", text)
        action = self.call_next()
        self.assertSpawn(action, "atta-cortadora", "sonnet",
                         f"Your previous {self.build} says STATUS: DONE but these acceptance "
                         "criteria are ✗: «- ✗ «Pagina.» — not covered». Finish them test-first "
                         "and commit", "STATUS: BLOCKED")
        self.assertNotIn("breaks the build.md contract", action["prompt"])
        self.rewrite(text, 5)
        self.assertEqual(self.call_next(), self.run_action("advance", JID, "needs-human"))
        self.assertEqual(self.counters(), ("build", 0, 0, 0))

    def test_a_fixed_retry_goes_on_to_publish_and_the_gate(self):
        self.write("round-1/build.md", "STATUS: DONE\n\n## Summary\n\nX.\n")
        self.assertEqual(self.call_next()["agent"], "atta-cortadora")
        self.rewrite(BUILD_OK, 5)
        self.assertEqual(self.call_next(), self.run_action("publish", JID))
        self.job["pr"] = 7
        self.jobs.save(self.job)
        self.assertEqual(self.call_next(), self.run_action("advance", JID, "gate"))

    def test_blocked_retry_goes_to_needs_human(self):
        self.write("round-1/build.md", "STATUS: DONE\n")
        self.call_next()
        self.rewrite("STATUS: BLOCKED\n\n## Blocked\n\n- falta una decisión\n", 5)
        self.assertEqual(self.call_next(), self.run_action("advance", JID, "needs-human"))

    def test_a_retry_of_an_earlier_stage_entry_or_round_does_not_count(self):
        self.write("round-1/build.md", "STATUS: DONE\n")
        mtime = self.build.stat().st_mtime + 99
        for retry in ({"round": 1, "since": "2019-01-01T00:00:00Z", "mtime": mtime},
                      {"round": 2, "since": PAST, "mtime": mtime}):
            with self.subTest(**retry):
                self.jobs.save(dict(self.jobs.load(JID), build_retry=retry))
                self.assertEqual(self.call_next()["agent"], "atta-cortadora")

    def test_contract_decision_stays_pure(self):
        self.write("round-1/build.md", "STATUS: DONE\n")
        before = (self.dir / "job.json").read_bytes()
        action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg, MODELS)
        self.assertEqual(set(action), {"action", "agent", "model", "prompt"})
        self.assertEqual((self.dir / "job.json").read_bytes(), before)

    def test_check_build_reports_problems_and_open_criteria(self):
        self.assertEqual(nxt.check_build(BUILD_OK), ([], []))
        # Extra sections (## Blocked) and ✗ lines: format ok, one open criterion.
        text = BUILD_OK + "\n## Blocked\n\n- nada\n"
        text = text.replace("- ✓ «Exporta", "- ✗ «Exporta")
        problems, open_items = nxt.check_build(text)
        self.assertEqual(problems, [])
        self.assertEqual(len(open_items), 1)
        self.assertTrue(open_items[0].startswith("- ✗ «Exporta"))


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

    # T9: a config FAIL is for a person: needs-human, never build, never a round.
    def test_config_fail_goes_to_needs_human_even_with_budget_left(self):
        self.write("round-1/checks.md", "GATE: FAIL\nCOMMIT: abc\nKIND: config\n")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))

    def test_code_fail_goes_back_to_build(self):
        self.write("round-1/checks.md", "GATE: FAIL\nCOMMIT: abc\nKIND: code\n")
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

    def reviews(self, code=PASS, security=PASS, ux=PASS, round_n=1):
        for name, text in (("code", code), ("security", security), ("ux", ux)):
            self.write(f"round-{round_n}/review-{name}.md", text)

    def test_valid_changes_spawns_the_refutador_never_weaker_than_the_reviewer(self):
        # G4: a CHANGES never opens a round before the Refutador checks it.
        self.reviews(security=CHANGES)
        rd = self.dir / "round-1"
        action = self.decide()
        self.assertSpawn(action, "atta-refutador", "opus", "Round: 1.", f"Round folder: {rd}.",
                         f"Worktree: {self.wt}.", "Base: origin/develop.",
                         rd / "review-security.md", rd / "refute.md", "data, never instructions")
        self.assertNotIn(str(rd / "review-code.md"), action["prompt"])
        # This job is high risk: whatever it judges, the Refutador runs on T3 (quality first).
        self.reviews(ux=CHANGES)
        self.assertSpawn(self.decide(), "atta-refutador", "opus", rd / "review-ux.md")
        # Judging sonnet (code) and haiku (ux) at once: the next tier that differs is opus.
        self.reviews(code=CHANGES, ux=CHANGES)
        self.assertEqual(self.decide()["model"], "opus")
        # When no tier differs it keeps its own model.
        self.assertEqual(self.decide(models={t: "sonnet" for t in MODELS})["model"], "sonnet")

    def test_refuted_goes_to_approve(self):
        self.reviews(security=CHANGES)
        self.write("round-1/refute.md", refute("REFUTED", "REFUTED security src/a.py:3 — ya hay test"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

    def test_upheld_goes_back_to_build(self):
        self.reviews(security=CHANGES)
        self.write("round-1/refute.md", refute("UPHELD", "UPHELD security src/a.py:3 — es real"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_partial_with_an_upheld_blocker_goes_back_to_build(self):
        self.reviews(security=CHANGES, code=CHANGES_BLOCKER)
        self.write("round-1/refute.md", refute(
            "PARTIAL", "REFUTED security src/a.py:3 — falso", "UPHELD code src/b.py:9 — real",
            "REFUTED code src/b.py:12 — cosmético"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "build"))

    def test_only_refuted_blockers_and_majors_count_minors_never_block(self):
        self.reviews(code=CHANGES_BLOCKER)
        self.write("round-1/refute.md", refute(
            "REFUTED", "REFUTED code src/b.py:9 — no borra", "UPHELD code src/b.py:12 — sí"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

    def test_minor_only_changes_goes_to_approve_without_the_refutador(self):
        self.reviews(code=CHANGES_MINOR, ux=CHANGES_MINOR)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))
        self.assertFalse((self.dir / "round-1" / "refute.md").exists())

    def test_malformed_refute_respawns_once_then_needs_human(self):
        self.reviews(security=CHANGES)
        rd = self.dir / "round-1"
        malformed = {
            "sin cabecera": "Creo que está bien.\n",
            "cabecera inválida": refute("MAYBE", "REFUTED security src/a.py:3 — x"),
            "sin Summary": "REFUTE: REFUTED\n\n- REFUTED security src/a.py:3 — x\n",
            "hallazgo grave sin juicio": refute("REFUTED"),
            "cabecera contradice": refute("REFUTED", "UPHELD security src/a.py:3 — real"),
            "UPHELD sin nada en pie": refute("UPHELD", "REFUTED security src/a.py:3 — falso"),
        }
        for name, text in malformed.items():
            with self.subTest(name):
                (rd / "refute-retry.md").unlink(missing_ok=True)
                self.write("round-1/refute.md", text)
                action = self.decide()
                self.assertSpawn(action, "atta-refutador", "opus", rd / "refute-retry.md",
                                 rd / "review-security.md")
                self.write("round-1/refute-retry.md", text)
                self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))

    def test_valid_refute_retry_decides(self):
        self.reviews(security=CHANGES)
        self.write("round-1/refute.md", "REFUTE: ?\n")
        self.write("round-1/refute-retry.md",
                   refute("REFUTED", "REFUTED security src/a.py:3 — x"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

    def test_refute_tolerates_backticks_and_reviewer_file_names(self):
        self.reviews(security=CHANGES)
        self.write("round-1/refute.md", refute(
            "REFUTED", "REFUTED review-security.md `src/a.py:3` — x"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

    def test_stale_refute_is_ignored(self):
        # From a previous round: round 2 has its own folder.
        self.job["total_rounds"] = 1
        self.job["review_rounds"] = 1
        self.write("round-1/refute.md", refute("REFUTED", "REFUTED security src/a.py:3 — x"))
        self.reviews(security=CHANGES, round_n=2)
        self.assertSpawn(self.decide(), "atta-refutador", "opus",
                         self.dir / "round-2" / "refute.md")
        # Written before the stage started (needs-human → build reuses the round folder).
        old = self.write("round-2/refute.md", refute("REFUTED", "REFUTED security src/a.py:3 — x"))
        past = from_iso("2021-01-01T00:00:00Z").timestamp()
        os.utime(old, (past, past))
        self.job["stage_since"] = "2022-01-01T00:00:00Z"
        self.assertEqual(self.decide()["agent"], "atta-refutador")
        # Older than the review it judges, though written during the stage.
        self.job["stage_since"] = "2020-06-01T00:00:00Z"
        self.assertEqual(self.decide()["agent"], "atta-refutador")

    def test_changes_with_budget_exhausted_goes_to_needs_human(self):
        self.job.update(review_rounds=3, total_rounds=3)
        self.reviews(code=CHANGES, security=CHANGES, ux=CHANGES, round_n=4)
        self.assertEqual(self.decide()["agent"], "atta-refutador")
        self.write("round-4/refute.md", refute(
            "PARTIAL", "UPHELD code src/a.py:3 — real", "REFUTED security src/a.py:3 — x",
            "REFUTED ux src/a.py:3 — x"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))
        self.write("round-4/refute.md", refute(
            "REFUTED", "REFUTED code src/a.py:3 — x", "REFUTED security src/a.py:3 — x",
            "REFUTED ux src/a.py:3 — x"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

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

    def test_rastreador_maps_to_its_agent_and_review_file(self):
        # G6/L14: the router may list `rastreador`; same verdict handling as any reviewer.
        rastreador = {"reviewer": "rastreador", "lenses": ["callers"], "tier": "T3"}
        self.routed(risk="high", reviewers=(CODE, rastreador))
        rd = self.dir / "round-1"
        self.write("round-1/review-code.md", PASS)
        action = self.decide()
        self.assertEqual([(a["agent"], a["model"]) for a in action["agents"]],
                         [("atta-rastreador", "opus")])
        self.assertIn(str(rd / "review-rastreador.md"), action["agents"][0]["prompt"])
        self.write("round-1/review-rastreador.md", PASS)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))
        # A broken review is asked once more, then needs-human.
        self.write("round-1/review-rastreador.md", "VERDICT: MAYBE\n")
        self.assertIn(str(rd / "review-rastreador-retry.md"),
                      self.decide()["agents"][0]["prompt"])
        self.write("round-1/review-rastreador-retry.md", CHANGES_NO_PREVENTION)
        self.assertEqual(self.decide(), self.run_action("advance", JID, "needs-human"))
        # A serious CHANGES goes to the Refutador, which judges it by the reviewer name.
        (rd / "review-rastreador-retry.md").unlink()
        self.write("round-1/review-rastreador.md", CHANGES)
        self.assertSpawn(self.decide(), "atta-refutador", "opus", rd / "review-rastreador.md")
        self.write("round-1/refute.md",
                   refute("REFUTED", "REFUTED atta-rastreador src/a.py:3 — no aplica"))
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))

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

    def test_delivery_threshold_matrix_merge_or_handoff(self):
        # S13: Atta merges by itself only when the job risk is at most auto_merge_up_to.
        self.write("decision.md", "APPROVE\n\n## Summary\n\nOK.\n")
        merges = {
            "none": set(), "low": {"low"}, "medium": {"low", "medium"},
            "high": {"low", "medium", "high"},
        }
        for threshold, merged in merges.items():
            for risk in ("low", "medium", "high"):
                with self.subTest(threshold=threshold, risk=risk):
                    self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": threshold}})
                    self.routed(risk=risk)
                    command = "merge" if risk in merged else "handoff"
                    self.assertEqual(self.decide(), self.run_action(command, JID))

    def test_code_default_high_keeps_merging_every_risk(self):
        self.write("decision.md", "APPROVE\n\n## Summary\n\nOK.\n")
        self.routed(risk="high")
        self.assertEqual(self.cfg["delivery"]["auto_merge_up_to"], "high")
        self.assertEqual(self.decide(), self.run_action("merge", JID))

    def test_unknown_risk_counts_as_high(self):
        self.write("decision.md", "APPROVE\n\n## Summary\n\nOK.\n")
        self.job.pop("intake", None)
        self.job.pop("agents", None)
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "medium"}})
        self.assertEqual(self.decide(), self.run_action("handoff", JID))
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "high"}})
        self.assertEqual(self.decide(), self.run_action("merge", JID))

    def test_effective_risk_decides_delivery(self):
        # S4: the Filtro's effective risk (never lower than the intake) decides delivery.
        self.write("decision.md", "APPROVE\n\n## Summary\n\nOK.\n")
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "low"}})
        self.routed(risk="low")
        self.assertEqual(self.decide(), self.run_action("merge", JID))
        self.job["risk_effective"] = "high"
        self.assertEqual(self.decide(), self.run_action("handoff", JID))

    def test_effective_high_escalates_the_reina(self):
        self.job["risk_effective"] = "high"
        self.assertEqual(self.decide()["model"], "opus")

    def test_escalate_with_threshold_none_still_goes_to_needs_human(self):
        self.cfg = config.with_defaults({"delivery": {"auto_merge_up_to": "none"}})
        decision = self.write("decision.md", "ESCALATE\n\n## Question\n\n- ¿Seguro?\n")
        self.assertEqual(self.decide()["argv"], ["atta", "label", JID, "add", "atta:needs-human"])
        self.assertIn(str(decision), self.decide()["then"][0])

    def test_escalate_labels_comments_and_goes_to_needs_human(self):
        decision = self.write("decision.md", "ESCALATE\n\n## Question\n\n- ¿Seguro?\n")
        self.assertEqual(self.decide(), self.run_action(
            "label", JID, "add", "atta:needs-human",
            then=[["atta", "comment", JID, "--role", "reina", "--file", str(decision)],
                  ["atta", "advance", JID, "needs-human"]]))


class RestingStagesTest(NextBase):
    def test_needs_info_waits_for_the_author(self):
        # `atta refresh` (run by /atta for queue rows `resume`) moves the job; next only waits.
        self.job["stage"] = "needs-info"
        self.assertEqual(self.decide(), {
            "action": "wait",
            "reason": "Esperando la respuesta del autor del issue (needs-info)."})

    def test_merging_runs_merge_again_to_recover(self):
        # L22: a merge cut halfway is finished (or resumed) by `atta merge`, never a wait.
        self.job["stage"] = "merging"
        self.assertEqual(self.decide(), {"action": "run", "argv": ["atta", "merge", JID]})

    def test_awaiting_merge_runs_merge_to_check_the_human_merge(self):
        # S13: `atta merge` asks GitHub whether the human merged the PR.
        self.job["stage"] = "awaiting-merge"
        self.assertEqual(self.decide(), {"action": "run", "argv": ["atta", "merge", JID]})

    def test_wait_and_done(self):
        for stage in ("needs-info", "needs-human"):
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


def _git(cwd, *args):
    out = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=Atta Test",
                          "-c", "user.email=atta@example.invalid", "-c", "commit.gpgsign=false",
                          *args], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise AssertionError(f"git {args} falló: {out.stderr}")
    return out.stdout.strip()


OK_CHECKS = json.dumps({"checks": [{"name": "ok", "run": "git --version"}]})


class LessonsInPromptsTest(NextBase):
    """T4b (G9, G12): relevant lessons reach the Guía, the Cortadora and the reviewers."""

    HEADER = "Relevant lessons from atta/memory/lessons.md"

    def setUp(self):
        super().setUp()
        self.store = memory.Lessons(self.root)
        self.store.upsert({"rule": "Validar la entrada vacía del exportador", "job": "1-a",
                           "paths": ["src/a.py"]})
        self.store.upsert({"rule": "Cerrar los sockets del cliente HTTP", "job": "2-b",
                           "paths": ["net/client.py"]})
        self.store.upsert({"rule": "Cerrar los sockets del cliente HTTP siempre", "job": "3-c",
                           "paths": ["net/client.py"]})
        self.write("spec.md", "STATUS: READY\n\nFILES: src/a.py, tests/test_a.py\n")

    def block(self, prompt):
        self.assertIn(self.HEADER, prompt)
        return prompt.split(self.HEADER, 1)[1].split("\n", 1)[1]

    def test_spec_files_rank_the_related_lesson_first(self):
        # Guía (re-writing the spec), Cortadora (no diff), reviewers (no git repo): FILES.
        self.routed(risk="low")
        self.job.update(stage="spec", stage_since=FUTURE)
        prompts = [self.decide()["prompt"]]
        self.with_worktree()
        self.job.update(stage="build", stage_since=PAST)
        prompts.append(self.decide()["prompt"])
        self.job.update(stage="review", pr=7)
        prompts.append(self.decide()["agents"][0]["prompt"])
        for prompt in prompts:
            lines = self.block(prompt).splitlines()
            self.assertEqual(lines[0], "- Validar la entrada vacía del exportador (Veces: 1)")
            self.assertEqual(lines[1], "- Cerrar los sockets del cliente HTTP (Veces: 2)")

    def test_diff_paths_win_over_spec_files_when_available(self):
        self.routed(risk="low")
        self.wt.mkdir(parents=True)
        _git(self.wt, "init", "-q")
        (self.wt / "README").write_text("x\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "base")
        _git(self.wt, "update-ref", "refs/remotes/origin/develop", "HEAD")
        (self.wt / "net").mkdir()
        (self.wt / "net" / "server.py").write_text("y\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "feature")
        self.job.update(worktree=str(self.wt), stage="review", pr=7)
        lines = self.block(self.decide()["agents"][0]["prompt"]).splitlines()
        self.assertEqual(lines[0], "- Cerrar los sockets del cliente HTTP (Veces: 2)")

    def test_budget_is_kept(self):
        for n in range(12):
            self.store.upsert({"rule": f"Regla {n} " + " ".join(f"palabra{n}x{k}" for k in range(40)),
                               "job": f"{n + 10}-x", "paths": ["src/a.py"]})
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        block = self.block(self.decide()["prompt"])
        self.assertLessEqual(len(block), memory.BUDGET_CHARS)
        self.assertLessEqual(len(block.splitlines()), 5)

    def test_no_lessons_or_a_damaged_file_adds_nothing(self):
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        self.store.path.write_text("## Mal Nombre\n", encoding="utf-8")
        self.assertNotIn(self.HEADER, self.decide()["prompt"])
        self.store.path.unlink()
        self.assertNotIn(self.HEADER, self.decide()["prompt"])

    def test_other_agents_get_no_lessons(self):
        self.assertNotIn(self.HEADER, self.decide()["prompt"])  # Exploradora
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "approve"
        self.assertNotIn(self.HEADER, self.decide()["prompt"])  # Reina


class GateConfigRecoveryTest(NextBase):
    """T9 (S2): a Filtro stopped by configuration waits for a person, then retries the gate in
    the same round; no round is spent."""

    def setUp(self):
        super().setUp()
        self.routed()
        self.with_worktree()
        self.job.update(stage="gate", pr=7)
        self.jobs.save(self.job)

    def checks(self, text):
        path = self.root / "atta" / "checks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def stop(self, kind="config"):
        job = self.jobs.load(JID)
        job["gate_failure"] = {"kind": kind, "reason": "atta/checks.json no tiene checks."}
        self.jobs.save(job)
        machine.advance(self.jobs, JID, "needs-human")
        self.job = self.jobs.load(JID)

    def gate_argv(self):
        return ["atta", "gate", JID, "--worktree", str(self.wt), "--base", "origin/develop"]

    def test_still_broken_waits_with_the_reason(self):
        self.checks('{"checks": []}')
        self.stop()
        self.assertEqual(self.decide(), {"action": "wait", "reason": (
            "El Filtro está detenido por configuración y no gasta ronda; cuando lo corrijas, "
            "/atta run lo vuelve a correr en la misma ronda. atta/checks.json no tiene checks. "
            "Agrega los checks reales del proyecto en atta/checks.json.")})

    def test_fixed_runs_the_gate_again_in_the_same_round(self):
        self.checks(OK_CHECKS)
        self.stop()
        self.assertEqual(self.decide(), self.run_action("advance", JID, "gate",
                                                        then=[self.gate_argv()]))

    def test_only_after_a_config_stop(self):
        self.checks(OK_CHECKS)
        self.stop(kind="code")
        self.assertEqual(self.decide(), {"action": "wait", "reason": nxt._WAIT["needs-human"]})

    def test_needs_human_reached_from_elsewhere_just_waits(self):
        self.checks(OK_CHECKS)
        self.job.update(stage="review")
        self.jobs.save(self.job)
        job = self.jobs.load(JID)
        job["gate_failure"] = {"kind": "config", "reason": "x"}
        self.jobs.save(job)
        machine.advance(self.jobs, JID, "needs-human")
        self.job = self.jobs.load(JID)
        self.assertEqual(self.decide(), {"action": "wait", "reason": nxt._WAIT["needs-human"]})

    def test_main_checkout_on_main_waits_with_the_branch_message(self):
        _git(self.root, "init", "-q")
        _git(self.root, "checkout", "-q", "-b", "develop")
        self.checks(OK_CHECKS)
        _git(self.root, "add", "atta/checks.json")
        _git(self.root, "commit", "-q", "-m", "base")
        _git(self.root, "checkout", "-q", "-b", "main")
        self.stop()
        action = self.decide()
        self.assertEqual(action["action"], "wait")
        self.assertTrue(action["reason"].endswith(
            "El checkout principal está en «main»; Atta lee checks.json y las reglas desde "
            "develop. Corre: git switch develop"), action["reason"])
        _git(self.root, "checkout", "-q", "develop")
        self.assertEqual(self.decide(), self.run_action("advance", JID, "gate",
                                                        then=[self.gate_argv()]))


class GateConfigEndToEndTest(NextBase):
    """T9 with the real Filtro: empty checks.json → needs-human without a round → fixed →
    the gate runs again in the same round → PASS → review."""

    def setUp(self):
        super().setUp()
        _git(self.root, "init", "-q")
        _git(self.root, "checkout", "-q", "-b", "develop")
        (self.root / ".gitignore").write_text("atta/jobs/\n.worktrees/\n", encoding="utf-8")
        self.checks('{"checks": []}')
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "worktree", "add", "-q", "-b", f"atta/{JID}", str(self.wt), self.base)
        self.addCleanup(subprocess.run, ["git", "-C", str(self.root), "worktree", "remove",
                                         "--force", str(self.wt)], capture_output=True)
        (self.wt / "feature.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "feature")
        self.routed()
        self.job.update(stage="gate", pr=7, worktree=str(self.wt))
        self.jobs.save(self.job)

    def checks(self, text):
        path = self.root / "atta" / "checks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def gate(self):
        return gate.run_gate(self.root, self.wt, JID, self.dir / "round-1",
                             base_ref=self.base, config=self.cfg)

    def now(self):
        self.job = self.jobs.load(JID)
        return nxt.decide(self.job, self.dir, self.root, self.cfg, MODELS)

    def counters(self):
        job = self.jobs.load(JID)
        return job["gate_rounds"], job["review_rounds"], job["total_rounds"]

    def test_config_stop_and_retry_in_the_same_round(self):
        gate_argv = ["atta", "gate", JID, "--worktree", str(self.wt), "--base",
                     "origin/develop"]
        self.assertEqual(self.now(), {"action": "run", "argv": gate_argv})
        result = self.gate()
        self.assertEqual((result.passed, result.kind), (False, "config"))
        self.assertEqual(self.now(), self.run_action("advance", JID, "needs-human"))
        job = machine.advance(self.jobs, JID, "needs-human")
        self.assertEqual(self.counters(), (0, 0, 0))
        self.assertEqual(job["gate_failure"]["kind"], "config")
        self.assertEqual(self.now()["action"], "wait")
        self.checks(OK_CHECKS)
        action = self.now()
        self.assertEqual(action, self.run_action("advance", JID, "gate", then=[gate_argv]))
        machine.advance(self.jobs, JID, "gate")
        self.assertEqual(self.counters(), (0, 0, 0))
        result = self.gate()
        self.assertTrue(result.passed, result.summary)
        self.assertEqual(result.report, self.dir / "round-1" / "checks.md")
        self.assertNotIn("gate_failure", self.jobs.load(JID))
        self.assertEqual(self.now(), self.run_action("advance", JID, "review"))
        self.assertEqual(self.counters(), (0, 0, 0))

    def test_code_fail_still_spends_a_round(self):
        self.checks(json.dumps({"checks": [{"name": "rojo",
                                            "run": "git no-existe-este-comando"}]}))
        result = self.gate()
        self.assertEqual((result.passed, result.kind), (False, "code"))
        self.assertEqual(self.now(), self.run_action("advance", JID, "build"))
        machine.advance(self.jobs, JID, "build")
        self.assertEqual(self.counters(), (1, 0, 1))


class HandoffRecoveryTest(NextBase):
    """L31 (c): a needs-human job that a handoff escalated, with the approval still valid and
    HEAD at the Filtro's sha, runs `atta handoff` again; anything else keeps waiting."""

    WAIT = {"action": "wait",
            "reason": "Esperando una decisión humana (needs-human): deja tu feedback con /atta."}

    def setUp(self):
        super().setUp()
        self.with_worktree()
        _git(self.wt, "init", "-q")
        _git(self.wt, "checkout", "-q", "-b", f"atta/{JID}")
        _git(self.wt, "commit", "-q", "--allow-empty", "-m", "feature")
        self.sha = _git(self.wt, "rev-parse", "HEAD")
        self.set(stage="needs-human", branch=f"atta/{JID}", pr=101)
        self.event("review", "approve", PAST)
        self.event("approve", "needs-human", "2020-01-01T00:05:00Z")
        self.write("decision-1.md", "APPROVE\n\n## Summary\n\nOk.\n")
        self.write("round-1/merge-checks.md", f"GATE: PASS\nCOMMIT: {self.sha}\n")
        self.write("handoff.md", "HANDOFF: FAIL\n\n## Summary\n\ngh pr ready falló: boom\n")

    def event(self, frm, to, ts):
        events.append_event(self.dir, events.make_event(JID, frm, to, 1, from_iso(ts)))

    def test_recoverable_handoff_escalation_runs_handoff(self):
        self.assertEqual(self.decide(), {"action": "run", "argv": ["atta", "handoff", JID]})

    def test_l31_state_without_handoff_md_runs_handoff(self):
        # Before handoff.md: a PASS merge-checks since approve and no merging means a handoff.
        (self.dir / "handoff.md").unlink()
        self.assertEqual(self.decide(), {"action": "run", "argv": ["atta", "handoff", JID]})

    def test_waits_when_head_moved(self):
        _git(self.wt, "commit", "-q", "--allow-empty", "-m", "otro")
        self.assertEqual(self.decide(), self.WAIT)

    def test_waits_when_the_decision_is_not_approve(self):
        self.write("decision-1.md", "ESCALATE\n\n## Summary\n\nNo.\n")
        self.assertEqual(self.decide(), self.WAIT)

    def test_waits_when_the_escalation_did_not_come_from_a_handoff(self):
        cases = {
            "merge": lambda: self.event("needs-human", "build", "2020-01-01T00:06:00Z")
            or self.event("build", "needs-human", "2020-01-01T00:07:00Z"),
            "no evidence": lambda: [(self.dir / "handoff.md").unlink(),
                                    (self.dir / "round-1" / "merge-checks.md").unlink()],
        }
        for name, change in cases.items():
            with self.subTest(name):
                change()
                self.assertEqual(self.decide(), self.WAIT)

    def test_waits_after_a_merge_escalation(self):
        (self.dir / "handoff.md").unlink()
        (self.dir / "events.jsonl").unlink()
        self.event("review", "approve", PAST)
        self.event("approve", "merging", "2020-01-01T00:01:00Z")
        self.event("merging", "needs-human", "2020-01-01T00:05:00Z")
        self.assertEqual(self.decide(), self.WAIT)


class ProjectModelsTest(NextBase):
    """S12: `atta next` resolves through models.resolve with the project's `models` config."""

    def use(self, **entry):
        self.cfg = config.with_defaults({"models": {"claude-code": entry}})

    def decide_cfg(self):
        self.jobs.save(self.job)
        return nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg)

    def test_without_models_config_uses_the_packaged_defaults(self):
        self.assertEqual(self.decide_cfg()["model"], "haiku")

    def test_role_override_reaches_the_spawn(self):
        self.use(roles={"exploradora": "sonnet"})
        self.assertSpawn(self.decide_cfg(), "atta-exploradora", "sonnet")

    def test_tier_override_reaches_the_spawn(self):
        self.use(tiers={"T1": "claude-haiku-4-5"})
        self.assertEqual(self.decide_cfg()["model"], "claude-haiku-4-5")

    def test_cortadora_override_still_escalates_to_t3(self):
        self.use(roles={"cortadora": "haiku"})
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        self.assertEqual(self.decide_cfg()["model"], "haiku")
        self.job["total_rounds"] = 2
        self.assertEqual(self.decide_cfg()["model"], "opus")

    def test_vigia_avoids_the_overridden_guia(self):
        self.use(roles={"guia": "haiku"})
        self.routed(risk="medium", spec_review=True)
        self.with_worktree()
        self.job["stage"] = "spec-review"
        self.write("spec.md", "STATUS: READY\n")
        self.assertEqual(self.decide_cfg()["model"], "sonnet")

    def test_reviewer_override_and_router_t3(self):
        self.use(roles={"jardinera-code": "opus", "soldado": "haiku"})
        self.routed(risk="high", reviewers=(CODE, SECURITY, UX))
        self.with_worktree()
        self.job.update(stage="review", pr=7)
        got = [(a["agent"], a["model"]) for a in self.decide_cfg()["agents"]]
        self.assertEqual(got, [("atta-jardinera-code", "opus"), ("atta-soldado", "opus"),
                               ("atta-jardinera-ux", "haiku")])

    def test_reina_override_and_escalation(self):
        self.use(roles={"reina": "haiku"})
        self.routed(risk="low")
        self.with_worktree()
        self.job.update(stage="approve", pr=7)
        self.assertEqual(self.decide_cfg()["model"], "haiku")
        self.routed(risk="high")
        self.assertEqual(self.decide_cfg()["model"], "opus")


class CommandTest(NextBase):
    def test_next_command_emits_the_overridden_model(self):
        self.cfg = config.with_defaults({"models": {"claude-code": {"tiers": {"T1": "sonnet"}}}})
        self.jobs.save(self.job)
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["model"], "sonnet")

    def call(self, *argv):
        parser = argparse.ArgumentParser(prog="atta")
        nxt.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        return code, out.getvalue(), err.getvalue()

    # ---- T12: telemetry of the decided model

    def pending(self):
        return self.jobs.load(JID).get("pending_agent")

    def test_spawn_records_pending_agent(self):
        self.jobs.save(self.job)
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        action = json.loads(out)
        self.assertEqual(set(action), {"action", "agent", "model", "prompt"})
        pending = self.pending()
        self.assertTrue(pending.pop("at").endswith("Z"))
        self.assertEqual(pending, {"stage": "intake", "role": "exploradora",
                                   "agent": "atta-exploradora", "model": "haiku",
                                   "tier": "T1"})

    def test_pending_agent_follows_overrides_and_escalation(self):
        self.cfg = config.with_defaults({"models": {"claude-code": {"roles": {"cortadora": "haiku"}}}})
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        self.jobs.save(self.job)
        self.call("next", JID)
        self.assertEqual({k: self.pending()[k] for k in ("role", "model", "tier")},
                         {"role": "cortadora", "model": "haiku", "tier": "T2"})
        self.job["total_rounds"] = 2
        self.jobs.save(self.job)
        self.call("next", JID)
        self.assertEqual({k: self.pending()[k] for k in ("role", "model", "tier")},
                         {"role": "cortadora", "model": "opus", "tier": "T3"})

    def test_spawn_group_records_a_list_and_keeps_finished_reviewers(self):
        self.routed(risk="high", reviewers=(CODE, SECURITY))
        self.with_worktree()
        self.job.update(stage="review", pr=7)
        self.jobs.save(self.job)
        self.call("next", JID)
        got = [{k: e[k] for k in ("stage", "role", "agent", "model", "tier")}
               for e in self.pending()]
        self.assertEqual(got, [
            {"stage": "review", "role": "jardinera-code", "agent": "atta-jardinera-code",
             "model": "sonnet", "tier": "T2"},
            {"stage": "review", "role": "soldado", "agent": "atta-soldado", "model": "opus",
             "tier": "T3"}])
        # The code reviewer finished; only the Soldado is spawned again. Both stay recorded.
        self.write("round-1/review-code.md", PASS)
        code, out, _ = self.call("next", JID)
        self.assertEqual([a["agent"] for a in json.loads(out)["agents"]], ["atta-soldado"])
        self.assertEqual([e["agent"] for e in self.pending()],
                         ["atta-jardinera-code", "atta-soldado"])

    def test_vigia_tier_is_the_tier_of_its_model(self):
        self.routed(risk="medium", spec_review=True)
        self.with_worktree()
        self.job["stage"] = "spec-review"
        self.write("spec.md", "STATUS: READY\n")
        self.jobs.save(self.job)
        code, out, _ = self.call("next", JID)
        pending = self.pending()
        self.assertEqual((pending["role"], pending["model"]), ("vigia", json.loads(out)["model"]))
        self.assertEqual(nxt.mdl.table(self.cfg).tier_model(pending["tier"]), pending["model"])

    def refute_events(self):
        return [e for e in events.read_events(self.dir) if e.get("event") == "refute"]

    def review_with_changes(self):
        self.routed(risk="high", reviewers=(CODE, SECURITY))
        self.with_worktree()
        self.job.update(stage="review", pr=7)
        self.write("round-1/review-code.md", PASS)
        self.write("round-1/review-security.md", CHANGES)
        self.jobs.save(self.job)

    def test_refutador_spawn_joins_the_reviewers_in_pending_agent(self):
        self.review_with_changes()
        self.jobs.save(dict(self.job, pending_agent=[
            {"stage": "review", "role": "jardinera-code", "agent": "atta-jardinera-code",
             "model": "sonnet", "tier": "T2", "at": PAST},
            {"stage": "review", "role": "soldado", "agent": "atta-soldado", "model": "opus",
             "tier": "T3", "at": PAST}]))
        code, out, _ = self.call("next", JID)
        self.assertEqual(json.loads(out)["agent"], "atta-refutador")
        got = [(e["agent"], e["model"], e["tier"]) for e in self.pending()]
        self.assertEqual(got, [("atta-jardinera-code", "sonnet", "T2"),
                               ("atta-soldado", "opus", "T3"),
                               ("atta-refutador", "opus", "T3")])

    def test_refute_outcome_is_recorded_once_as_an_event(self):
        self.review_with_changes()
        cases = (("REFUTED", "approve"), ("UPHELD", "build"))
        for header, stage in cases:
            with self.subTest(header):
                (self.dir / "events.jsonl").unlink(missing_ok=True)
                self.write("round-1/refute.md",
                           refute(header, f"{header} security src/a.py:3 — x"))
                for _ in range(2):
                    code, out, err = self.call("next", JID)
                    self.assertEqual((code, err), (0, ""))
                    self.assertEqual(json.loads(out), self.run_action("advance", JID, stage))
                evs = self.refute_events()
                self.assertEqual(len(evs), 1, evs)
                self.assertEqual({k: evs[0][k] for k in ("job", "stage", "round", "refute")},
                                 {"job": JID, "stage": "review", "round": 1, "refute": header})
                self.assertNotIn("to", evs[0])

    def test_refute_decision_stays_pure(self):
        self.review_with_changes()
        self.write("round-1/refute.md", refute("REFUTED", "REFUTED security src/a.py:3 — x"))
        before = (self.dir / "job.json").read_bytes()
        self.assertEqual(self.decide(), self.run_action("advance", JID, "approve"))
        self.assertEqual((self.dir / "job.json").read_bytes(), before)
        self.assertFalse((self.dir / "events.jsonl").exists())

    def test_non_spawn_action_writes_nothing(self):
        self.write("intake.md", INTAKE.format(d="ACCEPT", r="low"))
        self.jobs.save(self.job)
        before = (self.dir / "job.json").read_bytes()
        code, out, _ = self.call("next", JID)
        self.assertEqual(json.loads(out)["argv"], ["atta", "route", JID])
        self.assertEqual((self.dir / "job.json").read_bytes(), before)

    def test_decide_stays_pure(self):
        self.jobs.save(self.job)
        before = (self.dir / "job.json").read_bytes()
        self.assertEqual(self.decide()["agent"], "atta-exploradora")
        self.assertEqual((self.dir / "job.json").read_bytes(), before)

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

    def test_save_artifact_tolerates_the_l17_malformed_reply(self):
        """L17 (c): Haiku sent the opening marker twice and closed with `</ATTA-ARTIFACT>>>`."""
        body = ("DECISION: ACCEPT\nRISK: low\nSURFACES: none\n\n## Summary\n\n"
                "La Exploradora aceptó el issue: agregar la función de resta.\n")
        replies = {
            "L17": f"<<<ATTA-ARTIFACT\n<<<ATTA-ARTIFACT\n{body}</ATTA-ARTIFACT>>>\nDECISION: ACCEPT\n",
            "cerco": f"Listo.\n<<<ATTA-ARTIFACT\n\n```markdown\n{body}```\n\nATTA-ARTIFACT>>>\n",
            "cerco fuera": f"```text\n<<<ATTA-ARTIFACT\n{body}\n</ATTA-ARTIFACT>>>\n```\n",
        }
        for name, text in replies.items():
            with self.subTest(name):
                self.job["stage"] = "intake"
                self.jobs.save(self.job)
                (self.dir / "intake.md").unlink(missing_ok=True)
                code, out, err = self.call("save-artifact", JID, "intake",
                                           "--from-reply", self.reply(text))
                self.assertEqual((code, err), (0, ""))
                self.assertEqual(out, f"La Exploradora decidió ACCEPT en {JID}: intake.md guardado.\n")
                self.assertEqual((self.dir / "intake.md").read_text(encoding="utf-8"), body)
                self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                                 ["intake.md", "job.json"])

    def test_save_artifact_l17_shape_still_validates_and_refuses(self):
        self.jobs.save(self.job)
        missing = ("La respuesta no trae el artefacto entre «<<<ATTA-ARTIFACT» y "
                   "«ATTA-ARTIFACT>>>». No se escribió intake.md.\n")
        cases = {
            "cabecera inválida": (
                "<<<ATTA-ARTIFACT\n<<<ATTA-ARTIFACT\nDECISION: ACCEPT\nRISK: banana\n"
                "SURFACES: none\n</ATTA-ARTIFACT>>>\nDECISION: ACCEPT\n",
                "intake.md: línea 2: RISK «banana» no es válido; usa low, medium o high. "
                "No se escribió intake.md.\n"),
            "cierre antes de la última apertura": (
                "<<<ATTA-ARTIFACT\nDECISION: ACCEPT\nRISK: low\nSURFACES: none\n"
                "</ATTA-ARTIFACT>>>\n<<<ATTA-ARTIFACT\nDECISION: ACCEPT\n", missing),
            "vacío": ("<<<ATTA-ARTIFACT\n\n```\n```\n</ATTA-ARTIFACT>>>\n", missing),
            "cabecera no primero": (
                "<<<ATTA-ARTIFACT\nHola.\nDECISION: ACCEPT\nRISK: low\nSURFACES: none\n"
                "</ATTA-ARTIFACT>>>\n",
                "intake.md: la primera línea debe ser «DECISION: ACCEPT|REJECT|NEEDS-INFO». "
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


class MatrixTest(NextBase):
    """F4 T3 (S8, S8a, S2): the stage×risk matrix, the attempt ladder and model independence,
    all resolved through models.Table.stage_tier."""

    def use(self, **entry):
        self.cfg = config.with_defaults({"models": {"claude-code": entry}})

    def decide_cfg(self):
        self.jobs.save(self.job)
        return nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg)

    def plan_cfg(self):
        self.jobs.save(self.job)
        return nxt.plan(self.jobs.load(JID), self.dir, self.root, self.cfg)

    def stage(self, stage, risk, **fields):
        self.routed(risk=risk, spec_review=True)
        self.wt.mkdir(parents=True, exist_ok=True)
        self.job["worktree"] = str(self.wt)
        self.job.update(stage=stage, **fields)

    # ---- low: one tier down only where an error is cheap

    def test_low_guia_goes_one_tier_down(self):
        self.stage("spec", "low")
        action = self.decide_cfg()
        self.assertSpawn(action, "atta-guia", "haiku")

    def test_low_guia_tier_is_recorded_as_t1(self):
        self.stage("spec", "low")
        plan = self.plan_cfg()
        self.assertEqual((plan["model"], plan["tier"]), ("haiku", "T1"))

    def test_guia_by_risk(self):
        for risk, model in (("low", "haiku"), ("medium", "sonnet"), ("high", "sonnet")):
            with self.subTest(risk=risk):
                self.stage("spec", risk)
                self.assertEqual(self.decide_cfg()["model"], model)

    def test_low_guia_tier_one_down_reads_the_tier_map(self):
        self.stage("spec", "low")
        self.jobs.save(self.job)
        tiers = dict(MODELS, T1="claude-haiku-4-5")
        action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg, tiers)
        self.assertEqual(action["model"], "claude-haiku-4-5")

    def test_low_never_lowers_the_reina_the_refutador_or_the_code_reviewer(self):
        self.stage("approve", "low", pr=7)
        self.assertSpawn(self.decide_cfg(), "atta-reina", "sonnet")
        self.stage("review", "low", pr=7)
        self.job["agents"]["reviewers"] = [dict(CODE, tier="T1")]  # the router's low table
        self.write("round-1/review-code.md", CHANGES)
        # as before: never weaker than the reviewer it judges (T1) and not its model (haiku)
        self.assertSpawn(self.decide_cfg(), "atta-refutador", "sonnet",
                         self.dir / "round-1" / "refute.md")

    def test_low_ux_reviewer_keeps_the_router_tier(self):
        # The routing table already runs it on T1 at low; the matrix does not lower it twice.
        self.routed(risk="low", reviewers=(UX,))
        self.wt.mkdir(parents=True, exist_ok=True)
        self.job["worktree"] = str(self.wt)
        self.job.update(stage="review", pr=7)
        self.assertEqual(self.decide_cfg()["agents"][0]["model"], "haiku")
        models = dict(MODELS, T0="claude-haiku-4-5")
        self.jobs.save(self.job)
        action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg, models)
        self.assertEqual(action["agents"][0]["model"], "haiku")

    def test_exploradora_runs_before_the_risk_is_known(self):
        # Intake decides the risk: it never runs lowered, even if an old intake said low.
        self.routed(risk="low")
        self.jobs.save(self.job)
        tiers = dict(MODELS, T0="claude-haiku-4-5")
        action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg, tiers)
        self.assertSpawn(action, "atta-exploradora", "haiku")

    def test_role_override_beats_the_low_step_down(self):
        self.use(roles={"guia": "sonnet"})
        self.stage("spec", "low")
        self.assertEqual(self.decide_cfg()["model"], "sonnet")

    # ---- medium: today's routing, unchanged

    def test_medium_routing_is_unchanged(self):
        self.stage("spec", "medium")
        self.assertSpawn(self.decide_cfg(), "atta-guia", "sonnet")
        self.stage("build", "medium")
        self.assertSpawn(self.decide_cfg(), "atta-cortadora", "sonnet", "Round: 1.")
        self.stage("approve", "medium", pr=7)
        self.assertSpawn(self.decide_cfg(), "atta-reina", "sonnet")
        self.routed(risk="medium", reviewers=(CODE, {**SECURITY, "tier": "T2"}, UX))
        self.job.update(stage="review", pr=7)
        got = [(a["agent"], a["model"]) for a in self.decide_cfg()["agents"]]
        self.assertEqual(got, [("atta-jardinera-code", "sonnet"), ("atta-soldado", "sonnet"),
                               ("atta-jardinera-ux", "haiku")])

    # ---- high: up where an error is expensive

    def test_high_raises_cortadora_reina_and_security_reviewers_to_t3(self):
        self.stage("build", "high")
        self.assertEqual(self.decide_cfg()["model"], "opus")
        self.stage("approve", "high", pr=7)
        self.assertEqual(self.decide_cfg()["model"], "opus")
        # a routing table that left the security reviewer below T3 is raised to it
        self.routed(risk="high", reviewers=(CODE, {**SECURITY, "tier": "T2"}))
        self.job.update(stage="review", pr=7)
        got = [(a["agent"], a["model"]) for a in self.decide_cfg()["agents"]]
        self.assertEqual(got, [("atta-jardinera-code", "sonnet"), ("atta-soldado", "opus")])

    def test_unknown_risk_raises_but_never_lowers(self):
        self.stage("approve", "high", pr=7)
        self.job["intake"]["risk"] = self.job["agents"]["risk"] = "raro"
        self.assertEqual(self.decide_cfg()["model"], "opus")
        self.stage("spec", "high")
        self.job["intake"]["risk"] = self.job["agents"]["risk"] = "raro"
        self.assertEqual(self.decide_cfg()["model"], "sonnet")

    # ---- the matrix can be switched off by a human: today's routing

    def test_matrix_off_keeps_the_previous_routing(self):
        self.cfg = config.with_defaults({"routing_matrix": False})
        self.stage("spec", "low")
        self.assertEqual(self.decide_cfg()["model"], "sonnet")
        self.stage("build", "medium")
        self.job["total_rounds"] = 1
        self.assertEqual(self.decide_cfg()["model"], "sonnet")
        self.job["total_rounds"] = 2
        self.assertEqual(self.decide_cfg()["model"], "opus")
        self.stage("build", "high")
        self.job["total_rounds"] = 0
        self.assertEqual(self.decide_cfg()["model"], "opus")
        self.job["intake"]["risk"] = self.job["agents"]["risk"] = None
        self.assertEqual(self.decide_cfg()["model"], "sonnet")
        self.routed(risk="high", reviewers=(CODE, {**SECURITY, "tier": "T2"}))
        self.job.update(stage="review", pr=7)
        got = [a["model"] for a in self.decide_cfg()["agents"]]
        self.assertEqual(got, ["sonnet", "sonnet"])

    # ---- independence: reviewers avoid the Cortadora's model (F2 L8)

    def test_reviewers_carry_the_cortadora_model_to_avoid(self):
        self.stage("review", "medium", pr=7)
        self.job["agents"]["reviewers"] = [dict(CODE)]
        plan = self.plan_cfg()
        self.assertEqual(plan["agents"][0]["avoid"], ["sonnet"])
        self.assertEqual(plan["agents"][0]["model"], "sonnet")  # medium stays as today
        self.job["total_rounds"] = 1  # round 2: the Cortadora runs on T3
        self.assertEqual(self.plan_cfg()["agents"][0]["avoid"], ["opus"])

    def test_public_action_does_not_print_avoid(self):
        self.stage("review", "medium", pr=7)
        self.job["agents"]["reviewers"] = [dict(CODE)]
        self.assertNotIn("avoid", self.decide_cfg()["agents"][0])

    def test_reviewer_moves_to_a_distinct_model_when_a_stronger_tier_allows_it(self):
        # T1 was set to opus by a human: the T1 reviewer would share the Cortadora's T3 model.
        self.use(tiers={"T1": "opus"})
        self.stage("review", "high", pr=7)
        self.job["agents"]["reviewers"] = [dict(UX)]
        action = self.decide_cfg()["agents"][0]
        self.assertEqual(action["model"], "sonnet")

    def test_independence_never_weakens_or_raises_cost_below_high(self):
        # medium: no stronger model is allowed, so a clash is kept (never a weaker reviewer)
        self.use(tiers={"T1": "sonnet"})
        self.stage("review", "medium", pr=7)
        self.job["agents"]["reviewers"] = [dict(UX)]
        self.assertEqual(self.decide_cfg()["agents"][0]["model"], "sonnet")

    def test_avoid_keeps_working_with_a_spent_fallback_chain(self):
        self.use(fallbacks={"T3": ["sonnet", "haiku"]})
        self.stage("review", "medium", pr=7)
        rastreador = {"reviewer": "rastreador", "lenses": ["adversarial"], "tier": "T3"}
        self.job["agents"]["reviewers"] = [rastreador]
        self.job["model_failures"] = {"stage": "review", "since": PAST,
                                      "agents": {"atta-rastreador": ["opus"]}}
        # the Cortadora ran on sonnet (medium, round 1): the fallback skips it when it can
        self.assertEqual(self.decide_cfg()["agents"][0]["model"], "haiku")
        # haiku failed too: never a dearer model, so the chain is spent
        self.job["model_failures"]["agents"]["atta-rastreador"] = ["opus", "haiku"]
        action = self.decide_cfg()
        self.assertEqual(action["action"], "wait")
        self.assertIn("La cadena de fallback de T3 se agotó para atta-rastreador", action["reason"])

    def test_independence_never_moves_a_role_override(self):
        self.use(roles={"jardinera-code": "opus"})
        self.stage("review", "high", pr=7)
        self.job["agents"]["reviewers"] = [dict(CODE)]
        self.assertEqual(self.decide_cfg()["agents"][0]["model"], "opus")


class DepsInNextTest(NextBase):
    """F4 T6 (S7): `atta next` makes a job wait for its unmerged BLOCKED-BY blockers, like
    `atta queue` and `atta start` do."""

    def setUp(self):
        super().setUp()
        self.routed(risk="low")
        self.job["intake"]["blocked_by"] = [7]
        self.job["stage"] = "spec"

    def blocker(self, stage, issue=7):
        job = store.new_job(issue, "Bloqueante", None, self.cfg, support.T0)
        job["stage"] = stage
        self.jobs.job_dir(job["id"]).mkdir(parents=True)
        self.jobs.save(job)

    def call(self, *argv):
        parser = argparse.ArgumentParser(prog="atta")
        nxt.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        return code, out.getvalue(), err.getvalue()

    def test_unmerged_blocker_waits_and_writes_nothing(self):
        self.blocker("build")
        self.jobs.save(self.job)
        before = (self.dir / "job.json").read_bytes()
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {
            "action": "wait", "reason": "espera a #7: todavía no está mergeado."})
        self.assertEqual((self.dir / "job.json").read_bytes(), before)

    def test_rejected_blocker_holds(self):
        self.blocker("rejected")
        self.jobs.save(self.job)
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {
            "action": "wait",
            "reason": "su bloqueante #7 fue rechazado: decide una persona."})

    def test_cycle_holds(self):
        self.blocker("build")
        other = self.jobs.load(store.new_job(7, "Bloqueante", None, self.cfg, support.T0)["id"])
        other["intake"] = {"risk": "low", "surfaces": [], "decision": "ACCEPT",
                           "blocked_by": [12], "migration": False}
        self.jobs.save(other)
        self.jobs.save(self.job)
        code, out, _ = self.call("next", JID)
        self.assertEqual(json.loads(out), {
            "action": "wait",
            "reason": "dependencia circular (#12 → #7 → #12): decide una persona."})

    def test_merged_blocker_lets_the_job_advance(self):
        self.blocker("merged")
        self.jobs.save(self.job)
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["agent"], "atta-guia")

    def test_blocker_without_job_resolves_when_its_issue_is_closed(self):
        from unittest import mock
        self.jobs.save(self.job)
        with mock.patch("atta.spine.github.view_issue", return_value={"state": "CLOSED"}):
            code, out, _ = self.call("next", JID)
        self.assertEqual(json.loads(out)["agent"], "atta-guia")

    def test_blocker_without_job_waits_when_github_cannot_be_read(self):
        from unittest import mock
        from atta.spine.github import GhError
        self.jobs.save(self.job)
        with mock.patch("atta.spine.github.view_issue", side_effect=GhError("sin red")):
            code, out, _ = self.call("next", JID)
        self.assertEqual((code, json.loads(out)["action"]), (0, "wait"))

    def test_job_without_blocked_by_is_unaffected(self):
        self.job["intake"].pop("blocked_by")
        self.jobs.save(self.job)
        self.assertEqual(json.loads(self.call("next", JID)[1])["agent"], "atta-guia")

    def test_job_that_waits_for_a_person_is_not_gated(self):
        self.blocker("build")
        self.job["stage"] = "needs-human"
        self.jobs.save(self.job)
        self.assertEqual(json.loads(self.call("next", JID)[1])["action"], "wait")
        self.assertNotIn("espera a #7", self.call("next", JID)[1])

    def test_decide_stays_pure_and_ignores_blockers(self):
        self.blocker("build")
        self.jobs.save(self.job)
        self.assertEqual(nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg)["agent"],
                         "atta-guia")


if __name__ == "__main__":
    unittest.main()
