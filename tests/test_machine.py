import json
import unittest
import warnings

import support  # noqa: F401
from support import TempDirMixin, T0, at, snapshot

from atta.spine import config, events, machine, store


class MachineTestBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cfg = config.with_defaults({"max_total_rounds": 2})
        self.store = store.JobStore(self.tmp / "jobs")
        self.job = self.store.create(12, "Exportar CSV", "u", self.cfg, now=T0)
        self.jid = self.job["id"]
        self.dir = self.store.job_dir(self.jid)
        self.clock = 0

    def go(self, to, **kw):
        self.clock += 10
        return machine.advance(self.store, self.jid, to, now=at(self.clock), **kw)

    def walk(self, *stages):
        job = None
        for s in stages:
            job = self.go(s)
        return job

    def assertRefused(self, to, message):
        job_before = snapshot(self.dir / "job.json")
        events_before = snapshot(self.dir / "events.jsonl")
        with self.assertRaises(machine.Refused) as ctx:
            machine.advance(self.store, self.jid, to, now=at(self.clock + 1))
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(snapshot(self.dir / "job.json"), job_before)
        self.assertEqual(snapshot(self.dir / "events.jsonl"), events_before)


class TableTests(unittest.TestCase):
    def test_stages(self):
        self.assertEqual(machine.STAGES, (
            "intake", "needs-info", "spec", "spec-review", "build", "gate", "review",
            "approve", "merging", "needs-human", "merged", "rejected"))

    def test_transitions_table(self):
        self.assertEqual(machine.TRANSITIONS, {
            "intake": ("spec", "needs-info", "rejected"),
            "needs-info": ("intake", "rejected"),
            "spec": ("spec-review", "build"),
            "spec-review": ("spec", "build"),
            "build": ("gate", "needs-human"),
            "gate": ("review", "build", "needs-human"),
            "review": ("approve", "build", "needs-human"),
            "approve": ("merging", "needs-human"),
            "merging": ("merged", "needs-human"),
            "needs-human": ("build", "rejected"),
            "merged": (),
            "rejected": (),
        })


class AdvanceTests(MachineTestBase):
    def test_legal_path_to_merged(self):
        job = self.walk("spec", "spec-review", "build", "gate", "review", "approve",
                        "merging", "merged")
        self.assertEqual(job["stage"], "merged")
        self.assertEqual(job["ended"], "2025-01-01T12:01:20Z")
        self.assertEqual(self.store.load(self.jid), job)
        evs = events.read_events(self.dir)
        self.assertEqual([(e["from"], e["to"]) for e in evs][:2],
                         [("intake", "spec"), ("spec", "spec-review")])
        self.assertEqual(len(evs), 8)

    def test_event_has_duration_of_left_stage(self):
        self.go("spec")
        self.clock += 25
        self.go("build")
        evs = events.read_events(self.dir)
        self.assertEqual(evs[0]["duration_s"], 10)
        self.assertEqual(evs[1]["duration_s"], 35)
        self.assertEqual(evs[1]["ts"], "2025-01-01T12:00:45Z")

    def test_event_optional_usage_and_job_cost(self):
        self.go("spec", model="m", tier="low", tokens_in=1, tokens_out=2, cost_usd=0.5)
        job = self.go("build", model="m", cost_usd=0.25)
        ev = events.read_events(self.dir)[0]
        self.assertEqual((ev["model"], ev["tier"], ev["tokens_in"], ev["tokens_out"], ev["cost_usd"]),
                         ("m", "low", 1, 2, 0.5))
        self.assertEqual(job["cost_usd"], 0.75)

    def test_unknown_price_keeps_job_cost_null(self):
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            c = events.cost("m", {"tokens_in": 5}, {})
        job = self.go("spec", model="m", cost_usd=c)
        self.assertIsNone(job["cost_usd"])
        self.assertIsNone(events.read_events(self.dir)[0]["cost_usd"])

    def test_illegal_transition_refused_without_changes(self):
        self.go("spec")
        self.assertRefused(
            "merged",
            "Transición ilegal en 12-exportar-csv: spec → merged. "
            "Desde spec solo se puede ir a: spec-review, build.")

    def test_illegal_from_fresh_job_has_no_events_file(self):
        self.assertRefused(
            "build",
            "Transición ilegal en 12-exportar-csv: intake → build. "
            "Desde intake solo se puede ir a: spec, needs-info, rejected.")
        self.assertFalse((self.dir / "events.jsonl").exists())

    def test_unknown_stage_refused(self):
        self.assertRefused("deploy", "Etapa desconocida: deploy.")

    def test_terminal_stage_refused(self):
        self.go("rejected")
        self.assertRefused(
            "intake", "El job 12-exportar-csv está en rejected, una etapa final; no admite transiciones.")

    def test_gate_back_to_build_counts_gate_round(self):
        job = self.walk("spec", "build", "gate", "build")
        self.assertEqual((job["gate_rounds"], job["review_rounds"], job["total_rounds"]), (1, 0, 1))

    def test_review_back_to_build_counts_review_round(self):
        job = self.walk("spec", "build", "gate", "review", "build")
        self.assertEqual((job["gate_rounds"], job["review_rounds"], job["total_rounds"]), (0, 1, 1))

    def test_spec_to_build_does_not_count(self):
        job = self.walk("spec", "build")
        self.assertEqual(job["total_rounds"], 0)

    def test_total_rounds_cap_only_needs_human(self):
        # max_total_rounds = 2: two rework rounds allowed, the third is refused.
        self.walk("spec", "build", "gate", "build", "gate", "review", "build", "gate")
        self.assertEqual(self.store.load(self.jid)["total_rounds"], 2)
        self.assertRefused(
            "build",
            "Tope global alcanzado en 12-exportar-csv: 2 de 2 rondas. "
            "Desde gate solo se puede ir a needs-human.")
        job = self.go("needs-human")
        self.assertEqual(job["stage"], "needs-human")

    def test_human_feedback_never_resets_total_rounds(self):
        self.walk("spec", "build", "gate", "build", "gate", "build", "gate", "needs-human")
        job = self.go("build")  # human feedback: back to build
        self.assertEqual(job["total_rounds"], 2)
        job = self.go("gate")
        self.assertRefused(
            "build",
            "Tope global alcanzado en 12-exportar-csv: 2 de 2 rondas. "
            "Desde gate solo se puede ir a needs-human.")
        self.assertEqual(self.store.load(self.jid)["total_rounds"], 2)

    def test_cap_is_frozen_in_job_from_config(self):
        self.assertEqual(self.store.load(self.jid)["max_total_rounds"], 2)

    def test_sync_attempts_counter_on_merging_retry(self):
        job = self.walk("spec", "build", "gate", "review", "approve", "merging")
        self.assertEqual(job["sync_attempts"], 1)


class StoredEventsJsonTests(MachineTestBase):
    def test_events_file_is_jsonl(self):
        self.walk("spec", "build")
        lines = (self.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(l)["job"] for l in lines], [self.jid, self.jid])



class PendingAgentTests(MachineTestBase):
    """T12: the model `atta next` decided for a stage lands on the event that leaves it."""

    def set_pending(self, pending):
        job = self.store.load(self.jid)
        job["pending_agent"] = pending
        self.store.save(job)

    def test_event_gets_model_tier_and_agent_then_pending_is_cleared(self):
        self.set_pending({"stage": "intake", "role": "exploradora", "agent": "atta-exploradora",
                          "model": "haiku", "tier": "T1", "at": "2025-01-01T12:00:01Z"})
        job = self.go("spec")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual(ev, {"ts": "2025-01-01T12:00:10Z", "job": self.jid, "from": "intake",
                              "to": "spec", "duration_s": 10, "model": "haiku", "tier": "T1",
                              "agent": "atta-exploradora", "cost_usd": None})
        self.assertNotIn("pending_agent", job)
        self.assertNotIn("pending_agent", self.store.load(self.jid))
        self.assertIsNone(job["cost_usd"])
        # The next transition has no agent: no model fields.
        self.go("build")
        self.assertEqual(set(events.read_events(self.dir)[-1]),
                         {"ts", "job", "from", "to", "duration_s"})

    def test_group_attaches_models_list(self):
        self.walk("spec", "build", "gate", "review")
        self.set_pending([
            {"stage": "review", "role": "jardinera-code", "agent": "atta-jardinera-code",
             "model": "sonnet", "tier": "T2", "at": "x"},
            {"stage": "review", "role": "soldado", "agent": "atta-soldado", "model": "opus",
             "tier": "T3", "at": "x"}])
        job = self.go("approve")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual(ev["models"], [
            {"agent": "atta-jardinera-code", "model": "sonnet", "tier": "T2"},
            {"agent": "atta-soldado", "model": "opus", "tier": "T3"}])
        self.assertIsNone(ev["cost_usd"])
        self.assertNotIn("model", ev)
        self.assertNotIn("pending_agent", job)

    def test_pending_from_another_stage_is_dropped_without_attaching(self):
        self.go("spec")
        self.set_pending({"stage": "intake", "role": "exploradora", "agent": "atta-exploradora",
                          "model": "haiku", "tier": "T1", "at": "x"})
        job = self.go("build")
        self.assertNotIn("model", events.read_events(self.dir)[-1])
        self.assertNotIn("pending_agent", job)

    def test_explicit_model_wins_over_pending(self):
        self.set_pending({"stage": "intake", "role": "exploradora", "agent": "atta-exploradora",
                          "model": "haiku", "tier": "T1", "at": "x"})
        self.go("spec", model="m", tier="T9")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual((ev["model"], ev["tier"]), ("m", "T9"))

    def test_refused_transition_keeps_pending(self):
        self.set_pending({"stage": "intake", "role": "exploradora", "agent": "atta-exploradora",
                          "model": "haiku", "tier": "T1", "at": "x"})
        self.assertRefused("build", "Transición ilegal en 12-exportar-csv: intake → build. "
                                    "Desde intake solo se puede ir a: spec, needs-info, rejected.")
        self.assertEqual(self.store.load(self.jid)["pending_agent"]["model"], "haiku")


if __name__ == "__main__":
    unittest.main()
