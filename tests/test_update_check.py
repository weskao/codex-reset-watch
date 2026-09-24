"""GitHub update hint: version compare, daily cache, silent failure, TTY gate."""
import io
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest import mock

from codex_reset_watch import update_check as uc


class _Tty(io.StringIO):
    def isatty(self):
        return True


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = self.tmp / "update-check.json"

    def test_version_tuple(self):
        self.assertEqual(uc.version_tuple("v0.4.2"), (0, 4, 2))
        self.assertGreater(uc.version_tuple("0.10.0"), uc.version_tuple("v0.9.9"))
        self.assertIsNone(uc.version_tuple("dev"))

    def test_newer_release_is_found_then_served_from_cache(self):
        calls = []

        def fetch():
            calls.append(1)
            return "v0.5.0"

        self.assertEqual(uc.newer_release("0.4.2", now=1000, fetch=fetch), "v0.5.0")
        self.assertEqual(uc.newer_release("0.4.2", now=2000, fetch=fetch), "v0.5.0")
        self.assertEqual(calls, [1])

    def test_same_release_is_no_hint(self):
        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.4.2"))

    def test_offline_is_silent_and_retried_only_after_a_day(self):
        calls = []

        def fetch():
            calls.append(1)
            raise OSError("offline")

        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=fetch))
        self.assertIsNone(uc.newer_release("0.4.2", now=10, fetch=fetch))
        self.assertEqual(calls, [1])

    def _hint(self, stream):
        self.cache.write_text(json.dumps({"checked_at": time.time(), "latest": "v9.9.0"}))
        with mock.patch.object(uc.sys, "stderr", stream), \
                mock.patch.object(uc.ui, "package_version", return_value="0.4.2"):
            uc.maybe_hint()
        return stream.getvalue()

    def test_hint_on_a_tty(self):
        out = self._hint(_Tty())
        self.assertIn("9.9.0", out)
        self.assertIn("git+https://github.com/weskao/codex-reset-watch.git@v9.9.0 codex-reset-watch && crw apply-schedule", out)

    def test_no_hint_without_a_tty(self):
        self.assertEqual(self._hint(io.StringIO()), "")

    def test_no_hint_when_turned_off(self):
        (self.tmp / "config.json").write_text('{"update_check": false}')
        self.assertEqual(self._hint(_Tty()), "")


if __name__ == "__main__":
    unittest.main()
