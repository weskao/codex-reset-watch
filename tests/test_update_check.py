"""GitHub update hint: version compare, short-TTL cache, background check, TTY gate."""
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
        self.assertEqual(uc.newer_release("0.4.2", now=1000 + uc.TTL_SECONDS - 1, fetch=fetch), "v0.5.0")
        self.assertEqual(calls, [1])

    def test_same_release_is_no_hint(self):
        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.4.2"))

    def test_offline_is_silent_and_retried_only_after_the_ttl(self):
        calls = []

        def fetch():
            calls.append(1)
            raise OSError("offline")

        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=fetch))
        self.assertIsNone(uc.newer_release("0.4.2", now=10, fetch=fetch))
        self.assertEqual(calls, [1])

    def test_new_release_seen_within_the_hour(self):
        # Several releases can land in one day: the next one is seen soon, not tomorrow.
        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.4.2"))
        self.assertEqual(
            uc.newer_release("0.4.2", now=uc.TTL_SECONDS, fetch=lambda: "v0.5.0"), "v0.5.0"
        )
        self.assertLessEqual(uc.TTL_SECONDS, 3600)

    def _hint(self, stream):
        self.cache.write_text(json.dumps({"checked_at": time.time(), "latest": "v9.9.0"}))
        with mock.patch.object(uc.sys, "stderr", stream), \
                mock.patch.object(uc.ui, "package_version", return_value="0.4.2"), \
                mock.patch.object(uc, "_check", None), \
                mock.patch.object(uc, "_latest", []):
            uc.start_check()
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

    def test_hint_without_start_is_silent(self):
        self.cache.write_text(json.dumps({"checked_at": time.time(), "latest": "v9.9.0"}))
        stream = _Tty()
        with mock.patch.object(uc.sys, "stderr", stream), mock.patch.object(uc, "_check", None):
            uc.maybe_hint()
        self.assertEqual(stream.getvalue(), "")


class SkipVersionTests(unittest.TestCase):
    """A skipped release stays silent (checked-at/latest are preserved by the
    skip, and vice versa) until a newer one ships."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_skipped_release_stays_silent(self):
        uc.skip_version("v0.5.0")
        self.assertIsNone(uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.5.0"))

    def test_a_release_newer_than_the_skipped_one_is_reported(self):
        uc.skip_version("v0.5.0")
        self.assertEqual(uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.6.0"), "v0.6.0")

    def test_a_refetch_keeps_the_skipped_version(self):
        uc.skip_version("v0.5.0")
        uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.5.0")
        cache = self.tmp / "update-check.json"
        self.assertEqual(json.loads(cache.read_text())["skipped"], "v0.5.0")

    def test_skipping_keeps_the_existing_cache_entries(self):
        uc.newer_release("0.4.2", now=0, fetch=lambda: "v0.5.0")
        uc.skip_version("v0.5.0")
        cache = self.tmp / "update-check.json"
        data = json.loads(cache.read_text())
        self.assertEqual(data["latest"], "v0.5.0")
        self.assertEqual(data["skipped"], "v0.5.0")


class MaybeHintInteractiveTests(unittest.TestCase):
    """On a keyboard-capable terminal, the flat hint becomes the settings
    menu's own prompt; off one (redirected stdin/stdout), the flat hint
    stays exactly as it was."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = self.tmp / "update-check.json"
        self.cache.write_text(json.dumps({"checked_at": time.time(), "latest": "v9.9.0"}))

    def _run(self, answer, run=None):
        with mock.patch.object(uc.sys, "stderr", _Tty()),                 mock.patch.object(uc.ui, "package_version", return_value="0.4.2"),                 mock.patch.object(uc, "_check", None), mock.patch.object(uc, "_latest", []),                 mock.patch.object(uc.keys, "is_interactive_tty", return_value=True),                 mock.patch.object(uc.ui, "update_prompt", return_value=answer) as prompt,                 mock.patch.object(uc.subprocess, "run", run or mock.Mock()) as run_mock:
            uc.start_check()
            uc.maybe_hint()
        return prompt, run_mock

    def test_update_now_runs_install_then_apply_schedule(self):
        done = mock.Mock(returncode=0)
        prompt, run_mock = self._run(uc.ui.UPDATE_NOW, run=mock.Mock(return_value=done))
        prompt.assert_called_once_with("0.4.2", "v9.9.0")
        self.assertEqual(
            run_mock.call_args_list[0].args[0],
            ["uv", "tool", "install", "--force", "--from",
             "git+https://github.com/weskao/codex-reset-watch.git@v9.9.0", "codex-reset-watch"],
        )
        self.assertEqual(run_mock.call_args_list[1].args[0], ["crw", "apply-schedule"])

    def test_skip_asks_again_next_run(self):
        self._run(uc.ui.SKIP)
        self.assertNotIn("skipped", json.loads(self.cache.read_text()))

    def test_skip_version_is_remembered(self):
        self._run(uc.ui.SKIP_VERSION)
        self.assertEqual(json.loads(self.cache.read_text())["skipped"], "v9.9.0")

    def test_a_failed_install_step_skips_apply_schedule(self):
        done = mock.Mock(returncode=1)
        _prompt, run_mock = self._run(uc.ui.UPDATE_NOW, run=mock.Mock(return_value=done))
        self.assertEqual(run_mock.call_count, 1)

    def test_off_a_keyboard_terminal_the_flat_hint_still_prints(self):
        stream = _Tty()
        with mock.patch.object(uc.sys, "stderr", stream),                 mock.patch.object(uc.ui, "package_version", return_value="0.4.2"),                 mock.patch.object(uc, "_check", None), mock.patch.object(uc, "_latest", []),                 mock.patch.object(uc.keys, "is_interactive_tty", return_value=False),                 mock.patch.object(uc.ui, "update_prompt") as prompt:
            uc.start_check()
            uc.maybe_hint()
        prompt.assert_not_called()
        self.assertIn("9.9.0", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
