import json
import unittest
from unittest import mock

import support  # noqa: F401
from support import TempDirMixin, T0, snapshot

from spine import config, ids, store


SCHEMA_KEYS = {
    "id", "issue", "title", "issue_url", "pr", "pr_url", "branch", "worktree", "stage",
    "stage_since", "review_rounds", "gate_rounds", "sync_attempts", "total_rounds",
    "max_total_rounds", "intake", "agents", "started", "ended", "cost_usd",
}


class StoreTests(TempDirMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cfg = config.with_defaults({})
        self.store = store.JobStore(self.tmp / "jobs")

    def create(self, number=12, title="Añadir exportación CSV"):
        return self.store.create(number, title, f"https://github.com/o/r/issues/{number}",
                                 self.cfg, now=T0)

    def test_create_writes_full_schema(self):
        job = self.create()
        path = self.tmp / "jobs" / "12-anadir-exportacion-csv" / "job.json"
        self.assertTrue(path.exists())
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, job)
        self.assertEqual(set(job), SCHEMA_KEYS)
        self.assertEqual(job["id"], "12-anadir-exportacion-csv")
        self.assertEqual(job["branch"], "atta/12-anadir-exportacion-csv")
        self.assertEqual(job["stage"], "intake")
        self.assertEqual(job["started"], "2025-01-01T12:00:00Z")
        self.assertEqual(job["stage_since"], "2025-01-01T12:00:00Z")
        for key in ("review_rounds", "gate_rounds", "sync_attempts", "total_rounds"):
            self.assertEqual(job[key], 0)
        self.assertEqual(job["max_total_rounds"], 6)
        self.assertEqual(job["intake"], {"risk": None, "surfaces": [], "decision": None})
        self.assertIsNone(job["cost_usd"])
        self.assertIsNone(job["pr"])
        self.assertIsNone(job["ended"])

    def test_load_roundtrip(self):
        job = self.create()
        self.assertEqual(self.store.load(job["id"]), job)

    def test_create_twice_is_refused_and_leaves_file_unchanged(self):
        job = self.create()
        path = self.store.job_dir(job["id"]) / "job.json"
        before = snapshot(path)
        with self.assertRaises(store.StoreError) as ctx:
            self.create()
        self.assertEqual(str(ctx.exception), "El job 12-anadir-exportacion-csv ya existe.")
        self.assertEqual(snapshot(path), before)

    def test_load_missing_job(self):
        with self.assertRaises(store.StoreError) as ctx:
            self.store.load("99-nada")
        self.assertEqual(str(ctx.exception), "No existe el job 99-nada.")

    def test_paths_reject_unsafe_ids(self):
        with self.assertRaises(ids.InvalidId):
            self.store.job_dir("../x")
        with self.assertRaises(ids.InvalidId):
            self.store.load("a/b")

    def test_atomic_write_failure_keeps_previous_file(self):
        job = self.create()
        path = self.store.job_dir(job["id"]) / "job.json"
        before = snapshot(path)
        changed = dict(job, stage="spec")
        with mock.patch("spine.store.os.replace", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                self.store.save(changed)
        self.assertEqual(snapshot(path), before)
        leftovers = [p.name for p in path.parent.iterdir() if p.name not in ("job.json", ".lock")]
        self.assertEqual(leftovers, [])

    def test_save_rejects_unsafe_id(self):
        job = self.create()
        with self.assertRaises(ids.InvalidId):
            self.store.save(dict(job, id="../evil"))
        self.assertFalse((self.tmp / "evil").exists())

    def test_list_ids(self):
        self.create(12, "uno")
        self.create(3, "dos")
        self.assertEqual(self.store.list_ids(), ["12-uno", "3-dos"])


if __name__ == "__main__":
    unittest.main()
