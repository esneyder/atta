import json
import unittest
import warnings

import support  # noqa: F401
from support import TempDirMixin, T0, at, snapshot

from atta.spine import config, events, machine, store, voice


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
            "approve", "merging", "awaiting-merge", "needs-human", "merged", "rejected"))

    def test_transitions_table(self):
        self.assertEqual(machine.TRANSITIONS, {
            "intake": ("spec", "needs-info", "rejected"),
            "needs-info": ("intake", "rejected"),
            "spec": ("spec-review", "build"),
            "spec-review": ("spec", "build", "needs-human"),
            "build": ("gate", "needs-human"),
            "gate": ("review", "build", "needs-human"),
            "review": ("approve", "build", "needs-human"),
            "approve": ("merging", "awaiting-merge", "review", "build", "needs-human"),
            "merging": ("merged", "review", "build", "needs-human"),
            "awaiting-merge": ("merged", "build", "needs-human"),
            "needs-human": ("build", "rejected", "awaiting-merge", "gate"),
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

    def test_awaiting_merge_waits_for_the_human_merge(self):
        # S13: approve → awaiting-merge (the PR waits for a person) → merged or needs-human.
        self.assertNotIn("awaiting-merge", machine.TERMINAL)
        job = self.walk("spec", "build", "gate", "review", "approve", "awaiting-merge")
        self.assertEqual((job["stage"], job["sync_attempts"]), ("awaiting-merge", 0))
        self.assertIsNone(job.get("ended"))
        self.assertRefused(
            "merging",
            "Transición ilegal en 12-exportar-csv: awaiting-merge → merging. "
            "Desde awaiting-merge solo se puede ir a: merged, build, needs-human.")
        job = self.go("merged")
        self.assertEqual(job["stage"], "merged")
        self.assertEqual([(e["from"], e["to"]) for e in events.read_events(self.dir)][-2:],
                         [("approve", "awaiting-merge"), ("awaiting-merge", "merged")])

    def test_awaiting_merge_can_go_to_needs_human(self):
        job = self.walk("spec", "build", "gate", "review", "approve", "awaiting-merge",
                        "needs-human")
        self.assertEqual(job["stage"], "needs-human")

    def test_needs_human_can_go_back_to_awaiting_merge(self):
        # L31 (c): `atta handoff` retakes a handoff escalation when the approval still holds.
        job = self.walk("spec", "build", "gate", "review", "approve", "needs-human",
                        "awaiting-merge")
        self.assertEqual((job["stage"], job["total_rounds"], job["sync_attempts"]),
                         ("awaiting-merge", 0, 0))
        self.assertIsNone(job.get("ended"))
        self.assertEqual([(e["from"], e["to"]) for e in events.read_events(self.dir)][-1],
                         ("needs-human", "awaiting-merge"))

    def test_needs_human_still_refuses_merging_and_merged(self):
        self.walk("spec", "build", "needs-human")
        for to in ("merging", "merged"):
            self.assertRefused(
                to,
                f"Transición ilegal en 12-exportar-csv: needs-human → {to}. "
                "Desde needs-human solo se puede ir a: build, rejected, awaiting-merge, gate.")

    # T9: a Filtro stopped by configuration goes back to gate in the same round.
    def set_gate_failure(self, kind):
        job = self.store.load(self.jid)
        job["gate_failure"] = {"kind": kind, "reason": "atta/checks.json no tiene checks."}
        self.store.save(job)

    def test_needs_human_back_to_gate_after_a_config_stop_keeps_counters(self):
        self.walk("spec", "build", "gate")
        self.set_gate_failure("config")
        before = self.go("needs-human")
        job = self.go("gate")
        self.assertEqual(job["stage"], "gate")
        for key in ("gate_rounds", "review_rounds", "total_rounds", "sync_attempts"):
            self.assertEqual(job[key], before[key], key)
        self.assertEqual((job["gate_rounds"], job["total_rounds"]), (0, 0))
        self.assertEqual([(e["from"], e["to"]) for e in events.read_events(self.dir)][-2:],
                         [("gate", "needs-human"), ("needs-human", "gate")])

    def test_needs_human_to_gate_is_refused_without_a_config_stop(self):
        self.walk("spec", "build", "gate", "needs-human")
        message = ("El job 12-exportar-csv no vuelve de needs-human a gate: el Filtro solo se "
                   "retoma cuando lo detuvo una falla de configuración.")
        self.assertRefused("gate", message)
        self.set_gate_failure("code")
        self.assertRefused("gate", message)

    def test_code_fail_back_to_build_still_counts_a_round(self):
        job = self.walk("spec", "build", "gate", "build")
        self.assertEqual((job["gate_rounds"], job["total_rounds"]), (1, 1))

    def test_voice_for_a_config_stop_and_its_retry(self):
        job = {"gate_rounds": 0, "gate_failure": {"kind": "config",
                                                  "reason": "atta/checks.json no tiene checks."}}
        self.assertEqual(voice.for_transition(job, "gate", "needs-human"),
                         "El Filtro se detuvo por configuración: esto lo corrige una persona en "
                         "el checkout principal, no la Cortadora, y no gasta ronda. "
                         "atta/checks.json no tiene checks.")
        self.assertEqual(voice.for_transition(job, "needs-human", "gate"),
                         "La configuración ya está lista: el Filtro vuelve a correr en la misma "
                         "ronda.")
        # A code stop (rounds spent) keeps its message.
        self.assertEqual(voice.for_transition({"gate_rounds": 3}, "gate", "needs-human"),
                         "Tres rondas sin pasar el filtro. Te lo dejamos a ti.")

    def test_sync_attempts_counter_on_merging_retry(self):
        job = self.walk("spec", "build", "gate", "review", "approve", "merging")
        self.assertEqual(job["sync_attempts"], 1)

    # F3.x K7/S4/S2: a sync that changed the job diff or conflicted sends the job back.
    def test_sync_rework_from_merging_keeps_the_attempt_already_counted(self):
        for to in ("review", "build"):
            with self.subTest(to=to):
                self.setUp()
                job = self.walk("spec", "build", "gate", "review", "approve", "merging", to)
                self.assertEqual((job["stage"], job["sync_attempts"], job["total_rounds"]),
                                 (to, 1, 0))
                self.assertEqual([(e["from"], e["to"]) for e in events.read_events(self.dir)][-1],
                                 ("merging", to))

    def test_sync_rework_from_approve_and_awaiting_merge_counts_one_attempt(self):
        for walk, to in ((("approve",), "review"), (("approve",), "build"),
                         (("approve", "awaiting-merge"), "build")):
            with self.subTest(walk=walk, to=to):
                self.setUp()
                job = self.walk("spec", "build", "gate", "review", *walk, to)
                self.assertEqual((job["stage"], job["sync_attempts"], job["total_rounds"]),
                                 (to, 1, 0))

    def test_awaiting_merge_cannot_go_to_review(self):
        self.walk("spec", "build", "gate", "review", "approve", "awaiting-merge")
        self.assertRefused(
            "review",
            "Transición ilegal en 12-exportar-csv: awaiting-merge → review. "
            "Desde awaiting-merge solo se puede ir a: merged, build, needs-human.")

    def test_rework_into_build_is_not_a_round_even_at_the_global_cap(self):
        self.walk("spec", "build", "gate", "build", "gate", "review", "approve", "merging")
        job = self.go("build")
        self.assertEqual((job["total_rounds"], job["gate_rounds"], job["review_rounds"]),
                         (1, 1, 0))

    # S9: the Vigía's REVISE cap. spec_reviews counts the REVISEs the spine acted on.
    def test_revise_counts_spec_reviews_without_spending_rounds(self):
        job = self.walk("spec", "spec-review", "spec", "spec-review", "spec", "spec-review")
        self.assertEqual(job["spec_reviews"], 2)
        self.assertEqual((job["total_rounds"], job["gate_rounds"], job["review_rounds"]),
                         (0, 0, 0))
        self.assertEqual(machine.MAX_SPEC_REVISE, 3)
        self.assertTrue(machine.spec_revise_capped(job))

    def test_third_revise_cannot_go_back_to_spec_only_needs_human(self):
        self.walk("spec", "spec-review", "spec", "spec-review", "spec", "spec-review")
        self.assertRefused(
            "spec",
            "La Vigía ya pidió cambios al spec de 12-exportar-csv 2 veces (REVISE); la vez 3 "
            "no vuelve a spec. Desde spec-review solo se puede ir a build o needs-human.")
        job = self.go("needs-human")
        self.assertEqual((job["stage"], job["spec_reviews"], job["total_rounds"]),
                         ("needs-human", 3, 0))
        # A person decides: back to build like any needs-human.
        self.assertEqual(self.go("build")["stage"], "build")

    def test_jobs_without_the_counter_count_zero(self):
        job = self.walk("spec", "spec-review")
        self.assertNotIn("spec_reviews", job)
        self.assertEqual(machine.spec_reviews(job), 0)
        self.assertFalse(machine.spec_revise_capped(job))
        self.assertEqual(self.go("spec")["spec_reviews"], 1)

    def test_ready_to_build_keeps_the_counter(self):
        job = self.walk("spec", "spec-review", "spec", "spec-review", "build")
        self.assertEqual(job["spec_reviews"], 1)

    def test_voice_for_the_revise_cap(self):
        self.assertEqual(voice.for_transition({"spec_reviews": 3}, "spec-review", "needs-human"),
                         "La Vigía pidió cambios al spec tres veces (REVISE) y sigue sin estar "
                         "listo. Te lo dejamos a ti: revisa spec-review.md y deja tu feedback "
                         "con /atta.")


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


class BlockedTimeTests(MachineTestBase):
    """F4.x T3 (S3): the BLOCKED-BY wait `atta next` recorded leaves the stage time and goes to
    the event as `blocked_s`; a job that never waited emits the same event as before."""

    def set_blocked(self, **fields):
        job = self.store.load(self.jid)
        job.update(fields)
        self.store.save(job)

    def test_open_wait_is_subtracted_and_reported(self):
        self.set_blocked(blocked_since=at(3).isoformat().replace("+00:00", "Z"))
        job = self.go("spec")  # stage time 10 s, blocked 7 s
        ev = events.read_events(self.dir)[-1]
        self.assertEqual(ev, {"ts": "2025-01-01T12:00:10Z", "job": self.jid, "from": "intake",
                              "to": "spec", "duration_s": 3, "blocked_s": 7})
        for key in ("blocked_since", "blocked_s"):
            self.assertNotIn(key, job)
            self.assertNotIn(key, self.store.load(self.jid))

    def test_closed_and_open_waits_add_up(self):
        self.set_blocked(blocked_s=2, blocked_since="2025-01-01T12:00:08Z")
        self.go("spec")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual((ev["duration_s"], ev["blocked_s"]), (6, 4))

    def test_closed_wait_only(self):
        self.set_blocked(blocked_s=4.5)
        self.go("spec")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual((ev["duration_s"], ev["blocked_s"]), (5.5, 4.5))

    def test_blocked_never_exceeds_the_stage_time(self):
        # A wait recorded before the stage started counts from stage_since; never negative.
        self.set_blocked(blocked_since="2024-12-31T00:00:00Z", blocked_s=50)
        self.go("spec")
        ev = events.read_events(self.dir)[-1]
        self.assertEqual((ev["duration_s"], ev["blocked_s"]), (0, 10))

    def test_never_blocked_event_is_unchanged(self):
        job = self.go("spec")
        self.assertEqual(events.read_events(self.dir)[-1],
                         {"ts": "2025-01-01T12:00:10Z", "job": self.jid, "from": "intake",
                          "to": "spec", "duration_s": 10})
        self.assertFalse({"blocked_since", "blocked_s"} & set(job))

    def test_wait_is_only_on_the_stage_it_happened_in(self):
        self.set_blocked(blocked_since="2025-01-01T12:00:05Z")
        self.go("spec")
        self.go("build")
        self.assertEqual(set(events.read_events(self.dir)[-1]),
                         {"ts", "job", "from", "to", "duration_s"})

    def test_refused_transition_keeps_the_wait(self):
        self.set_blocked(blocked_since="2025-01-01T12:00:05Z")
        self.assertRefused("build", "Transición ilegal en 12-exportar-csv: intake → build. "
                           "Desde intake solo se puede ir a: spec, needs-info, rejected.")
        self.assertEqual(self.store.load(self.jid)["blocked_since"], "2025-01-01T12:00:05Z")


if __name__ == "__main__":
    unittest.main()
