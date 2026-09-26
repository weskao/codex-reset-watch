"""The OS-keychain secret store.

Almost no test touches a real keychain: nearly every one replaces the single
subprocess funnel (``telegram_kit._run``) and the platform probe, so the
macOS, Linux and Windows paths are all exercised on whatever machine runs the
suite.

``RoundTripTests`` is the deliberate exception, and it exists because mocking
that funnel is exactly what hid a real bug: ``add-generic-password -w`` never
reads stdin — it prompts /dev/tty — so the old write stored an empty item and
still exited 0. Every mocked assertion passed while the feature was inert. A
contract test cannot catch a tool ignoring the contract; only a real write
followed by a real read can.
"""
import unittest
import unittest.mock as mock
import pathlib
import tempfile
import sys

import telegram_kit
from codex_reset_watch import secrets_store as store
from tests import real_credential_store


class Recorder:
    """Stands in for ``_run``: records argv, replays queued results."""

    def __init__(self, *results):
        self.calls = []
        self.results = list(results)

    def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        return self.results.pop(0) if self.results else (1, "")


def backend(name, run):
    """Pin the active store and the subprocess funnel.

    Patches ``backend`` itself rather than ``_detect_backend``: detection is
    memoized in production (it probes the filesystem once), so patching the
    probe alone would leak the first test's answer into every later one.
    """
    return mock.patch.multiple(telegram_kit, backend=lambda: name, _run=run)


class BackendDetectionTests(unittest.TestCase):
    def test_macos_uses_the_security_tool_when_present(self):
        with mock.patch.object(telegram_kit, "IS_MACOS", True), \
                mock.patch.object(telegram_kit, "IS_WINDOWS", False), \
                mock.patch("shutil.which", return_value="/usr/bin/security"):
            self.assertEqual(telegram_kit._detect_backend(), "keychain")

    def test_linux_uses_secret_tool_when_present(self):
        with mock.patch.object(telegram_kit, "IS_MACOS", False), \
                mock.patch.object(telegram_kit, "IS_WINDOWS", False), \
                mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/secret-tool"
                           if n == "secret-tool" else None):
            self.assertEqual(telegram_kit._detect_backend(), "libsecret")

    def test_windows_uses_dpapi(self):
        with mock.patch.object(telegram_kit, "IS_MACOS", False), \
                mock.patch.object(telegram_kit, "IS_WINDOWS", True), \
                mock.patch("shutil.which", return_value="powershell.exe"):
            self.assertEqual(telegram_kit._detect_backend(), "dpapi")

    def test_no_tool_means_no_backend(self):
        with mock.patch.object(telegram_kit, "IS_MACOS", False), \
                mock.patch.object(telegram_kit, "IS_WINDOWS", False), \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(telegram_kit._detect_backend())

    def test_available_reflects_the_backend(self):
        with mock.patch.object(telegram_kit, "backend", return_value=None):
            self.assertFalse(store.available())
        with mock.patch.object(telegram_kit, "backend", return_value="keychain"):
            self.assertTrue(store.available())


class KeychainTests(unittest.TestCase):
    def test_get_returns_the_stored_value(self):
        run = Recorder((0, "the-token\n"))
        with backend("keychain", run):
            self.assertEqual(store.get("telegram_bot_token"), "the-token")
        self.assertIn("find-generic-password", run.calls[0][0])

    def test_get_returns_empty_when_the_item_is_missing(self):
        with backend("keychain", Recorder((44, ""))):
            self.assertEqual(store.get("telegram_bot_token"), "")

    def test_set_stores_the_value_and_reports_success(self):
        run = Recorder((0, ""))
        with backend("keychain", run):
            self.assertTrue(store.set("telegram_bot_token", "abc"))
        argv, stdin = run.calls[0]
        # Batch mode, never `add-generic-password -w`: that flag prompts
        # /dev/tty instead of reading stdin, storing an empty item and exiting 0.
        self.assertEqual(argv, ["security", "-i"])
        self.assertIn("add-generic-password", stdin)
        self.assertIn("-U", stdin)  # update in place rather than duplicate
        self.assertIn("-X", stdin)  # hex-encoded payload, not a -w tty prompt

    def test_the_secret_is_passed_on_stdin_not_in_the_command_line(self):
        run = Recorder((0, ""))
        with backend("keychain", run):
            store.set("telegram_bot_token", "super-secret")
        argv, stdin = run.calls[0]
        self.assertNotIn("super-secret", " ".join(argv))
        # -X hex-encodes it, so the plaintext is not even in the stdin stream.
        self.assertNotIn("super-secret", stdin)
        self.assertIn("super-secret".encode().hex(), stdin)

    def test_a_newline_in_an_identifier_is_refused_not_injected(self):
        with backend("keychain", Recorder((0, ""))):
            with self.assertRaises(ValueError):
                telegram_kit._batch_quote("svc\nadd-generic-password -s evil")

    def test_delete_removes_the_item(self):
        run = Recorder((0, ""))
        with backend("keychain", run):
            self.assertTrue(store.delete("telegram_bot_token"))
        self.assertIn("delete-generic-password", run.calls[0][0])

    def test_setting_an_empty_value_deletes_instead_of_storing_a_blank(self):
        run = Recorder((0, ""))
        with backend("keychain", run):
            store.set("telegram_bot_token", "")
        self.assertIn("delete-generic-password", run.calls[0][0])


class LibsecretTests(unittest.TestCase):
    def test_get_uses_secret_tool_lookup(self):
        run = Recorder((0, "linux-token"))
        with backend("libsecret", run):
            self.assertEqual(store.get("telegram_bot_token"), "linux-token")
        self.assertEqual(run.calls[0][0][:2], ["secret-tool", "lookup"])

    def test_set_passes_the_secret_on_stdin(self):
        run = Recorder((0, ""))
        with backend("libsecret", run):
            store.set("telegram_bot_token", "linux-secret")
        argv, stdin = run.calls[0]
        self.assertEqual(argv[:2], ["secret-tool", "store"])
        self.assertNotIn("linux-secret", " ".join(argv))
        self.assertEqual(stdin, "linux-secret")


class DpapiTests(unittest.TestCase):
    def test_legacy_windows_registry_token_is_detected_without_printing_it(self):
        registry = mock.MagicMock(HKEY_CURRENT_USER=object())
        registry.OpenKey.return_value.__enter__.return_value = object()
        registry.QueryValueEx.return_value = ("fake-token", 1)
        with mock.patch.object(telegram_kit, "IS_WINDOWS", True), mock.patch.dict(sys.modules, {"winreg": registry}):
            self.assertTrue(store.legacy_windows_env_token_present())

    def test_encrypted_file_is_written_privately_without_path_in_command(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "token.dpapi"
            run = Recorder((0, "synthetic-ciphertext\n"))
            with backend("dpapi", run), mock.patch.object(store._store, "_dpapi_path", return_value=path):
                self.assertTrue(store.set("telegram_bot_token", "fake-token"))
            self.assertEqual(path.read_text(), "synthetic-ciphertext")
            self.assertNotIn("fake-token", " ".join(run.calls[0][0]))
            self.assertNotIn(str(path), " ".join(run.calls[0][0]))

    def test_decrypt_reads_ciphertext_from_stdin_without_path_in_command(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "token.dpapi"
            path.write_text("synthetic-ciphertext")
            run = Recorder((0, "fake-token\n"))
            with backend("dpapi", run), mock.patch.object(store._store, "_dpapi_path", return_value=path):
                self.assertEqual(store.get("telegram_bot_token"), "fake-token")
            self.assertEqual(run.calls[0][1], "synthetic-ciphertext")
            self.assertNotIn(str(path), " ".join(run.calls[0][0]))

    def test_account_cannot_escape_dpapi_directory(self):
        with self.assertRaises(ValueError):
            store._store._dpapi_path("../elsewhere")


class NoBackendTests(unittest.TestCase):
    def test_get_is_empty_without_a_backend(self):
        with backend(None, Recorder()):
            self.assertEqual(store.get("telegram_bot_token"), "")

    def test_set_reports_failure_rather_than_writing_plaintext(self):
        run = Recorder()
        with backend(None, run):
            self.assertFalse(store.set("telegram_bot_token", "abc"))
        self.assertEqual(run.calls, [])

    def test_delete_is_a_harmless_no_op(self):
        with backend(None, Recorder()):
            self.assertFalse(store.delete("telegram_bot_token"))


class FailureTests(unittest.TestCase):
    def test_a_crashing_helper_is_not_fatal(self):
        def boom(argv, stdin=None):
            raise OSError("no such tool")

        with backend("keychain", boom):
            self.assertEqual(store.get("telegram_bot_token"), "")
            self.assertFalse(store.set("telegram_bot_token", "abc"))

    def test_a_nonzero_exit_on_set_reports_failure(self):
        with backend("keychain", Recorder((1, "denied"))):
            self.assertFalse(store.set("telegram_bot_token", "abc"))

    def test_trailing_newline_is_stripped_from_a_read_secret(self):
        with backend("keychain", Recorder((0, "token-with-newline\n"))):
            self.assertEqual(store.get("k"), "token-with-newline")


class RoundTripTests(unittest.TestCase):
    """Against the machine's real credential store — see the module docstring.

    The suite-wide fence in ``tests/__init__.py`` keeps every *other* module
    away from that store; these two lift it deliberately, for their own probe
    account, and never for the real ``telegram_bot_token`` item.
    """

    PROBE_KEY = "_roundtrip_probe"

    def setUp(self):
        fence = real_credential_store()
        fence.__enter__()
        self.addCleanup(fence.__exit__, None, None, None)
        if not store.available():
            self.skipTest("no credential store on this machine")
        self.addCleanup(store.delete, self.PROBE_KEY)

    def test_a_stored_secret_reads_back_identical(self):
        secret = "sentinel:AAH-x_9/+aB=" * 2  # ':' and '/' exercise the encoding
        if not store.set(self.PROBE_KEY, secret):
            self.skipTest("credential store present but refused the write")
        # The assertion that matters: set() returning True must mean the secret
        # is actually retrievable, not merely that the helper exited 0.
        self.assertEqual(store.get(self.PROBE_KEY), secret)

    def test_deleting_leaves_nothing_behind(self):
        if not store.set(self.PROBE_KEY, "to-be-removed"):
            self.skipTest("credential store present but refused the write")
        store.delete(self.PROBE_KEY)
        self.assertEqual(store.get(self.PROBE_KEY), "")


if __name__ == "__main__":
    unittest.main()
