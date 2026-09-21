"""`--`-optional command syntax, the version flag, and export/import wiring.

Every subcommand may be written with or without leading dashes, and so may
every one of its flags: `crw --config`, `crw config list`, `crw check no-notify`
all mean what they look like. `_normalize_argv` is the single pure function
that rewrites argv before argparse sees it.
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
        with mock.patch.dict(os.environ, {"CRW_CONFIG": str(path)}), \
                mock.patch.object(config.secrets_store, "get", lambda key: ""), \
                mock.patch.object(config.secrets_store, "set", lambda key, value: True), \
                mock.patch.object(config.secrets_store, "delete", lambda key: True):
            yield path


class SubcommandDashTests(unittest.TestCase):
    def test_a_dashed_subcommand_becomes_the_plain_one(self):
        self.assertEqual(crw._normalize_argv(["--config"]), ["config"])
        self.assertEqual(crw._normalize_argv(["--doctor"]), ["doctor"])
        self.assertEqual(crw._normalize_argv(["--apply-schedule"]), ["apply-schedule"])

    def test_a_plain_subcommand_is_untouched(self):
        self.assertEqual(crw._normalize_argv(["config"]), ["config"])

    def test_every_subcommand_accepts_both_spellings(self):
        for name in crw.SUBCOMMANDS:
            self.assertEqual(crw._normalize_argv([f"--{name}"]), [name], name)

    def test_dashed_config_parses_into_the_config_command(self):
        args = crw.build_parser().parse_args(crw._normalize_argv(["--config"]))
        self.assertEqual(args.command, "config")


class FlagDashTests(unittest.TestCase):
    def test_a_bare_flag_after_its_subcommand_gains_dashes(self):
        self.assertEqual(crw._normalize_argv(["config", "list"]), ["config", "--list"])

    def test_a_bare_value_taking_flag_keeps_its_value_intact(self):
        self.assertEqual(
            crw._normalize_argv(["config", "set", "daily_time=09:00"]),
            ["config", "--set", "daily_time=09:00"])

    def test_a_value_that_collides_with_a_flag_name_is_not_rewritten(self):
        # "export" is a config flag name, but here it is --set's value.
        self.assertEqual(
            crw._normalize_argv(["config", "set", "export"]),
            ["config", "--set", "export"])

    def test_no_notify_works_bare_on_check(self):
        self.assertEqual(crw._normalize_argv(["check", "no-notify"]), ["check", "--no-notify"])

    def test_force_works_bare_on_daily(self):
        self.assertEqual(crw._normalize_argv(["daily", "force"]), ["daily", "--force"])

    def test_an_already_dashed_flag_is_untouched(self):
        self.assertEqual(crw._normalize_argv(["check", "--no-notify"]), ["check", "--no-notify"])

    def test_a_short_flag_and_its_value_survive(self):
        self.assertEqual(crw._normalize_argv(["logs", "-n", "50"]), ["logs", "-n", "50"])

    def test_a_bare_lines_flag_gains_dashes(self):
        self.assertEqual(crw._normalize_argv(["logs", "lines", "50"]), ["logs", "--lines", "50"])

    def test_apply_schedule_is_a_subcommand_first_and_a_config_flag_second(self):
        self.assertEqual(crw._normalize_argv(["apply-schedule"]), ["apply-schedule"])
        self.assertEqual(
            crw._normalize_argv(["config", "set", "daily_time=09:00", "apply-schedule"]),
            ["config", "--set", "daily_time=09:00", "--apply-schedule"])

    def test_a_flag_belonging_to_another_subcommand_is_left_alone(self):
        # "force" is a daily flag; after `check` it is not one, so it stays a
        # positional and argparse reports it rather than us inventing a flag.
        self.assertEqual(crw._normalize_argv(["check", "force"]), ["check", "force"])

    def test_an_empty_argv_is_unchanged(self):
        self.assertEqual(crw._normalize_argv([]), [])

    def test_a_double_dash_separator_stops_rewriting(self):
        self.assertEqual(crw._normalize_argv(["config", "--", "list"]),
                         ["config", "--", "list"])


class EndToEndSyntaxTests(unittest.TestCase):
    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_dashless_config_set_reaches_the_config_command(self, mock_apply):
        with isolated_config():
            code = crw.main(["config", "set", "scan_interval_minutes=45m"])
            self.assertEqual(config.load()["scan_interval_minutes"], 45)
        self.assertEqual(code, 0)

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_dashed_config_with_a_dashless_flag(self, mock_apply):
        with isolated_config():
            code = crw.main(["--config", "list"])
        self.assertEqual(code, 0)

    def test_version_flag_prints_the_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            crw.main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(crw.ui.package_version(), out.getvalue())


class ExportImportCommandTests(unittest.TestCase):
    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_export_writes_a_json_file(self, mock_apply):
        with isolated_config(), tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "settings.json"
            code = crw.main(["config", "export", str(target)])
            payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["daily_time"], config.DEFAULTS["daily_time"])

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_export_never_contains_the_token(self, mock_apply):
        with isolated_config(), tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "settings.json"
            with mock.patch.object(config.secrets_store, "get", lambda key: "123:LEAKME"):
                crw.main(["config", "export", str(target)])
            written = target.read_text(encoding="utf-8")
        self.assertNotIn("LEAKME", written)
        self.assertNotIn("telegram_bot_token", written)

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_import_applies_the_settings(self, mock_apply):
        with isolated_config(), tempfile.TemporaryDirectory() as d:
            source = pathlib.Path(d) / "settings.json"
            source.write_text(json.dumps({"daily_time": "06:15"}), encoding="utf-8")
            code = crw.main(["config", "import", str(source)])
            self.assertEqual(config.load()["daily_time"], "06:15")
        self.assertEqual(code, 0)

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_import_of_a_bad_value_changes_nothing_and_fails(self, mock_apply):
        with isolated_config(), tempfile.TemporaryDirectory() as d:
            source = pathlib.Path(d) / "settings.json"
            source.write_text(json.dumps({"daily_time": "06:15",
                                          "scan_interval_minutes": "9 weeks"}), encoding="utf-8")
            code = crw.main(["config", "import", str(source)])
            self.assertEqual(config.load()["daily_time"], config.DEFAULTS["daily_time"])
        self.assertEqual(code, 1)

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_import_of_a_missing_file_fails_without_a_traceback(self, mock_apply):
        with isolated_config():
            self.assertEqual(crw.main(["config", "import", "/nope/nothing.json"]), 1)

    @mock.patch("codex_reset_watch.scheduler.apply", return_value=("launchd", ("daily",)))
    def test_a_round_trip_preserves_the_settings(self, mock_apply):
        with isolated_config(), tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "settings.json"
            crw.main(["config", "set", "daily_time=04:05", "--set", "request_retries=7"])
            crw.main(["config", "export", str(target)])
            crw.main(["config", "set", "daily_time=10:00", "--set", "request_retries=3"])
            crw.main(["config", "import", str(target)])
            cfg = config.load()
        self.assertEqual(cfg["daily_time"], "04:05")
        self.assertEqual(cfg["request_retries"], 7)


class TelegramCredentialWiringTests(unittest.TestCase):
    def _no_env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TG_BOT_TOKEN", "TG_CHAT_ID")}
        return mock.patch.dict(os.environ, env, clear=True)

    def test_send_telegram_uses_the_stored_credentials(self):
        cfg = {"telegram_bot_token": "stored-token", "telegram_chat_id": "stored-chat"}
        logger = mock.Mock()
        with self._no_env(), mock.patch.object(crw.telegram_notify, "send_telegram",
                                               return_value=True) as send:
            self.assertTrue(crw.send_telegram(cfg, "hello", logger))
        send.assert_called_once_with("stored-token", "stored-chat", "hello")

    def test_the_stored_credentials_outrank_a_stale_environment(self):
        # The bug this pins: a TG_BOT_TOKEN left in a shell profile or baked into
        # an old launchd plist kept notifying through a bot `crw config` replaced.
        cfg = {"telegram_bot_token": "stored-token", "telegram_chat_id": "stored-chat"}
        logger = mock.Mock()
        with mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "env-token", "TG_CHAT_ID": "env-chat"}), \
                mock.patch.object(crw.telegram_notify, "send_telegram", return_value=True) as send:
            crw.send_telegram(cfg, "hello", logger)
        send.assert_called_once_with("stored-token", "stored-chat", "hello")

    def test_the_environment_is_used_when_nothing_is_stored(self):
        logger = mock.Mock()
        with mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "env-token", "TG_CHAT_ID": "env-chat"}), \
                mock.patch.object(crw.telegram_notify, "send_telegram", return_value=True) as send:
            crw.send_telegram({}, "hello", logger)
        send.assert_called_once_with("env-token", "env-chat", "hello")

    def test_missing_credentials_are_logged_not_sent(self):
        logger = mock.Mock()
        with self._no_env(), mock.patch.object(crw.telegram_notify, "send_telegram") as send:
            self.assertFalse(crw.send_telegram({}, "hello", logger))
        send.assert_not_called()
        logger.event.assert_called_once()

    def test_the_token_is_never_written_to_the_log(self):
        cfg = {"telegram_bot_token": "123456:SUPERSECRET", "telegram_chat_id": "42"}
        logger = mock.Mock()
        with self._no_env(), mock.patch.object(crw.telegram_notify, "send_telegram",
                                               return_value=True):
            crw.send_telegram(cfg, "hello", logger)
        logged = json.dumps([[str(a) for a in call.args] + [str(call.kwargs)]
                             for call in logger.event.call_args_list])
        self.assertNotIn("SUPERSECRET", logged)


if __name__ == "__main__":
    unittest.main()
