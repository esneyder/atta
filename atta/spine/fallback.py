"""`atta model-failed`: a pending agent failed by quota, rate limit or network (F4 T4, S4).

The /atta driver runs it when the Agent tool returns one of those errors instead of the agent's
reply; never for a context error, a refusal or an agent that ended without its artifact (S4:
«Fallback solo por cuota, rate limit o red, no por contexto»). A subagent may not run it (the
guard denies it when the hook payload carries `agent_id`).

    atta model-failed <job> --agent atta-<rol> --reason quota|rate-limit|network

Under the job lock it checks that the agent is in job.json `pending_agent` for the current stage
(a single entry or the reviewers' list), records the model it ran on in job.json
`model_failures` and appends one `model_fallback` event:

    {"ts", "job", "event": "model_fallback", "stage", "round" (build/gate/review/approve),
     "agent", "tier", "from_model", "to_model", "reason"}

`tier` is the tier whose chain the agent walks: the entry's `chain_tier` when `atta next` put it
on a fallback model (F4.x S5: its `tier` is then the tier of that model), else its `tier` (also
an entry written before F4.x, without `chain_tier`).

`from_model`/`to_model` (not `from`/`to`) keep the event out of every reader of stage
transitions. `to_model` is the model `atta next` will spawn next (computed by next.plan, so the
Vigía/Refutador avoid rules and escalation hold), or null when the tier's chain is spent.

`model_failures` = {stage, since, agents: {agent: [failed models in order]}} counts only while
`stage`/`since` match the job's current stage entry, so re-entering a stage starts with the
routed models again. `atta next` (next.py) relaunches a failed agent on Table.fallback(tier,
failed, primary=<last failed>, avoid=...), never more expensive than the model that failed
(L4); with the chain spent it prints a `wait` and clears that agent's failures, so the next
`/atta run` tries the routed model again instead of a more expensive substitute.

Refused (exit 2, nothing written): a reason other than quota, rate-limit or network, or an agent
that is not pending in the current stage. Repeating a failure already recorded for the pending
model writes nothing (exit 0).
"""

from __future__ import annotations

import sys

from . import models as mdl
from .events import append_event
from .lock import job_lock
from .timeutil import to_iso, utcnow

OK, REFUSED = 0, 2
FIELD = "model_failures"
EVENT = "model_fallback"
REASONS = {"quota": "cuota", "rate-limit": "rate limit", "network": "red"}
ROUND_STAGES = ("build", "gate", "review", "approve")


def failed_models(job: dict, agent: str) -> list[str]:
    """The models `agent` failed on during the job's current stage entry, in order."""
    record = job.get(FIELD)
    if (not isinstance(record, dict) or record.get("stage") != job.get("stage")
            or record.get("since") != job.get("stage_since")):
        return []
    agents = record.get("agents")
    found = agents.get(agent) if isinstance(agents, dict) else None
    return [m for m in found if isinstance(m, str)] if isinstance(found, list) else []


def pending_entry(job: dict, agent: str) -> dict | None:
    """The `pending_agent` entry of `agent` recorded during the current stage, if any."""
    pending = job.get("pending_agent")
    entries = pending if isinstance(pending, list) else [pending]
    for entry in entries:
        if (isinstance(entry, dict) and entry.get("agent") == agent
                and entry.get("stage") == job.get("stage")
                and isinstance(entry.get("model"), str) and entry.get("tier") in mdl.TIERS):
            return entry
    return None


def with_failure(job: dict, agent: str, model: str) -> dict:
    """A copy of `job` whose `model_failures` adds `model` to `agent` (current stage entry)."""
    record = job.get(FIELD)
    agents = {}
    if (isinstance(record, dict) and record.get("stage") == job.get("stage")
            and record.get("since") == job.get("stage_since")
            and isinstance(record.get("agents"), dict)):
        agents = {a: list(m) for a, m in record["agents"].items() if isinstance(m, list)}
    agents[agent] = failed_models(job, agent) + [model]
    new = dict(job)
    new[FIELD] = {"stage": job["stage"], "since": job["stage_since"], "agents": agents}
    return new


def clear(jobs, jid: str, reset: dict) -> None:
    """Forget the failures of `reset["agent"]` (the spent-chain wait of `atta next`) and drop
    its `pending_agent` entry, under the job lock, when the job is still in the stage entry
    `reset` names. Without the pending entry a repeated `atta model-failed` is refused instead
    of recording a "fallback" to the routed, more expensive model (S4, L4)."""
    with job_lock(jobs.job_dir(jid)):
        job = jobs.load(jid)
        record = job.get(FIELD)
        if (job.get("stage") != reset.get("stage") or job.get("stage_since") != reset.get("since")
                or not isinstance(record, dict) or not isinstance(record.get("agents"), dict)):
            return
        agent = reset.get("agent")
        agents = {a: m for a, m in record["agents"].items() if a != agent}
        if agents:
            job[FIELD] = dict(record, agents=agents)
        else:
            job.pop(FIELD, None)
        pending = job.get("pending_agent")
        if isinstance(pending, dict) and pending.get("agent") == agent:
            job.pop("pending_agent")
        elif isinstance(pending, list):
            kept = [e for e in pending if not (isinstance(e, dict) and e.get("agent") == agent)]
            if kept:
                job["pending_agent"] = kept
            else:
                job.pop("pending_agent")
        jobs.save(job)


def chain_tier(entry: dict) -> str:
    """The tier whose fallback chain a `pending_agent` entry walks (see the module docstring)."""
    chain = entry.get("chain_tier")
    return chain if chain in mdl.TIERS else entry["tier"]


def _next_model(job: dict, job_dir, root, cfg: dict, agent: str, tier: str,
                failed: list[str]) -> str | None:
    """The model `atta next` would spawn `agent` on with these failures (None: chain spent)."""
    from . import next as nxt
    action = nxt.plan(job, job_dir, root, cfg)
    spawns = action["agents"] if action["action"] == "spawn-group" else [action]
    for spawn in spawns:
        if spawn.get("action", "spawn") == "spawn" and spawn.get("agent") == agent:
            return spawn["model"]
    if action["action"] == "wait" and (action.get("model_reset") or {}).get("agent") == agent:
        return None
    return mdl.table(cfg).fallback(tier, failed, primary=failed[-1])


def _refuse(message: str) -> int:
    print(f"{message} No registré nada.", file=sys.stderr)
    return REFUSED


def cmd_model_failed(args, cfg, jobs) -> int:
    reason, agent = args.reason, args.agent
    if reason not in REASONS:
        return _refuse(f"El motivo «{reason}» no vale: usa quota, rate-limit o network. Un error "
                       "de contexto nunca cambia de modelo.")
    jid = jobs.load(args.job)["id"]
    job_dir = jobs.job_dir(jid)
    with job_lock(job_dir):
        job = jobs.load(jid)
        entry = pending_entry(job, agent)
        if entry is None:
            return _refuse(f"{agent} no está pendiente en {jid} (etapa {job['stage']}): solo se "
                           "registra la falla de un agente que `atta next` lanzó en esta etapa.")
        model, tier = entry["model"], chain_tier(entry)
        if model in failed_models(job, agent):
            print(f"{agent} ya tenía registrada la falla en {model} en {jid}; no cambié nada.")
            return OK
        new = with_failure(job, agent, model)
        try:
            to = _next_model(new, job_dir, args.root, cfg, agent, tier,
                             failed_models(new, agent))
        except mdl.ModelsError as exc:
            return _refuse(str(exc))
        if to is not None and (mdl.cost(to) is None or mdl.cost(model) is None
                               or mdl.cost(to) > mdl.cost(model)):
            return _refuse(f"El próximo modelo de {agent} ({to}) costaría más que {model}: un "
                           "fallback nunca sube de costo.")
        event = {"ts": to_iso(utcnow()), "job": jid, "event": EVENT, "stage": job["stage"]}
        if job["stage"] in ROUND_STAGES:
            event["round"] = job["total_rounds"] + 1
        event.update({"agent": agent, "tier": tier, "from_model": model, "to_model": to,
                      "reason": reason})
        append_event(job_dir, event)
        jobs.save(new)
    head = f"{agent} falló en {model} por {REASONS[reason]} en {jid}:"
    if to is None:
        print(f"{head} la cadena de fallback de {tier} se agotó; Atta no sube a un modelo más "
              "caro y `atta next` espera.")
    else:
        print(f"{head} el próximo intento usa {to} (cadena de fallback de {tier}).")
    return OK


def register(subparsers) -> None:
    """Add `atta model-failed` to the `atta` CLI."""
    p = subparsers.add_parser(
        "model-failed",
        help="Registra que un agente falló por cuota, rate limit o red (lo corre /atta).",
        usage="atta model-failed <job> --agent atta-<rol> --reason quota|rate-limit|network",
        description="Lo corre el orquestador /atta cuando el Agent tool falla por cuota, rate "
                    "limit o red, nunca por contexto. Registra en job.json el modelo que falló "
                    "y un evento model_fallback en events.jsonl; el siguiente atta next relanza "
                    "al agente con el próximo modelo de la cadena de su tier (atta models "
                    "fallback), nunca más caro, o espera si la cadena se agotó. Sale 2 sin "
                    "escribir nada si el motivo no vale o el agente no está pendiente en la "
                    "etapa actual.")
    p.add_argument("job", help="id del job")
    p.add_argument("--agent", required=True, metavar="atta-<rol>",
                   help="el agente pendiente que falló (p. ej. atta-cortadora)")
    p.add_argument("--reason", required=True, metavar="quota|rate-limit|network",
                   help="por qué falló: quota (cuota), rate-limit o network (red)")
    p.set_defaults(func=cmd_model_failed)
