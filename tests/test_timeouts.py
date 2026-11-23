"""F4 T5 (S6): `atta next` reports a pending agent that outlived the limit of its stage."""

import json
import unittest
from unittest import mock

import support  # noqa: F401
from support import at, snapshot

from atta.spine import config, events, timeouts
from atta.spine import next as nxt
from atta.spine.timeutil import to_iso
from test_next import CODE, JID, SECURITY, NextBase

START = to_iso(at(0))


class TimeoutBase(NextBase):
    """`self.call("next", JID)` runs with the clock at T0 + `self.now` seconds."""

    now = 0

    def next(self, seconds=None):
        clock = at(self.now if seconds is None else seconds)
        with mock.patch.object(nxt, "utcnow", return_value=clock):
            return self.call("next", JID)

    def call(self, *argv):
        from test_next import CommandTest
        return CommandTest.call(self, *argv)

    def pend(self, agent, model, tier, stage=None, when=START):
        stage = stage or self.job["stage"]
        return {"stage": stage, "role": agent[len("atta-"):], "agent": agent, "model": model,
                "tier": tier, "at": when}

    def stage_events(self):
        return [e for e in events.read_events(self.dir) if e.get("event") == "stage_timeout"]

    def intake_job(self):
        self.set(pending_agent=self.pend("atta-exploradora", "haiku", "T1"))

    def build_job(self):
        self.routed(risk="low")
        self.with_worktree()
        self.job["stage"] = "build"
        self.set(pending_agent=self.pend("atta-cortadora", "sonnet", "T2"))

    def review_job(self, *reviewers):
        self.routed(risk="high", reviewers=reviewers)
        self.with_worktree()
        self.job.update(stage="review", pr=7)
        self.set(pending_agent=[
            self.pend("atta-jardinera-code", "sonnet", "T2"),
            self.pend("atta-soldado", "opus", "T3")])


class RetryTest(TimeoutBase):
    def test_under_the_limit_nothing_is_written(self):
        self.intake_job()
        before = snapshot(self.dir / "job.json")
        code, out, err = self.next(600)  # exactly the limit is not over it
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), nxt.decide(self.jobs.load(JID), self.dir, self.root,
                                                     self.cfg))
        self.assertEqual(snapshot(self.dir / "job.json"), before)
        self.assertEqual(events.read_events(self.dir), [])

    def test_first_timeout_retries_the_same_agent_and_records_one_event(self):
        self.intake_job()
        code, out, err = self.next(601)
        self.assertEqual(code, 0)
        action = json.loads(out)
        self.assertEqual(action, nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg))
        self.assertEqual((action["action"], action["agent"]), ("spawn", "atta-exploradora"))
        self.assertEqual(err, "Atta: atta-exploradora superó el límite de la etapa intake "
                              "(600 s); lo reintento.\n")
        self.assertEqual(self.stage_events(), [{
            "ts": to_iso(at(601)), "job": JID, "event": "stage_timeout", "stage": "intake",
            "agent": "atta-exploradora", "model": "haiku", "limit_s": 600, "elapsed_s": 601,
            "outcome": "retry"}])
        job = self.jobs.load(JID)
        self.assertEqual(job["pending_agent"]["at"], to_iso(at(601)))  # the clock restarts
        self.assertEqual(job["stage_timeouts"], {"stage": "intake", "since": job["stage_since"],
                                                 "agents": {"atta-exploradora": 1}})
        self.assertNotIn("model_failures", job)  # S4: a timeout never eats the fallback chain
        # Asking again before the new clock expires: same spawn, nothing new written.
        before = snapshot(self.dir / "job.json")
        code, out, err = self.next(700)
        self.assertEqual((code, err, json.loads(out)["agent"]),
                         (0, "", "atta-exploradora"))
        self.assertEqual(len(self.stage_events()), 1)
        self.assertEqual(snapshot(self.dir / "job.json"), before)

    def test_round_is_recorded_where_it_applies(self):
        self.build_job()
        self.set(total_rounds=1,  # round 2: the Cortadora escalates to T3
                 pending_agent=self.pend("atta-cortadora", "opus", "T3"))
        self.next(3601)
        self.assertEqual(self.stage_events()[0]["round"], 2)

    def test_model_failures_and_fallback_events_stay_untouched(self):
        self.build_job()
        record = {"stage": "build", "since": self.job["stage_since"],
                  "agents": {"atta-otra": ["opus"]}}
        self.set(model_failures=record)
        self.next(4000)
        job = self.jobs.load(JID)
        self.assertEqual(job["model_failures"], record)
        self.assertEqual([e for e in events.read_events(self.dir)
                          if e.get("event") == "model_fallback"], [])

    def test_a_group_retries_only_the_agents_over_the_limit(self):
        self.review_job(CODE, SECURITY)
        self.job["pending_agent"][1]["at"] = to_iso(at(1000))
        self.set(pending_agent=self.job["pending_agent"])
        code, out, err = self.next(1201)  # code: 1201 s; soldado: 201 s
        action = json.loads(out)
        self.assertEqual(action["action"], "spawn-group")
        self.assertEqual([e["agent"] for e in self.stage_events()], ["atta-jardinera-code"])
        self.assertEqual(self.stage_events()[0]["round"], 1)
        pending = {e["agent"]: e["at"] for e in self.jobs.load(JID)["pending_agent"]}
        self.assertEqual(pending, {"atta-jardinera-code": to_iso(at(1201)),
                                   "atta-soldado": to_iso(at(1000))})

    def test_rerunning_next_does_not_postpone_the_clock(self):
        self.intake_job()
        self.next(100)
        self.assertEqual(self.jobs.load(JID)["pending_agent"]["at"], START)
        code, out, err = self.next(601)
        self.assertEqual(len(self.stage_events()), 1)  # the original clock expired

    def test_a_build_retry_starts_its_own_clock(self):
        self.build_job()
        self.write("round-1/build.md", "STATUS: DONE\n\n## Summary\n\nX.\n")
        self.set(pending_agent=self.pend("atta-cortadora", "sonnet", "T2", when=START),
                 stage_since=to_iso(at(-10)))
        code, out, err = self.next(10)
        self.assertEqual(json.loads(out)["agent"], "atta-cortadora")
        self.assertEqual(self.jobs.load(JID)["pending_agent"]["at"], to_iso(at(10)))

    def test_stale_or_malformed_entries_are_ignored(self):
        for pending in (self.pend("atta-exploradora", "haiku", "T1", when="2000-01-01T00:00:00Z"),
                        self.pend("atta-exploradora", "haiku", "T1", when="not a date"),
                        self.pend("atta-exploradora", "haiku", "T1", stage="spec"),
                        self.pend("atta-exploradora", "opus", "T3")):
            with self.subTest(pending=pending):
                self.set(pending_agent=pending)
                code, out, err = self.next(99999)
                self.assertEqual((code, err, json.loads(out)["action"]), (0, "", "spawn"))
                self.assertEqual(self.stage_events(), [])

    def test_config_overrides_one_stage_and_the_rest_keep_defaults(self):
        self.cfg = config.with_defaults({"stage_timeouts_s": {"intake": 30}})
        self.intake_job()
        self.next(31)
        self.assertEqual(self.stage_events()[0]["limit_s"], 30)
        self.assertEqual(timeouts.limits(self.cfg)["build"], timeouts.DEFAULT_LIMITS_S["build"])
        self.assertEqual(timeouts.limits(config.with_defaults({})), timeouts.DEFAULT_LIMITS_S)


class EscalationTest(TimeoutBase):
    def test_second_timeout_in_build_goes_to_needs_human(self):
        self.build_job()
        self.next(3601)
        code, out, err = self.next(3601 + 3601)
        self.assertEqual((code, json.loads(out)), (0, {
            "action": "run",
            "argv": ["atta", "label", JID, "add", "atta:needs-human"],
            "then": [["atta", "comment", JID, "--role", "cortadora", "--file",
                      str(self.dir / "stage-timeout.md")],
                     ["atta", "advance", JID, "needs-human"]]}))
        self.assertEqual(err, "Atta: atta-cortadora superó otra vez el límite de la etapa "
                              "build; el job pasa a una persona.\n")
        self.assertEqual([(e["outcome"], e["round"], e["elapsed_s"])
                          for e in self.stage_events()], [("retry", 1, 3601),
                                                          ("needs-human", 1, 3601)])
        self.assertEqual((self.dir / "stage-timeout.md").read_text(encoding="utf-8"), (
            "Un agente no terminó dentro del límite de la etapa build y su reintento tampoco. "
            "Te lo dejamos a ti: revisa el job y deja tu feedback con /atta.\n\n"
            "- atta-cortadora (sonnet): 3601 s de 3600 s permitidos.\n"))
        # A retry of the driver (a step failed): same action, no third event.
        code, again, _ = self.next(3601 + 3601)
        self.assertEqual(json.loads(again), json.loads(out))
        self.assertEqual(len(self.stage_events()), 2)

    def test_needs_human_stages_escalate_with_their_signer(self):
        self.routed(risk="medium", spec_review=True)
        self.with_worktree()
        for stage, signer in {"spec-review": "vigia", "review": "jardineras",
                              "approve": "reina"}.items():
            with self.subTest(stage=stage):
                self.job.pop("pending_agent", None)
                self.job.update(stage=stage, pr=7, stage_since=START)
                self.jobs.save(self.job)
                action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg)
                spawn = action["agents"][0] if action["action"] == "spawn-group" else action
                self.set(pending_agent=self.pend(spawn["agent"], spawn["model"], "T2"),
                         stage_timeouts={"stage": stage, "since": START,
                                         "agents": {spawn["agent"]: 1}})
                code, out, err = self.next(10000)
                result = json.loads(out)
                self.assertEqual(result["then"][0][4], signer)
                self.assertEqual(result["then"][1], ["atta", "advance", JID, "needs-human"])
                self.assertEqual(self.stage_events()[-1]["outcome"], "needs-human")

    def test_intake_has_no_needs_human_step_so_it_labels_comments_and_then_waits(self):
        self.intake_job()
        self.next(601)
        code, out, err = self.next(1202)
        self.assertEqual(json.loads(out), {
            "action": "run",
            "argv": ["atta", "label", JID, "add", "atta:needs-human"],
            "then": [["atta", "comment", JID, "--role", "exploradora", "--file",
                      str(self.dir / "stage-timeout.md"), "--on", "issue"]]})
        before = len(events.read_events(self.dir))
        code, out, err = self.next(1203)
        self.assertEqual(json.loads(out), {"action": "wait", "reason": (
            f"atta-exploradora superó dos veces el límite de la etapa intake en {JID} y esa "
            "etapa no tiene paso a needs-human: el job queda en manos de una persona. Revisa "
            "el job y muévelo con /atta.")})
        self.assertEqual(len(events.read_events(self.dir)), before)
        self.assertEqual([e["outcome"] for e in self.stage_events()],
                         ["retry", "needs-human"])

    def test_a_new_stage_entry_resets_the_counts(self):
        self.intake_job()
        self.next(601)
        job = self.jobs.load(JID)
        self.set(stage_since=to_iso(at(5000)),
                 pending_agent=self.pend("atta-exploradora", "haiku", "T1", when=to_iso(at(5000))))
        self.next(5000 + 601)
        self.assertEqual([e["outcome"] for e in self.stage_events()], ["retry", "retry"])
        self.assertEqual(timeouts.count(self.jobs.load(JID), "atta-exploradora"), 1)
        self.assertEqual(job["stage_timeouts"]["agents"], {"atta-exploradora": 1})


class PreserveTest(TimeoutBase):
    def test_a_job_without_timeouts_prints_what_decide_prints(self):
        self.jobs.save(self.job)
        code, out, err = self.next(5)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), nxt.decide(self.jobs.load(JID), self.dir, self.root,
                                                     self.cfg))
        job = self.jobs.load(JID)
        self.assertEqual(job["pending_agent"], self.pend("atta-exploradora", "haiku", "T1",
                                                         when=to_iso(at(5))))
        self.assertNotIn("stage_timeouts", job)
        self.assertEqual(events.read_events(self.dir), [])


if __name__ == "__main__":
    unittest.main()
