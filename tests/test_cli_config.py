"""CLI-level tests for `crw config` / `crw apply-schedule`.

`scheduler.apply` is mocked everywhere here for the same reason as in
test_ui.py: these commands are DESIGNED to shell out to the real OS scheduler,
and a unit test must never do that to whatever this machine actually has
registered. See tests/test_scheduler.py and tests/test_launchd.py for the
scheduler-rendering logic itself, exercised without any real OS calls.
"""
import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
import unittest.mock as mock

import codex_reset_watch as crw
from codex_reset_watch import config


@contextlib.contextmanager
def isolated_config():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "config.json"
        old = os.environ.get("CRW_CONFIG")
        os.environ["CRW_CONFIG"] = str(path)
        try:
            yield path
        finally:
            if old is None:
                os.environ.pop("CRW_CONFIG", None)
            else:
                os.environ["CRW_CONFIG"] = old


@mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily", "monitor")))
class ConfigListTests(unittest.TestCase):
    def test_list_prints_settings_and_makes_no_changes(self, mock_apply):
        with isolated_config() as path:
            args = crw.build_parser().parse_args(["config", "--list"])
            code = crw.config_cmd(args)
        self.assertEqual(code, 0)
        self.assertFalse(path.exists())
        mock_apply.assert_not_called()


@mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily", "monitor")))
class ConfigSetTests(unittest.TestCase):
    def test_set_persists_the_value(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(["config", "--set", "scan_interval_minutes=45m"])
            code = crw.config_cmd(args)
            self.assertEqual(config.load()["scan_interval_minutes"], 45)
        self.assertEqual(code, 0)

    def test_schedule_relevant_set_auto_reapplies(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(["config", "--set", "daily_time=07:30"])
            crw.config_cmd(args)
        mock_apply.assert_called_once()

    def test_non_schedule_set_does_not_reapply(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(["config", "--set", "notify_upcoming_reset=off"])
            crw.config_cmd(args)
        mock_apply.assert_not_called()

    def test_apply_schedule_flag_forces_reapply_even_for_non_schedule_key(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(
                ["config", "--set", "notify_upcoming_reset=off", "--apply-schedule"])
            crw.config_cmd(args)
        mock_apply.assert_called_once()

    def test_multiple_sets_in_one_call(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(
                ["config", "--set", "daily_enabled=off", "--set", "monitor_enabled=off"])
            crw.config_cmd(args)
            cfg = config.load()
            self.assertFalse(cfg["daily_enabled"])
            self.assertFalse(cfg["monitor_enabled"])

    def test_unknown_key_returns_error_code_and_does_not_save(self, mock_apply):
        with isolated_config() as path:
            args = crw.build_parser().parse_args(["config", "--set", "not_a_real_key=1"])
            code = crw.config_cmd(args)
        self.assertEqual(code, 2)
        self.assertFalse(path.exists())

    def test_invalid_value_returns_error_code_and_does_not_save(self, mock_apply):
        with isolated_config() as path:
            args = crw.build_parser().parse_args(["config", "--set", "scan_interval_minutes=2weeks"])
            code = crw.config_cmd(args)
        self.assertEqual(code, 2)
        self.assertFalse(path.exists())

    def test_malformed_set_item_without_equals_sign(self, mock_apply):
        with isolated_config():
            args = crw.build_parser().parse_args(["config", "--set", "daily_time"])
            code = crw.config_cmd(args)
        self.assertEqual(code, 2)

    def test_apply_failure_is_reported_not_raised(self, mock_apply):
        mock_apply.side_effect = RuntimeError("launchctl exploded")
        with isolated_config():
            args = crw.build_parser().parse_args(["config", "--set", "daily_time=09:00"])
            code = crw.config_cmd(args)
        self.assertEqual(code, 1)


@mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily", "monitor")))
class ApplyScheduleCommandTests(unittest.TestCase):
    def test_apply_schedule_cmd_delegates_to_scheduler(self, mock_apply):
        with isolated_config():
            code = crw.apply_schedule_cmd()
        self.assertEqual(code, 0)
        mock_apply.assert_called_once()

    def test_main_routes_apply_schedule_subcommand(self, mock_apply):
        with isolated_config():
            code = crw.main(["apply-schedule"])
        self.assertEqual(code, 0)
        mock_apply.assert_called_once()

    def test_all_jobs_disabled_reports_none_without_crashing(self, mock_apply):
        mock_apply.return_value = ("launchd", ())
        with isolated_config():
            code = crw.apply_schedule_cmd()
        self.assertEqual(code, 0)


class ArgparseWiringTests(unittest.TestCase):
    def test_config_subcommand_parses(self):
        args = crw.build_parser().parse_args(["config"])
        self.assertEqual(args.command, "config")
        self.assertFalse(args.list)
        self.assertIsNone(args.set)

    def test_config_set_is_repeatable(self):
        args = crw.build_parser().parse_args(["config", "--set", "a=1", "--set", "b=2"])
        self.assertEqual(args.set, ["a=1", "b=2"])

    def test_apply_schedule_subcommand_parses(self):
        args = crw.build_parser().parse_args(["apply-schedule"])
        self.assertEqual(args.command, "apply-schedule")


if __name__ == "__main__":
    unittest.main()
