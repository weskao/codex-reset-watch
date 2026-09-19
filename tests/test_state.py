import sys, importlib.util, pathlib, tempfile, unittest
import codex_reset_watch as crw
class StateTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            s = crw.StateStore(pathlib.Path(td)); s.save({"x":1}); self.assertEqual(s.load()["x"],1)
    def test_nonblocking_lock(self):
        with tempfile.TemporaryDirectory() as td:
            s = crw.StateStore(pathlib.Path(td))
            with s.lock() as a:
                self.assertTrue(a)
                with s.lock() as b: self.assertFalse(b)
if __name__ == "__main__": unittest.main()
