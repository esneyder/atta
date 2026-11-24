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

Sync rework (F3.x, K7/S4/S2): `atta merge`/`atta handoff` sync the job branch with the
integration branch. A clean sync that changed the job's own diff sends the job back to review
(`approve|merging → review`); a conflict sends it to the Cortadora (`approve|merging|
awaiting-merge → build`), and so does GitHub reporting the waiting PR CONFLICTING. These are
not rework rounds (no `total_rounds`, no `gate_rounds`/`review_rounds`): `sync_attempts` is the
one counter and bounds them. It counts once per attempt: on `→ merging` (the attempt starts
there) or, when the attempt had no merging stage (handoff from approve, awaiting-merge), on the
move back to review/build. Leaving `merging` never counts again.

Vigía REVISE cap (T7b, S9): `spec_reviews` in job.json counts the REVISE verdicts the spine acted
on (each `spec-review → spec` and the final `spec-review → needs-human`; a job without the field
counts 0). The `MAX_SPEC_REVISE`-th REVISE never goes back to spec: `spec-review → spec` is
refused once `spec_reviews + 1` reaches the cap, and `atta next` sends the job to needs-human.

BLOCKED-BY wait (F4.x T3, S3): `atta next` writes `blocked_since` the first time the job waits
for an unmerged blocker and, once it is unblocked, folds that wait into `blocked_s`. Leaving the
stage puts the total wait in the event's `blocked_s`, subtracts it from `duration_s` (never below
0) and clears both fields. A job that never waited emits the same event as before.
"""

from __future__ import annotations

from datetime import datetime

from .events import append_event, make_event
from .lock import job_lock
from .store import JobStore
from .timeutil import from_iso, seconds_between, to_iso, utcnow

STAGES = (
    "intake", "needs-info", "spec", "spec-review", "build", "gate", "review",
    "approve", "merging", "awaiting-merge", "needs-human", "merged", "rejected",
)

TRANSITIONS = {
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
}

TERMINAL = frozenset(s for s, exits in TRANSITIONS.items() if not exits)

# Rework rounds: re-entering build from these stages bumps their counter and total_rounds.
ROUND_COUNTERS = {"gate": "gate_rounds", "review": "review_rounds"}

# Sync rework: stages whose move back to review/build is itself the sync attempt (F3.x).
SYNC_REWORK_FROM = frozenset({"approve", "awaiting-merge"})

# Vigía REVISE cap (S9): the third REVISE of a job goes to a person, not back to spec.
SPEC_REVIEWS = "spec_reviews"
MAX_SPEC_REVISE = 3

# BLOCKED-BY wait of the current stage (S3): open wait start and closed waits, in job.json.
BLOCKED_SINCE = "blocked_since"
BLOCKED_S = "blocked_s"


def _secs(value: float) -> float | int:
    value = round(max(value, 0.0), 3)
    return int(value) if value == int(value) else value


def blocked_seconds(job: dict, now: datetime) -> float | int | None:
    """Seconds the job waited for a BLOCKED-BY blocker in its current stage (closed waits plus
    the open one up to `now`), at most the stage time; None when it never waited."""
    if BLOCKED_SINCE not in job and BLOCKED_S not in job:
        return None
    closed = job.get(BLOCKED_S)
    total = closed if isinstance(closed, (int, float)) and not isinstance(closed, bool) \
        and closed > 0 else 0
    stage_time = seconds_between(job["stage_since"], now)
    since = job.get(BLOCKED_SINCE)
    if isinstance(since, str):
        try:
            start = max(from_iso(since), from_iso(job["stage_since"]))
        except ValueError:
            start = None
        if start is not None:
            total += max((now - start).total_seconds(), 0.0)
    return _secs(min(total, stage_time))


def spec_reviews(job: dict) -> int:
    """REVISE verdicts of the Vigía the spine already acted on (0 for older jobs)."""
    value = job.get(SPEC_REVIEWS)
    return value if isinstance(value, int) and value >= 0 else 0


def spec_revise_capped(job: dict) -> bool:
    """True when one more REVISE reaches MAX_SPEC_REVISE (it goes to needs-human)."""
    return spec_reviews(job) + 1 >= MAX_SPEC_REVISE


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
    if frm == "spec-review" and to == "spec" and spec_revise_capped(job):
        raise Refused(
            f"La Vigía ya pidió cambios al spec de {job['id']} {spec_reviews(job)} veces "
            f"(REVISE); la vez {MAX_SPEC_REVISE} no vuelve a spec. Desde spec-review solo se "
            "puede ir a build o needs-human."
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
    if to == "merging" or (frm in SYNC_REWORK_FROM and to in ("review", "build")):
        new["sync_attempts"] = job["sync_attempts"] + 1
    if frm == "spec-review" and to in ("spec", "needs-human"):
        new[SPEC_REVIEWS] = spec_reviews(job) + 1
    new["stage"] = to
    new["stage_since"] = to_iso(now)
    if to in TERMINAL:
        new["ended"] = to_iso(now)
    new.pop(BLOCKED_SINCE, None)
    new.pop(BLOCKED_S, None)
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
    S3: the BLOCKED-BY wait of the stage being left goes to `blocked_s`, out of `duration_s`.
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
        duration = seconds_between(job["stage_since"], now)
        blocked = blocked_seconds(job, now)
        if blocked is not None:
            duration = _secs(duration - blocked)
        event = make_event(
            job["id"], job["stage"], to, duration, now,
            model=model, tier=tier, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=cost_usd, agent=agent, models=models, blocked_s=blocked,
        )
        append_event(job_dir, event)
        store.save(new)
        return new
