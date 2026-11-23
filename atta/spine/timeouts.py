"""Per-stage time limits of a pending agent (F4 T5, S6).

`atta next` records when it launched an agent (job.json `pending_agent[].at`). The /atta driver
runs agents synchronously, so a run that hangs only shows up when `atta next` is asked again
(F2 L20: an intake of 1359 s for a stuck run). If that agent is still the one `atta next` would
spawn and its entry is older than the limit of the stage, the spine reports it instead of
letting the job hang:

- First time in this stage entry → retry: `atta next` prints the same spawn again, restarts the
  agent's clock (`at`) and appends one `stage_timeout` event (outcome `retry`).
- Second time for that agent in the same stage entry → `needs-human`: `stage_timeout` event
  (outcome `needs-human`), `stage-timeout.md` in the job folder and the action
  `label needs_human` then `comment` and `advance needs-human`, like the Reina's escalation.
  intake and spec have no legal transition to needs-human (machine.TRANSITIONS): there the same
  label and comment are printed and the job stays put; every later `atta next` in that stage
  entry prints a `wait` (the safe action legal in every stage, as in the spent fallback chain)
  until a person moves the job; re-entering the stage resets everything.

    {"ts", "job", "event": "stage_timeout", "stage", "round" (build/review/approve), "agent",
     "model", "limit_s", "elapsed_s", "outcome": "retry" | "needs-human"}

The event has no `from`/`to` (the readers of transitions would take it for one). The counts live
in job.json `stage_timeouts` = {stage, since, agents: {agent: n}}, valid only for that stage
entry. A timeout is not a quota, rate-limit or network failure (S4): it never touches
`model_failures`, so it never consumes the fallback chain; the retry uses the model `atta next`
resolves for the spawn (the same one, or the chain's when a failure was already recorded).

Limits (seconds): DEFAULT_LIMITS_S, overridden per stage by `stage_timeouts_s` in
atta/config.json (written only by humans; the guard denies agents any write to it; absent =
defaults). The time comes from timeutil.utcnow() (tests patch it in next.py).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import machine
from .events import append_event
from .lock import job_lock
from .timeutil import from_iso, seconds_between, to_iso

EVENT = "stage_timeout"
FIELD = "stage_timeouts"
FILE = "stage-timeout.md"
CONFIG_KEY = "stage_timeouts_s"
# Stages where `atta next` spawns an agent. The Cortadora builds test-first, so build is longest.
DEFAULT_LIMITS_S = {"intake": 600, "spec": 900, "spec-review": 600, "build": 3600,
                    "review": 1200, "approve": 900}
ROUND_STAGES = ("build", "review", "approve")
# Who signs the comment of an escalation (github.ROLES), by stage.
SIGNER = {"intake": "exploradora", "spec": "guia", "spec-review": "vigia", "build": "cortadora",
          "review": "jardineras", "approve": "reina"}


def limits(cfg: dict) -> dict:
    """The limit of every stage: DEFAULT_LIMITS_S with the config's `stage_timeouts_s` over it."""
    configured = cfg.get(CONFIG_KEY)
    merged = dict(DEFAULT_LIMITS_S)
    if isinstance(configured, dict):
        merged.update({k: v for k, v in configured.items() if k in merged})
    return merged


def config_error(value) -> str | None:
    """Why a `stage_timeouts_s` value is invalid (config.validate), None when it is fine."""
    if not isinstance(value, dict):
        return f"{CONFIG_KEY} debe ser un objeto {{etapa: segundos}}."
    for stage, seconds in value.items():
        if stage not in DEFAULT_LIMITS_S:
            return (f"{CONFIG_KEY}: «{stage}» no es una etapa con agente "
                    f"(usa: {', '.join(DEFAULT_LIMITS_S)}).")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
            return f"{CONFIG_KEY}.{stage} debe ser un entero de segundos mayor que 0."
    return None


def count(job: dict, agent: str) -> int:
    """Timeouts already reported for `agent` in the job's current stage entry."""
    record = job.get(FIELD)
    if (not isinstance(record, dict) or record.get("stage") != job.get("stage")
            or record.get("since") != job.get("stage_since")
            or not isinstance(record.get("agents"), dict)):
        return 0
    n = record["agents"].get(agent)
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _entries(job: dict) -> list[dict]:
    pending = job.get("pending_agent")
    items = pending if isinstance(pending, list) else [pending]
    return [e for e in items if isinstance(e, dict) and e.get("stage") == job.get("stage")]


def expired(job: dict, cfg: dict, spawns: list[dict], now: datetime) -> list[dict]:
    """The spawns whose pending entry (same agent and model, recorded in this stage entry) is
    older than the stage limit: [{spawn, limit_s, elapsed_s, count}]. Pure."""
    limit = limits(cfg).get(job.get("stage"))
    if limit is None:
        return []
    try:
        since = from_iso(job["stage_since"])
    except (KeyError, TypeError, ValueError):
        return []
    found = []
    for spawn in spawns:
        for entry in _entries(job):
            if entry.get("agent") != spawn.get("agent") or entry.get("model") != spawn.get("model"):
                continue
            try:
                if from_iso(entry["at"]) < since:
                    continue
                elapsed = seconds_between(entry["at"], now)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if elapsed > limit:
                found.append({"spawn": spawn, "limit_s": limit, "elapsed_s": elapsed,
                              "count": count(job, spawn["agent"])})
            break
    return found


def _round(job: dict) -> int | None:
    total = job.get("total_rounds")
    return total + 1 if job.get("stage") in ROUND_STAGES and isinstance(total, int) else None


def _event(job: dict, now: datetime, item: dict, outcome: str) -> dict:
    event = {"ts": to_iso(now), "job": job["id"], "event": EVENT, "stage": job["stage"]}
    if _round(job) is not None:
        event["round"] = _round(job)
    spawn = item["spawn"]
    event.update({"agent": spawn["agent"], "model": spawn["model"], "limit_s": item["limit_s"],
                  "elapsed_s": item["elapsed_s"], "outcome": outcome})
    return event


def _text(job: dict, items: list[dict]) -> str:
    lines = [f"Un agente no terminó dentro del límite de la etapa {job['stage']} y su reintento "
             "tampoco. Te lo dejamos a ti: revisa el job y deja tu feedback con /atta.", ""]
    for item in items:
        spawn = item["spawn"]
        lines.append(f"- {spawn['agent']} ({spawn['model']}): {item['elapsed_s']} s de "
                     f"{item['limit_s']} s permitidos.")
    return "\n".join(lines) + "\n"


def _escalation(job: dict, cfg: dict, job_dir: Path) -> dict:
    needs_human = ["atta", "advance", job["id"], "needs-human"]
    comment = ["atta", "comment", job["id"], "--role", SIGNER[job["stage"]],
               "--file", str(job_dir / FILE)]
    can_advance = "needs-human" in machine.TRANSITIONS[job["stage"]]
    if can_advance:
        then = [comment, needs_human]
    else:
        then = [comment + ["--on", "issue"]]
    return {"action": "run", "argv": ["atta", "label", job["id"], "add",
                                       cfg["labels"]["needs_human"]], "then": then}


def _hold(job: dict, items: list[dict]) -> dict:
    agent = items[0]["spawn"]["agent"]
    return {"action": "wait", "reason": (
        f"{agent} superó dos veces el límite de la etapa {job['stage']} en {job['id']} y esa "
        "etapa no tiene paso a needs-human: el job queda en manos de una persona. Revisa el "
        "job y muévelo con /atta.")}


def handle(jobs, jid: str, cfg: dict, spawns: list[dict], now: datetime):
    """Report the timed-out agents among `spawns` under the job lock. Returns (action, note):
    `action` replaces the spawn when it escalates (None for a retry or nothing expired),
    `note` is a line for stderr (or None). Writes nothing when no agent is over its limit."""
    job = jobs.load(jid)
    if not expired(job, cfg, spawns, now):
        return None, None
    job_dir = jobs.job_dir(jid)
    with job_lock(job_dir):
        job = jobs.load(jid)
        items = expired(job, cfg, spawns, now)  # re-check: another `atta next` may have won
        if not items:
            return None, None
        record = job.get(FIELD) if isinstance(job.get(FIELD), dict) else {}
        agents = dict(record.get("agents") or {}) if (
            record.get("stage") == job["stage"] and record.get("since") == job["stage_since"]) else {}
        escalate = [i for i in items if i["count"] >= 1]
        for item in items:
            agent = item["spawn"]["agent"]
            if item["count"] >= 2:
                continue  # already reported (repeat of an escalation): no new event
            outcome = "needs-human" if escalate else "retry"
            append_event(job_dir, _event(job, now, item, outcome))
            agents[agent] = item["count"] + 1
        new = dict(job)
        new[FIELD] = {"stage": job["stage"], "since": job["stage_since"], "agents": agents}
        if not escalate:
            retried = {(i["spawn"]["agent"], i["spawn"]["model"]) for i in items}
            pending = job.get("pending_agent")
            fresh = [dict(e, at=to_iso(now)) if (e.get("agent"), e.get("model")) in retried else e
                     for e in (pending if isinstance(pending, list) else [pending])
                     if isinstance(e, dict)]
            new["pending_agent"] = fresh if isinstance(pending, list) else fresh[0]
            jobs.save(new)
            names = ", ".join(sorted(a for a, _m in retried))
            return None, (f"Atta: {names} superó el límite de la etapa {job['stage']} "
                          f"({items[0]['limit_s']} s); lo reintento.")
        jobs.save(new)
        if "needs-human" in machine.TRANSITIONS[job["stage"]]:
            (job_dir / FILE).write_text(_text(job, escalate), encoding="utf-8", newline="\n")
            action = _escalation(job, cfg, job_dir)
        elif all(i["count"] >= 2 for i in escalate):
            action = _hold(job, escalate)
        else:
            (job_dir / FILE).write_text(_text(job, escalate), encoding="utf-8", newline="\n")
            action = _escalation(job, cfg, job_dir)
    return action, (f"Atta: {escalate[0]['spawn']['agent']} superó otra vez el límite de la "
                    f"etapa {job['stage']}; el job pasa a una persona.")
