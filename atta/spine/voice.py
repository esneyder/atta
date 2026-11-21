"""Brand voice: short, direct Spanish messages signed by role. Contract tokens stay in English."""

from __future__ import annotations

# Tokens of the artifact contracts (arquitectura §3). Never translated.
CONTRACT_TOKENS = frozenset({
    "ACCEPT", "REJECT", "NEEDS-INFO", "READY", "REVISE", "DONE", "BLOCKED",
    "PASS", "FAIL", "CHANGES", "APPROVE", "ESCALATE",
})

RISK_ES = {"low": "bajo", "medium": "medio", "high": "alto"}

_NUMBERS_ES = {
    1: "Una", 2: "Dos", 3: "Tres", 4: "Cuatro", 5: "Cinco",
    6: "Seis", 7: "Siete", 8: "Ocho", 9: "Nueve", 10: "Diez",
}


def _risk(risk: str) -> str:
    try:
        return RISK_ES[risk]
    except KeyError:
        raise ValueError(f"Riesgo desconocido: {risk}.") from None


def intake_accepted(issue: int, risk: str) -> str:
    return f"La Exploradora aceptó el issue #{issue} (riesgo {_risk(risk)})."


def intake_accepted_comment(issue: int, risk: str, summary: str | None = None) -> str:
    """The Exploradora's ACCEPT comment on the issue (T11): decision, risk and the scope."""
    text = f"{intake_accepted(issue, risk)} La Guía sigue con la spec."
    if summary:
        text += f"\n\nAlcance: {summary}"
    return text


def intake_rejected(issue: int) -> str:
    return f"La Exploradora descartó el issue #{issue} (REJECT)."


def intake_needs_info(issue: int) -> str:
    return f"La Exploradora necesita más información en el issue #{issue} (NEEDS-INFO)."


def gate_passed() -> str:
    return "El Filtro pasó (PASS)."


def gate_failed(failing: int) -> str:
    if failing == 1:
        return "El Filtro no pasó: 1 test falla."
    return f"El Filtro no pasó: {failing} tests fallan."


def rounds_exhausted(rounds: int) -> str:
    word = _NUMBERS_ES.get(rounds, str(rounds))
    noun = "ronda" if rounds == 1 else "rondas"
    return f"{word} {noun} sin pasar el filtro. Te lo dejamos a ti."


def reina_approved(pr: int) -> str:
    return f"La Reina aprobó el PR #{pr} (APPROVE)."


def reina_escalates(reason: str) -> str:
    return f"La Reina lo deja en tus manos: {reason}"


def merged(pr: int, branch: str) -> str:
    return f"La Reina integró el PR #{pr} en {branch}."


def pr_opened(pr: int, branch: str) -> str:
    """The Cortadora's comment on the issue when `atta publish` opens the PR (T11)."""
    return f"La Cortadora abrió el PR #{pr} contra {branch}."


def merged_issue(pr: int, branch: str, issue: int) -> str:
    """The Reina's closing comment on the issue after the merge (T11)."""
    return f"La Reina mergeó el PR #{pr} a {branch}. El issue #{issue} queda resuelto."


def for_transition(job: dict, frm: str, to: str, integration_branch: str = "develop") -> str | None:
    """Message for a key transition, or None when the transition is not announced."""
    if frm == "intake" and to == "spec":
        return intake_accepted(job["issue"], job["intake"]["risk"])
    if to == "needs-info":
        return intake_needs_info(job["issue"])
    if frm == "intake" and to == "rejected":
        return intake_rejected(job["issue"])
    if frm == "gate" and to == "needs-human":
        return rounds_exhausted(job["gate_rounds"])
    if frm == "approve" and to == "merging":
        return reina_approved(job["pr"])
    if frm == "merging" and to == "merged":
        return merged(job["pr"], integration_branch)
    return None
