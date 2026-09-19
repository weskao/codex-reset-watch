import contextlib, os, pathlib, tempfile, unittest
from codex_reset_watch import filelock

class FileLockTests(unittest.TestCase):
    def test_acquire_nonblocking_succeeds_when_free(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "run.lock"
            with filelock.lock(path) as acquired:
                self.assertTrue(acquired)

    def test_second_nonblocking_lock_fails_while_first_held(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "run.lock"
            with filelock.lock(path) as first:
                self.assertTrue(first)
                with filelock.lock(path) as second:
                    self.assertFalse(second)

    def test_lock_released_after_context_exit(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "run.lock"
            with filelock.lock(path) as first:
                self.assertTrue(first)
            with filelock.lock(path) as second:
                self.assertTrue(second)

    def test_creates_parent_lock_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "run.lock"
            with filelock.lock(path):
                self.assertTrue(path.exists())

if __name__ == "__main__":
    unittest.main()
