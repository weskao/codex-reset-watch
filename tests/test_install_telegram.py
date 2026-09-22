"""The installer's first-run Telegram setup prompt.

No test calls the real ``getpass.getpass`` (which insists on a real tty) or
touches this machine's real keychain — ``ask``/``ask_secret``/the secret store
are all injected, matching the module's existing dependency-injection style
(see ``run_menu``'s ``read=`` parameter for the same pattern).
"""
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
import unittest.mock as mock

from codex_reset_watch import config
from scripts import install


@contextlib.contextmanager
def isolated_config():
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "config.json"
        with mock.patch.dict(os.environ, {"CRW_CONFIG": str(path)}), \
                mock.patch.object(config.secrets_store, "available", return_value=True), \
                mock.patch.object(config.secrets_store, "get", return_value=""), \
                mock.patch.object(config.secrets_store, "set", return_value=True), \
                mock.patch.object(config.secrets_store, "delete", return_value=True):
            yield path


class AlreadyConfiguredTests(unittest.TestCase):
    def test_stored_credentials_count_as_configured(self):
        self.assertTrue(install.telegram_already_configured(
            {"telegram_bot_token": "x", "telegram_chat_id": "42"}))

    def test_a_missing_chat_id_is_not_configured(self):
        self.assertFalse(install.telegram_already_configured({"telegram_bot_token": "x"}))

    def test_environment_credentials_count_as_configured(self):
        with mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "e", "TG_CHAT_ID": "1"}):
            self.assertTrue(install.telegram_already_configured({}))

    def test_nothing_anywhere_is_not_configured(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TG_BOT_TOKEN", "TG_CHAT_ID")}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(install.telegram_already_configured({}))


class PromptSetupTests(unittest.TestCase):
    def test_declining_stores_nothing(self):
        with isolated_config():
            out = io.StringIO()
            configured = install.prompt_telegram_setup(
                dict(config.DEFAULTS), ask=lambda p: "n", ask_secret=lambda p: "", out=out)
        self.assertFalse(configured)
        self.assertIn("crw config", out.getvalue())

    def test_accepting_stores_chat_id_and_token(self):
        answers = iter(["y", "-1001234567890"])
        with isolated_config():
            cfg = dict(config.DEFAULTS)
            configured = install.prompt_telegram_setup(
                cfg, ask=lambda p: next(answers), ask_secret=lambda p: "123:ABC", out=io.StringIO())
            self.assertTrue(configured)
            self.assertEqual(config.load()["telegram_chat_id"], "-1001234567890")

    def test_the_token_is_never_written_to_the_config_file(self):
        answers = iter(["y", "-1001234567890"])
        with isolated_config() as path:
            install.prompt_telegram_setup(
                dict(config.DEFAULTS), ask=lambda p: next(answers),
                ask_secret=lambda p: "123456:REALTOKEN", out=io.StringIO())
            written = path.read_text(encoding="utf-8")
        self.assertNotIn("REALTOKEN", written)

    def test_an_empty_token_does_not_count_as_configured(self):
        answers = iter(["y", "-1001234567890"])
        with isolated_config():
            configured = install.prompt_telegram_setup(
                dict(config.DEFAULTS), ask=lambda p: next(answers),
                ask_secret=lambda p: "", out=io.StringIO())
        self.assertFalse(configured)

    def test_an_empty_chat_id_does_not_count_as_configured(self):
        answers = iter(["y", ""])
        with isolated_config():
            configured = install.prompt_telegram_setup(
                dict(config.DEFAULTS), ask=lambda p: next(answers),
                ask_secret=lambda p: "123:ABC", out=io.StringIO())
        self.assertFalse(configured)

    def test_a_bare_enter_defaults_to_yes(self):
        answers = iter(["", "-42"])
        with isolated_config():
            configured = install.prompt_telegram_setup(
                dict(config.DEFAULTS), ask=lambda p: next(answers),
                ask_secret=lambda p: "123:ABC", out=io.StringIO())
        self.assertTrue(configured)

    def test_the_prompt_never_asks_when_already_configured(self):
        # main() must not even call this when telegram_already_configured() is
        # true — covered end-to-end below.
        pass


class MainSkipsWhenNonInteractiveOrConfiguredTests(unittest.TestCase):
    def test_setup_is_skipped_on_a_non_interactive_stdin(self):
        asked = []
        with isolated_config(), mock.patch.object(install.sys.stdin, "isatty", return_value=False):
            install.maybe_setup_telegram(dict(config.DEFAULTS),
                                         ask=lambda p: asked.append(p) or "y",
                                         ask_secret=lambda p: "x", out=io.StringIO())
        self.assertEqual(asked, [])

    def test_setup_is_skipped_when_already_configured(self):
        asked = []
        with isolated_config(), mock.patch.dict(os.environ, {"TG_BOT_TOKEN": "e", "TG_CHAT_ID": "1"}), \
                mock.patch.object(install.sys.stdin, "isatty", return_value=True):
            install.maybe_setup_telegram(dict(config.DEFAULTS),
                                         ask=lambda p: asked.append(p) or "y",
                                         ask_secret=lambda p: "x", out=io.StringIO())
        self.assertEqual(asked, [])

    def test_setup_runs_on_an_interactive_unconfigured_stdin(self):
        answers = iter(["y", "42"])
        with isolated_config(), mock.patch.object(install.sys.stdin, "isatty", return_value=True):
            install.maybe_setup_telegram(dict(config.DEFAULTS),
                                         ask=lambda p: next(answers),
                                         ask_secret=lambda p: "123:ABC", out=io.StringIO())
            self.assertEqual(config.load()["telegram_chat_id"], "42")


if __name__ == "__main__":
    unittest.main()
