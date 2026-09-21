import os, pathlib, tempfile, unittest
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

class ShadowDetectionTests(unittest.TestCase):
    def test_rc_alias_is_reported_with_line_number(self):
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d)
            (home / ".zshrc").write_text("# comment\nalias crw='$HOME/scripts/crw'\n")
            hits = install.rc_shadows(home)
            self.assertEqual(len(hits), 1)
            self.assertIn(".zshrc:2:", hits[0])

    def test_rc_without_a_conflicting_definition_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            home = pathlib.Path(d)
            (home / ".bashrc").write_text("alias crwx='nope'\necho crw\n")
            self.assertEqual(install.rc_shadows(home), [])

    def test_earlier_path_entry_shadows_the_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            stale, bin_dir = root / "stale", root / "bin"
            for p in (stale, bin_dir):
                p.mkdir()
                (p / "crw").write_text("#!/bin/sh\n")
            env = {"PATH": os.pathsep.join([str(stale), str(bin_dir)]), "PATHEXT": ""}
            self.assertEqual(install.path_shadows(bin_dir, env), [stale / "crw"])

    def test_install_bin_dir_first_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            stale, bin_dir = root / "stale", root / "bin"
            for p in (stale, bin_dir):
                p.mkdir()
                (p / "crw").write_text("#!/bin/sh\n")
            env = {"PATH": os.pathsep.join([str(bin_dir), str(stale)]), "PATHEXT": ""}
            self.assertEqual(install.path_shadows(bin_dir, env), [])


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
