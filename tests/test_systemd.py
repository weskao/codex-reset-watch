import configparser, pathlib, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parents[1]

class SystemdTests(unittest.TestCase):
    def render(self, out, logs, program):
        subprocess.check_call([sys.executable, str(ROOT / "scripts/render_systemd.py"),
                               "--program", str(program), "--out-dir", str(out), "--log-dir", str(logs)])

    def test_render(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d); out = root / "units"; logs = root / "logs"
            program = root / "bin" / "codex-reset-watch"
            self.render(out, logs, program)

            daily_service = configparser.ConfigParser(strict=False)
            daily_service.read(out / "codex-reset-watch-daily.service")
            self.assertEqual(daily_service["Service"]["ExecStart"], f"{program} daily")

            daily_timer = configparser.ConfigParser(strict=False)
            daily_timer.read(out / "codex-reset-watch-daily.timer")
            self.assertEqual(daily_timer["Timer"]["OnCalendar"], "*-*-* 10:00:00")

            monitor_service = configparser.ConfigParser(strict=False)
            monitor_service.read(out / "codex-reset-watch-monitor.service")
            self.assertEqual(monitor_service["Service"]["ExecStart"], f"{program} monitor")

            monitor_timer = configparser.ConfigParser(strict=False)
            monitor_timer.read(out / "codex-reset-watch-monitor.timer")
            self.assertEqual(monitor_timer["Timer"]["OnCalendar"], "*-*-* 0/2:05:00")

    def test_service_logs_to_log_dir(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d); out = root / "units"; logs = root / "logs"
            program = root / "bin" / "codex-reset-watch"
            self.render(out, logs, program)
            daily_service = configparser.ConfigParser(strict=False)
            daily_service.read(out / "codex-reset-watch-daily.service")
            self.assertEqual(daily_service["Service"]["StandardOutput"], f"append:{logs / 'systemd-daily.out.log'}")
            self.assertEqual(daily_service["Service"]["StandardError"], f"append:{logs / 'systemd-daily.err.log'}")

if __name__ == "__main__":
    unittest.main()
