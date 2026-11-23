"""Risk router (arquitectura §5): from intake RISK + SURFACES to the job's review plan.

The tables live in `config.json` under `routing`, so routing changes without code:

    routing.risk.<risk>      = {"spec_review": bool, "reviewers": [reviewer, ...]}
    routing.surfaces.<name>  = {"<risk>": [reviewer, ...], ...}
    reviewer                 = {"reviewer": str, "lenses": [str, ...], "tier": "T0".."T3"}

The default table adds the adversarial `rastreador` (lens `adversarial`, T3; agent
`atta-rastreador`, file `review-rastreador.md`) to the `high` risk plan only (G6).

Tiers are logical (resolved to models in F4). The tier here is the stage's base tier per risk; the
stage×risk matrix (security reviewer up to T3 at high or unknown risk, the Cortadora ladder) and
model independence are applied where the model is resolved, in `models.Table.stage_tier` and
`Table.review_model`, so the router stays a pure review planner. A surface reviewer already present
from the risk table is not added twice; the risk table's entry wins.
"""

from __future__ import annotations

import copy

TIERS = ("T0", "T1", "T2", "T3")


class RoutingError(ValueError):
    """Unknown risk/surface or a malformed routing table."""


def _table(cfg: dict) -> dict:
    routing = cfg.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("risk"), dict) \
            or not isinstance(routing.get("surfaces"), dict):
        raise RoutingError("config.json: falta la tabla routing.")
    return routing


def _reviewers(entries, where: str) -> list[dict]:
    if not isinstance(entries, list):
        raise RoutingError(f"config.json: {where}: reviewers debe ser una lista.")
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("reviewer"), str) \
                or not entry["reviewer"] or not isinstance(entry.get("lenses"), list) \
                or not entry["lenses"] or not all(isinstance(x, str) and x for x in entry["lenses"]):
            raise RoutingError(f"config.json: {where}: cada reviewer necesita reviewer, lenses y tier.")
        if entry.get("tier") not in TIERS:
            raise RoutingError(
                f"config.json: {where}: tier «{entry.get('tier')}» no es válido; usa T0, T1, T2 o T3."
            )
        out.append({"reviewer": entry["reviewer"], "lenses": list(entry["lenses"]),
                    "tier": entry["tier"]})
    return out


def plan(risk: str, surfaces, cfg: dict) -> dict:
    """Return {risk, surfaces, spec_review, reviewers} for a job; raise RoutingError otherwise."""
    routing = _table(cfg)
    risks = routing["risk"]
    if not isinstance(risk, str) or risk not in risks:
        raise RoutingError(f"Riesgo desconocido: «{risk}». Usa {_choices(list(risks))}.")
    known = routing["surfaces"]
    surfaces = list(surfaces or [])
    for s in surfaces:
        if not isinstance(s, str) or s not in known:
            raise RoutingError(f"Superficie desconocida: «{s}». Usa {_choices(list(known))}.")

    row = risks[risk]
    if not isinstance(row, dict) or not isinstance(row.get("spec_review"), bool):
        raise RoutingError(f"config.json: routing.risk.{risk}: spec_review debe ser true o false.")
    reviewers = _reviewers(row.get("reviewers"), f"routing.risk.{risk}")
    present = {r["reviewer"] for r in reviewers}
    for s in surfaces:
        by_risk = known[s]
        if not isinstance(by_risk, dict) or risk not in by_risk:
            raise RoutingError(f"config.json: routing.surfaces.{s}: falta el riesgo {risk}.")
        for r in _reviewers(by_risk[risk], f"routing.surfaces.{s}.{risk}"):
            if r["reviewer"] not in present:
                reviewers.append(r)
                present.add(r["reviewer"])
    return {
        "risk": risk,
        "surfaces": surfaces,
        "spec_review": row["spec_review"],
        "reviewers": copy.deepcopy(reviewers),
    }


def plan_for_intake(header: dict, cfg: dict) -> dict:
    """Plan from a parsed intake header (see spine.intake.parse_header)."""
    return plan(header["risk"], header["surfaces"], cfg)


def _choices(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " o " + items[-1]
