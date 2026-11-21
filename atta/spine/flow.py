"""GitHub flow of a job (F2 T3): queue, start, refresh, worktree, publish, comment, label and safe
merge.

Commands are exposed through `register(subparsers)`; cli.py wires them. Each handler takes
`(args, cfg, jobs)` like the F1 commands and returns the exit code: 0 ok, 1 negative verdict
(merge did not go through: conflict, Filtro, gh), 2 refused or usage error. Errors are printed
in Spanish to stderr by the handler itself.

Branch policy (arquitectura §12, D3): every push goes through branches.check_push_target with an
explicit `refs/heads/<b>:refs/heads/<b>` refspec and never `--force`; every PR create/merge goes
through branches.check_pr_base with the PR's real `baseRefName`; `gh pr merge` uses
`--match-head-commit` with the sha that passed `merge-checks` after syncing with develop.

Closing the loop on GitHub (T11): `publish` announces a new PR on the issue (Cortadora), and a
successful `merge` closes the issue with the Reina's comment (GitHub only honours «Closes #n» on
the default branch), removes the queue label, deletes the remote `atta/<id>` through
`delete_remote_branch` (the one deletion Atta runs; agents never can, the guard denies it) and
removes the local worktree and branch when clean. Those post-merge steps only warn: the merge
already happened, so they exit 0 and are listed under `## Avisos` in merge.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import branches, gate, github, intake, issue, machine, voice
from .board import summary as artifact_summary
from .github import GhError
from .ids import InvalidId
from .lock import LockTimeout, job_lock
from .next import _fresh
from .store import JobStore, StoreError, _replace

OK, NEGATIVE, REFUSED = 0, 1, 2
GIT_TIMEOUT_S = 600
WORKTREES_DIR = ".worktrees"
DECISION_FILE = "decision.md"
MERGE_FILE = "merge.md"
DEFAULT_MERGE_METHOD = "merge"  # local fallback for config key `merge_method`
WAITING_HUMAN = frozenset({"needs-info", "needs-human"})
RUNNING = frozenset(machine.STAGES) - machine.TERMINAL - WAITING_HUMAN


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


def _push(worktree: Path, branch: str, cfg: dict) -> None:
    """Push `branch` to origin under the branch policy: explicit refspec, never forced."""
    branches.check_push_target(branch, cfg)
    git(worktree, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


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
    mine = [j for j in job_list if j.get("issue") == number]
    if not mine:
        return None
    mine.sort(key=lambda j: (j["stage"] not in machine.TERMINAL, j.get("started") or ""))
    return mine[-1]


# ---------------------------------------------------------------- queue

def _running(job_list: list[dict]) -> int:
    """Jobs holding a `concurrency` slot: not terminal and not waiting for a person."""
    return sum(1 for j in job_list if j["stage"] in RUNNING)


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
               answered=None) -> list[dict]:
    """Rows {action, issue, title, job, stage, reason}; action is new|resume|wait|hold|skip.

    Jobs in running stages (not terminal, not waiting for a human) hold the `concurrency` slots,
    even when their issue left the queue; each `new` takes one. A needs-info job is `resume`
    (and takes a slot) only when `answered(number)` says the author answered the Exploradora;
    otherwise it is `wait`. needs-human jobs are `hold`.
    """
    cap = cfg["concurrency"]
    running = _running(job_list)
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
            elif stage in WAITING_HUMAN:
                row.update(action="hold", reason=f"espera a una persona ({stage}).")
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

    rows = plan_queue(github.list_issues(label), _all_jobs(jobs), cfg, answered)
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
    running, cap = _running(job_list), cfg["concurrency"]
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
    _push(wt, branch, cfg)
    if pr is None:
        branches.check_pr_base(integration, cfg)
        body = github.sign("cortadora", f"La Cortadora abre este PR para el issue "
                                        f"#{job['issue']}. Se integra solo en {integration}, "
                                        f"cuando la Reina lo apruebe.\n\nCloses #{job['issue']}")
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


def cmd_label(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
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


def _check_pr(pr: dict, branch: str, cfg: dict) -> None:
    if pr.get("isCrossRepository"):
        raise _MergeFailure(_fork_message(pr.get("number")), REFUSED)
    try:
        branches.check_pr_base(pr.get("baseRefName") or "", cfg)
    except branches.BranchRefused as exc:
        raise _MergeFailure(str(exc), REFUSED) from None
    if pr.get("headRefName") != branch:
        raise _MergeFailure(f"El PR #{pr.get('number')} sale de «{pr.get('headRefName')}», "
                            f"no de {branch}.", REFUSED)
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
        github.edit_label(job["issue"], "add", cfg["labels"]["needs_human"])
    except GhError as exc:
        _err(f"Aviso: no se pudo poner la label en el issue #{job['issue']}: {exc}")
    _err(reason)
    return code


def _sync_gate_push(args, cfg, jobs: JobStore, job: dict, wt: Path) -> str:
    """Sync with origin/<integration>, run merge-checks, push; return the sha that passed."""
    integration = cfg["integration_branch"]
    branch, number = job["branch"], job["pr"]
    current = git(wt, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout.strip()
    if current != branch:
        raise _MergeFailure(f"El worktree de {job['id']} está en «{current or 'HEAD suelto'}», "
                            f"no en {branch}.")
    _check_pr(github.view_pr(number), branch, cfg)
    git(wt, "fetch", "origin")
    merged = git(wt, "merge", "--no-edit", f"origin/{integration}", check=False)
    if merged.returncode != 0:
        conflicts = git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        git(wt, "merge", "--abort", check=False)
        if conflicts:
            raise _MergeFailure(f"Conflicto al sincronizar con origin/{integration} en "
                                f"{', '.join(conflicts)}.")
        detail = (merged.stderr or merged.stdout).strip()
        raise _MergeFailure(f"No se pudo sincronizar con origin/{integration}: {detail}")
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
        _push(wt, branch, cfg)
    except branches.BranchRefused as exc:
        raise _MergeFailure(str(exc), REFUSED) from None
    return result.commit


def _remove_local(root, wt: Path, branch: str, sha: str) -> list[str]:
    """Remove the job's worktree and local branch when clean; warnings otherwise."""
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


def _after_merge(args, cfg, job: dict, wt: Path, sha: str) -> list[str]:
    """Close the loop on GitHub after a merge (T11); every failure is a warning."""
    warnings: list[str] = []
    number, pr, branch = job["issue"], job["pr"], job["branch"]
    integration, queue = cfg["integration_branch"], cfg["labels"]["queue"]
    try:
        github.close_issue(number, github.sign("reina", voice.merged_issue(pr, integration,
                                                                          number)))
    except GhError as exc:
        warnings.append(f"No se pudo cerrar el issue #{number}: {exc}")
    try:
        github.edit_label(number, "remove", queue)
    except GhError as exc:
        warnings.append(f"No se pudo quitar la label {queue} del issue #{number}: {exc}")
    try:
        delete_remote_branch(args.root, job["id"], branch, cfg)
    except (FlowError, branches.BranchRefused) as exc:
        warnings.append(f"No se pudo borrar la rama remota {branch}: {exc}")
    warnings += _remove_local(args.root, wt, branch, sha)
    return warnings


def cmd_merge(args, cfg, jobs: JobStore) -> int:
    job = jobs.load(args.job)
    jid = job["id"]
    if job["stage"] != "approve":
        raise FlowError(f"El job {jid} está en {job['stage']}; merge exige la etapa approve.")
    if not _decision_approved(jobs.job_dir(jid)):
        raise FlowError(f"La Reina no aprobó {jid}: decision.md debe empezar con APPROVE.")
    if not _fresh(jobs.job_dir(jid) / DECISION_FILE, job):
        raise FlowError(f"La decisión de la Reina es vieja: decision.md de {jid} es anterior a "
                        "la etapa approve; hace falta una nueva.")
    expected = branches.work_branch(jid, cfg)
    if job.get("branch") != expected:
        raise FlowError(f"La rama del job {jid} debe ser {expected}; job.json dice "
                        f"«{job.get('branch') or ''}».")
    if not job.get("pr"):
        raise FlowError(f"El job {jid} no tiene PR; corre `atta publish {jid}`.")
    wt = _worktree(job)
    method = _merge_method(cfg)
    try:
        branches.assert_round_budget(job, "sync_attempts", cfg)
    except branches.RoundBudgetExhausted as exc:
        return _escalate(jobs, job, str(exc), NEGATIVE, cfg)
    job = machine.advance(jobs, jid, "merging")
    print(voice.reina_approved(job["pr"]))
    integration, number = cfg["integration_branch"], job["pr"]
    try:
        sha = _sync_gate_push(args, cfg, jobs, job, wt)
        pr = github.view_pr(number)
        _check_pr(pr, job["branch"], cfg)
        if pr.get("isDraft"):
            github.ready_pr(number)
        github.merge_pr(number, sha, method)
    except _MergeFailure as exc:
        return _escalate(jobs, job, exc.reason, exc.code, cfg)
    except branches.BranchRefused as exc:
        return _escalate(jobs, job, str(exc), REFUSED, cfg)
    except (GhError, FlowError) as exc:
        return _escalate(jobs, job, str(exc), NEGATIVE, cfg)
    line = voice.merged(number, integration)
    _write_merge_md(jobs.job_dir(jid), "DONE", line, sha)
    job = machine.advance(jobs, jid, "merged")
    print(line)
    warnings = _after_merge(args, cfg, job, wt, sha)
    if warnings:
        _write_merge_md(jobs.job_dir(jid), "DONE", line, sha, warnings)
        for warning in warnings:
            _err(f"Aviso: {warning}")
    return OK


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
    """Add queue, start, refresh, worktree, publish, comment, label and merge to the parser."""
    q = subparsers.add_parser("queue", help="Cola de issues abiertos con la label de Atta.",
                              description="Lista los issues abiertos con la label de la cola y "
                                          "qué hacer con cada uno (new, resume, wait, hold, "
                                          "skip), respetando concurrency. Un job en needs-info "
                                          "sale resume solo cuando el autor comentó después "
                                          "de la pregunta de la Exploradora.")
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
                                          "existe; guarda pr y pr_url.")
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

    m = subparsers.add_parser("merge", help="Sincroniza, pasa el Filtro y mergea a develop.",
                              description="Exige etapa approve y un decision.md = APPROVE "
                                          "escrito en esa etapa. Mergea "
                                          "origin/<integración> en el worktree, corre el Filtro "
                                          "merge-checks, empuja, verifica la base del PR y usa "
                                          "gh pr merge --match-head-commit. Cualquier fallo deja "
                                          "el job en needs-human con el motivo. Tras el merge "
                                          "cierra el issue, quita la label de la cola y borra "
                                          "la rama atta/<id> (remota y local) y el worktree si "
                                          "está limpio; esos pasos solo avisan. Sale 0 si "
                                          "mergea, 1 si no pasó, 2 si se rechaza.")
    m.add_argument("job", help="id del job")
    m.set_defaults(func=_guarded(cmd_merge))
