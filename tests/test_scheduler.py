import contextlib
import os
import pathlib
import plistlib
import tempfile
import unittest
import unittest.mock

from codex_reset_watch import config, scheduler


@contextlib.contextmanager
def no_ambient_telegram_env():
    """merged_env() intentionally falls back to os.environ; blank it out here so
    a developer's/CI's real TG_BOT_TOKEN/TG_CHAT_ID can never leak into a test
    assertion or its failure output."""
    saved = {k: os.environ.pop(k, None) for k in scheduler.SECRET_ENV_KEYS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def cfg(**overrides):
    d = dict(config.DEFAULTS)
    # "local" makes daily_time pass straight through into Hour/Minute (no
    # UTC+8-vs-host-timezone conversion), so these tests assert exact wall-clock
    # values the same way on a UTC CI runner as on a UTC+8 dev machine. The
    # conversion itself is covered separately in tests/test_config.py.
    d["timezone"] = "local"
    d.update(overrides)
    return d


class BackendForTests(unittest.TestCase):
    def test_maps_known_systems(self):
        self.assertEqual(scheduler.backend_for("Darwin"), "launchd")
        self.assertEqual(scheduler.backend_for("Linux"), "systemd")
        self.assertEqual(scheduler.backend_for("Windows"), "schtasks")

    def test_unknown_system_raises(self):
        with self.assertRaises(ValueError):
            scheduler.backend_for("Plan9")


class EnabledJobsTests(unittest.TestCase):
    def test_both_enabled_by_default(self):
        self.assertEqual(scheduler.enabled_jobs(cfg()), ("daily", "monitor"))

    def test_daily_disabled(self):
        self.assertEqual(scheduler.enabled_jobs(cfg(daily_enabled=False)), ("monitor",))

    def test_both_disabled(self):
        self.assertEqual(scheduler.enabled_jobs(cfg(daily_enabled=False, monitor_enabled=False)), ())


class LaunchdRenderTests(unittest.TestCase):
    def test_default_daily_calendar_and_monitor_interval(self):
        plists = scheduler.launchd_plists("/bin/crw", pathlib.Path("/tmp/logs"), cfg())
        daily = plists["com.wes.codex-reset-watch.daily.plist"]
        monitor = plists["com.wes.codex-reset-watch.monitor.plist"]
        self.assertEqual(daily["StartCalendarInterval"], {"Hour": 10, "Minute": 0})
        self.assertEqual(daily["ProgramArguments"], ["/bin/crw", "daily"])
        self.assertNotIn("StartInterval", daily)
        self.assertEqual(monitor["StartInterval"], 120 * 60)
        self.assertNotIn("StartCalendarInterval", monitor)
        self.assertEqual(monitor["ProgramArguments"], ["/bin/crw", "monitor"])

    def test_custom_interval_and_daily_time(self):
        plists = scheduler.launchd_plists(
            "/bin/crw", pathlib.Path("/tmp"), cfg(scan_interval_minutes=15, daily_time="07:30"))
        self.assertEqual(plists["com.wes.codex-reset-watch.monitor.plist"]["StartInterval"], 900)

    def test_disabled_job_is_not_rendered(self):
        plists = scheduler.launchd_plists("/bin/crw", pathlib.Path("/tmp"), cfg(daily_enabled=False))
        self.assertNotIn("com.wes.codex-reset-watch.daily.plist", plists)
        self.assertIn("com.wes.codex-reset-watch.monitor.plist", plists)

    def test_env_is_baked_into_every_job(self):
        plists = scheduler.launchd_plists(
            "/bin/crw", pathlib.Path("/tmp"), cfg(), env={"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})
        for plist in plists.values():
            self.assertEqual(plist["EnvironmentVariables"], {"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})

    def test_write_launchd_removes_stale_disabled_plist(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            scheduler.write_launchd(out, scheduler.launchd_plists("/bin/crw", pathlib.Path("/tmp"), cfg()))
            self.assertTrue((out / "com.wes.codex-reset-watch.daily.plist").exists())
            scheduler.write_launchd(out, scheduler.launchd_plists("/bin/crw", pathlib.Path("/tmp"), cfg(daily_enabled=False)))
            self.assertFalse((out / "com.wes.codex-reset-watch.daily.plist").exists())
            self.assertTrue((out / "com.wes.codex-reset-watch.monitor.plist").exists())

    def test_existing_env_is_recovered_from_a_written_plist(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            scheduler.write_launchd(out, scheduler.launchd_plists(
                "/bin/crw", pathlib.Path("/tmp"), cfg(), env={"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"}))
            recovered = scheduler.launchd_existing_env(out)
        self.assertEqual(recovered, {"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})

    def test_existing_env_is_empty_when_no_plist_present(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(scheduler.launchd_existing_env(pathlib.Path(d)), {})


class SystemdRenderTests(unittest.TestCase):
    def test_default_daily_oncalendar_and_monitor_onactivesec(self):
        units = scheduler.systemd_units("/bin/crw", pathlib.Path("/tmp/logs"), cfg())
        self.assertIn("OnCalendar=*-*-* 10:00:00", units["codex-reset-watch-daily.timer"])
        self.assertIn("OnUnitActiveSec=120min", units["codex-reset-watch-monitor.timer"])
        self.assertIn("OnBootSec=120min", units["codex-reset-watch-monitor.timer"])
        self.assertIn("ExecStart=/bin/crw daily", units["codex-reset-watch-daily.service"])

    def test_disabled_monitor_omits_its_units(self):
        units = scheduler.systemd_units("/bin/crw", pathlib.Path("/tmp"), cfg(monitor_enabled=False))
        self.assertNotIn("codex-reset-watch-monitor.service", units)
        self.assertNotIn("codex-reset-watch-monitor.timer", units)
        self.assertIn("codex-reset-watch-daily.timer", units)

    def test_custom_daily_time_reflected_in_oncalendar(self):
        units = scheduler.systemd_units("/bin/crw", pathlib.Path("/tmp"), cfg(daily_time="23:45"))
        self.assertIn("OnCalendar=*-*-* 23:45:00", units["codex-reset-watch-daily.timer"])

    def test_write_systemd_removes_stale_disabled_unit(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            scheduler.write_systemd(out, scheduler.systemd_units("/bin/crw", pathlib.Path("/tmp"), cfg()))
            scheduler.write_systemd(out, scheduler.systemd_units(
                "/bin/crw", pathlib.Path("/tmp"), cfg(monitor_enabled=False)))
            self.assertFalse((out / "codex-reset-watch-monitor.service").exists())
            self.assertFalse((out / "codex-reset-watch-monitor.timer").exists())
            self.assertTrue((out / "codex-reset-watch-daily.timer").exists())

    def test_existing_env_recovered_from_written_service_file(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            scheduler.write_systemd(out, scheduler.systemd_units(
                "/bin/crw", pathlib.Path("/tmp"), cfg(), env={"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"}))
            recovered = scheduler.systemd_existing_env(out)
        self.assertEqual(recovered, {"TG_BOT_TOKEN": "tok", "TG_CHAT_ID": "42"})


class SchtasksCommandTests(unittest.TestCase):
    def test_default_daily_and_monitor_commands(self):
        self.assertEqual(scheduler.daily_task_command(r"C:\bin\crw.exe"), [
            "schtasks", "/Create", "/F", "/TN", "CodexResetWatchDaily",
            "/TR", r'"C:\bin\crw.exe" daily', "/SC", "DAILY", "/ST", "10:00",
        ])
        self.assertEqual(scheduler.monitor_task_command(r"C:\bin\crw.exe"), [
            "schtasks", "/Create", "/F", "/TN", "CodexResetWatchMonitor",
            "/TR", r'"C:\bin\crw.exe" monitor', "/SC", "HOURLY", "/MO", "2", "/ST", "00:05",
        ])

    def test_hour_aligned_interval_uses_hourly(self):
        cmd = scheduler.monitor_task_command("C:/crw.exe", cfg(scan_interval_minutes=180))
        self.assertEqual(cmd[-6:], ["/SC", "HOURLY", "/MO", "3", "/ST", "00:05"])

    def test_non_hour_aligned_interval_uses_minute(self):
        cmd = scheduler.monitor_task_command("C:/crw.exe", cfg(scan_interval_minutes=15))
        self.assertEqual(cmd[-4:], ["/SC", "MINUTE", "/MO", "15"])

    def test_full_day_interval_uses_daily(self):
        cmd = scheduler.monitor_task_command("C:/crw.exe", cfg(scan_interval_minutes=1440))
        self.assertEqual(cmd[-4:], ["/SC", "DAILY", "/ST", "00:05"])

    def test_custom_daily_time(self):
        cmd = scheduler.daily_task_command("C:/crw.exe", cfg(daily_time="18:30"))
        self.assertEqual(cmd[-2:], ["/ST", "18:30"])

    def test_delete_task_command(self):
        self.assertEqual(scheduler.delete_task_command("CodexResetWatchDaily"),
                         ["schtasks", "/Delete", "/F", "/TN", "CodexResetWatchDaily"])


class ProgramPathTests(unittest.TestCase):
    """Regression coverage for a real incident: program_path() previously
    preferred `shutil.which`/`sys.argv[0]`, which inside `uv run` (or any dev
    checkout) resolves to the project's OWN ephemeral .venv shim rather than
    the actually-installed CLI — pointing a real scheduler job at a path that
    stops working the moment that venv is rebuilt or removed. It must prefer
    the stable install.py location ($CRW_BIN_DIR / ~/scripts) instead."""

    def setUp(self):
        self._old_bin_dir = os.environ.get("CRW_BIN_DIR")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_bin_dir is None:
            os.environ.pop("CRW_BIN_DIR", None)
        else:
            os.environ["CRW_BIN_DIR"] = self._old_bin_dir

    def test_prefers_the_stable_bin_dir_executable_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = pathlib.Path(d)
            exe = bin_dir / "codex-reset-watch"
            exe.write_text("#!/bin/sh\n")
            os.environ["CRW_BIN_DIR"] = str(bin_dir)
            with unittest.mock.patch("shutil.which", return_value="/some/other/venv/codex-reset-watch"):
                self.assertEqual(scheduler.program_path(), str(exe))

    def test_falls_back_to_which_when_bin_dir_has_no_executable(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CRW_BIN_DIR"] = str(d)  # exists, but empty
            with unittest.mock.patch("shutil.which", return_value="/opt/homebrew/bin/codex-reset-watch"):
                self.assertEqual(scheduler.program_path(), "/opt/homebrew/bin/codex-reset-watch")

    def test_falls_back_to_argv0_when_nothing_else_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CRW_BIN_DIR"] = str(d)
            with unittest.mock.patch("shutil.which", return_value=None), \
                 unittest.mock.patch("sys.argv", ["/abs/path/to/some-shim"]):
                self.assertEqual(scheduler.program_path(), "/abs/path/to/some-shim")

    def test_windows_looks_for_the_exe_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = pathlib.Path(d)
            exe = bin_dir / "codex-reset-watch.exe"
            exe.write_text("stub")
            os.environ["CRW_BIN_DIR"] = str(bin_dir)
            with unittest.mock.patch("platform.system", return_value="Windows"):
                self.assertEqual(scheduler.program_path(), str(exe))


class MergedEnvTests(unittest.TestCase):
    def test_process_env_overrides_existing(self):
        with no_ambient_telegram_env():
            merged = scheduler.merged_env({"TG_BOT_TOKEN": "new"}, {"TG_BOT_TOKEN": "old", "TG_CHAT_ID": "42"})
        self.assertEqual(merged, {"TG_BOT_TOKEN": "new", "TG_CHAT_ID": "42"})

    def test_missing_process_env_keeps_existing(self):
        with no_ambient_telegram_env():
            merged = scheduler.merged_env({}, {"TG_CHAT_ID": "42"})
        self.assertEqual(merged, {"TG_CHAT_ID": "42"})

    def test_neither_present_yields_empty(self):
        with no_ambient_telegram_env():
            merged = scheduler.merged_env({}, {})
        self.assertEqual(merged, {})

    def test_ambient_shell_env_is_used_only_as_a_last_resort(self):
        with no_ambient_telegram_env():
            os.environ["TG_BOT_TOKEN"] = "from-shell"
            try:
                merged = scheduler.merged_env({}, {})
            finally:
                os.environ.pop("TG_BOT_TOKEN", None)
        self.assertEqual(merged, {"TG_BOT_TOKEN": "from-shell"})


if __name__ == "__main__":
    unittest.main()
