"""GitHub flow of a job (F2 T3): queue, start, refresh, worktree, publish, comment, label and safe
merge.

Commands are exposed through `register(subparsers)`; cli.py wires them. Each handler takes
`(args, cfg, jobs)` like the F1 commands and returns the exit code: 0 ok, 1 negative verdict
(merge did not go through: conflict, Filtro, gh), 2 refused or usage error. Errors are printed
in Spanish to stderr by the handler itself.

Branch policy (arquitectura §12, D3): every push goes through branches.check_push_target with an
explicit `refs/heads/<b>:refs/heads/<b>` refspec and never `--force` or `+refspec`. The only
exception (S3) is the spine pushing a job's own work branch `atta/<id>` (`branches.work_branch`)
from merge, handoff and their resumes when the Cortadora rebased over the spine's own last push:
origin's tip (freshly fetched) is not an ancestor of the local branch and equals the job's
`pushed_sha` (job.json, set after every spine push of the branch):
`--force-with-lease=refs/heads/<b>:<pushed_sha>`, recorded as a `force_push` event (branch, old,
new). Any commit the spine did not push (origin ahead, a different tip, a job without
`pushed_sha`) is never overwritten: `push_lease_rejected` event and needs-human, no push. If
someone pushes after the fetch the lease fails the same way, no retry. The integration
branch, forbidden branches, any other branch and `publish` keep the plain fast-forward push, and
agents never force (the guard denies `--force*`). Every PR create/merge goes
through branches.check_pr_base with the PR's real `baseRefName`; `gh pr merge` uses
`--match-head-commit` with the sha that passed `merge-checks` after syncing with develop.

Closing the loop on GitHub (T11): `publish` announces a new PR on the issue (Cortadora), and a
successful `merge` closes the issue with the Reina's comment (GitHub only honours «Closes #n» on
the default branch), removes the queue label, deletes the remote `atta/<id>` through
`delete_remote_branch` (the one deletion Atta runs; agents never can, the guard denies it) and
removes the local worktree and branch when clean. Those post-merge steps only warn: the merge
already happened, so they exit 0 and are listed under `## Avisos` in merge.md. Each step is
isolated (any exception is a warning, never a traceback) and runs after merge.md and the
`merging → merged` advance, which happen as soon as GitHub confirms the merge.

PR body (K16, T7b): a new PR's body is the packaged `templates/pr-body.md` filled from the round's
build.md: `## Summary`, `## Changes` (cross-checked with `git diff --name-only
<origin/integration>...HEAD`, the file list of `git diff --stat`: files missing from either side
are listed under a warning line), `## Tests`, `## Acceptance` and `Closes #<issue>`. Without
build.md, with any of those sections empty or missing, or without the template, the body is the
previous one-line text. An existing PR is never edited.

Lessons (T4b, S6): once a job is merged (by Atta or by a person), `_finish_merge` harvests its
`PREVENTION:` lines into `atta/memory/lessons.md` (memory.Lessons.harvest, idempotent) as the
last isolated post-merge step: a failure is one more warning, never a block.

Safe sync (F3.x, K7/S4/S2): `_sync_gate_push` captures the job's own diff
(`git diff origin/<integration>...HEAD`, per file, hunk numbers and `index` ids stripped, context
lines kept) before the fetch and after a clean merge. Same diff → merge-checks, push, merge or
handoff as always. Different → the sync commit is pushed, decision.md is retired, a `sync_diff`
event (before/after ids, paths, via) is appended and the job goes back to `review`. A conflict
aborts the merge, writes `feedback-<k>.md` for the Cortadora, appends `sync_conflict` and the job
goes to `build`. In `awaiting-merge`, GitHub's `mergeable: CONFLICTING` does the same (UNKNOWN keeps
waiting). All of it is bounded by `sync_attempts` (machine.py); with no room left a conflict or a
changed diff fails like any merge failure (needs-human). Exit 0: it is progress, not a failure.

Resumable merge (L22): `atta merge` on a job in `merging` asks GitHub. MERGED at the sha recorded
in this stage's merge-checks.md → merge.md, merged and the post-merge steps; OPEN → continue
from the merge step (sync and Filtro again only if HEAD moved); CLOSED or another sha →
needs-human. On a merged job it is a no-op. `atta next` in `merging` runs it again.

Delivery by risk (S13): above `delivery.auto_merge_up_to` `atta next` runs `atta handoff` instead
of `atta merge`: the same identity checks, sync with develop, `merge-checks` and push as a merge
(so the person reviews exactly what lands), then the PR leaves draft and the job goes
`approve → awaiting-merge`; a failure in any of those essential steps → needs-human (handoff.md
`HANDOFF: FAIL` plus merge.md FAIL, as before). Then, non-essential (L31): the Reina comments on
the PR and the issue gets `labels.needs_review`; their failures are warnings (stderr and
`## Avisos` in handoff.md `HANDOFF: READY`), never an escalation. Every Atta label add creates the
label when the repository lacks it (init's LABEL_STYLE) and retries once.

Handoff recovery (L31): `atta handoff` on a needs-human job retakes it only when
`next.handoff_recovery` accepts it (a handoff escalated it, the latest archived decision is an
APPROVE newer than the last entry into approve, HEAD is the commit that passed merge-checks) and
the PR is open from atta/<id> into the integration branch: then the gated sha is pushed (a no-op
when origin has it), the PR leaves draft if needed, the job goes `needs-human → awaiting-merge`
and the non-essential steps run again, removing `labels.needs_human`. Otherwise exit 2 and
nothing changes. `atta merge` on `awaiting-merge` asks GitHub: OPEN → keeps waiting (exit 0, no
change); MERGED into the integration branch → merge.md DONE with the merged head sha (not
necessarily the Filtro's: the person may push fixes) and the same post-merge steps, also removing
`labels.needs_review`; CLOSED, another base or another head → needs-human. `awaiting-merge` holds
no `concurrency` slot; `atta queue` lists it as `resume` so `/atta run` checks it each pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import branches, config, deps, gate, github, intake, issue, machine, memory, project, voice
from .board import summary as artifact_summary
from .github import GhError
from .ids import InvalidId
from .events import append_event
from .initcmd import LABEL_STYLE
from .lock import LockTimeout, job_lock
from .next import _fresh, build_section_map, handoff_recovery
from .store import JobStore, StoreError, _replace
from .timeutil import to_iso, utcnow

OK, NEGATIVE, REFUSED = 0, 1, 2
GIT_TIMEOUT_S = 600
WORKTREES_DIR = ".worktrees"
DECISION_FILE = "decision.md"
MERGE_FILE = "merge.md"
HANDOFF_FILE = "handoff.md"
DEFAULT_MERGE_METHOD = "merge"  # local fallback for config key `merge_method`
WAITING_HUMAN = frozenset({"needs-info", "needs-human"})
AWAITING_MERGE = "awaiting-merge"
# Stages that do not hold a `concurrency` slot: a person has the next move.
NO_SLOT = WAITING_HUMAN | {AWAITING_MERGE}
RUNNING = frozenset(machine.STAGES) - machine.TERMINAL - NO_SLOT
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
PR_BODY_TEMPLATE = "pr-body.md"
PR_BODY_SECTIONS = ("Summary", "Changes", "Tests", "Acceptance")
_TEMPLATE_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
_CHANGE_PATH = re.compile(r"^\s*[-*]\s+`?([^\s`]+)`?")


class FlowError(RuntimeError):
    """A refused flow command (exit 2) or a failed git step."""


def _fork_message(number) -> str:
    return (f"El PR #{number} viene de otro repositorio (fork); Atta solo trabaja con ramas de "
            "este repositorio.")


class _MergeFailure(Exception):
    def __init__(self, reason: str, code: int = NEGATIVE):
        super().__init__(reason)
        self.reason, self.code = reason, code


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------- git

def git(cwd, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run `git -C <cwd> <args>` (argument list, no prompts); FlowError on failure if `check`."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                              env=env, timeout=GIT_TIMEOUT_S)
    except FileNotFoundError:
        raise FlowError("No se encontró git en el PATH.") from None
    except subprocess.TimeoutExpired:
        raise FlowError(f"git {args[0]} no terminó en {GIT_TIMEOUT_S} s.") from None
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise FlowError(f"git {args[0]} falló: {detail}")
    return proc


def _push(worktree: Path, branch: str, cfg: dict, *, job: dict | None = None,
          jobs: JobStore | None = None, lease: bool = True) -> None:
    """Push `branch` to origin under the branch policy: explicit refspec, never `--force`.

    With `job` and `jobs` and the job's own work branch (S3) the spine records `pushed_sha` in
    job.json after every push and, unless `lease` is False (publish), decides from origin's
    freshly fetched tip R, the local tip L and the recorded `pushed_sha` P:
    R == L: nothing to push. R ancestor of L: plain fast-forward push. L ancestor of R, or
    diverged with R != P (or no P): origin holds commits the spine did not push, so never force,
    needs-human. Diverged with R == P (the Cortadora rebased over our own last push): leased push
    on P. Any other branch, or no job, keeps the plain push.
    """
    branches.check_push_target(branch, cfg)
    spec = f"refs/heads/{branch}:refs/heads/{branch}"
    owned = (job is not None and jobs is not None
             and branches.owns_work_branch(branch, job["id"], cfg))
    if owned and lease:
        remote = git(worktree, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()
        if remote:
            git(worktree, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
            tip = git(worktree, "rev-parse", "--verify",
                      f"refs/remotes/origin/{branch}").stdout.strip()
            local = git(worktree, "rev-parse", "--verify", f"refs/heads/{branch}").stdout.strip()
            if tip == local:
                return
            behind = git(worktree, "merge-base", "--is-ancestor", tip, local, check=False)
            if behind.returncode == 1:  # origin's tip is not an ancestor of the local branch
                pushed = jobs.load(job["id"]).get("pushed_sha")
                ahead = git(worktree, "merge-base", "--is-ancestor", local, tip,
                            check=False).returncode == 0
                if ahead or not pushed or pushed != tip:
                    _foreign_commits(branch, pushed, tip, local, job, jobs)
                _push_leased(worktree, branch, spec, pushed, local, job, jobs)
                return
    git(worktree, "push", "origin", spec)
    if owned:
        _record_pushed(jobs, job["id"], git(worktree, "rev-parse", "--verify",
                                            f"refs/heads/{branch}").stdout.strip())


def _record_pushed(jobs: JobStore, jid: str, sha: str) -> None:
    """Remember the sha the spine last pushed to the job's branch (the basis of a later lease)."""
    _update(jobs, jid, pushed_sha=sha)


def _foreign_commits(branch: str, pushed: str | None, tip: str, local: str, job: dict,
                     jobs: JobStore):
    """Origin's branch holds something the spine did not push: record it and stop, no push."""
    append_event(jobs.job_dir(job["id"]), {
        "ts": to_iso(utcnow()), "job": job["id"], "event": "push_lease_rejected",
        "branch": branch, "expected": pushed, "actual": tip, "local": local})
    raise _MergeFailure(voice.push_foreign_commits(branch, pushed, tip, local))


def _push_leased(worktree: Path, branch: str, spec: str, expected: str, local: str,
                 job: dict, jobs: JobStore) -> None:
    """The Cortadora rebased over the spine's own last push (`expected`, also origin's tip):
    `--force-with-lease=<ref>:<that sha>`. Never a bare force and never a fresher lease: if
    someone pushed after the fetch the push fails and the job escalates."""
    job_dir = jobs.job_dir(job["id"])
    proc = git(worktree, "push", f"--force-with-lease=refs/heads/{branch}:{expected}", "origin",
               spec, check=False)
    if proc.returncode != 0:
        now = git(worktree, "ls-remote", "origin", f"refs/heads/{branch}",
                  check=False).stdout.split()
        actual = now[0] if now else None
        if actual == expected:
            detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
            raise FlowError(f"git push falló: {detail}")
        append_event(job_dir, {"ts": to_iso(utcnow()), "job": job["id"],
                               "event": "push_lease_rejected", "branch": branch,
                               "expected": expected, "actual": actual, "local": local})
        raise _MergeFailure(voice.push_lease_rejected(branch, expected, actual, local))
    append_event(job_dir, {"ts": to_iso(utcnow()), "job": job["id"], "event": "force_push",
                           "branch": branch, "old": expected, "new": local})
    _record_pushed(jobs, job["id"], local)


def delete_remote_branch(cwd, jid: str, branch: str, cfg: dict) -> None:
    """Delete origin's `atta/<jid>` after its merge: only this job's work branch, never the
    integration or a forbidden branch (same policy as a push), by its full ref."""
    expected = branches.work_branch(jid, cfg)
    if branch != expected:
        raise FlowError(f"La rama {branch} no es la rama de trabajo de {jid}; no la borro.")
    branches.check_push_target(branch, cfg)
    git(cwd, "push", "origin", "--delete", f"refs/heads/{branch}")


# ---------------------------------------------------------------- job helpers

def _update(jobs: JobStore, jid: str, **fields) -> dict:
    """Set job fields under the job lock (reload, change, atomic save)."""
    with job_lock(jobs.job_dir(jid)):
        job = jobs.load(jid)
        job.update(fields)
        jobs.save(job)
        return job


def _worktree(job: dict) -> Path:
    path = job.get("worktree")
    if not path or not Path(path).is_dir():
        raise FlowError(f"El job {job['id']} no tiene worktree; corre `atta worktree {job['id']}`.")
    return Path(path)


def _all_jobs(jobs: JobStore) -> list[dict]:
    return [jobs.load(jid) for jid in jobs.list_ids()]


def _job_for_issue(job_list: list[dict], number: int) -> dict | None:
    """The job of an issue: a non-terminal one first, else the most recent."""
    return deps.job_for_issue(job_list, number)


# ---------------------------------------------------------------- queue

def _running(job_list: list[dict], blocked=()) -> int:
    """Jobs holding a `concurrency` slot: not terminal, not waiting for a person and not
    stopped by BLOCKED-BY (`blocked`: job ids, see `_blocked`)."""
    return sum(1 for j in job_list if j["stage"] in RUNNING and j["id"] not in blocked)


def _blocked(job_list: list[dict], closed=None) -> dict[str, dict]:
    """{job id: deps verdict} for running jobs whose BLOCKED-BY stops them (F4 S7)."""
    index = deps.by_issue(job_list)
    out = {}
    for j in job_list:
        if j["stage"] in RUNNING:
            verdict = deps.check(j, index, closed)
            if verdict is not None:
                out[j["id"]] = verdict
    return out


def _issue_closed(open_numbers=()):
    """closed(number) for deps: open queue issues need no call; others ask gh once each. An
    issue gh cannot read counts as open, so its dependents keep waiting."""
    known = {n: False for n in open_numbers}

    def closed(number: int) -> bool:
        if number not in known:
            try:
                data = github.view_issue(number)
            except GhError:
                known[number] = False
            else:
                known[number] = str(data.get("state", "OPEN")).upper() != "OPEN"
        return known[number]

    return closed


def author_answered(data: dict, operator_login: str | None) -> bool:
    """True when the issue author commented after the last Atta comment (the Exploradora's
    question: the operator's comment with the marker). No Atta comment means no question yet."""
    author = github.login_of(data.get("author"))
    kept = github.filter_comments(data.get("comments"), author, operator_login)
    asked = [i for i, c in enumerate(kept) if c["author"] == github.ATTA_AUTHOR]
    if not asked or not author:
        return False
    return any(c["author"] == author for c in kept[asked[-1] + 1:])


def plan_queue(issues: list[dict], job_list: list[dict], cfg: dict,
               answered=None, closed=None) -> list[dict]:
    """Rows {action, issue, title, job, stage, reason}; action is new|resume|wait|hold|skip.

    Jobs in running stages (not terminal, not waiting for a human) hold the `concurrency` slots,
    even when their issue left the queue; each `new` takes one. A needs-info job is `resume`
    (and takes a slot) only when `answered(number)` says the author answered the Exploradora;
    otherwise it is `wait`. needs-human jobs are `hold`. awaiting-merge jobs are `resume` without
    taking a slot: `atta merge` checks whether the person merged the PR (S13). A running job
    whose BLOCKED-BY is not merged is `wait` (a rejected blocker or a cycle: `hold`) and frees
    its slot; `closed(number)` says whether a blocker's issue is closed (F4 S7, `deps`).
    """
    cap = cfg["concurrency"]
    blocked = _blocked(job_list, closed)
    running = _running(job_list, blocked)
    hold_labels = [cfg["labels"]["needs_human"], cfg["labels"]["needs_info"]]
    rows = []
    for it in sorted(issues, key=lambda i: i["number"]):
        number = it["number"]
        job = _job_for_issue(job_list, number)
        row = {"issue": number, "title": it.get("title"), "job": None, "stage": None}
        if job is not None:
            stage = job["stage"]
            row.update(job=job["id"], stage=stage)
            if stage in machine.TERMINAL:
                row.update(action="skip", reason=f"el job ya terminó ({stage}).")
            elif stage == "needs-info":
                if not (answered and answered(number)):
                    row.update(action="wait",
                               reason="espera la respuesta del autor del issue (needs-info).")
                elif running < cap:
                    row.update(action="resume",
                               reason="el autor respondió (needs-info): toca atta refresh.")
                    running += 1
                else:
                    row.update(action="wait", reason=f"el autor respondió, pero sin cupo: "
                                                     f"{running} de {cap} jobs corriendo.")
            elif stage == AWAITING_MERGE:
                row.update(action="resume", reason=f"espera que mergees el PR #{job.get('pr')}: "
                                                   "toca atta merge.")
            elif stage in WAITING_HUMAN:
                row.update(action="hold", reason=f"espera a una persona ({stage}).")
            elif job["id"] in blocked:
                verdict = blocked[job["id"]]
                row.update(action=verdict["action"], reason=verdict["reason"])
            else:
                row.update(action="resume", reason=f"en curso en la etapa {stage}.")
        else:
            labels = github.labels_of(it)
            held = next((lb for lb in hold_labels if lb in labels), None)
            if held:
                row.update(action="hold", reason=f"tiene la label {held}.")
            elif running < cap:
                row.update(action="new", reason=f"sin job; hay cupo ({running} de {cap} corriendo).")
                running += 1
            else:
                row.update(action="wait", reason=f"sin cupo: {running} de {cap} jobs corriendo.")
        rows.append(row)
    return rows


def cmd_queue(args, cfg, jobs: JobStore) -> int:
    label = cfg["labels"]["queue"]
    operator_login: list[str] = []

    def answered(number: int) -> bool:
        # Only needs-info jobs need the comments: one `gh issue view` each, `gh api user` once.
        if not operator_login:
            operator_login.append(github.operator())
        return author_answered(github.view_issue(number), operator_login[0])

    issues = github.list_issues(label)
    closed = _issue_closed(i["number"] for i in issues)
    rows = plan_queue(issues, _all_jobs(jobs), cfg, answered, closed)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return OK
    if not rows:
        print(f"No hay issues abiertos con la label {label}.")
        return OK
    table = [("ACCIÓN", "ISSUE", "JOB", "ETAPA", "MOTIVO")]
    table += [(r["action"], f"#{r['issue']}", r["job"] or "-", r["stage"] or "-", r["reason"])
              for r in rows]
    widths = [max(len(r[i]) for r in table) for i in range(4)]
    for r in table:
        print("  ".join([*(c.ljust(w) for c, w in zip(r, widths)), r[4]]).rstrip())
    return OK


# ---------------------------------------------------------------- start / worktree / publish

def cmd_start(args, cfg, jobs: JobStore) -> int:
    number = args.issue
    job_list = _all_jobs(jobs)
    existing = _job_for_issue(job_list, number)
    if existing is not None:
        # Resume path: the issue already has a job (also while it waits with a hold label).
        raise FlowError(f"El issue #{number} ya tiene el job {existing['id']}.")
    running, cap = _running(job_list, _blocked(job_list, _issue_closed())), cfg["concurrency"]
    if running >= cap:
        raise FlowError(f"El issue #{number} espera: sin cupo ({running} de {cap} jobs "
                        "corriendo).")
    data = github.view_issue(number)
    if str(data.get("state", "OPEN")).upper() != "OPEN":
        raise FlowError(f"El issue #{number} está cerrado.")
    labels = github.labels_of(data)
    queue_label = cfg["labels"]["queue"]
    if queue_label not in labels:
        raise FlowError(f"El issue #{number} no tiene la label {queue_label}: Atta solo toma "
                        "issues de la cola.")
    for held in (cfg["labels"]["needs_info"], cfg["labels"]["needs_human"]):
        if held in labels:
            raise FlowError(f"El issue #{number} tiene la label {held}: espera a una persona; "
                            "quítala para empezar.")
    comments = github.filter_comments(data.get("comments"), github.login_of(data.get("author")),
                                      github.operator())
    title = data.get("title") or ""
    job = jobs.create(number, title, data.get("url"), cfg)
    issue.write_issue_md(jobs.job_dir(job["id"]), number, title, data.get("body"), comments,
                         allowed_authors={c["author"] for c in comments})
    print(job["id"])
    return OK


def _write_issue_md_atomic(job_dir: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".issue.", suffix=".tmp", dir=job_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        _replace(tmp, job_dir / issue.ISSUE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_refresh(args, cfg, jobs: JobStore) -> int:
    """Re-read the issue into issue.md; a needs-info job goes back to intake without the label."""
    job = jobs.load(args.job)
    jid, number = job["id"], job["issue"]
    data = github.view_issue(number)
    if str(data.get("state", "OPEN")).upper() != "OPEN":
        raise FlowError(f"El issue #{number} está cerrado; no actualicé {jid}.")
    queue_label = cfg["labels"]["queue"]
    if queue_label not in github.labels_of(data):
        raise FlowError(f"El issue #{number} ya no tiene la label {queue_label}: salió de la "
                        f"cola; no actualicé {jid}.")
    comments = github.filter_comments(data.get("comments"), github.login_of(data.get("author")),
                                      github.operator())
    text = issue.render(number, data.get("title") or job.get("title") or "", data.get("body"),
                        comments, allowed_authors={c["author"] for c in comments})
    job_dir = jobs.job_dir(jid)
    with job_lock(job_dir):
        _write_issue_md_atomic(job_dir, text)
    print(f"issue.md de {jid} actualizado desde el issue #{number}.")
    if job["stage"] == "needs-info":
        machine.check(job, "intake")
        held = cfg["labels"]["needs_info"]
        # Label first: if advance fails, running refresh again redoes both.
        github.edit_label(number, "remove", held)
        machine.advance(jobs, jid, "intake")
        print(f"{jid} vuelve a intake: la Exploradora lee la respuesta. Label {held} quitada "
              f"del issue #{number}.")
    return OK


def cmd_worktree(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    jid = job["id"]
    if job.get("worktree") and Path(job["worktree"]).is_dir():
        print(f"El worktree ya existe: {job['worktree']}.")
        return OK
    branch = branches.work_branch(jid, cfg)
    integration = cfg["integration_branch"]
    # Absolute: `atta next` and the guard read job["worktree"] from any cwd.
    path = Path(os.path.abspath(Path(args.root) / WORKTREES_DIR / jid))
    git(args.root, "fetch", "origin")
    git(args.root, "worktree", "add", "--no-track", "-b", branch, str(path),
        f"origin/{integration}")
    _update(jobs, jid, worktree=str(path), branch=branch)
    print(f"Worktree listo en {path} (rama {branch}, desde origin/{integration}).")
    return OK


def _fallback_body(job: dict, integration: str) -> str:
    return (f"La Cortadora abre este PR para el issue #{job['issue']}. Se integra solo en "
            f"{integration}, cuando la Reina lo apruebe.\n\nCloses #{job['issue']}")


def _listed_paths(changes: str) -> list[str]:
    """The file of each `- path — what` line of build.md `## Changes`, in order."""
    out: list[str] = []
    for line in changes.splitlines():
        m = _CHANGE_PATH.match(line)
        if m:
            path = m.group(1).replace("\\", "/")
            if path not in out:
                out.append(path)
    return out


def _changes_check(wt: Path, base: str, changes: str) -> str:
    """The warning lines when build.md `## Changes` and the real diff list other files."""
    diff = git(wt, "diff", "--name-only", f"{base}...HEAD", check=False)
    if diff.returncode != 0:
        detail = (diff.stderr or diff.stdout).strip() or f"exit {diff.returncode}"
        return f"⚠️ No se pudo comparar con `git diff --stat {base}...HEAD`: {detail}"
    actual = [p.strip() for p in diff.stdout.splitlines() if p.strip()]
    listed = _listed_paths(changes)
    missing = [p for p in actual if p not in listed]
    extra = [p for p in listed if p not in actual]
    if not missing and not extra:
        return ""
    lines = [f"⚠️ La lista de cambios de build.md no coincide con "
             f"`git diff --stat {base}...HEAD`:"]
    if missing:
        lines.append("- En el diff pero no en build.md: " + ", ".join(f"`{p}`" for p in missing))
    if extra:
        lines.append("- En build.md pero no en el diff: " + ", ".join(f"`{p}`" for p in extra))
    return "\n".join(lines)


def _pr_body(job: dict, jobs: JobStore, wt: Path, integration: str) -> str:
    """K16: the PR body from pr-body.md and the round's build.md; the old text as fallback."""
    fallback = _fallback_body(job, integration)
    build = jobs.job_dir(job["id"]) / f"round-{job['total_rounds'] + 1}" / "build.md"
    try:
        sections = build_section_map(build.read_text(encoding="utf-8", errors="replace"))
        template = (project.TEMPLATES_DIR / PR_BODY_TEMPLATE).read_text(encoding="utf-8")
    except OSError:
        return fallback
    if any(not sections.get(name) for name in PR_BODY_SECTIONS):
        return fallback
    changes = sections["Changes"]
    warning = _changes_check(wt, f"origin/{integration}", changes)
    if warning:
        changes = f"{changes}\n\n{warning}"
    values = {"summary": sections["Summary"], "changes": changes, "tests": sections["Tests"],
              "acceptance": sections["Acceptance"], "issue": str(job["issue"])}
    body = _TEMPLATE_COMMENT.sub("", template, count=1)
    # One pass, so a value that contains `{{...}}` is never filled again.
    return re.sub(r"\{\{(summary|changes|tests|acceptance|issue)\}\}",
                  lambda m: values[m.group(1)], body).strip()


def cmd_publish(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    jid = job["id"]
    branch = job.get("branch") or ""
    branches.check_push_target(branch, cfg)
    expected = branches.work_branch(jid, cfg)
    if branch != expected:
        raise FlowError(f"La rama del job {jid} debe ser {expected}; job.json dice «{branch}».")
    wt = _worktree(job)
    current = git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if current != branch:
        raise FlowError(f"El worktree de {jid} está en «{current or 'HEAD suelto'}», no en {branch}.")
    integration = cfg["integration_branch"]
    pr = github.find_pr(branch)
    if pr is not None:
        if pr.get("isCrossRepository"):
            raise FlowError(_fork_message(pr.get("number")))
        branches.check_pr_base(pr.get("baseRefName") or "", cfg)
    _push(wt, branch, cfg, job=job, jobs=jobs, lease=False)
    if pr is None:
        branches.check_pr_base(integration, cfg)
        body = github.sign("cortadora", _pr_body(job, jobs, wt, integration))
        url = github.create_pr(branch, integration, job["title"], body)
        pr = github.view_pr(url)
        if pr.get("isCrossRepository"):
            raise FlowError(_fork_message(pr.get("number")))
        branches.check_pr_base(pr.get("baseRefName") or "", cfg)
        message = f"PR #{pr['number']} abierto en borrador contra {integration}: {pr['url']}"
        opened = True
    else:
        message = f"PR #{pr['number']} ya existe contra {integration}: {pr['url']}"
        opened = False
    _update(jobs, jid, pr=int(pr["number"]), pr_url=pr["url"])
    print(message)
    if opened:
        try:
            github.comment("issue", job["issue"], github.sign(
                "cortadora", voice.pr_opened(int(pr["number"]), integration)))
        except GhError as exc:
            _err(f"Aviso: no se pudo comentar en el issue #{job['issue']}: {exc}")
    return OK


# ---------------------------------------------------------------- comment / label

def _intake_accepted_text(job: dict, jobs: JobStore) -> str:
    """The Exploradora's ACCEPT comment, built from intake.md (risk and `## Summary`)."""
    path = jobs.job_dir(job["id"]) / "intake.md"
    try:
        header = intake.parse_file(path)
    except intake.IntakeError as exc:
        raise FlowError(str(exc)) from None
    if header["decision"] != "ACCEPT":
        raise FlowError(f"intake.md de {job['id']} no es ACCEPT ({header['decision']}): no "
                        "publico la aceptación.")
    text = path.read_text(encoding="utf-8", errors="replace")
    return voice.intake_accepted_comment(job["issue"], header["risk"], artifact_summary(text))


VOICES = {"intake-accepted": _intake_accepted_text}


def cmd_comment(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    if args.voice:
        text = VOICES[args.voice](job, jobs)
    else:
        text = _read_comment_file(Path(args.file))
    body = github.sign(args.role, text)
    target = args.on or ("pr" if job.get("pr") else "issue")
    number = job.get("pr") if target == "pr" else job["issue"]
    if not number:
        raise FlowError(f"El job {job['id']} no tiene PR todavía.")
    github.comment(target, number, body)
    print(f"Comentario publicado en el {'PR' if target == 'pr' else 'issue'} #{number}.")
    return OK


def _read_comment_file(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FlowError(f"No se pudo leer {path}: {exc.strerror or exc}.") from exc
    try:
        # utf-8-sig drops the BOM of PowerShell's `-Encoding utf8`; a NUL means UTF-16 text.
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise UnicodeDecodeError("utf-8", raw, 0, 1, "NUL")
    except UnicodeDecodeError:
        raise FlowError(f"{path} no es texto UTF-8; guárdalo en UTF-8 (en PowerShell 5: "
                        "Set-Content -Encoding utf8).") from None
    if not text.strip():
        raise FlowError(f"{path} está vacío: no se publica un comentario vacío.")
    return text


def _label_style(cfg: dict, name: str) -> tuple[str, str] | None:
    """(color, description) of an Atta label by its configured name, as `atta init` creates it;
    None for any other label."""
    for key, style in LABEL_STYLE.items():
        configured = (config.label(cfg, key) if key in config.OPTIONAL_LABELS
                      else (cfg.get("labels") or {}).get(key))
        if configured == name:
            return style
    return None


def _add_label(cfg: dict, number: int, name: str) -> None:
    """Add a label to the issue; an Atta label the repository lacks is created first (L31)."""
    github.edit_label(number, "add", name, create=_label_style(cfg, name))


def cmd_label(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    if args.action == "add":
        _add_label(cfg, job["issue"], args.name)
    else:
        github.edit_label(job["issue"], args.action, args.name)
    verb = "agregada al" if args.action == "add" else "quitada del"
    print(f"Label {args.name} {verb} issue #{job['issue']}.")
    return OK


# ---------------------------------------------------------------- merge

def _merge_method(cfg: dict) -> str:
    method = cfg.get("merge_method", DEFAULT_MERGE_METHOD)
    if method not in github.MERGE_METHODS:
        raise FlowError(f"config.json: merge_method «{method}» no es válido "
                        f"(usa: {', '.join(github.MERGE_METHODS)}).")
    return method


def _decision_approved(job_dir: Path) -> bool:
    try:
        with open(job_dir / DECISION_FILE, encoding="utf-8") as fh:
            first = fh.readline()
    except OSError:
        return False
    return first.strip() == "APPROVE"


def _check_pr_identity(pr: dict, branch: str, cfg: dict) -> None:
    """The PR is this repository's `branch` into the integration branch (any state)."""
    if pr.get("isCrossRepository"):
        raise _MergeFailure(_fork_message(pr.get("number")), REFUSED)
    try:
        branches.check_pr_base(pr.get("baseRefName") or "", cfg)
    except branches.BranchRefused as exc:
        raise _MergeFailure(str(exc), REFUSED) from None
    if pr.get("headRefName") != branch:
        raise _MergeFailure(f"El PR #{pr.get('number')} sale de «{pr.get('headRefName')}», "
                            f"no de {branch}.", REFUSED)


def _check_pr(pr: dict, branch: str, cfg: dict) -> None:
    _check_pr_identity(pr, branch, cfg)
    if str(pr.get("state", "OPEN")).upper() != "OPEN":
        raise _MergeFailure(f"El PR #{pr.get('number')} no está abierto ({pr.get('state')}).")


def _write_merge_md(job_dir: Path, verdict: str, summary: str, commit: str | None = None,
                    warnings: list[str] | None = None) -> None:
    lines = [f"MERGE: {verdict}"]
    if commit:
        lines.append(f"COMMIT: {commit}")
    lines += ["", "## Summary", "", summary, ""]
    if warnings:
        lines += ["## Avisos", "", *(f"- {w}" for w in warnings), ""]
    with open(job_dir / MERGE_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def _retire_decision(job_dir: Path) -> None:
    """Rename decision.md to the first free decision-<k>.md: a new approval needs a new one."""
    current = job_dir / DECISION_FILE
    if not current.exists():
        return
    k = 1
    while (job_dir / f"decision-{k}.md").exists():
        k += 1
    os.replace(current, job_dir / f"decision-{k}.md")


def _escalate(jobs: JobStore, job: dict, reason: str, code: int, cfg: dict) -> int:
    """Any merge failure: reason to merge.md, decision.md retired, job to needs-human, Reina
    comment, label."""
    jid = job["id"]
    _write_merge_md(jobs.job_dir(jid), "FAIL", reason)
    _retire_decision(jobs.job_dir(jid))
    machine.advance(jobs, jid, "needs-human")
    try:
        github.comment("pr", job["pr"], github.sign("reina", voice.reina_escalates(reason)))
    except GhError as exc:
        _err(f"Aviso: no se pudo comentar en el PR #{job['pr']}: {exc}")
    try:
        _add_label(cfg, job["issue"], cfg["labels"]["needs_human"])
    except GhError as exc:
        _err(f"Aviso: no se pudo poner la label en el issue #{job['issue']}: {exc}")
    _err(reason)
    return code


class _SyncRework(Exception):
    """The sync found the job needs another pass (F3.x): `to` is `review` (the clean merge
    changed the job's own diff, K7) or `build` (conflict, S4). Carries what to record."""

    def __init__(self, to: str, event: dict, message: str, feedback: str | None = None):
        super().__init__(message)
        self.to, self.event, self.message, self.feedback = to, event, message, feedback


def _sync_budget_left(job: dict, cfg: dict, *, counted: bool) -> bool:
    """True when, after this attempt, another sync attempt would still fit in `sync_attempts`
    (`max_rounds`): a rework whose next attempt is already refused would only waste a round.
    `counted`: the job is in `merging`, so this attempt is already in `sync_attempts`."""
    used = job["sync_attempts"] + (0 if counted else 1)
    return used < cfg["max_rounds"]


_DIFF_FILE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK = re.compile(r"^@@ .*? @@.*$")


def _job_diff(wt: Path, integration: str) -> dict[str, str]:
    """The job's own diff against origin/<integration> (`git diff origin/<integration>...HEAD`,
    from the merge base) as {path: digest}.

    K7 compares this before and after a sync. The digest covers the file's header and every
    changed and context line, but not the line numbers of the hunk headers, the function name
    after them nor the `index` blob ids (they move whenever the base moves and say nothing about
    what the job changes; the `index` line is kept for binary files, where it is the content).
    Context lines stay in: when the integration branch edits next to the job's hunks the
    reviewers saw different code, which `git patch-id` (changed lines only) would miss.
    """
    text = git(wt, "diff", "--no-color", "--no-ext-diff", "--no-renames",
               f"origin/{integration}...HEAD").stdout
    sections: dict[str, list[str]] = {}
    path = None
    for line in text.splitlines():
        match = _DIFF_FILE.match(line)
        if match:
            path = match.group(2)
            sections[path] = []
        if path is None:
            continue
        sections[path].append("@@" if _HUNK.match(line) else line)
    digests = {}
    for path, lines in sections.items():
        binary = any(l.startswith(("Binary files", "GIT binary patch")) for l in lines)
        kept = [l for l in lines if binary or not l.startswith("index ")]
        digests[path] = hashlib.sha256("\n".join(kept).encode("utf-8", "replace")).hexdigest()
    return digests


def _diff_id(digests: dict[str, str]) -> str:
    """One short identifier for a `_job_diff` (sha256 of its sorted path/digest pairs)."""
    blob = "\n".join(f"{p}\t{d}" for p, d in sorted(digests.items()))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _diff_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths whose part of the job diff differs (or exists on one side only), sorted."""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def _sync_gate_push(args, cfg, jobs: JobStore, job: dict, wt: Path, via: str = "merge") -> str:
    """Sync with origin/<integration>, run merge-checks, push; return the sha that passed.

    K7 (S1): the job's own diff is captured before the fetch and again after a clean merge. If
    it changed, the sync commit is pushed (reviewers see the new diff) and _SyncRework("review")
    is raised instead of continuing; a conflict raises _SyncRework("build") (S4). Both only while
    `sync_attempts` leaves room for another attempt; otherwise they fail like any merge failure
    (→ needs-human). `via` (merge, handoff) is recorded in the events.
    """
    integration = cfg["integration_branch"]
    branch, number = job["branch"], job["pr"]
    current = git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if current != branch:
        raise _MergeFailure(f"El worktree de {job['id']} está en «{current or 'HEAD suelto'}», "
                            f"no en {branch}.")
    _check_pr(github.view_pr(number), branch, cfg)
    before = _job_diff(wt, integration)
    git(wt, "fetch", "origin")
    merged = git(wt, "merge", "--no-edit", f"origin/{integration}", check=False)
    room = _sync_budget_left(job, cfg, counted=job["stage"] == "merging")
    if merged.returncode != 0:
        conflicts = git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        git(wt, "merge", "--abort", check=False)
        if conflicts:
            if room:
                raise _SyncRework(
                    "build", {"event": "sync_conflict", "files": conflicts, "via": via},
                    voice.sync_conflict_build(number, integration, conflicts),
                    voice.sync_conflict_feedback(branch, integration, conflicts, number))
            raise _MergeFailure(f"Conflicto al sincronizar con origin/{integration} en "
                                f"{', '.join(conflicts)}.")
        detail = (merged.stderr or merged.stdout).strip()
        raise _MergeFailure(f"No se pudo sincronizar con origin/{integration}: {detail}")
    after = _job_diff(wt, integration)
    if after != before:
        paths = _diff_changes(before, after)
        if not room:
            raise _MergeFailure(
                f"El diff del PR cambió al sincronizar con origin/{integration} "
                f"({', '.join(paths)}) y ya no quedan sincronizaciones: sync_attempts llegó a "
                f"{job['sync_attempts'] + (0 if job['stage'] == 'merging' else 1)} de "
                f"{cfg['max_rounds']}.")
        try:
            _push(wt, branch, cfg, job=job, jobs=jobs)
        except branches.BranchRefused as exc:
            raise _MergeFailure(str(exc), REFUSED) from None
        raise _SyncRework(
            "review", {"event": "sync_diff", "before": _diff_id(before),
                       "after": _diff_id(after), "paths": paths, "via": via},
            voice.sync_diff_review(number, integration, paths))
    round_dir = jobs.job_dir(job["id"]) / f"round-{job['total_rounds'] + 1}"
    try:
        result = gate.run_gate(Path(args.root), wt, job, round_dir,
                               base_ref=f"origin/{integration}", config=cfg, kind="merge-checks")
    except gate.GateError as exc:
        raise _MergeFailure(str(exc)) from None
    if not result.passed:
        raise _MergeFailure(result.summary)
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    if head != result.commit:
        raise _MergeFailure("El worktree cambió mientras corría el Filtro.")
    tip = git(wt, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).stdout.strip()
    if tip != result.commit:
        raise _MergeFailure(f"La rama {branch} no apunta al commit que pasó el Filtro "
                            f"({result.commit}); no se empuja.")
    try:
        _push(wt, branch, cfg, job=job, jobs=jobs)
    except branches.BranchRefused as exc:
        raise _MergeFailure(str(exc), REFUSED) from None
    return result.commit


def _next_feedback(job_dir: Path) -> Path:
    k = 1
    while (job_dir / f"feedback-{k}.md").exists():
        k += 1
    return job_dir / f"feedback-{k}.md"


def _apply_rework(jobs: JobStore, job: dict, cfg: dict, rework: _SyncRework) -> int:
    """Act on a _SyncRework: feedback for the Cortadora (conflicts), the old decision retired,
    the sync event, and the job back to review or build. Exit 0: it is progress, not a failure.
    From awaiting-merge the review label goes, nobody waits for a person any more."""
    jid = job["id"]
    job_dir = jobs.job_dir(jid)
    if rework.feedback is not None:
        with open(_next_feedback(job_dir), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(rework.feedback)
    _retire_decision(job_dir)
    append_event(job_dir, {"ts": to_iso(utcnow()), "job": jid, **rework.event})
    machine.advance(jobs, jid, rework.to)
    if job["stage"] == AWAITING_MERGE:
        review = config.label(cfg, "needs_review")
        try:
            github.edit_label(job["issue"], "remove", review)
        except GhError as exc:
            _err(f"Aviso: no se pudo quitar la label {review} del issue #{job['issue']}: {exc}")
    print(rework.message)
    return OK


def _push_gated(job: dict, wt: Path, sha: str, cfg: dict, jobs: JobStore | None = None) -> str:
    """Resume with the sha merge-checks already passed: same checks as after the Filtro (branch
    checked out, branch tip = sha), then the push (a no-op when origin has it)."""
    branch = job["branch"]
    current = git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if current != branch:
        raise _MergeFailure(f"El worktree de {job['id']} está en «{current or 'HEAD suelto'}», "
                            f"no en {branch}.")
    tip = git(wt, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).stdout.strip()
    if tip != sha:
        raise _MergeFailure(f"La rama {branch} no apunta al commit que pasó el Filtro "
                            f"({sha}); no se empuja.")
    _push(wt, branch, cfg, job=job, jobs=jobs)
    return sha


def _merged_at(state: dict, sha: str) -> bool:
    return str(state.get("state") or "").upper() == "MERGED" and state.get("headRefOid") == sha


def _merge_at(cfg: dict, job: dict, sha: str, method: str) -> list[str]:
    """Ready the PR and merge it at `sha`. When `gh pr merge` fails but GitHub shows the PR
    MERGED at `sha` (timeout, network), the merge happened: return that as a warning."""
    number = job["pr"]
    pr = github.view_pr(number)
    _check_pr(pr, job["branch"], cfg)
    if pr.get("isDraft"):
        github.ready_pr(number)
    try:
        github.merge_pr(number, sha, method)
    except GhError as exc:
        try:
            confirmed = _merged_at(github.merge_state(number), sha)
        except GhError:
            confirmed = False
        if not confirmed:
            raise
        return [f"gh pr merge devolvió un error ({exc}), pero GitHub confirma el merge del PR "
                f"#{number} con {sha}."]
    return []


def _remove_local(root, wt: Path | None, branch: str, sha: str) -> list[str]:
    """Remove the job's worktree (when clean) and local branch; warnings otherwise. A worktree
    already gone is pruned and only the branch is deleted."""
    if wt is not None and wt.is_dir():
        status = git(wt, "status", "--porcelain", check=False)
        if status.returncode != 0:
            detail = (status.stderr or status.stdout).strip()
            return [f"No se pudo revisar el worktree {wt}: {detail}; lo dejé."]
        if status.stdout.strip():
            return [f"El worktree {wt} tiene cambios sin commitear: lo dejé, junto con la rama "
                    f"local {branch}."]
        try:
            git(root, "worktree", "remove", str(wt))
        except FlowError as exc:
            return [f"No se pudo quitar el worktree {wt}: {exc}"]
    else:
        git(root, "worktree", "prune", check=False)
    if not git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}",
               check=False).stdout.strip():
        return []
    # `git branch -d` compares with the local HEAD, which is behind origin/<integration>; the
    # proof here is stronger: the branch still points at the exact sha GitHub merged.
    tip = git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False)
    if tip.stdout.strip() != sha:
        return [f"La rama local {branch} ya no apunta al commit mergeado ({sha}); la dejé."]
    try:
        git(root, "branch", "-D", branch)
    except FlowError as exc:
        return [f"No se pudo borrar la rama local {branch}: {exc}"]
    return []


_STEP_ERRORS = (GhError, FlowError, branches.BranchRefused)


def _step(warnings: list[str], label: str, func) -> None:
    """Run one post-merge step; any exception becomes the warning `<label>: <detail>` (L22: no
    traceback after GitHub merged). A step may also return its own list of warnings."""
    try:
        result = func()
    except _STEP_ERRORS as exc:
        warnings.append(f"{label}: {exc}")
    except Exception as exc:  # noqa: BLE001 - the merge already happened; never crash here
        warnings.append(f"{label}: {type(exc).__name__}: {exc}")
    else:
        warnings.extend(result or [])


def _delete_remote_after_merge(root, jid: str, branch: str, cfg: dict) -> None:
    try:
        delete_remote_branch(root, jid, branch, cfg)
    except FlowError as exc:
        # GitHub's «automatically delete head branches» may have deleted it already.
        if "remote ref does not exist" not in str(exc):
            raise


def _after_merge(args, cfg, job: dict, wt: Path | None, sha: str, closing: str | None = None,
                 extra_labels: tuple = ()) -> list[str]:
    """Close the loop on GitHub after a merge (T11). Each step is isolated: a failure is a
    warning and the next step still runs. `closing` replaces the Reina's closing text and
    `extra_labels` are removed after the queue label (S13: the person's merge)."""
    warnings: list[str] = []
    number, pr, branch = job["issue"], job["pr"], job["branch"]
    integration, queue = cfg["integration_branch"], cfg["labels"]["queue"]
    body = github.sign("reina", closing or voice.merged_issue(pr, integration, number))
    _step(warnings, f"No se pudo comentar el cierre en el issue #{number}",
          lambda: github.comment("issue", number, body))
    _step(warnings, f"No se pudo cerrar el issue #{number}", lambda: github.close_issue(number))
    for name in (queue, *extra_labels):
        _step(warnings, f"No se pudo quitar la label {name} del issue #{number}",
              lambda name=name: github.edit_label(number, "remove", name))
    _step(warnings, f"No se pudo borrar la rama remota {branch}",
          lambda: _delete_remote_after_merge(args.root, job["id"], branch, cfg))
    _step(warnings, f"No se pudo limpiar el worktree {wt} y la rama local {branch}",
          lambda: _remove_local(args.root, wt, branch, sha))
    return warnings


def _finish_merge(args, cfg, jobs: JobStore, job: dict, wt: Path | None, sha: str,
                  warnings: list[str] | None = None, *, line: str | None = None,
                  closing: str | None = None, extra_labels: tuple = ()) -> int:
    """GitHub merged `sha`: merge.md and `→ merged` first, then the post-merge steps."""
    jid = job["id"]
    warnings = list(warnings or [])
    line = line or voice.merged(job["pr"], cfg["integration_branch"])
    _write_merge_md(jobs.job_dir(jid), "DONE", line, sha, warnings)
    job = machine.advance(jobs, jid, "merged")
    print(line)
    warnings += _after_merge(args, cfg, job, wt, sha, closing, extra_labels)
    # T4b: the Cultivadora runs once the job is closed; isolated like every post-merge step.
    harvested = len(warnings)
    _step(warnings, f"No se pudieron cosechar las lecciones de {jid}",
          lambda: _harvest(args.root, jobs, jid))
    if len(warnings) == harvested:  # a damaged lessons.md is reported once, by the harvest
        _step(warnings, f"No se pudo abrir el issue {config.label(cfg, 'ratchet')}",
              lambda: _ratchet(args.root, cfg, jobs, jid))
    if warnings:
        _write_merge_md(jobs.job_dir(jid), "DONE", line, sha, warnings)
        for warning in warnings:
            _err(f"Aviso: {warning}")
    return OK


def _harvest(root, jobs: JobStore, jid: str) -> None:
    """Fold the job's PREVENTION: lines into atta/memory/lessons.md (once per job)."""
    try:
        memory.Lessons(root).harvest(jid, jobs_dir=jobs.root)
    except (memory.LessonsError, LockTimeout) as exc:
        raise FlowError(str(exc)) from None  # the message already says what to do


def _ratchet(root, cfg: dict, jobs: JobStore, jid: str) -> list[str]:
    """K1 (F3.x T3): one `atta:ratchet` issue per mechanizable lesson seen in 2+ jobs. Only that
    label, never the queue's (S5). An issue already on GitHub with the same title is reused, so
    a number lost before reaching lessons.md never makes a duplicate."""
    label = config.label(cfg, "ratchet")
    queue = cfg["labels"]["queue"]
    failed: list[str] = []

    def open_issue(lesson) -> int:
        title = voice.ratchet_title(lesson.slug)
        try:
            number = github.find_issue(label, title)
            if number is None:
                body = github.sign("cultivadora", voice.ratchet_issue(
                    lesson.slug, lesson.rule, lesson.times, lesson.mechanizable, lesson.cases,
                    queue))
                number = github.create_issue(title, body, label, create=_label_style(cfg, label))
        except GhError:
            failed.append(lesson.slug)
            raise
        return number

    try:
        opened = memory.Lessons(root).ratchet(open_issue)
    except GhError as exc:
        return [f"No se pudo abrir el issue {label} de la lección {failed[-1]}: {exc}"]
    except (memory.LessonsError, LockTimeout) as exc:
        return [f"No se pudo abrir el issue {label}: {exc}"]
    for slug, number in opened:
        append_event(jobs.job_dir(jid), {"ts": to_iso(utcnow()), "job": jid,
                                         "event": "ratchet_issue", "lesson": slug,
                                         "issue": number})
        print(voice.ratchet_opened(number, slug))
    return []


_MERGE_ERRORS = (_MergeFailure, branches.BranchRefused, GhError, FlowError)


def _failure(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, _MergeFailure):
        return exc.reason, exc.code
    if isinstance(exc, branches.BranchRefused):
        return str(exc), REFUSED
    return str(exc), NEGATIVE


def _check_refs(job: dict, cfg: dict) -> None:
    jid = job["id"]
    expected = branches.work_branch(jid, cfg)
    if job.get("branch") != expected:
        raise FlowError(f"La rama del job {jid} debe ser {expected}; job.json dice "
                        f"«{job.get('branch') or ''}».")
    if not job.get("pr"):
        raise FlowError(f"El job {jid} no tiene PR; corre `atta publish {jid}`.")


def _gated_sha(jobs: JobStore, job: dict) -> str | None:
    """The sha merge-checks passed during this `merging` stage (round-N/merge-checks.md written
    since stage_since, `GATE: PASS` and `COMMIT: <sha>`), or None."""
    path = jobs.job_dir(job["id"]) / f"round-{job['total_rounds'] + 1}" / "merge-checks.md"
    if not _fresh(path, job):
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if len(lines) < 2 or lines[0].strip() != "GATE: PASS" or not lines[1].startswith("COMMIT:"):
        return None
    sha = lines[1][len("COMMIT:"):].strip()
    return sha if _SHA.fullmatch(sha) else None


def _resume_merge(args, cfg, jobs: JobStore, job: dict) -> int:
    """A job left in `merging` (L22): ask GitHub. MERGED at the gated sha → finish; OPEN →
    continue from the merge step (sync and Filtro again only if HEAD moved); anything else →
    needs-human. A failed read of the PR leaves the job in merging for another try."""
    jid = job["id"]
    _check_refs(job, cfg)
    number = job["pr"]
    gated = _gated_sha(jobs, job)
    state = github.merge_state(number)
    try:
        _check_pr_identity(state, job["branch"], cfg)
    except _MergeFailure as exc:
        return _escalate(jobs, job, exc.reason, exc.code, cfg)
    status = str(state.get("state") or "").upper()
    head = state.get("headRefOid") or "?"
    if status == "MERGED":
        if not gated:
            return _escalate(jobs, job, f"El PR #{number} ya está mergeado en GitHub con el "
                             f"commit {head}, pero no hay un merge-checks.md con PASS de este "
                             "merge para comprobarlo; revísalo.", NEGATIVE, cfg)
        if head != gated:
            return _escalate(jobs, job, f"El PR #{number} ya está mergeado en GitHub, pero con "
                             f"el commit {head}, no con el que pasó el Filtro ({gated}); "
                             "revísalo.", NEGATIVE, cfg)
        print(f"El PR #{number} ya está mergeado en GitHub con el commit que pasó el Filtro; "
              f"la Reina termina el merge de {jid}.")
        wt = Path(job["worktree"]) if job.get("worktree") else None
        return _finish_merge(args, cfg, jobs, job, wt, gated)
    if status != "OPEN":
        reason = (f"El PR #{number} está cerrado en GitHub sin merge; la Reina no lo reabre."
                  if status == "CLOSED" else
                  f"El PR #{number} está en un estado inesperado en GitHub "
                  f"({status or 'desconocido'}); revísalo.")
        return _escalate(jobs, job, reason, NEGATIVE, cfg)
    if not _decision_approved(jobs.job_dir(jid)):
        raise FlowError(f"La Reina no aprobó {jid}: decision.md debe empezar con APPROVE.")
    wt = _worktree(job)
    method = _merge_method(cfg)
    try:
        local = git(wt, "rev-parse", "HEAD", check=False).stdout.strip()
        if gated and local == gated:
            print(f"El PR #{number} sigue abierto; la Reina retoma el merge de {jid} con el "
                  "commit que pasó el Filtro.")
            sha = _push_gated(job, wt, gated, cfg, jobs)
        else:
            why = ("pero la rama cambió desde el Filtro" if gated else
                   "pero no hay un merge-checks.md con PASS de este merge")
            print(f"El PR #{number} sigue abierto, {why}; la Reina vuelve a sincronizar {jid}.")
            sha = _sync_gate_push(args, cfg, jobs, job, wt)
        warnings = _merge_at(cfg, job, sha, method)
    except _SyncRework as rework:
        return _apply_rework(jobs, job, cfg, rework)
    except _MERGE_ERRORS as exc:
        return _escalate(jobs, job, *_failure(exc), cfg)
    return _finish_merge(args, cfg, jobs, job, wt, sha, warnings)


def _escalate_review(jobs: JobStore, job: dict, reason: str, code: int, cfg: dict) -> int:
    """needs-human from awaiting-merge: like any merge failure, and the review label goes."""
    result = _escalate(jobs, job, reason, code, cfg)
    review = config.label(cfg, "needs_review")
    try:
        github.edit_label(job["issue"], "remove", review)
    except GhError as exc:
        _err(f"Aviso: no se pudo quitar la label {review} del issue #{job['issue']}: {exc}")
    return result


def _await_merge(args, cfg, jobs: JobStore, job: dict) -> int:
    """S13: the PR waits for a person. OPEN → keep waiting; MERGED into the integration branch →
    finish like a merge of Atta's own, at the head GitHub merged; anything else → needs-human. A
    failed read of the PR leaves the job as it was."""
    jid = job["id"]
    _check_refs(job, cfg)
    number, integration = job["pr"], cfg["integration_branch"]
    state = github.merge_state(number)
    try:
        _check_pr_identity(state, job["branch"], cfg)
    except _MergeFailure as exc:
        return _escalate_review(jobs, job, exc.reason, exc.code, cfg)
    status = str(state.get("state") or "").upper()
    if status == "OPEN":
        if str(state.get("mergeable") or "").upper() == "CONFLICTING":
            # S2: GitHub says the PR conflicts with the integration branch; UNKNOWN (still
            # computing) and MERGEABLE keep waiting.
            if not _sync_budget_left(job, cfg, counted=False):
                return _escalate_review(
                    jobs, job, f"El PR #{number} está en conflicto con origin/{integration} en "
                    f"GitHub y ya no quedan sincronizaciones: sync_attempts llegó a "
                    f"{job['sync_attempts'] + 1} de {cfg['max_rounds']}.", NEGATIVE, cfg)
            return _apply_rework(jobs, job, cfg, _SyncRework(
                "build", {"event": "sync_conflict", "files": [], "via": "awaiting-merge"},
                voice.github_conflict_build(number, integration),
                voice.sync_conflict_feedback(job["branch"], integration, [], number)))
        print(voice.waiting_review(number))
        return OK
    if status == "MERGED":
        head = state.get("headRefOid") or ""
        if not (isinstance(head, str) and _SHA.fullmatch(head)):
            return _escalate_review(jobs, job, f"El PR #{number} está mergeado en GitHub, pero "
                                    "no pude leer el commit que se integró; revísalo.",
                                    NEGATIVE, cfg)
        print(f"El PR #{number} ya está mergeado en {integration}; la Reina cierra el ciclo de "
              f"{jid}.")
        wt = Path(job["worktree"]) if job.get("worktree") else None
        return _finish_merge(args, cfg, jobs, job, wt, head,
                             line=voice.human_merged(number, integration),
                             closing=voice.human_merged_issue(number, integration, job["issue"]),
                             extra_labels=(config.label(cfg, "needs_review"),))
    reason = (f"El PR #{number} está cerrado en GitHub sin merge; la Reina no lo reabre."
              if status == "CLOSED" else
              f"El PR #{number} está en un estado inesperado en GitHub "
              f"({status or 'desconocido'}); revísalo.")
    return _escalate_review(jobs, job, reason, NEGATIVE, cfg)


def _check_approved(jobs: JobStore, job: dict, command: str) -> None:
    """Stage approve and a decision.md = APPROVE written during it (merge and handoff)."""
    jid = job["id"]
    if job["stage"] != "approve":
        raise FlowError(f"El job {jid} está en {job['stage']}; {command} exige la etapa approve.")
    if not _decision_approved(jobs.job_dir(jid)):
        raise FlowError(f"La Reina no aprobó {jid}: decision.md debe empezar con APPROVE.")
    if not _fresh(jobs.job_dir(jid) / DECISION_FILE, job):
        raise FlowError(f"La decisión de la Reina es vieja: decision.md de {jid} es anterior a "
                        "la etapa approve; hace falta una nueva.")


def _write_handoff_md(job_dir: Path, verdict: str, summary: str, commit: str | None = None,
                      pr: int | None = None, warnings: list[str] | None = None) -> None:
    """handoff.md: `HANDOFF: READY` (COMMIT, PR, avisos) or `HANDOFF: FAIL` with the reason."""
    lines = [f"HANDOFF: {verdict}"]
    if commit:
        lines.append(f"COMMIT: {commit}")
    if pr:
        lines.append(f"PR: #{pr}")
    lines += ["", "## Summary", "", summary, ""]
    if warnings:
        lines += ["## Avisos", "", *(f"- {w}" for w in warnings), ""]
    with open(job_dir / HANDOFF_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def _job_risk(job: dict):
    """The Filtro's effective risk (G3) when set, else the intake's or the routed one."""
    return (job.get("risk_effective") or (job.get("intake") or {}).get("risk")
            or (job.get("agents") or {}).get("risk"))


def _finish_handoff(cfg, jobs: JobStore, job: dict, sha: str, *,
                    clear_needs_human: bool = False) -> int:
    """The essential steps passed: awaiting-merge first, then the non-essential steps (Reina's
    PR comment, review label, and on a recovery removing needs_human); each failure is a
    warning on stderr and under `## Avisos` in handoff.md (L31)."""
    jid, number, issue_n = job["id"], job["pr"], job["issue"]
    integration, risk = cfg["integration_branch"], _job_risk(job)
    review = config.label(cfg, "needs_review")
    machine.advance(jobs, jid, AWAITING_MERGE)
    warnings: list[str] = []
    escalation = job.get("risk_escalation")
    comment = voice.handoff_comment(risk, integration)
    if escalation:
        comment = f"{comment} {escalation}"
    body = github.sign("reina", comment)
    _step(warnings, f"No se pudo comentar en el PR #{number}",
          lambda: github.comment("pr", number, body))
    _step(warnings, f"No se pudo poner la label {review} en el issue #{issue_n}",
          lambda: _add_label(cfg, issue_n, review))
    if clear_needs_human:
        held = cfg["labels"]["needs_human"]
        _step(warnings, f"No se pudo quitar la label {held} del issue #{issue_n}",
              lambda: github.edit_label(issue_n, "remove", held))
    line = voice.handoff_ready(number, risk, integration, job.get("pr_url"))
    _write_handoff_md(jobs.job_dir(jid), "READY", line, sha, number, warnings)
    if escalation:
        print(escalation)
    print(line)
    for warning in warnings:
        _err(f"Aviso: {warning}")
    return OK


def _recover_handoff(args, cfg, jobs: JobStore, job: dict) -> int:
    """L31 (c): retake a needs-human job that a handoff escalated, when the approval still holds
    and nothing changed since the Filtro; otherwise refuse (exit 2) without changes."""
    jid = job["id"]
    _check_refs(job, cfg)
    sha, reason = handoff_recovery(job, jobs.job_dir(jid), job.get("worktree"))
    if reason:
        raise FlowError(reason)
    wt = _worktree(job)
    number = job["pr"]
    pr = github.view_pr(number)
    try:
        _check_pr_identity(pr, job["branch"], cfg)
    except _MergeFailure as exc:
        raise FlowError(exc.reason) from None
    if str(pr.get("state") or "").upper() != "OPEN":
        raise FlowError(f"El PR #{number} no está abierto ({pr.get('state')}); no se retoma "
                        "el handoff.")
    try:
        _push_gated(job, wt, sha, cfg, jobs)
        if pr.get("isDraft"):
            github.ready_pr(number)
    except _MERGE_ERRORS as exc:
        raise FlowError(_failure(exc)[0]) from None
    print(voice.handoff_resumed(number))
    return _finish_handoff(cfg, jobs, job, sha, clear_needs_human=True)


def cmd_handoff(args, cfg, jobs: JobStore) -> int:
    """S13: the job's risk is above auto_merge_up_to. Sync, Filtro and push like a merge, the PR
    is made ready and the job waits in awaiting-merge for a person to merge it; an essential
    failure → needs-human. The Reina's comment and the review label only warn (L31). On a
    needs-human job that a handoff escalated, retakes it when nothing changed."""
    job = jobs.load(args.job)
    jid = job["id"]
    if job["stage"] == AWAITING_MERGE:
        print(f"El job {jid} ya espera tu merge en el PR #{job.get('pr')}.")
        return OK
    if job["stage"] == "needs-human":
        return _recover_handoff(args, cfg, jobs, job)
    _check_approved(jobs, job, "handoff")
    _check_refs(job, cfg)
    wt = _worktree(job)
    number = job["pr"]
    print(voice.reina_approved(number))
    try:
        sha = _sync_gate_push(args, cfg, jobs, job, wt, via="handoff")
        pr = github.view_pr(number)
        _check_pr(pr, job["branch"], cfg)
        if pr.get("isDraft"):
            github.ready_pr(number)
    except _SyncRework as rework:
        return _apply_rework(jobs, job, cfg, rework)
    except _MERGE_ERRORS as exc:
        reason, code = _failure(exc)
        _write_handoff_md(jobs.job_dir(jid), "FAIL", reason)
        return _escalate(jobs, job, reason, code, cfg)
    return _finish_handoff(cfg, jobs, job, sha)


def cmd_merge(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    jid = job["id"]
    if job["stage"] == "merged":
        print(f"El job {jid} ya está mergeado: nada que hacer.")
        return OK
    if job["stage"] == "merging":
        return _resume_merge(args, cfg, jobs, job)
    if job["stage"] == AWAITING_MERGE:
        return _await_merge(args, cfg, jobs, job)
    _check_approved(jobs, job, "merge")
    _check_refs(job, cfg)
    wt = _worktree(job)
    method = _merge_method(cfg)
    try:
        branches.assert_round_budget(job, "sync_attempts", cfg)
    except branches.RoundBudgetExhausted as exc:
        return _escalate(jobs, job, str(exc), NEGATIVE, cfg)
    job = machine.advance(jobs, jid, "merging")
    print(voice.reina_approved(job["pr"]))
    try:
        sha = _sync_gate_push(args, cfg, jobs, job, wt)
        warnings = _merge_at(cfg, job, sha, method)
    except _SyncRework as rework:
        return _apply_rework(jobs, job, cfg, rework)
    except _MERGE_ERRORS as exc:
        return _escalate(jobs, job, *_failure(exc), cfg)
    return _finish_merge(args, cfg, jobs, job, wt, sha, warnings)


# ---------------------------------------------------------------- parser

_HANDLED = (FlowError, GhError, branches.BranchRefused, machine.Refused, StoreError, InvalidId,
            LockTimeout, gate.GateError)


def _guarded(func):
    def handler(args, cfg, jobs):
        try:
            return func(args, cfg, jobs)
        except _HANDLED as exc:
            _err(str(exc))
            return REFUSED
    handler.__name__ = func.__name__
    handler.__doc__ = func.__doc__
    return handler


def register(subparsers) -> None:
    """Add queue, start, refresh, worktree, publish, comment, label, handoff and merge to the
    parser."""
    q = subparsers.add_parser("queue", help="Cola de issues abiertos con la label de Atta.",
                              description="Lista los issues abiertos con la label de la cola y "
                                          "qué hacer con cada uno (new, resume, wait, hold, "
                                          "skip), respetando concurrency. Un job en needs-info "
                                          "sale resume solo cuando el autor comentó después "
                                          "de la pregunta de la Exploradora. Un job con "
                                          "BLOCKED-BY sale wait hasta que su bloqueante se "
                                          "mergee o se cierre, y hold si el bloqueante fue "
                                          "rechazado o hay un ciclo.")
    q.add_argument("--json", action="store_true", help="salida JSON (una lista de filas)")
    q.set_defaults(func=_guarded(cmd_queue))

    s = subparsers.add_parser("start", help="Crea el job de un issue de GitHub.",
                              description="Lee el issue con gh, crea el job e issue.md con los "
                                          "comentarios del autor y del operador, citados. "
                                          "Exige la label de la cola, rechaza las labels de "
                                          "espera y respeta concurrency.")
    s.add_argument("issue", type=int, help="número del issue")
    s.set_defaults(func=_guarded(cmd_start))

    r = subparsers.add_parser("refresh", help="Vuelve a leer el issue (needs-info → intake).",
                              description="Vuelve a leer el issue con gh y reescribe issue.md "
                                          "(comentarios del autor y del operador, citados). Si "
                                          "el job está en needs-info, quita la label de espera "
                                          "y lo devuelve a intake. Rechaza (sale 2) si el "
                                          "issue está cerrado o ya no tiene la label de la "
                                          "cola.")
    r.add_argument("job", help="id del job")
    r.set_defaults(func=_guarded(cmd_refresh))

    w = subparsers.add_parser("worktree", help="Crea el worktree atta/<id> desde origin/develop.",
                              description="git fetch y git worktree add -b atta/<id> "
                                          "<raíz>/.worktrees/<id> origin/<integración>.")
    w.add_argument("job", help="id del job")
    w.set_defaults(func=_guarded(cmd_worktree))

    p = subparsers.add_parser("publish", help="Empuja atta/<id> y abre el PR en borrador.",
                              description="Empuja solo la rama atta/<id> (sin --force) y abre un "
                                          "PR en borrador contra la rama de integración si no "
                                          "existe; guarda pr y pr_url. El cuerpo del PR sale "
                                          "de la plantilla pr-body.md con el build.md de la "
                                          "ronda (resumen, cambios contrastados con git diff "
                                          "--stat, pruebas, criterios) y Closes #<issue>.")
    p.add_argument("job", help="id del job")
    p.set_defaults(func=_guarded(cmd_publish))

    c = subparsers.add_parser("comment", help="Comenta en el issue o PR, firmado por un rol.",
                              description="Publica el archivo (o un texto de la espina con "
                                          "--voice) como comentario con el marcador "
                                          f"{github.MARKER} y la firma del rol.")
    c.add_argument("job", help="id del job")
    c.add_argument("--role", required=True, help="rol que firma: " + ", ".join(github.ROLES))
    text = c.add_mutually_exclusive_group(required=True)
    text.add_argument("--file", help="archivo UTF-8 con el texto (no vacío)")
    text.add_argument("--voice", choices=tuple(VOICES),
                      help="texto que arma la espina: intake-accepted (aceptación de la "
                           "Exploradora con el riesgo y el ## Summary de intake.md)")
    c.add_argument("--on", choices=("issue", "pr"),
                   help="dónde comentar (por defecto: el PR si existe, si no el issue)")
    c.set_defaults(func=_guarded(cmd_comment))

    lb = subparsers.add_parser("label", help="Agrega o quita una label en el issue del job.",
                               description="Agrega o quita una label en el issue del job.")
    lb.add_argument("job", help="id del job")
    lb.add_argument("action", choices=("add", "remove"), help="add o remove")
    lb.add_argument("name", help="nombre de la label (p. ej. atta:needs-human)")
    lb.set_defaults(func=_guarded(cmd_label))

    h = subparsers.add_parser("handoff", help="Deja el PR listo para que una persona lo mergee.",
                              description="Entrega por riesgo (delivery.auto_merge_up_to): "
                                          "exige etapa approve y un decision.md = APPROVE "
                                          "escrito en esa etapa. Verifica el PR (base de "
                                          "integración, rama atta/<id>), sincroniza con "
                                          "origin/<integración>, corre el Filtro merge-checks y "
                                          "empuja, como merge; luego saca el PR de borrador y "
                                          "el job pasa a awaiting-merge. Un fallo en esos pasos "
                                          "deja el job en needs-human. Después la Reina comenta "
                                          "en el PR y pone la label needs_review "
                                          "(atta:needs-review) en el issue (la crea si falta): "
                                          "si fallan, solo avisan (handoff.md, ## Avisos). En "
                                          "needs-human retoma un handoff escalado si la última "
                                          "decisión archivada es APPROVE vigente, el PR sigue "
                                          "abierto contra la rama de integración y HEAD es el "
                                          "commit que pasó el Filtro; si no, se rechaza sin "
                                          "cambios. Nunca mergea. Si al sincronizar cambió el "
                                          "diff del job, vuelve a review; si hay conflicto, "
                                          "vuelve a build con un feedback automático (acotado "
                                          "por sync_attempts; sale 0). Sale 0 si queda "
                                          "esperando, 1 si no pasó, 2 si se rechaza.")
    h.add_argument("job", help="id del job")
    h.set_defaults(func=_guarded(cmd_handoff))

    m = subparsers.add_parser("merge", help="Sincroniza, pasa el Filtro y mergea a develop.",
                              description="Exige etapa approve y un decision.md = APPROVE "
                                          "escrito en esa etapa. Mergea "
                                          "origin/<integración> en el worktree, corre el Filtro "
                                          "merge-checks, empuja, verifica la base del PR y usa "
                                          "gh pr merge --match-head-commit. Cualquier fallo deja "
                                          "el job en needs-human con el motivo. Tras el merge "
                                          "cierra el issue, quita la label de la cola y borra "
                                          "la rama atta/<id> (remota y local) y el worktree si "
                                          "está limpio; esos pasos solo avisan. Se puede "
                                          "repetir: en merging consulta el PR en GitHub y "
                                          "termina (MERGED con el sha del Filtro) o retoma "
                                          "(abierto); cerrado sin merge o con otro sha va a "
                                          "needs-human; en merged no hace nada. En "
                                          "awaiting-merge consulta el PR: abierto, sigue "
                                          "esperando (sale 0 sin cambios); mergeado en la rama "
                                          "de integración, cierra el ciclo como un merge propio "
                                          "(también quita atta:needs-review); cerrado sin merge o "
                                          "con otra base, needs-human; si GitHub lo marca "
                                          "CONFLICTING, vuelve a build con un feedback "
                                          "automático (UNKNOWN sigue esperando). Si al "
                                          "sincronizar cambió el diff del job, vuelve a review; "
                                          "si hay conflicto, a build con feedback automático "
                                          "(ambos acotados por sync_attempts; salen 0). Sale 0 "
                                          "si mergea, 1 si no pasó, 2 si se rechaza.")
    m.add_argument("job", help="id del job")
    m.set_defaults(func=_guarded(cmd_merge))
