import threading
import unittest

import support  # noqa: F401
from support import TempDirMixin

from spine import lock


class LockTests(TempDirMixin, unittest.TestCase):
    def test_usable_twice_sequentially(self):
        job_dir = self.tmp / "1-a"
        job_dir.mkdir()
        with lock.job_lock(job_dir):
            pass
        with lock.job_lock(job_dir):
            pass

    def test_lock_is_per_job_not_global(self):
        a = self.tmp / "1-a"
        b = self.tmp / "2-b"
        a.mkdir()
        b.mkdir()
        with lock.job_lock(a):
            with lock.job_lock(b, timeout=0.5):
                pass

    def test_same_job_is_exclusive(self):
        job_dir = self.tmp / "1-a"
        job_dir.mkdir()
        errors = []

        def contender():
            try:
                with lock.job_lock(job_dir, timeout=0.3):
                    errors.append("acquired")
            except lock.LockTimeout as exc:
                errors.append(str(exc))

        with lock.job_lock(job_dir):
            t = threading.Thread(target=contender)
            t.start()
            t.join(5)
        self.assertEqual(errors, [f"No se pudo bloquear el job en {job_dir} (ocupado por otro proceso)."])

    def test_released_after_exception(self):
        job_dir = self.tmp / "1-a"
        job_dir.mkdir()
        with self.assertRaises(RuntimeError):
            with lock.job_lock(job_dir):
                raise RuntimeError("boom")
        with lock.job_lock(job_dir, timeout=0.3):
            pass


if __name__ == "__main__":
    unittest.main()
