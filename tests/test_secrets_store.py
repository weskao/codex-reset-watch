"""The OS-keychain secret store.

No test touches a real keychain: every one replaces the single subprocess
funnel (``secrets_store._run``) and the platform probe, so the macOS, Linux and
Windows paths are all exercised on whatever machine runs the suite.
"""
import unittest
import unittest.mock as mock

from codex_reset_watch import secrets_store as store


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
    return mock.patch.multiple(store, backend=lambda: name, _run=run)


class BackendDetectionTests(unittest.TestCase):
    def test_macos_uses_the_security_tool_when_present(self):
        with mock.patch.object(store, "IS_MACOS", True), \
                mock.patch.object(store, "IS_WINDOWS", False), \
                mock.patch("shutil.which", return_value="/usr/bin/security"):
            self.assertEqual(store._detect_backend(), "keychain")

    def test_linux_uses_secret_tool_when_present(self):
        with mock.patch.object(store, "IS_MACOS", False), \
                mock.patch.object(store, "IS_WINDOWS", False), \
                mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/secret-tool"
                           if n == "secret-tool" else None):
            self.assertEqual(store._detect_backend(), "libsecret")

    def test_windows_uses_dpapi(self):
        with mock.patch.object(store, "IS_MACOS", False), \
                mock.patch.object(store, "IS_WINDOWS", True), \
                mock.patch("shutil.which", return_value="powershell.exe"):
            self.assertEqual(store._detect_backend(), "dpapi")

    def test_no_tool_means_no_backend(self):
        with mock.patch.object(store, "IS_MACOS", False), \
                mock.patch.object(store, "IS_WINDOWS", False), \
                mock.patch("shutil.which", return_value=None):
            self.assertIsNone(store._detect_backend())

    def test_available_reflects_the_backend(self):
        with mock.patch.object(store, "backend", return_value=None):
            self.assertFalse(store.available())
        with mock.patch.object(store, "backend", return_value="keychain"):
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
        argv = run.calls[0][0]
        self.assertIn("add-generic-password", argv)
        self.assertIn("-U", argv)  # update in place rather than duplicate

    def test_the_secret_is_passed_on_stdin_not_in_the_command_line(self):
        run = Recorder((0, ""))
        with backend("keychain", run):
            store.set("telegram_bot_token", "super-secret")
        argv, stdin = run.calls[0]
        self.assertNotIn("super-secret", " ".join(argv))
        self.assertIn("super-secret", stdin or "")

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


if __name__ == "__main__":
    unittest.main()
