import contextlib
import datetime as dt
import json
import os
import pathlib
import tempfile
import unittest

from codex_reset_watch import config


@contextlib.contextmanager
def isolated_config(raw=None):
    """Point CRW_CONFIG at a throwaway file so tests never touch the real one."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "config.json"
        if raw is not None:
            path.write_text(json.dumps(raw), encoding="utf-8")
        old = os.environ.get("CRW_CONFIG")
        os.environ["CRW_CONFIG"] = str(path)
        try:
            yield path
        finally:
            if old is None:
                os.environ.pop("CRW_CONFIG", None)
            else:
                os.environ["CRW_CONFIG"] = old


class IntervalParsingTests(unittest.TestCase):
    def test_minutes_hours_days_and_bare_number(self):
        self.assertEqual(config.parse_interval("30m"), 30)
        self.assertEqual(config.parse_interval("2h"), 120)
        self.assertEqual(config.parse_interval("1d"), 1440)
        self.assertEqual(config.parse_interval("90"), 90)
        self.assertEqual(config.parse_interval(45), 45)

    def test_whitespace_and_case_and_plural_units(self):
        self.assertEqual(config.parse_interval(" 3 Hours "), 180)
        self.assertEqual(config.parse_interval("1 day"), 1440)

    def test_minimum_is_one_minute(self):
        self.assertEqual(config.parse_interval("1m"), 1)
        with self.assertRaises(ValueError):
            config.parse_interval("0m")

    def test_maximum_is_one_day(self):
        self.assertEqual(config.parse_interval("1440"), 1440)
        with self.assertRaises(ValueError):
            config.parse_interval("1441")
        with self.assertRaises(ValueError):
            config.parse_interval("2d")

    def test_garbage_rejected(self):
        for bad in ("", "abc", "2 weeks", "-5m", "5x"):
            with self.assertRaises(ValueError):
                config.parse_interval(bad)

    def test_format_interval_picks_the_largest_clean_unit(self):
        self.assertEqual(config.format_interval(1), "1 minute")
        self.assertEqual(config.format_interval(45), "45 minutes")
        self.assertEqual(config.format_interval(60), "1 hour")
        self.assertEqual(config.format_interval(120), "2 hours")
        self.assertEqual(config.format_interval(1440), "1 day")
        self.assertEqual(config.format_interval(2880), "2 days")
        self.assertEqual(config.format_interval(90), "90 minutes")  # not hour-aligned


class TimeParsingTests(unittest.TestCase):
    def test_canonicalizes_hh_mm(self):
        self.assertEqual(config.parse_hhmm("9"), "09:00")
        self.assertEqual(config.parse_hhmm("9:5"), "09:05")
        self.assertEqual(config.parse_hhmm("23:59"), "23:59")
        self.assertEqual(config.parse_hhmm("00:00"), "00:00")

    def test_rejects_impossible_times(self):
        for bad in ("24:00", "12:60", "nope", ""):
            with self.assertRaises(ValueError):
                config.parse_hhmm(bad)


class TimezoneTests(unittest.TestCase):
    def test_utc_alias(self):
        self.assertEqual(config.tzinfo_for({"timezone": "UTC"}), dt.timezone.utc)

    def test_fixed_positive_offset(self):
        tz = config.tzinfo_for({"timezone": "UTC+8"})
        self.assertEqual(tz.utcoffset(None), dt.timedelta(hours=8))

    def test_fixed_negative_offset_with_minutes(self):
        tz = config.tzinfo_for({"timezone": "UTC-05:30"})
        self.assertEqual(tz.utcoffset(None), dt.timedelta(hours=-5, minutes=-30))

    def test_unresolvable_name_falls_back_to_utc8(self):
        tz = config.tzinfo_for({"timezone": "not/a/real/zone"})
        self.assertIs(tz, config.FALLBACK_TZ)

    def test_missing_timezone_uses_default(self):
        self.assertEqual(config.tzinfo_for({}).utcoffset(None), dt.timedelta(hours=8))

    def test_tz_label_passes_through_named_offsets(self):
        self.assertEqual(config.tz_label({"timezone": "UTC+8"}), "UTC+8")

    def test_tz_label_for_local_reports_current_offset(self):
        label = config.tz_label({"timezone": "local"})
        self.assertTrue(label.startswith("UTC"))


class DailyTimeTests(unittest.TestCase):
    def test_daily_hm_reads_configured_time(self):
        self.assertEqual(config.daily_hm({"daily_time": "07:30"}), (7, 30))

    def test_daily_os_local_hm_matches_when_machine_is_already_in_that_timezone(self):
        # UTC+8 configured, and `now` supplied already in UTC+8: the OS-local
        # hour/minute must equal the configured wall-clock time exactly.
        cfg = {"daily_time": "10:00", "timezone": "UTC+8"}
        now = dt.datetime(2026, 1, 1, 9, 0, tzinfo=config.tzinfo_for(cfg))
        hour, minute = config.daily_os_local_hm(cfg, now=now)
        # The local system timezone is unknown in CI, but the target instant
        # (10:00 UTC+8 == 02:00 UTC) is fixed; recompute independently to check.
        expected = dt.datetime(2026, 1, 1, 2, 0, tzinfo=dt.timezone.utc).astimezone()
        self.assertEqual((hour, minute), (expected.hour, expected.minute))


class CoercionTests(unittest.TestCase):
    def test_bool_accepts_common_spellings(self):
        s = config.BY_KEY["daily_enabled"]
        for truthy in ("1", "true", "Yes", "on", "開"):
            self.assertIs(config.coerce(s, truthy), True)
        for falsy in ("0", "false", "No", "off", "關"):
            self.assertIs(config.coerce(s, falsy), False)
        with self.assertRaises(ValueError):
            config.coerce(s, "maybe")

    def test_int_enforces_bounds(self):
        s = config.BY_KEY["request_retries"]
        self.assertEqual(config.coerce(s, "5"), 5)
        with self.assertRaises(ValueError):
            config.coerce(s, "0")
        with self.assertRaises(ValueError):
            config.coerce(s, "999")
        with self.assertRaises(ValueError):
            config.coerce(s, "nope")

    def test_path_expands_user_and_allows_blank(self):
        s = config.BY_KEY["log_dir"]
        self.assertEqual(config.coerce(s, ""), "")
        self.assertEqual(config.coerce(s, "~"), str(pathlib.Path.home()))

    def test_text_rejects_blank(self):
        s = config.BY_KEY["api_base"]
        with self.assertRaises(ValueError):
            config.coerce(s, "   ")

    def test_render_bool_and_interval_and_blank_path(self):
        # No language argument renders English on purpose: a piped `--list`
        # must not change shape with the machine's locale.
        self.assertEqual(config.render(config.BY_KEY["daily_enabled"], True), "On")
        self.assertEqual(config.render(config.BY_KEY["daily_enabled"], False), "Off")
        self.assertEqual(config.render(config.BY_KEY["scan_interval_minutes"], 120), "2 hours")
        self.assertEqual(config.render(config.BY_KEY["log_dir"], ""), "(platform default)")

    def test_render_follows_the_requested_language(self):
        self.assertEqual(config.render(config.BY_KEY["daily_enabled"], True, "zh-TW"), "開啟")
        self.assertEqual(config.render(config.BY_KEY["log_dir"], "", "zh-TW"), "（系統預設）")
        self.assertEqual(
            config.render(config.BY_KEY["scan_interval_minutes"], 120, "zh-TW"), "2 小時")


class SetValueTests(unittest.TestCase):
    def test_unknown_key_raises_keyerror(self):
        with self.assertRaises(KeyError):
            config.set_value({}, "not_a_real_setting", "x")

    def test_valid_value_is_stored_and_returned(self):
        cfg = {}
        result = config.set_value(cfg, "scan_interval_minutes", "3h")
        self.assertEqual(result, 180)
        self.assertEqual(cfg["scan_interval_minutes"], 180)

    def test_invalid_value_raises_and_leaves_cfg_untouched(self):
        cfg = {"scan_interval_minutes": 60}
        with self.assertRaises(ValueError):
            config.set_value(cfg, "scan_interval_minutes", "2 weeks")
        self.assertEqual(cfg["scan_interval_minutes"], 60)


class LoadSaveTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        with isolated_config():
            cfg = config.load()
        self.assertEqual(cfg, config.DEFAULTS)

    def test_roundtrip_through_save_and_load(self):
        with isolated_config() as path:
            cfg = config.load()
            cfg["scan_interval_minutes"] = 30
            cfg["daily_time"] = "08:15"
            written = config.save(cfg)
            self.assertEqual(written, path)
            reloaded = config.load()
        self.assertEqual(reloaded["scan_interval_minutes"], 30)
        self.assertEqual(reloaded["daily_time"], "08:15")

    def test_save_is_0600(self):
        with isolated_config() as path:
            config.save(config.load())
            mode = path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_invalid_value_in_file_is_dropped_not_fatal(self):
        with isolated_config({"scan_interval_minutes": "not-a-duration"}):
            cfg = config.load()
        self.assertEqual(cfg["scan_interval_minutes"], config.DEFAULTS["scan_interval_minutes"])

    def test_unknown_keys_in_file_survive_a_load_save_roundtrip(self):
        with isolated_config({"some_future_key": "kept"}):
            cfg = config.load()
            self.assertEqual(cfg["some_future_key"], "kept")
            config.save(cfg)
            reloaded = config.load()
        self.assertEqual(reloaded["some_future_key"], "kept")

    def test_migrates_legacy_daily_hour_minute_and_timezone_label(self):
        with isolated_config({"daily_hour": 7, "daily_minute": 45, "timezone_label": "UTC+9"}):
            cfg = config.load()
        self.assertEqual(cfg["daily_time"], "07:45")
        self.assertEqual(cfg["timezone"], "UTC+9")

    def test_corrupt_json_file_does_not_crash_load(self):
        with isolated_config() as path:
            path.write_text("{not json", encoding="utf-8")
            cfg = config.load()
        self.assertEqual(cfg, config.DEFAULTS)


class DirectoryResolutionTests(unittest.TestCase):
    def test_env_override_wins_over_configured_value(self):
        old = os.environ.get("CRW_STATE_DIR")
        os.environ["CRW_STATE_DIR"] = "/env/wins"
        try:
            result = config.state_dir({"state_dir": "/configured/path"})
        finally:
            if old is None:
                os.environ.pop("CRW_STATE_DIR", None)
            else:
                os.environ["CRW_STATE_DIR"] = old
        self.assertEqual(str(result), "/env/wins")

    def test_configured_value_used_when_no_env_override(self):
        os.environ.pop("CRW_STATE_DIR", None)
        result = config.state_dir({"state_dir": "/configured/path"})
        self.assertEqual(str(result), "/configured/path")

    def test_blank_configured_value_falls_back_to_platform_default(self):
        os.environ.pop("CRW_LOG_DIR", None)
        result = config.log_dir({"log_dir": ""})
        self.assertTrue(str(result))  # some concrete platform default, not ""


class SchemaConsistencyTests(unittest.TestCase):
    def test_every_setting_key_is_unique(self):
        keys = [s.key for s in config.SETTINGS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_defaults_round_trip_through_coerce(self):
        # Every shipped default must itself validate under its own setting's rules.
        for setting in config.SETTINGS:
            config.coerce(setting, setting.default if not isinstance(setting.default, bool)
                          else ("on" if setting.default else "off"))


if __name__ == "__main__":
    unittest.main()
