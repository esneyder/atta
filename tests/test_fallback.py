"""F4 T4 (S4): `atta model-failed` records a quota, rate-limit or network failure of a pending
agent and `atta next` relaunches it on the next model of its tier's fallback chain."""

import argparse
import contextlib
import io
import json
import unittest

import support  # noqa: F401
from support import TempDirMixin

from atta.spine import cli, config, events, fallback, machine, store
from atta.spine import models as mdl
from atta.spine import next as nxt

JID = "12-agregar-exportacion-a-csv"
PAST = "2020-01-01T00:00:00Z"
CHAINS = {"T3": ["sonnet", "haiku"], "T2": ["haiku"]}
CODE = {"reviewer": "code", "lenses": ["reliability"], "tier": "T2"}
SECURITY = {"reviewer": "security", "lenses": ["security"], "tier": "T3"}
UX = {"reviewer": "ux", "lenses": ["ux"], "tier": "T1"}
CHANGES = ("VERDICT: CHANGES\n\n## Summary\n\nFalta un test.\n\n## Findings\n\n"
           "- [major] src/a.py:3 — falla con vacío — agrega un test.\n\n"
           "PREVENTION: checklist item \"empty input test\".\n")


class FallbackBase(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp.resolve()
        self.use()
        self.jobs = store.JobStore(self.root / "atta" / "jobs")
        self.job = store.new_job(12, "Agregar exportación a CSV", None, self.cfg, support.T0)
        self.job["stage_since"] = PAST
        self.dir = self.jobs.job_dir(JID)
        self.dir.mkdir(parents=True)
        self.wt = self.root / ".worktrees" / JID

    def use(self, **entry):
        entry.setdefault("fallbacks", CHAINS)
        self.cfg = config.with_defaults({"models": {"claude-code": entry}})

    def routed(self, risk="low", reviewers=(CODE,), spec_review=False):
        self.job["intake"] = {"risk": risk, "surfaces": [], "decision": "ACCEPT"}
        self.job["agents"] = {"risk": risk, "surfaces": [], "spec_review": spec_review,
                              "reviewers": [dict(r) for r in reviewers]}

    def build(self, rounds=2):
        """A build job whose Cortadora escalates to T3 (opus) from total_rounds 2."""
        self.routed()
        self.wt.mkdir(parents=True)
        self.job.update(stage="build", worktree=str(self.wt), total_rounds=rounds)
        self.jobs.save(self.job)

    def write(self, rel, text):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def call(self, *argv):
        parser = argparse.ArgumentParser(prog="atta")
        sub = parser.add_subparsers(dest="command")
        nxt.register(sub)
        fallback.register(sub)
        args = parser.parse_args(list(argv))
        args.root = self.root
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args, self.cfg, self.jobs)
        return code, out.getvalue(), err.getvalue()

    def next(self):
        code, out, err = self.call("next", JID)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def failed(self, agent, reason):
        return self.call("model-failed", JID, "--agent", agent, "--reason", reason)

    def fallback_events(self):
        evs = [e for e in events.read_events(self.dir) if e.get("event") == "model_fallback"]
        for e in evs:
            self.assertTrue(e.pop("ts").endswith("Z"))
        return evs

    def snapshot(self):
        events_file = self.dir / "events.jsonl"
        return ((self.dir / "job.json").read_bytes(),
                events_file.read_bytes() if events_file.exists() else None)


class ModelFailedTest(FallbackBase):
    def test_walks_the_tier_chain_and_waits_when_it_is_spent(self):
        self.build()
        self.assertEqual(self.next()["model"], "opus")
        code, out, err = self.failed("atta-cortadora", "quota")
        self.assertEqual((code, out, err), (0, (
            f"atta-cortadora falló en opus por cuota en {JID}: el próximo intento usa sonnet "
            "(cadena de fallback de T3).\n"), ""))
        self.assertEqual(self.fallback_events(), [{
            "job": JID, "event": "model_fallback", "stage": "build", "round": 3,
            "agent": "atta-cortadora", "tier": "T3", "from_model": "opus",
            "to_model": "sonnet", "reason": "quota"}])
        self.assertEqual(self.jobs.load(JID)["model_failures"],
                         {"stage": "build", "since": PAST,
                          "agents": {"atta-cortadora": ["opus"]}})
        action = self.next()
        self.assertEqual((action["agent"], action["model"]), ("atta-cortadora", "sonnet"))
        self.assertEqual(set(action), {"action", "agent", "model", "prompt"})
        pending = self.jobs.load(JID)["pending_agent"]
        self.assertEqual((pending["model"], pending["tier"]), ("sonnet", "T3"))

        code, out, err = self.failed("atta-cortadora", "rate-limit")
        self.assertEqual((code, out, err), (0, (
            f"atta-cortadora falló en sonnet por rate limit en {JID}: el próximo intento usa "
            "haiku (cadena de fallback de T3).\n"), ""))
        self.assertEqual(self.next()["model"], "haiku")

        code, out, err = self.failed("atta-cortadora", "network")
        self.assertEqual((code, out, err), (0, (
            f"atta-cortadora falló en haiku por red en {JID}: la cadena de fallback de T3 se "
            "agotó; Atta no sube a un modelo más caro y `atta next` espera.\n"), ""))
        self.assertEqual([(e["from_model"], e["to_model"], e["reason"])
                          for e in self.fallback_events()],
                         [("opus", "sonnet", "quota"), ("sonnet", "haiku", "rate-limit"),
                          ("haiku", None, "network")])
        self.assertEqual(self.next(), {"action": "wait", "reason": (
            f"La cadena de fallback de T3 se agotó para atta-cortadora en {JID}: falló en opus, "
            "sonnet, haiku por cuota, rate limit o red y Atta no sube a un modelo más caro. El "
            "próximo /atta run lo vuelve a intentar con opus.")})
        # The wait is reported once: the failures of that agent are cleared for the next pass.
        self.assertNotIn("model_failures", self.jobs.load(JID))
        self.assertEqual(self.next()["model"], "opus")

    def test_escalated_agent_uses_the_t3_chain_not_its_own_tier(self):
        self.build()
        self.next()
        self.failed("atta-cortadora", "quota")
        self.assertEqual(self.next()["model"], "sonnet")  # T2's chain would give haiku

    def test_no_chain_means_wait_on_the_first_failure(self):
        self.use(fallbacks={})
        self.build(rounds=0)
        self.assertEqual(self.next()["model"], "sonnet")
        code, out, err = self.failed("atta-cortadora", "quota")
        self.assertEqual((code, out, err), (0, (
            f"atta-cortadora falló en sonnet por cuota en {JID}: la cadena de fallback de T2 se "
            "agotó; Atta no sube a un modelo más caro y `atta next` espera.\n"), ""))
        self.assertEqual(self.fallback_events()[0]["to_model"], None)
        self.assertEqual(self.next()["action"], "wait")

    def test_refused_inputs_write_nothing(self):
        self.build()
        self.next()
        reason = ("El motivo «{}» no vale: usa quota, rate-limit o network. Un error de "
                  "contexto nunca cambia de modelo. No registré nada.\n")
        not_pending = ("{} no está pendiente en " + JID + " (etapa build): solo se registra la "
                       "falla de un agente que `atta next` lanzó en esta etapa. No registré "
                       "nada.\n")
        cases = (
            (("atta-cortadora", "context"), reason.format("context")),
            (("atta-cortadora", "contexto"), reason.format("contexto")),
            (("atta-cortadora", "QUOTA"), reason.format("QUOTA")),
            (("atta-reina", "quota"), not_pending.format("atta-reina")),
            (("cortadora", "quota"), not_pending.format("cortadora")),
        )
        for (agent, why), message in cases:
            with self.subTest(agent=agent, reason=why):
                before = self.snapshot()
                self.assertEqual(self.failed(agent, why), (2, "", message))
                self.assertEqual(self.snapshot(), before)

    def test_pending_agent_of_another_stage_is_refused(self):
        self.build()
        self.next()
        job = self.jobs.load(JID)
        job["stage"] = "gate"
        self.jobs.save(job)
        before = self.snapshot()
        self.assertEqual(self.failed("atta-cortadora", "quota"), (2, "", (
            f"atta-cortadora no está pendiente en {JID} (etapa gate): solo se registra la falla "
            "de un agente que `atta next` lanzó en esta etapa. No registré nada.\n")))
        self.assertEqual(self.snapshot(), before)

    def test_repeating_the_same_failure_changes_nothing(self):
        self.build()
        self.next()
        self.failed("atta-cortadora", "quota")
        before = self.snapshot()
        self.assertEqual(self.failed("atta-cortadora", "network"), (0, (
            f"atta-cortadora ya tenía registrada la falla en opus en {JID}; no cambié nada.\n"),
            ""))
        self.assertEqual(self.snapshot(), before)

    def test_failures_of_an_earlier_stage_entry_do_not_count(self):
        self.build()
        self.job["model_failures"] = {"stage": "build", "since": "2019-01-01T00:00:00Z",
                                      "agents": {"atta-cortadora": ["opus"]}}
        self.jobs.save(self.job)
        self.assertEqual(self.next()["model"], "opus")

    def test_decide_with_failures_stays_pure(self):
        self.build()
        self.next()
        self.failed("atta-cortadora", "quota")
        before = self.snapshot()
        action = nxt.decide(self.jobs.load(JID), self.dir, self.root, self.cfg)
        self.assertEqual(action["model"], "sonnet")
        self.assertEqual(self.snapshot(), before)

    def test_without_failures_next_keeps_the_routed_model(self):
        # PRESERVE: a configured chain changes nothing until an agent fails.
        self.build()
        action = self.next()
        self.assertEqual((action["agent"], action["model"]), ("atta-cortadora", "opus"))
        self.assertEqual(self.jobs.load(JID)["pending_agent"]["model"], "opus")
        self.assertNotIn("model_failures", self.jobs.load(JID))


    def test_repeat_after_the_spent_wait_is_refused_and_never_climbs(self):  # S4 (B1)
        self.build()
        self.next()
        for _ in range(3):
            self.failed("atta-cortadora", "quota")
            action = self.next()
        self.assertEqual(action["action"], "wait")
        self.assertNotIn("atta-cortadora", json.dumps(self.jobs.load(JID).get(
            "pending_agent")))
        before = self.snapshot()
        code, out, err = self.failed("atta-cortadora", "quota")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("atta-cortadora no está pendiente", err)
        self.assertEqual(self.snapshot(), before)
        for e in self.fallback_events():
            if e["to_model"] is not None:
                self.assertLessEqual(mdl.cost(e["to_model"]), mdl.cost(e["from_model"]))

    def test_lowered_tier_never_climbs(self):  # S4
        self.use(tiers={"T3": "haiku"}, fallbacks={"T3": ["sonnet"]})
        self.build()
        self.assertEqual(self.next()["model"], "haiku")
        self.failed("atta-cortadora", "quota")
        self.assertEqual(self.next()["action"], "wait")

    def test_walk_is_monotone_and_ends(self):  # S4
        self.use(fallbacks={"T3": ["sonnet", "claude-sonnet-4-5", "haiku", "claude-haiku-4-5"]})
        self.build()
        seen = []
        for _ in range(10):
            action = self.next()
            if action["action"] != "spawn":
                break
            seen.append(action["model"])
            self.failed("atta-cortadora", "network")
        self.assertEqual(action["action"], "wait")
        costs = [mdl.cost(m) for m in seen]
        self.assertEqual(costs, sorted(costs, reverse=True))
        self.assertEqual(len(seen), len(set(seen)))

class AvoidTest(FallbackBase):
    def test_vigia_fallback_never_takes_the_guia_model(self):
        self.use(roles={"guia": "claude-haiku-4-5"},
                 fallbacks={"T2": ["claude-haiku-4-5", "haiku"]})
        self.routed(risk="medium", spec_review=True)
        self.wt.mkdir(parents=True)
        self.job.update(stage="spec-review", worktree=str(self.wt))
        self.jobs.save(self.job)
        self.write("spec.md", "STATUS: READY\n")
        self.assertEqual(self.next()["model"], "sonnet")
        code, out, _err = self.failed("atta-vigia", "quota")
        self.assertEqual(code, 0)
        self.assertIn("el próximo intento usa haiku", out)
        self.assertEqual(self.next()["model"], "haiku")

    def review(self, risk, reviewers):
        self.routed(risk=risk, reviewers=reviewers)
        self.wt.mkdir(parents=True)
        self.job.update(stage="review", worktree=str(self.wt), pr=7)
        self.jobs.save(self.job)

    def test_refutador_fallback_avoids_the_reviewers_it_judges(self):
        self.review("high", (CODE, SECURITY))
        self.write("round-1/review-code.md", CHANGES)
        self.write("round-1/review-security.md", CHANGES)
        action = self.next()
        self.assertEqual((action["agent"], action["model"]), ("atta-refutador", "opus"))
        self.failed("atta-refutador", "quota")
        # T3 chain: sonnet (the code reviewer's model) is skipped for haiku.
        self.assertEqual(self.next()["model"], "haiku")

    def test_refutador_avoids_the_model_a_reviewer_fell_back_to(self):
        self.review("medium", (CODE, UX))
        group = self.next()
        self.assertEqual([(a["agent"], a["model"]) for a in group["agents"]],
                         [("atta-jardinera-code", "sonnet"), ("atta-jardinera-ux", "haiku")])
        self.failed("atta-jardinera-code", "rate-limit")
        group = self.next()
        self.assertEqual([(a["agent"], a["model"]) for a in group["agents"]],
                         [("atta-jardinera-code", "haiku"), ("atta-jardinera-ux", "haiku")])
        self.write("round-1/review-code.md", CHANGES)
        self.write("round-1/review-ux.md", CHANGES)
        # Both reviewers ran on haiku, so the Refutador (floor T2) keeps sonnet; without the
        # fallback it would have avoided sonnet and gone to opus.
        self.assertEqual(self.next()["model"], "sonnet")

    def test_spent_reviewer_chain_waits_instead_of_launching_the_group(self):
        self.review("medium", (CODE, UX))
        self.next()
        self.failed("atta-jardinera-ux", "network")
        action = self.next()
        self.assertEqual(action["action"], "wait")
        self.assertIn("La cadena de fallback de T1 se agotó para atta-jardinera-ux", action["reason"])


class WiringTest(unittest.TestCase):
    def test_model_failed_is_reachable_from_the_cli(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["model-failed", "--help"])
        self.assertEqual(code, 0)
        text = " ".join(out.getvalue().split())
        self.assertIn("atta model-failed", text)
        self.assertIn("cuota, rate limit o red", text)
        self.assertIn("nunca por contexto", text)

    def test_skill_allows_model_failed_only_for_quota_rate_limit_or_network(self):
        skill = (support.ATTA_DIR / "templates" / "claude" / "skills" / "atta" / "SKILL.md")
        text = " ".join(skill.read_text(encoding="utf-8").split())
        self.assertIn("atta model-failed <job> --agent <agent> --reason "
                      "quota|rate-limit|network", text)
        self.assertIn("Never run it for any other failure: a context-length or context-window "
                      "error", text)
        self.assertIn("a spawn of the same agent with a different `model` is a new action: "
                      "run it", text)


PASS = "VERDICT: PASS\n\n## Summary\n\nOk.\n"


class SpentWaitFlowsTest(FallbackBase):  # S4 (B1 fix must not break other flows)
    def review(self, risk, reviewers):
        self.routed(risk=risk, reviewers=reviewers)
        self.wt.mkdir(parents=True)
        self.job.update(stage="review", worktree=str(self.wt), pr=7)
        self.jobs.save(self.job)

    def pend(self):
        return self.jobs.load(JID).get("pending_agent")

    def spend_cortadora(self):
        self.build()
        self.next()
        for _ in range(3):
            self.failed("atta-cortadora", "quota")
            self.next()

    def test_respawn_after_spent_wait_records_pending_again(self):
        self.spend_cortadora()
        action = self.next()
        self.assertEqual((action["action"], action["model"]), ("spawn", "opus"))
        p = self.pend()
        self.assertEqual((p["agent"], p["model"], p["tier"]), ("atta-cortadora", "opus", "T3"))
        code, out, _ = self.failed("atta-cortadora", "quota")
        self.assertEqual(code, 0)
        self.assertIn("usa sonnet", out)

    def test_spent_reviewer_keeps_the_other_reviewers_pending(self):
        self.review("medium", (CODE, UX))
        self.next()
        self.failed("atta-jardinera-ux", "network")
        self.assertEqual(self.next()["action"], "wait")
        self.assertEqual([e["agent"] for e in self.pend()], ["atta-jardinera-code"])
        before = self.snapshot()
        self.assertEqual(self.failed("atta-jardinera-ux", "network")[0], 2)
        self.assertEqual(self.snapshot(), before)
        code, out, _ = self.failed("atta-jardinera-code", "quota")
        self.assertEqual(code, 0)
        self.assertIn("usa haiku", out)
        group = self.next()
        self.assertEqual([(a["agent"], a["model"]) for a in group["agents"]],
                         [("atta-jardinera-code", "haiku"), ("atta-jardinera-ux", "haiku")])
        self.write("round-1/review-code.md", PASS)
        self.write("round-1/review-ux.md", PASS)
        machine.advance(self.jobs, JID, "approve")
        ev = [e for e in events.read_events(self.dir) if e.get("to") == "approve"][-1]
        self.assertEqual(sorted(m["agent"] for m in ev["models"]),
                         ["atta-jardinera-code", "atta-jardinera-ux"])
        self.assertNotIn("pending_agent", self.jobs.load(JID))

    def test_spent_refutador_leaves_the_group_and_rejoins(self):
        self.review("high", (CODE, SECURITY))
        self.next()
        self.write("round-1/review-code.md", CHANGES)
        self.write("round-1/review-security.md", CHANGES)
        self.assertEqual(self.next()["agent"], "atta-refutador")
        self.assertEqual(len(self.pend()), 3)
        for _ in range(6):
            self.assertEqual(self.failed("atta-refutador", "quota")[0], 0)
            if self.next()["action"] == "wait":
                break
        self.assertNotIn("atta-refutador", [e["agent"] for e in self.pend()])
        self.assertEqual(len(self.pend()), 2)
        before = self.snapshot()
        self.assertEqual(self.failed("atta-refutador", "quota")[0], 2)
        self.assertEqual(self.snapshot(), before)
        action = self.next()
        self.assertEqual((action["agent"], action["model"]), ("atta-refutador", "opus"))
        self.assertEqual(len(self.pend()), 3)

    def test_stage_transition_after_spent_wait(self):
        self.spend_cortadora()
        new = machine.advance(self.jobs, JID, "gate")
        self.assertNotIn("pending_agent", new)
        self.assertEqual(self.failed("atta-cortadora", "quota")[0], 2)

    def test_equal_or_cheaper_fallbacks_are_never_refused(self):
        cases = (({"fallbacks": {"T2": ["claude-sonnet-4-5", "haiku"]}}, 0, "claude-sonnet-4-5"),
                 ({"tiers": {"T2": "claude-sonnet-4-5"}, "fallbacks": {"T2": ["sonnet"]}}, 0,
                  "sonnet"),
                 ({"roles": {"cortadora": "claude-opus-4-1"}, "fallbacks": {"T2": ["haiku"]}},
                  0, "haiku"),
                 ({"tiers": {"T3": "haiku"},
                   "fallbacks": {"T3": ["sonnet", "claude-haiku-4-5"]}}, 2, "claude-haiku-4-5"),
                 ({"roles": {"cortadora": "haiku"}, "fallbacks": {"T2": ["sonnet"]}}, 0, None))
        for entry, rounds, expected in cases:
            with self.subTest(entry=entry):
                self.tearDown()
                self.setUp()
                self.use(**entry)
                self.build(rounds=rounds)
                self.next()
                self.assertEqual(self.failed("atta-cortadora", "quota")[0], 0)
                self.assertEqual(self.fallback_events()[-1]["to_model"], expected)

    def test_config_raised_mid_flight_is_refused_without_writes(self):
        self.use(fallbacks={"T2": ["haiku"]})
        self.build(rounds=0)
        self.next()
        self.failed("atta-cortadora", "quota")
        self.assertEqual(self.next()["model"], "haiku")
        self.use(roles={"cortadora": "opus"}, fallbacks={"T2": ["haiku"]})
        before = self.snapshot()
        self.assertEqual(self.failed("atta-cortadora", "quota"), (2, "", (
            "El próximo modelo de atta-cortadora (opus) costaría más que haiku: un fallback "
            "nunca sube de costo. No registré nada.\n")))
        self.assertEqual(self.snapshot(), before)

if __name__ == "__main__":
    unittest.main()
