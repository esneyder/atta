"""Thin adapter over the GitHub CLI `gh` (arquitectura §12, F2 S3).

`gh` is `$ATTA_GH` when set (a path; a `.py` file runs with this Python, which is how the tests
inject a fake) or `gh` from the PATH. Every call is an argument list (never a shell), bodies go
through stdin (`--body-file -`), JSON output is parsed here and failures raise GhError in Spanish.

Comments written by Atta carry MARKER and a role signature; when reading an issue only comments
from its author and from the operator (`gh api user`) are kept, and the operator's marked ones count as «Atta»
(the issue author's marked comments stay theirs).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ENV_VAR = "ATTA_GH"
MARKER = "<!-- atta -->"
ATTA_AUTHOR = "Atta"
GH_TIMEOUT_S = 120
MERGE_METHODS = ("merge", "squash", "rebase")
ISSUE_FIELDS = "number,title,body,url,state,author,labels,comments"
QUEUE_FIELDS = "number,title,url,labels"
PR_FIELDS = "number,url,state,baseRefName,headRefName,isDraft,isCrossRepository"
# What a resumed merge needs to know: did GitHub merge it, and at which head (L22).
# `mergeable` (MERGEABLE, CONFLICTING or UNKNOWN while GitHub computes it) lets a PR that waits
# for a person notice a conflict with the integration branch (F3.x, S2).
MERGE_STATE_FIELDS = ("number,state,baseRefName,headRefName,headRefOid,isCrossRepository,"
                      "mergeable")

# Role key -> signature, in brand voice (arquitectura §3).
ROLES = {
    "exploradora": "La Exploradora",
    "guia": "La Guía",
    "vigia": "La Vigía",
    "cortadora": "La Cortadora",
    "filtro": "El Filtro",
    "jardineras": "Las Jardineras",
    "soldado": "El Soldado",
    "reina": "La Reina",
    "cultivadora": "La Cultivadora",
}


class GhError(RuntimeError):
    """`gh` is missing, failed or returned something unusable."""


def command() -> list[str]:
    """argv prefix that runs gh: $ATTA_GH (a .py runs with this interpreter) or `gh`."""
    exe = (os.environ.get(ENV_VAR) or "").strip() or "gh"
    if exe.lower().endswith(".py"):
        return [sys.executable, exe]
    return [exe]


def run(args: list[str], *, input: str | None = None) -> str:
    """Run `gh <args>` and return stdout; GhError on any failure."""
    argv = [*command(), *[str(a) for a in args]]
    label = " ".join(str(a) for a in args[:2])
    try:
        proc = subprocess.run(argv, input=input, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              stdin=None if input is not None else subprocess.DEVNULL,
                              timeout=GH_TIMEOUT_S)
    except FileNotFoundError:
        raise GhError(f"No se encontró gh ({argv[0]}). Instálalo desde https://cli.github.com "
                      "y corre `gh auth login`.") from None
    except subprocess.TimeoutExpired:
        raise GhError(f"gh {label} no respondió en {GH_TIMEOUT_S} s.") from None
    except OSError as exc:
        raise GhError(f"No se pudo ejecutar gh: {exc}.") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise GhError(f"gh {label} falló: {detail}")
    return proc.stdout


def run_json(args: list[str], **kw):
    out = run(args, **kw)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        label = " ".join(str(a) for a in args[:2])
        raise GhError(f"gh {label} no devolvió JSON válido.") from None


# ---------------------------------------------------------------- reads

def operator() -> str:
    login = run(["api", "user", "--jq", ".login"]).strip()
    if not login:
        raise GhError("gh api user no devolvió el usuario; corre `gh auth login`.")
    return login


def list_issues(label: str, limit: int = 100) -> list[dict]:
    rows = run_json(["issue", "list", "--label", label, "--state", "open", "--json",
                     QUEUE_FIELDS, "--limit", str(limit)])
    if not isinstance(rows, list):
        raise GhError("gh issue list no devolvió una lista.")
    return rows


def view_issue(number: int) -> dict:
    data = run_json(["issue", "view", str(int(number)), "--json", ISSUE_FIELDS])
    if not isinstance(data, dict):
        raise GhError("gh issue view no devolvió un objeto.")
    return data


def find_pr(head: str) -> dict | None:
    """The open PR whose head is `head`, or None."""
    rows = run_json(["pr", "list", "--head", head, "--state", "open", "--json", PR_FIELDS])
    rows = [r for r in rows if isinstance(r, dict) and r.get("headRefName") == head]
    return rows[0] if rows else None


def view_pr(ref) -> dict:
    data = run_json(["pr", "view", str(ref), "--json", PR_FIELDS])
    if not isinstance(data, dict):
        raise GhError("gh pr view no devolvió un objeto.")
    return data


def merge_state(ref) -> dict:
    """The PR's state and head sha as GitHub sees them now (`headRefOid` stays the merged head
    once the PR is MERGED)."""
    data = run_json(["pr", "view", str(ref), "--json", MERGE_STATE_FIELDS])
    if not isinstance(data, dict):
        raise GhError("gh pr view no devolvió un objeto.")
    return data


def login_of(author) -> str | None:
    if isinstance(author, dict):
        return author.get("login") or None
    return author if isinstance(author, str) and author else None


def labels_of(item: dict) -> list[str]:
    return [lb.get("name") if isinstance(lb, dict) else lb for lb in item.get("labels") or []]


def filter_comments(comments, issue_author: str | None, operator_login: str | None) -> list[dict]:
    """Comments by the issue author or the operator as {author, body}.

    Only the operator's comments carrying MARKER are «Atta»; anyone else can type the marker.
    """
    allowed = {a for a in (issue_author, operator_login) if a}
    kept = []
    for c in comments or ():
        login = login_of(c.get("author")) if isinstance(c, dict) else None
        if login not in allowed:
            continue
        body = c.get("body") or ""
        is_atta = bool(operator_login) and login == operator_login and MARKER in body
        kept.append({"author": ATTA_AUTHOR if is_atta else login, "body": body})
    return kept


# ---------------------------------------------------------------- writes

def sign(role: str, text: str) -> str:
    """Body of an Atta comment: marker, text and the role signature."""
    try:
        name = ROLES[role]
    except KeyError:
        raise GhError(f"Rol desconocido: {role}. Usa: {', '.join(ROLES)}.") from None
    return f"{MARKER}\n{text.strip()}\n\n— {name} · Atta\n"


def comment(kind: str, number: int, body: str) -> None:
    if kind not in ("issue", "pr"):
        raise GhError(f"Destino de comentario desconocido: {kind}.")
    run([kind, "comment", str(int(number)), "--body-file", "-"], input=body)


def close_issue(number: int) -> None:
    """Close the issue as completed. GitHub only closes issues through «Closes #n» when the PR
    lands on the default branch; Atta integrates into develop, so it closes them. The closing
    comment goes first through `comment` (stdin), never as a multi-line `--comment` argument."""
    run(["issue", "close", str(int(number)), "--reason", "completed"])


def _label_missing(exc: GhError, name: str) -> bool:
    """gh reports an unknown label as `... 'name' not found` (L31)."""
    text = str(exc)
    return "not found" in text.lower() and name in text


def create_label(name: str, color: str, description: str) -> None:
    run(["label", "create", name, "--color", color, "--description", description, "--force"])


def edit_label(number: int, action: str, name: str,
               create: tuple[str, str] | None = None) -> None:
    """Add or remove a label on an issue. With `create` = (color, description), an add that
    fails because the repository lacks the label creates it (`gh label create --force`) and
    retries once (L31: a project prepared before the label existed); if the creation fails,
    GhError says so."""
    flags = {"add": "--add-label", "remove": "--remove-label"}
    if action not in flags:
        raise GhError(f"Acción de label desconocida: {action} (usa add o remove).")
    argv = ["issue", "edit", str(int(number)), flags[action], name]
    try:
        run(argv)
    except GhError as exc:
        if action != "add" or create is None or not _label_missing(exc, name):
            raise
        try:
            create_label(name, *create)
        except GhError as failed:
            raise GhError(f"La label {name} no existe en el repositorio y no se pudo crear: "
                          f"{failed}") from None
        run(argv)


def create_pr(head: str, base: str, title: str, body: str) -> str:
    """Open a draft PR and return its URL."""
    out = run(["pr", "create", "--draft", "--base", base, "--head", head, "--title", title,
               "--body-file", "-"], input=body)
    urls = re.findall(r"https://\S+/pull/\d+", out)
    if not urls:
        raise GhError("gh pr create no devolvió la URL del PR.")
    return urls[-1]


def create_issue(title: str, body: str, label: str,
                 create: tuple[str, str] | None = None) -> int:
    """Open an issue with one label and return its number (K1: the ratchet issue). With
    `create` = (color, description), a repository without the label gets it and the issue is
    retried once, like `edit_label` (L31)."""
    argv = ["issue", "create", "--title", title, "--label", label, "--body-file", "-"]
    try:
        out = run(argv, input=body)
    except GhError as exc:
        if create is None or not _label_missing(exc, label):
            raise
        try:
            create_label(label, *create)
        except GhError as failed:
            raise GhError(f"La label {label} no existe en el repositorio y no se pudo crear: "
                          f"{failed}") from None
        out = run(argv, input=body)
    numbers = re.findall(r"https://\S+/issues/(\d+)", out)
    if not numbers:
        raise GhError("gh issue create no devolvió la URL del issue.")
    return int(numbers[-1])


def find_issue(label: str, title: str) -> int | None:
    """Number of an issue with `label` and exactly `title`, open or closed; None if there is none
    (K1: an issue opened before its number reached lessons.md is reused, never duplicated)."""
    rows = run_json(["issue", "list", "--label", label, "--state", "all", "--json",
                     "number,title", "--limit", "500"])
    if not isinstance(rows, list):
        raise GhError("gh issue list no devolvió una lista.")
    for row in rows:
        if isinstance(row, dict) and row.get("title") == title:
            return int(row["number"])
    return None


def ready_pr(number: int) -> None:
    run(["pr", "ready", str(int(number))])


def merge_pr(number: int, head_sha: str, method: str) -> None:
    """Merge only if the PR head is still `head_sha` (no admin, no auto, no branch deletion)."""
    if method not in MERGE_METHODS:
        raise GhError(f"merge_method «{method}» no es válido (usa: {', '.join(MERGE_METHODS)}).")
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head_sha):
        raise GhError("merge necesita el sha exacto que pasó el Filtro.")
    run(["pr", "merge", str(int(number)), f"--{method}", "--match-head-commit", head_sha])
