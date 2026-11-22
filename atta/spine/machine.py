"""Job state machine: explicit legal transitions, counters and the global round cap (K13).

`awaiting-merge` (S13): the Reina approved a job whose risk is above
`delivery.auto_merge_up_to`; the PR is ready and a person merges it. It is not terminal and,
like the stages that wait for a person, does not hold a `concurrency` slot (flow.NO_SLOT).

`needs-human → awaiting-merge` (L31): `atta handoff` retakes a job that a handoff escalated when
the Reina's archived APPROVE still holds, the PR is open against the integration branch and
HEAD is still the commit that passed merge-checks (flow.cmd_handoff checks all of it).

`needs-human → gate` (T9): the Filtro stopped on a configuration failure (job.json
`gate_failure.kind == "config"`, written by gate.run_gate) and a person fixed it; the gate runs
again in the same round. Like every move into gate, it changes no counter.
"""

from __future__ import annotations

from datetime import datetime

from .events import append_event, make_event
from .lock import job_lock
from .store import JobStore
from .timeutil import seconds_between, to_iso, utcnow

STAGES = (
    "intake", "needs-info", "spec", "spec-review", "build", "gate", "review",
    "approve", "merging", "awaiting-merge", "needs-human", "merged", "rejected",
)

TRANSITIONS = {
    "intake": ("spec", "needs-info", "rejected"),
    "needs-info": ("intake", "rejected"),
    "spec": ("spec-review", "build"),
    "spec-review": ("spec", "build"),
    "build": ("gate", "needs-human"),
    "gate": ("review", "build", "needs-human"),
    "review": ("approve", "build", "needs-human"),
    "approve": ("merging", "awaiting-merge", "needs-human"),
    "merging": ("merged", "needs-human"),
    "awaiting-merge": ("merged", "needs-human"),
    "needs-human": ("build", "rejected", "awaiting-merge", "gate"),
    "merged": (),
    "rejected": (),
}

TERMINAL = frozenset(s for s, exits in TRANSITIONS.items() if not exits)

# Rework rounds: re-entering build from these stages bumps their counter and total_rounds.
ROUND_COUNTERS = {"gate": "gate_rounds", "review": "review_rounds"}


class Refused(Exception):
    """An illegal transition. Nothing was written."""


def gate_config_stop(job: dict) -> bool:
    """True when the job's last Filtro run failed on configuration (T9)."""
    failure = job.get("gate_failure")
    return isinstance(failure, dict) and failure.get("kind") == "config"


def check(job: dict, to: str) -> None:
    """Raise Refused if `job` may not move to `to`."""
    frm = job["stage"]
    if to not in TRANSITIONS:
        raise Refused(f"Etapa desconocida: {to}.")
    if frm in TERMINAL:
        raise Refused(
            f"El job {job['id']} está en {frm}, una etapa final; no admite transiciones."
        )
    exits = TRANSITIONS.get(frm, ())
    if to not in exits:
        raise Refused(
            f"Transición ilegal en {job['id']}: {frm} → {to}. "
            f"Desde {frm} solo se puede ir a: {', '.join(exits)}."
        )
    if frm == "needs-human" and to == "gate" and not gate_config_stop(job):
        raise Refused(
            f"El job {job['id']} no vuelve de needs-human a gate: el Filtro solo se retoma "
            "cuando lo detuvo una falla de configuración."
        )
    # K13: total_rounds is never reset; once another round would exceed the cap,
    # the only way out of the rework loop is needs-human.
    if to == "build" and frm in ROUND_COUNTERS:
        total, cap = job["total_rounds"], job["max_total_rounds"]
        if total + 1 > cap:
            raise Refused(
                f"Tope global alcanzado en {job['id']}: {total} de {cap} rondas. "
                f"Desde {frm} solo se puede ir a needs-human."
            )


def apply(job: dict, to: str, now: datetime) -> dict:
    """Return a new job dict after a checked transition (pure)."""
    frm = job["stage"]
    new = dict(job)
    counter = ROUND_COUNTERS.get(frm) if to == "build" else None
    if counter:
        new[counter] = job[counter] + 1
        new["total_rounds"] = job["total_rounds"] + 1
    if to == "merging":
        new["sync_attempts"] = job["sync_attempts"] + 1
    new["stage"] = to
    new["stage_since"] = to_iso(now)
    if to in TERMINAL:
        new["ended"] = to_iso(now)
    return new


def _from_pending(pending, stage: str) -> tuple:
    """(model, tier, agent, models) from a pending_agent recorded during `stage`."""
    if isinstance(pending, dict) and pending.get("stage") == stage:
        return pending.get("model"), pending.get("tier"), pending.get("agent"), None
    if isinstance(pending, list):
        entries = [{"agent": p.get("agent"), "model": p.get("model"), "tier": p.get("tier")}
                   for p in pending if isinstance(p, dict) and p.get("stage") == stage]
        if entries:
            return None, None, None, entries
    return None, None, None, None


def advance(store: JobStore, jid: str, to: str, *, now: datetime | None = None,
            model: str | None = None, tier: str | None = None,
            tokens_in: int | None = None, tokens_out: int | None = None,
            cost_usd: float | None = None) -> dict:
    """Move job `jid` to stage `to` under its lock; append the event, then save job.json.

    T12: the `pending_agent` that `atta next` recorded for the stage being left gives the event
    its `model`, `tier` and `agent` (a list gives `models`) unless `model`/`tier` are passed;
    it is cleared on every transition. Refused leaves job.json and events.jsonl untouched.
    """
    now = now or utcnow()
    job_dir = store.job_dir(jid)
    with job_lock(job_dir):
        job = store.load(jid)
        check(job, to)
        new = apply(job, to, now)
        pending = new.pop("pending_agent", None)
        agent = models = None
        if model is None and tier is None:
            model, tier, agent, models = _from_pending(pending, job["stage"])
        if cost_usd is not None:
            new["cost_usd"] = round((job.get("cost_usd") or 0) + cost_usd, 6)
        event = make_event(
            job["id"], job["stage"], to, seconds_between(job["stage_since"], now), now,
            model=model, tier=tier, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost_usd, agent=agent, models=models,
        )
        append_event(job_dir, event)
        store.save(new)
        return new
