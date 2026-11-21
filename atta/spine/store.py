"""Job store under `atta/jobs/<id>/`. The only writer of job.json (atomic: temp file + os.replace)."""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .ids import job_id as make_job_id, sanitize_id
from .lock import job_lock
from .timeutil import to_iso, utcnow

JOBS_DIR = Path(__file__).resolve().parents[1] / "jobs"
JOB_FILE = "job.json"


class StoreError(RuntimeError):
    """Job missing, duplicated or unreadable."""


def new_job(issue: int, title: str, issue_url: str | None, cfg: dict, now: datetime) -> dict:
    jid = make_job_id(issue, title)
    stamp = to_iso(now)
    return {
        "id": jid,
        "issue": issue,
        "title": title,
        "issue_url": issue_url,
        "pr": None,
        "pr_url": None,
        "branch": f"{cfg['branch_prefix']}{jid}",
        "worktree": None,
        "stage": "intake",
        "stage_since": stamp,
        "review_rounds": 0,
        "gate_rounds": 0,
        "sync_attempts": 0,
        "total_rounds": 0,
        "max_total_rounds": cfg["max_total_rounds"],
        "intake": {"risk": None, "surfaces": [], "decision": None},
        "agents": {},
        "started": stamp,
        "ended": None,
        "cost_usd": None,
    }


class JobStore:
    def __init__(self, root: str | Path = JOBS_DIR):
        self.root = Path(root)

    def job_dir(self, jid: str) -> Path:
        return self.root / sanitize_id(jid)

    def exists(self, jid: str) -> bool:
        return (self.job_dir(jid) / JOB_FILE).exists()

    def list_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.parent.name for p in self.root.glob(f"*/{JOB_FILE}"))

    def load(self, jid: str) -> dict:
        path = self.job_dir(jid) / JOB_FILE
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise StoreError(f"No existe el job {jid}.") from None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(f"job.json de {jid} está dañado: {exc}") from exc

    def create(self, issue: int, title: str, issue_url: str | None, cfg: dict,
               now: datetime | None = None) -> dict:
        job = new_job(issue, title, issue_url, cfg, now or utcnow())
        directory = self.job_dir(job["id"])
        directory.mkdir(parents=True, exist_ok=True)
        with job_lock(directory):
            if (directory / JOB_FILE).exists():
                raise StoreError(f"El job {job['id']} ya existe.")
            self.save(job)
        return job

    def save(self, job: dict) -> None:
        """Atomically replace job.json. Callers mutating state must hold the job lock."""
        directory = self.job_dir(job["id"])
        data = json.dumps(job, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".job.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            _replace(tmp, directory / JOB_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _replace(src: str, dst: Path, attempts: int = 5) -> None:
    # On Windows a concurrent reader can briefly hold dst open; retry only that case.
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
