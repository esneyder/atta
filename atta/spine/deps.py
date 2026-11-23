"""BLOCKED-BY between jobs (K5, F4 S7): who waits and who stops. Pure: no I/O.

`atta route` stores the intake's `BLOCKED-BY: #n[, #m]` in `job["intake"]["blocked_by"]`. A
blocker is resolved when its job is `merged` or its issue is closed; a `rejected` blocker job
stops the dependent (a person decides); a cycle (A blocks B blocks A) stops it too.

    jobs_by_issue = deps.by_issue(job_list)
    verdict = deps.check(job, jobs_by_issue, closed)   # None: free to advance
    # else {"action": "wait"|"hold", "reason": "<Spanish>", "blockers": [issue numbers]}

`closed(number) -> bool` says whether a GitHub issue is closed (None: never). It is only asked
about blockers whose job is neither merged nor rejected.
"""

from __future__ import annotations

from . import machine

MERGED, REJECTED = "merged", "rejected"
# Blocker states.
RESOLVED, PENDING = "resolved", "pending"


def job_for_issue(job_list: list[dict], number: int) -> dict | None:
    """The job of an issue: a non-terminal one first, else the most recent."""
    mine = [j for j in job_list if j.get("issue") == number]
    if not mine:
        return None
    mine.sort(key=lambda j: (j["stage"] not in machine.TERMINAL, j.get("started") or ""))
    return mine[-1]


def by_issue(job_list: list[dict]) -> dict[int, dict]:
    """{issue number: its job} with the rule of `job_for_issue`."""
    numbers = {j.get("issue") for j in job_list if isinstance(j.get("issue"), int)}
    return {n: job_for_issue(job_list, n) for n in numbers}


def blocked_by(job: dict) -> list[int]:
    """The issues this job waits for (empty when the intake has no BLOCKED-BY)."""
    intake = job.get("intake")
    numbers = intake.get("blocked_by") if isinstance(intake, dict) else None
    if not isinstance(numbers, list):
        return []
    return [n for n in numbers if isinstance(n, int) and not isinstance(n, bool)]


def state(number: int, jobs_by_issue: dict, closed=None) -> str:
    """RESOLVED (merged job or closed issue), REJECTED (rejected job) or PENDING."""
    blocker = jobs_by_issue.get(number)
    stage = blocker.get("stage") if blocker else None
    if stage == REJECTED:
        return REJECTED
    if stage == MERGED or (closed is not None and closed(number)):
        return RESOLVED
    return PENDING


def rejected(job: dict, jobs_by_issue: dict) -> list[int]:
    """Blockers whose job was rejected: the dependent must not advance."""
    return [n for n in blocked_by(job) if state(n, jobs_by_issue) == REJECTED]


def unresolved(job: dict, jobs_by_issue: dict, closed=None) -> list[int]:
    """Blockers neither merged nor closed (rejected ones included)."""
    return [n for n in blocked_by(job) if state(n, jobs_by_issue, closed) != RESOLVED]


def cycle(job: dict, jobs_by_issue: dict, closed=None) -> list[int]:
    """The issue path back to this job through pending blockers ([5, 7, 5]), else []."""
    start = job.get("issue")
    seen: set[int] = set()

    def walk(current: dict, path: list[int]) -> list[int]:
        for n in blocked_by(current):
            if state(n, jobs_by_issue, closed) != PENDING:
                continue
            if n == start:
                return path + [n]
            nxt = jobs_by_issue.get(n)
            if n in seen or nxt is None:
                continue
            seen.add(n)
            found = walk(nxt, path + [n])
            if found:
                return found
        return []

    return walk(job, [start])


def _issues(numbers: list[int]) -> str:
    refs = [f"#{n}" for n in numbers]
    return refs[0] if len(refs) == 1 else f"{', '.join(refs[:-1])} y {refs[-1]}"


def check(job: dict, jobs_by_issue: dict, closed=None) -> dict | None:
    """None when every blocker is resolved; else {action, reason, blockers}.

    A rejected blocker or a cycle is `hold` (a person decides); a pending blocker is `wait`."""
    if not blocked_by(job):
        return None
    stopped = rejected(job, jobs_by_issue)
    if stopped:
        reason = (f"su bloqueante {_issues(stopped)} fue rechazado" if len(stopped) == 1 else
                  f"sus bloqueantes {_issues(stopped)} fueron rechazados")
        return {"action": "hold", "reason": f"{reason}: decide una persona.", "blockers": stopped}
    loop = cycle(job, jobs_by_issue, closed)
    if loop:
        path = " → ".join(f"#{n}" for n in loop)
        return {"action": "hold", "reason": f"dependencia circular ({path}): decide una persona.",
                "blockers": loop}
    pending = unresolved(job, jobs_by_issue, closed)
    if not pending:
        return None
    tail = "todavía no está mergeado" if len(pending) == 1 else "todavía no están mergeados"
    return {"action": "wait", "reason": f"espera a {_issues(pending)}: {tail}.",
            "blockers": pending}
