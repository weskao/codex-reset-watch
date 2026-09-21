"""The settings schema additions: choice + secret kinds, language, Telegram
credentials, and the export/import payloads.

The pre-existing schema behaviour (intervals, times, timezones, load/save) is
covered by tests/test_config.py — this file only exercises what was added.
"""
import contextlib
import json
import os
import pathlib
import tempfile
import unittest
import unittest.mock as mock

from codex_reset_watch import config


@contextlib.contextmanager
def isolated_config():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "config.json"
        with mock.patch.dict(os.environ, {"CRW_CONFIG": str(path)}):
            yield path


class SchemaShapeTests(unittest.TestCase):
    def test_language_is_a_choice_setting(self):
        setting = config.BY_KEY["language"]
        self.assertEqual(setting.kind, "choice")
        self.assertEqual(setting.choices, ("auto", "en", "zh-TW"))
        self.assertEqual(setting.default, "auto")

    def test_telegram_settings_exist_in_their_own_group(self):
        self.assertEqual(config.BY_KEY["telegram_bot_token"].group, "telegram")
        self.assertEqual(config.BY_KEY["telegram_chat_id"].group, "telegram")

    def test_bot_token_is_the_only_secret(self):
        self.assertEqual(config.SECRET_KEYS, frozenset({"telegram_bot_token"}))

    def test_secret_keys_are_derived_from_the_kind_not_hand_listed(self):
        by_kind = frozenset(s.key for s in config.SETTINGS if s.kind == "secret")
        self.assertEqual(config.SECRET_KEYS, by_kind)

    def test_groups_are_ids_translated_through_i18n(self):
        for group in config.GROUPS:
            self.assertRegex(group, r"^[a-z_]+$")
        self.assertEqual(config.group_label("scheduling", "en"), "Scheduling")
        self.assertEqual(config.group_label("scheduling", "zh-TW"), "排程")

    def test_labels_and_help_are_translated(self):
        setting = config.BY_KEY["daily_time"]
        self.assertEqual(config.label(setting, "en"), "Daily time")
        self.assertEqual(config.label(setting, "zh-TW"), "每日時間")
        self.assertIn("HH:MM", config.help_text(setting, "zh-TW"))

    def test_an_untranslated_label_falls_back_to_the_english_source(self):
        setting = config.Setting("nope", "text", "", "api", "Fallback label", "Fallback help")
        self.assertEqual(config.label(setting, "zh-TW"), "Fallback label")
        self.assertEqual(config.help_text(setting, "zh-TW"), "Fallback help")

    def test_telegram_keys_do_not_trigger_a_schedule_reapply(self):
        self.assertNotIn("telegram_bot_token", config.SCHEDULE_KEYS)
        self.assertNotIn("telegram_chat_id", config.SCHEDULE_KEYS)


class ChoiceKindTests(unittest.TestCase):
    def test_a_valid_choice_is_stored(self):
        self.assertEqual(config.coerce(config.BY_KEY["language"], "zh-TW"), "zh-TW")

    def test_an_invalid_choice_is_rejected(self):
        with self.assertRaises(ValueError):
            config.coerce(config.BY_KEY["language"], "klingon")

    def test_the_rejection_names_the_valid_choices(self):
        with self.assertRaises(ValueError) as caught:
            config.coerce(config.BY_KEY["language"], "klingon")
        self.assertIn("zh-TW", str(caught.exception))

    def test_a_choice_renders_as_its_human_label(self):
        self.assertEqual(config.render(config.BY_KEY["language"], "zh-TW"), "繁體中文")
        self.assertEqual(config.render(config.BY_KEY["language"], "en"), "English")


class SecretKindTests(unittest.TestCase):
    def test_a_long_secret_shows_only_its_last_four_characters(self):
        shown = config.render(config.BY_KEY["telegram_bot_token"], "1234567890:ABCDEFGHWXYZ")
        self.assertTrue(shown.startswith("*"))
        self.assertTrue(shown.endswith("WXYZ"))
        self.assertNotIn("1234567890", shown)

    def test_a_short_secret_reveals_nothing(self):
        self.assertEqual(config.render(config.BY_KEY["telegram_bot_token"], "short"), "*" * 8)

    def test_an_empty_secret_renders_as_unset(self):
        shown = config.render(config.BY_KEY["telegram_bot_token"], "", "en")
        self.assertEqual(shown, "(not set)")

    def test_a_secret_accepts_an_empty_value_to_clear_it(self):
        self.assertEqual(config.coerce(config.BY_KEY["telegram_bot_token"], ""), "")

    def test_surrounding_whitespace_is_stripped_from_a_pasted_token(self):
        self.assertEqual(config.coerce(config.BY_KEY["telegram_bot_token"], "  abc  "), "abc")

    def test_chat_id_may_be_empty_although_it_is_not_secret(self):
        self.assertEqual(config.coerce(config.BY_KEY["telegram_chat_id"], ""), "")


class TelegramCredentialTests(unittest.TestCase):
    def _env(self, **overrides):
        env = {k: v for k, v in os.environ.items() if k not in ("TG_BOT_TOKEN", "TG_CHAT_ID")}
        env.update(overrides)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_config_values_are_used_when_the_environment_is_empty(self):
        cfg = {"telegram_bot_token": "cfg-token", "telegram_chat_id": "cfg-chat"}
        with self._env():
            self.assertEqual(config.telegram_credentials(cfg), ("cfg-token", "cfg-chat"))

    def test_the_config_file_wins_over_the_environment(self):
        cfg = {"telegram_bot_token": "cfg-token", "telegram_chat_id": "cfg-chat"}
        with self._env(TG_BOT_TOKEN="env-token", TG_CHAT_ID="env-chat"):
            self.assertEqual(config.telegram_credentials(cfg), ("cfg-token", "cfg-chat"))

    def test_the_environment_fills_an_unset_credential(self):
        with self._env(TG_BOT_TOKEN="env-token", TG_CHAT_ID="env-chat"):
            self.assertEqual(config.telegram_credentials({}), ("env-token", "env-chat"))

    def test_each_credential_falls_back_independently(self):
        cfg = {"telegram_chat_id": "cfg-chat"}
        with self._env(TG_BOT_TOKEN="env-token", TG_CHAT_ID="env-chat"):
            self.assertEqual(config.telegram_credentials(cfg), ("env-token", "cfg-chat"))

    def test_an_empty_environment_variable_does_not_shadow_the_config(self):
        cfg = {"telegram_bot_token": "cfg-token", "telegram_chat_id": "cfg-chat"}
        with self._env(TG_BOT_TOKEN=""):
            self.assertEqual(config.telegram_credentials(cfg)[0], "cfg-token")

    def test_missing_everywhere_is_two_empty_strings(self):
        with self._env():
            self.assertEqual(config.telegram_credentials({}), ("", ""))


class ExportTests(unittest.TestCase):
    def test_every_non_secret_setting_is_exported(self):
        payload = config.export_payload(dict(config.DEFAULTS))
        expected = {s.key for s in config.SETTINGS if s.kind != "secret"}
        self.assertEqual(set(payload), expected)

    def test_the_bot_token_is_never_exported(self):
        cfg = dict(config.DEFAULTS, telegram_bot_token="super-secret")
        payload = config.export_payload(cfg)
        self.assertNotIn("telegram_bot_token", payload)
        self.assertNotIn("super-secret", json.dumps(payload))

    def test_the_chat_id_is_exported_because_it_is_not_a_secret(self):
        cfg = dict(config.DEFAULTS, telegram_chat_id="12345")
        self.assertEqual(config.export_payload(cfg)["telegram_chat_id"], "12345")

    def test_current_values_are_exported_not_defaults(self):
        cfg = dict(config.DEFAULTS, scan_interval_minutes=45)
        self.assertEqual(config.export_payload(cfg)["scan_interval_minutes"], 45)


class ImportTests(unittest.TestCase):
    def test_known_keys_are_applied(self):
        updates, skipped = config.import_updates({"scan_interval_minutes": "45m"})
        self.assertEqual(updates, {"scan_interval_minutes": 45})
        self.assertEqual(skipped, ())

    def test_a_secret_in_the_file_is_skipped_not_written(self):
        updates, skipped = config.import_updates({"telegram_bot_token": "********WXYZ"})
        self.assertEqual(updates, {})
        self.assertEqual(skipped, ("telegram_bot_token",))

    def test_an_unknown_key_is_skipped_not_stored_as_a_blob(self):
        updates, skipped = config.import_updates({"from_a_newer_version": 1})
        self.assertEqual(updates, {})
        self.assertEqual(skipped, ("from_a_newer_version",))

    def test_one_bad_value_aborts_the_whole_import(self):
        with self.assertRaises(ValueError):
            config.import_updates({"daily_time": "09:00", "scan_interval_minutes": "2 weeks"})

    def test_json_typed_values_round_trip(self):
        updates, _ = config.import_updates({"daily_enabled": False, "request_retries": 5})
        self.assertEqual(updates, {"daily_enabled": False, "request_retries": 5})

    def test_an_export_imports_back_unchanged(self):
        original = dict(config.DEFAULTS, scan_interval_minutes=45, daily_time="07:30",
                        timezone="Asia/Taipei", telegram_chat_id="999")
        updates, skipped = config.import_updates(config.export_payload(original))
        self.assertEqual(skipped, ())
        for key, value in updates.items():
            self.assertEqual(value, original[key], key)


class SecretStorageTests(unittest.TestCase):
    """The bot token lives in the OS keychain, never in config.json."""

    def setUp(self):
        self.vault = {}

        def fake_set(key, value):
            # Mirrors the real store's contract: an empty value deletes the
            # item rather than storing a blank one. config.save_secrets must
            # never reach that branch — see the tests below.
            if not value:
                return fake_delete(key)
            self.vault[key] = value
            return True

        def fake_delete(key):
            return self.vault.pop(key, None) is not None

        patcher = mock.patch.multiple(
            config.secrets_store,
            available=lambda: True,
            get=lambda key: self.vault.get(key, ""),
            set=fake_set,
            delete=fake_delete,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_token_is_never_written_to_the_config_file(self):
        with isolated_config() as path:
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
            written = path.read_text(encoding="utf-8")
        self.assertNotIn("SECRET", written)
        self.assertNotIn("telegram_bot_token", written)

    def test_the_token_goes_to_the_keychain_instead(self):
        with isolated_config():
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
        self.assertEqual(self.vault["telegram_bot_token"], "123456:SECRET")

    def test_the_token_is_read_back_from_the_keychain(self):
        with isolated_config():
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
            self.assertEqual(config.load()["telegram_bot_token"], "123456:SECRET")

    def test_saving_without_the_token_leaves_the_stored_one_alone(self):
        # The regression: every ordinary save (a menu edit, --set, an import,
        # a reinstall) used to delete the keychain item whenever the cfg it was
        # handed had no token in it — so the token had to be retyped.
        with isolated_config():
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
            config.save(dict(config.DEFAULTS, telegram_bot_token=""))
            config.save(dict(config.DEFAULTS))
        self.assertEqual(self.vault["telegram_bot_token"], "123456:SECRET")

    def test_restoring_defaults_keeps_the_token(self):
        with isolated_config():
            cfg = dict(config.DEFAULTS, telegram_bot_token="123456:SECRET", daily_time="08:00")
            config.save(cfg)
            config.save(config.restore_defaults(config.load()))
        self.assertEqual(self.vault["telegram_bot_token"], "123456:SECRET")
        self.assertEqual(config.restore_defaults(cfg)["daily_time"], config.DEFAULTS["daily_time"])

    def test_clearing_the_token_is_explicit(self):
        with isolated_config():
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
            config.clear_secret("telegram_bot_token")
        self.assertNotIn("telegram_bot_token", self.vault)

    def test_a_non_secret_setting_still_round_trips_through_the_file(self):
        with isolated_config() as path:
            config.save(dict(config.DEFAULTS, telegram_chat_id="12345"))
            self.assertIn("12345", path.read_text(encoding="utf-8"))
            self.assertEqual(config.load()["telegram_chat_id"], "12345")

    def test_a_hand_written_plaintext_token_is_migrated_out_of_the_file(self):
        with isolated_config() as path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"telegram_bot_token": "leaked-token",
                                        "daily_time": "08:00"}), encoding="utf-8")
            loaded = config.load()
            remaining = path.read_text(encoding="utf-8")
        self.assertEqual(loaded["telegram_bot_token"], "leaked-token")
        self.assertEqual(self.vault["telegram_bot_token"], "leaked-token")
        self.assertNotIn("leaked-token", remaining)
        self.assertIn("08:00", remaining)  # the rest of the file survives


class NoKeychainTests(unittest.TestCase):
    """With no credential store, refuse to persist rather than write plaintext."""

    def setUp(self):
        patcher = mock.patch.multiple(
            config.secrets_store,
            available=lambda: False,
            get=lambda key: "",
            set=lambda key, value: False,
            delete=lambda key: False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saving_does_not_fall_back_to_the_config_file(self):
        with isolated_config() as path:
            config.save(dict(config.DEFAULTS, telegram_bot_token="123456:SECRET"))
            written = path.read_text(encoding="utf-8")
        self.assertNotIn("SECRET", written)

    def test_saving_reports_that_the_secret_was_not_stored(self):
        with isolated_config():
            unstored = config.save_secrets(dict(config.DEFAULTS, telegram_bot_token="x"))
        self.assertEqual(unstored, ("telegram_bot_token",))

    def test_nothing_to_store_reports_nothing_unstored(self):
        with isolated_config():
            self.assertEqual(config.save_secrets(dict(config.DEFAULTS)), ())


class LanguagePersistenceTests(unittest.TestCase):
    def test_a_saved_language_is_read_back(self):
        with isolated_config():
            cfg = dict(config.DEFAULTS, language="zh-TW")
            config.save(cfg)
            self.assertEqual(config.load()["language"], "zh-TW")

    def test_an_invalid_stored_language_falls_back_to_the_default(self):
        with isolated_config() as path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"language": "klingon"}), encoding="utf-8")
            self.assertEqual(config.load()["language"], "auto")


if __name__ == "__main__":
    unittest.main()
