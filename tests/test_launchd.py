import json
import os
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class LaunchdTests(unittest.TestCase):
    def render(self, tmp_root, config_raw=None, env_extra=None):
        """Invoke the real render script as a subprocess, isolated from any
        config.json/TG_* this machine happens to have — a clean CI checkout
        must render the same jobs as a developer's already-configured one."""
        out = tmp_root / "agents"
        logs = tmp_root / "logs"
        program = tmp_root / "bin" / "codex-reset-watch"
        cfg_path = tmp_root / "config.json"
        # "timezone": "local" makes daily_time pass straight through into
        # Hour/Minute with no UTC+8-vs-host-timezone conversion, so a fixed
        # expected Hour/Minute below holds on a UTC CI runner exactly as it
        # does on a UTC+8 dev machine (the conversion itself is covered in
        # tests/test_config.py). Any config_raw passed in can still override it.
        cfg_path.write_text(json.dumps({"timezone": "local", **(config_raw or {})}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("TG_BOT_TOKEN", "TG_CHAT_ID")}
        env["CRW_CONFIG"] = str(cfg_path)
        env.update(env_extra or {})
        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts/render_launchd.py"),
             "--program", str(program), "--out-dir", str(out), "--log-dir", str(logs)],
            env=env,
        )
        return out, program

    def test_default_config_renders_both_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            out, program = self.render(pathlib.Path(d))
            daily = plistlib.loads((out / "com.wes.codex-reset-watch.daily.plist").read_bytes())
            monitor = plistlib.loads((out / "com.wes.codex-reset-watch.monitor.plist").read_bytes())
            self.assertEqual(daily["ProgramArguments"], [str(program), "daily"])
            self.assertEqual(daily["StartCalendarInterval"], {"Hour": 10, "Minute": 0})
            self.assertEqual(monitor["ProgramArguments"], [str(program), "monitor"])
            self.assertEqual(monitor["StartInterval"], 120 * 60)  # default: every 2 hours
            self.assertTrue(daily["RunAtLoad"])

    def test_custom_interval_and_daily_time_are_honoured(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.render(pathlib.Path(d), config_raw={
                "daily_time": "07:15", "scan_interval_minutes": 20,
            })
            daily = plistlib.loads((out / "com.wes.codex-reset-watch.daily.plist").read_bytes())
            monitor = plistlib.loads((out / "com.wes.codex-reset-watch.monitor.plist").read_bytes())
            self.assertEqual(daily["StartCalendarInterval"], {"Hour": 7, "Minute": 15})
            self.assertEqual(monitor["StartInterval"], 20 * 60)

    def test_disabling_daily_removes_its_plist(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.render(pathlib.Path(d), config_raw={"daily_enabled": False})
            self.assertFalse((out / "com.wes.codex-reset-watch.daily.plist").exists())
            self.assertTrue((out / "com.wes.codex-reset-watch.monitor.plist").exists())

    def test_environment_only_credentials_cannot_be_scheduled(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(subprocess.CalledProcessError):
                self.render(pathlib.Path(d), env_extra={"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})

    def test_rerender_without_env_keeps_schedule_settings(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            out, _ = self.render(root)
            out, _ = self.render(root, config_raw={"daily_time": "11:00"})
            daily = plistlib.loads((out / "com.wes.codex-reset-watch.daily.plist").read_bytes())
            self.assertNotIn("EnvironmentVariables", daily)
            self.assertEqual(daily["StartCalendarInterval"], {"Hour": 11, "Minute": 0})


if __name__ == "__main__":
    unittest.main()
