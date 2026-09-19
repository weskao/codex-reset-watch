import pathlib, tempfile, unittest
from scripts import install

class TrimLaunchdLogsTests(unittest.TestCase):
    def test_leaves_small_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            log_dir = pathlib.Path(d)
            f = log_dir / "launchd-daily.out.log"
            f.write_bytes(b"x" * 100)
            install.trim_launchd_logs(log_dir, max_bytes=1000, keep_bytes=500)
            self.assertEqual(f.stat().st_size, 100)

    def test_trims_oversized_file_keeping_the_tail(self):
        with tempfile.TemporaryDirectory() as d:
            log_dir = pathlib.Path(d)
            f = log_dir / "launchd-monitor.err.log"
            f.write_bytes(b"a" * 1000 + b"b" * 100)
            install.trim_launchd_logs(log_dir, max_bytes=1000, keep_bytes=100)
            self.assertEqual(f.read_bytes(), b"b" * 100)

class BackendForTests(unittest.TestCase):
    def test_darwin_uses_launchd(self):
        self.assertEqual(install.backend_for("Darwin"), "launchd")

    def test_linux_uses_systemd(self):
        self.assertEqual(install.backend_for("Linux"), "systemd")

    def test_windows_uses_schtasks(self):
        self.assertEqual(install.backend_for("Windows"), "schtasks")

    def test_unknown_platform_raises(self):
        with self.assertRaises(ValueError):
            install.backend_for("Plan9")

if __name__ == "__main__":
    unittest.main()
