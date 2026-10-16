"""Update check: TTY + config gate, the interactive prompt, skip-version, `update` wiring."""
import contextlib
import importlib
import io
import json
import os
import pathlib
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import codex_reset_watch as crw
from codex_reset_watch import update_check as uc


class _Tty(io.StringIO):
    def isatty(self):
        return True


class HintTests(unittest.TestCase):
    """Off a keyboard terminal (stdin/stdout redirected, stderr still a TTY) the
    prompt cannot be answered, so the result stays the flat stderr hint."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json"),
               "CRW_LANG": "en"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hint(self, stream, latest="v9.9.0"):
        with mock.patch.object(uc.sys, "stderr", stream), \
                mock.patch.object(uc.sys, "argv", ["crw"]), \
                mock.patch.object(uc.ui, "package_version", return_value="0.4.2"), \
                mock.patch.object(uc.update_kit, "check", return_value=latest) as check, \
                mock.patch.object(uc.keys, "is_interactive_tty", return_value=False), \
                mock.patch.object(uc, "_check", None), mock.patch.object(uc, "_latest", []):
            uc.start_check()
            uc.maybe_hint()
        return stream.getvalue(), check

    def test_hint_on_a_tty(self):
        out, check = self._hint(_Tty())
        self.assertIn("codex-reset-watch 9.9.0 is available (you have 0.4.2)", out)
        self.assertIn("  crw update\n", out)
        self.assertIn("Release notes: https://github.com/weskao/codex-reset-watch/releases/tag/v9.9.0", out)
        self.assertEqual(check.call_args.kwargs["repo"], "weskao/codex-reset-watch")
        self.assertEqual(check.call_args.kwargs["current"], "0.4.2")

    def test_no_hint_and_no_request_without_a_tty(self):
        out, check = self._hint(io.StringIO())
        self.assertEqual(out, "")
        check.assert_not_called()

    def test_no_hint_when_turned_off(self):
        (self.tmp / "config.json").write_text('{"update_check": false}')
        out, check = self._hint(_Tty())
        self.assertEqual(out, "")
        check.assert_not_called()

    def test_up_to_date_is_silent(self):
        self.assertEqual(self._hint(_Tty(), latest=None)[0], "")

    def test_hint_without_start_is_silent(self):
        stream = _Tty()
        with mock.patch.object(uc.sys, "stderr", stream), mock.patch.object(uc, "_check", None):
            uc.maybe_hint()
        self.assertEqual(stream.getvalue(), "")


class SkipVersionTests(unittest.TestCase):
    """A skipped release stays silent until a newer one ships."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json"),
               "CRW_LANG": "en"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _report(self, latest):
        stream = _Tty()
        with mock.patch.object(uc.sys, "stderr", stream), \
                mock.patch.object(uc.ui, "package_version", return_value="0.4.2"), \
                mock.patch.object(uc.update_kit, "check", return_value=latest), \
                mock.patch.object(uc.keys, "is_interactive_tty", return_value=False), \
                mock.patch.object(uc, "_check", None), mock.patch.object(uc, "_latest", []):
            uc.start_check()
            uc.maybe_hint()
        return stream.getvalue()

    def test_a_skipped_release_stays_silent(self):
        uc.skip_version("v0.5.0")
        self.assertEqual(self._report("v0.5.0"), "")

    def test_a_release_newer_than_the_skipped_one_is_reported(self):
        uc.skip_version("v0.5.0")
        self.assertIn("v0.6.0", self._report("v0.6.0"))

    def test_skipping_keeps_the_other_entries_of_the_state_file(self):
        cache = self.tmp / "update-check.json"
        cache.write_text(json.dumps({"other": 1}), encoding="utf-8")
        uc.skip_version("v0.5.0")
        self.assertEqual(json.loads(cache.read_text(encoding="utf-8")),
                         {"other": 1, "skipped": "v0.5.0"})


class MaybeHintInteractiveTests(unittest.TestCase):
    """On a keyboard-capable terminal the result is the settings menu's own
    prompt: Update now / Skip / Skip until next version."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        env = {"CRW_STATE_DIR": str(self.tmp), "CRW_CONFIG": str(self.tmp / "config.json"),
               "CRW_LANG": "en"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = self.tmp / "update-check.json"

    def _run(self, answer, code=0):
        with mock.patch.object(uc.sys, "stderr", _Tty()), \
                mock.patch.object(uc.sys, "argv", ["crw"]), \
                mock.patch.object(uc.ui, "package_version", return_value="0.4.2"), \
                mock.patch.object(uc.update_kit, "check", return_value="v9.9.0"), \
                mock.patch.object(uc, "_check", None), mock.patch.object(uc, "_latest", []), \
                mock.patch.object(uc.keys, "is_interactive_tty", return_value=True), \
                mock.patch.object(uc.ui, "update_prompt", return_value=answer) as prompt, \
                mock.patch.object(uc.update_kit, "update", return_value=code) as update:
            uc.start_check()
            uc.maybe_hint()
        return prompt, update

    def test_the_prompt_gets_the_versions_and_the_release_notes_link(self):
        prompt, _ = self._run(uc.ui.SKIP)
        prompt.assert_called_once_with(
            "0.4.2", "v9.9.0",
            release_url="https://github.com/weskao/codex-reset-watch/releases/tag/v9.9.0",
        )

    def test_update_now_installs_the_newest_tag_then_reapplies_the_schedule(self):
        _, update = self._run(uc.ui.UPDATE_NOW)
        update.assert_called_once()
        kw = update.call_args.kwargs
        self.assertEqual((kw["app"], kw["repo"], kw["dist"], kw["lang"]),
                         ("crw", "weskao/codex-reset-watch", "codex-reset-watch", "en"))
        self.assertEqual(kw["after"], [["crw", "apply-schedule"]])

    def test_a_failed_update_never_raises(self):
        self._run(uc.ui.UPDATE_NOW, code=1)  # update_kit reports the failure itself

    def test_skip_installs_nothing_and_asks_again_next_run(self):
        _, update = self._run(uc.ui.SKIP)
        update.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_skip_version_is_remembered(self):
        _, update = self._run(uc.ui.SKIP_VERSION)
        update.assert_not_called()
        self.assertEqual(json.loads(self.cache.read_text(encoding="utf-8"))["skipped"], "v9.9.0")


class UpdateCommandTests(unittest.TestCase):
    def _run(self, argv, code=0, prog="crw"):
        with mock.patch.object(uc.update_kit, "update", return_value=code) as update, \
                mock.patch.object(crw.update_check, "start_check") as start, \
                mock.patch.object(crw.sys, "argv", [prog]), \
                mock.patch.dict(os.environ, {"CRW_LANG": "zh-TW"}):
            result = crw.main(argv)
        return result, update, start

    def test_update_and_dash_dash_update_call_update_kit_and_return_its_code(self):
        for argv in (["update"], ["--update"]):
            with self.subTest(argv=argv):
                result, update, start = self._run(argv, code=2)
                self.assertEqual(result, 2)
                kw = update.call_args.kwargs
                self.assertEqual((kw["app"], kw["repo"], kw["dist"], kw["lang"]),
                                 ("crw", "weskao/codex-reset-watch", "codex-reset-watch", "zh-TW"))
                self.assertIs(kw["out"], crw.sys.stderr)
                start.assert_not_called()  # no "update available" hint right after updating

    def test_app_is_the_invoked_program_name(self):
        _, update, _ = self._run(["update"], prog="/x/bin/codex-reset-watch")
        self.assertEqual(update.call_args.kwargs["app"], "codex-reset-watch")

    def test_update_no_longer_means_check(self):
        with mock.patch.object(crw, "run_check") as run_check:
            self._run(["update"])
        run_check.assert_not_called()

    def test_update_reapplies_the_schedule_with_the_new_binary(self):
        _, update, _ = self._run(["update"])
        self.assertEqual(update.call_args.kwargs["after"], [["crw", "apply-schedule"]])

    def test_help_lists_update(self):
        text = crw.build_parser().format_help()
        self.assertRegex(text, r"\bupdate\b")


def _console_scripts():
    """``[project.scripts]`` from pyproject.toml: name → ``module:func``."""
    text = (pathlib.Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    return dict(re.findall(r'^([\w-]+)\s*=\s*"([^"]+)"', section, re.M))


class EveryEntryPointChecksTests(unittest.TestCase):
    """Every console script runs the update check on every way out — --help,
    --version, no args, a typo. A new script that skips the flow fails here."""

    def test_every_script_starts_and_reports_the_check(self):
        scripts = _console_scripts()
        self.assertIn("crw", scripts)
        for name, target in scripts.items():
            module, func = target.split(":")
            main = getattr(importlib.import_module(module), func)
            for argv in (["--help"], ["--version"], [], ["bogus"], ["doctor", "--help"]):
                with self.subTest(script=name, argv=argv), \
                        mock.patch.object(uc, "start_check") as start, \
                        mock.patch.object(uc, "maybe_hint") as hint, \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    with contextlib.suppress(SystemExit):
                        main(argv)
                    start.assert_called_once()
                    hint.assert_called_once()


if __name__ == "__main__":
    unittest.main()
