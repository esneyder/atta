"""Branch policy (arquitectura §12): Atta always integrates into develop, never into main.

Pure functions over data; no git, no network. Callers pass what git/gh reported.
Also holds the per-counter round budget (`max_rounds`), which routes to needs-human.
"""

from __future__ import annotations

from .ids import InvalidId, sanitize_id

ROUND_COUNTERS = ("review_rounds", "gate_rounds", "sync_attempts")
_HEADS = "refs/heads/"


class BranchRefused(Exception):
    """A branch, PR base or push target that the policy forbids."""


class RoundBudgetExhausted(Exception):
    """A counter reached max_rounds; the only way forward is needs-human."""


def _forbidden(name: str, cfg: dict) -> bool:
    return name.strip().lower() in {b.strip().lower() for b in cfg["forbidden_branches"]}


def work_branch(job_id: str, cfg: dict) -> str:
    """`<branch_prefix><job-id>`; never a forbidden or integration branch."""
    branch = cfg["branch_prefix"] + sanitize_id(job_id)
    if _forbidden(branch, cfg) or branch.strip().lower() == cfg["integration_branch"].strip().lower():
        raise BranchRefused(
            f"La rama de trabajo «{branch}» coincide con una rama prohibida o de integración."
        )
    return branch


def check_pr_base(base_ref_name: str, cfg: dict) -> None:
    """Accept only the integration branch as PR base (as from `gh pr view --json baseRefName`)."""
    integration = cfg["integration_branch"]
    if base_ref_name != integration or _forbidden(base_ref_name, cfg):
        raise BranchRefused(
            f"La Reina no mergea a {base_ref_name}: Atta solo integra en {integration}."
        )


def check_push_target(ref: str, cfg: dict) -> None:
    """Only `<branch_prefix><job-id>` is pushable; integration and forbidden branches never."""
    name = ref[len(_HEADS):] if ref.startswith(_HEADS) else ref
    if _forbidden(name, cfg):
        raise BranchRefused(f"Atta no empuja a {name}: es una rama prohibida.")
    if name.strip().lower() == cfg["integration_branch"].strip().lower():
        raise BranchRefused(f"Atta no empuja a {name}: solo se integra por PR.")
    prefix = cfg["branch_prefix"]
    outside = BranchRefused(f"Atta solo empuja a ramas {prefix}<job-id>; «{ref}» queda fuera.")
    if not name.startswith(prefix):
        raise outside
    try:
        sanitize_id(name[len(prefix):])
    except InvalidId:
        raise outside from None


def assert_round_budget(job: dict, counter: str, cfg: dict) -> None:
    """Raise RoundBudgetExhausted when job[counter] reached max_rounds (callers go to needs-human)."""
    if counter not in ROUND_COUNTERS:
        raise ValueError(
            f"Contador desconocido: {counter}. Usa review_rounds, gate_rounds o sync_attempts."
        )
    used, cap = job[counter], cfg["max_rounds"]
    if used >= cap:
        raise RoundBudgetExhausted(
            f"{job['id']}: {counter} llegó a {used} de {cap}. Solo queda needs-human."
        )
