import configparser
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class SystemdTests(unittest.TestCase):
    def render(self, tmp_root, config_raw=None, env_extra=None):
        out = tmp_root / "units"
        logs = tmp_root / "logs"
        program = tmp_root / "bin" / "codex-reset-watch"
        cfg_path = tmp_root / "config.json"
        # "timezone": "local" keeps daily_time -> OnCalendar deterministic across
        # CI runners (see tests/test_launchd.py for the full rationale).
        cfg_path.write_text(json.dumps({"timezone": "local", **(config_raw or {})}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("TG_BOT_TOKEN", "TG_CHAT_ID")}
        env["CRW_CONFIG"] = str(cfg_path)
        env.update(env_extra or {})
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts/render_systemd.py"),
             "--program", str(program), "--out-dir", str(out), "--log-dir", str(logs)],
            env=env,
        )
        return out, program, logs

    def test_render(self):
        with tempfile.TemporaryDirectory() as d:
            out, program, _ = self.render(pathlib.Path(d))

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
            self.assertEqual(monitor_timer["Timer"]["OnUnitActiveSec"], "120min")  # default: every 2 hours
            self.assertEqual(monitor_timer["Timer"]["OnBootSec"], "120min")

    def test_service_logs_to_log_dir(self):
        with tempfile.TemporaryDirectory() as d:
            out, program, logs = self.render(pathlib.Path(d))
            daily_service = configparser.ConfigParser(strict=False)
            daily_service.read(out / "codex-reset-watch-daily.service")
            self.assertEqual(daily_service["Service"]["StandardOutput"], f"append:{logs / 'systemd-daily.out.log'}")
            self.assertEqual(daily_service["Service"]["StandardError"], f"append:{logs / 'systemd-daily.err.log'}")

    def test_custom_interval_and_daily_time(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, _ = self.render(pathlib.Path(d), config_raw={
                "daily_time": "23:45", "scan_interval_minutes": 30,
            })
            daily_timer = configparser.ConfigParser(strict=False)
            daily_timer.read(out / "codex-reset-watch-daily.timer")
            self.assertEqual(daily_timer["Timer"]["OnCalendar"], "*-*-* 23:45:00")
            monitor_timer = configparser.ConfigParser(strict=False)
            monitor_timer.read(out / "codex-reset-watch-monitor.timer")
            self.assertEqual(monitor_timer["Timer"]["OnUnitActiveSec"], "30min")

    def test_disabling_monitor_removes_its_units(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, _ = self.render(pathlib.Path(d), config_raw={"monitor_enabled": False})
            self.assertFalse((out / "codex-reset-watch-monitor.service").exists())
            self.assertFalse((out / "codex-reset-watch-monitor.timer").exists())
            self.assertTrue((out / "codex-reset-watch-daily.timer").exists())

    def test_credentials_are_baked_in_as_environment_lines(self):
        with tempfile.TemporaryDirectory() as d:
            out, _, _ = self.render(pathlib.Path(d), env_extra={"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})
            text = (out / "codex-reset-watch-daily.service").read_text(encoding="utf-8")
            self.assertIn("Environment=TG_BOT_TOKEN=tok", text)
            self.assertIn("Environment=TG_CHAT_ID=42", text)


if __name__ == "__main__":
    unittest.main()
