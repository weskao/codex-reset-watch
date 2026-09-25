import io
import unittest
import unittest.mock as mock

from codex_reset_watch import config, keys, ui


class WidthTests(unittest.TestCase):
    def test_ascii_is_one_column_per_char(self):
        self.assertEqual(ui.width("abc"), 3)

    def test_cjk_is_two_columns_per_char(self):
        self.assertEqual(ui.width("每日"), 4)

    def test_mixed_text(self):
        self.assertEqual(ui.width("a每b"), 4)


class PaintTests(unittest.TestCase):
    def test_disabled_paint_has_no_escape_codes(self):
        p = ui.Paint(False)
        self.assertEqual(p.accent, "")
        self.assertEqual(p.bold, "")

    def test_enabled_paint_uses_real_codes(self):
        p = ui.Paint(True)
        self.assertEqual(p.accent, ui.ACCENT)
        self.assertTrue(p.reset)

    def test_colour_enabled_respects_no_color_env(self):
        import os
        old = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertFalse(ui.colour_enabled(io.StringIO()))
        finally:
            if old is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = old

    def test_colour_enabled_false_for_non_tty_stream(self):
        self.assertFalse(ui.colour_enabled(io.StringIO()))

    def test_colour_enabled_arms_windows_vt_processing_on_a_real_console(self):
        import os
        tty_stream = mock.Mock(isatty=lambda: True)
        # Hosts that export NO_COLOR=1 (CI agents, some shells) must not
        # bleed into the positive-path assertion — only the dedicated
        # NO_COLOR test covers that gate.
        with mock.patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            with mock.patch.object(ui.keys, "IS_WINDOWS", True), \
                 mock.patch.object(ui, "_enable_windows_vt") as enable:
                self.assertTrue(ui.colour_enabled(tty_stream))
                enable.assert_called_once()

    def test_colour_enabled_skips_windows_vt_processing_elsewhere(self):
        import os
        tty_stream = mock.Mock(isatty=lambda: True)
        with mock.patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            with mock.patch.object(ui.keys, "IS_WINDOWS", False), \
                 mock.patch.object(ui, "_enable_windows_vt") as enable:
                self.assertTrue(ui.colour_enabled(tty_stream))
                enable.assert_not_called()

    def test_enable_windows_vt_processing_is_best_effort_off_windows(self):
        # ctypes.windll does not exist here; the call must swallow that, not raise.
        ui._enable_windows_vt.cache_clear()
        ui._enable_windows_vt()


class RenderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.plain = ui.Paint(False)
        self.cfg = dict(config.DEFAULTS)

    def test_plain_render_has_no_escape_codes(self):
        text = ui.render_settings(self.cfg, paint=self.plain, lang="en")
        self.assertNotIn("\033", text)

    def test_every_setting_label_appears(self):
        text = ui.render_settings(self.cfg, paint=self.plain, lang="en")
        for setting in config.SETTINGS:
            self.assertIn(config.label(setting, "en"), text)

    def test_current_values_are_rendered(self):
        self.cfg["scan_interval_minutes"] = 45
        text = ui.render_settings(self.cfg, paint=self.plain, lang="en")
        self.assertIn("45 minutes", text)

    def test_group_headings_appear_once_each(self):
        # A group name can also appear inside a row's own label or help text
        # ("API" inside "API base"), so match the heading's own glyph prefix
        # rather than counting a bare substring.
        text = ui.render_settings(self.cfg, paint=self.plain, lang="en")
        for group in config.GROUPS:
            heading = f"{ui.GLYPH_GROUP} {config.group_label(group, 'en')}"
            self.assertEqual(text.count(heading), 1, heading)

    def test_headings_follow_the_requested_language(self):
        text = ui.render_settings(self.cfg, paint=self.plain, lang="zh-TW")
        self.assertIn(f"{ui.GLYPH_GROUP} 排程", text)


class SummaryLineTests(unittest.TestCase):
    def test_shows_daily_time_and_interval(self):
        cfg = dict(config.DEFAULTS)
        line = ui.summary_line(cfg, paint=ui.Paint(False))
        self.assertIn("10:00", line)
        self.assertIn("2 hours", line)

    def test_shows_disabled_jobs(self):
        cfg = dict(config.DEFAULTS, daily_enabled=False, monitor_enabled=False)
        line = ui.summary_line(cfg, paint=ui.Paint(False))
        self.assertIn("Daily off", line)
        self.assertIn("Scan off", line)

    def test_disabled_jobs_in_chinese(self):
        cfg = dict(config.DEFAULTS, daily_enabled=False, monitor_enabled=False)
        line = ui.summary_line(cfg, paint=ui.Paint(False), lang="zh-TW")
        self.assertIn("每日 關閉", line)
        self.assertIn("掃描 關閉", line)


class EditTests(unittest.TestCase):
    def setUp(self):
        self.paint = ui.Paint(False)

    def test_bool_toggles_without_reading_input(self):
        cfg = dict(config.DEFAULTS)
        out = io.StringIO()
        changed = ui._edit(config.BY_KEY["daily_enabled"], cfg, self.paint, io.StringIO(""), out)
        self.assertTrue(changed)
        self.assertFalse(cfg["daily_enabled"])

    def test_valid_value_is_applied(self):
        cfg = dict(config.DEFAULTS)
        out = io.StringIO()
        changed = ui._edit(config.BY_KEY["scan_interval_minutes"], cfg, self.paint, io.StringIO("45m\n"), out)
        self.assertTrue(changed)
        self.assertEqual(cfg["scan_interval_minutes"], 45)

    def test_invalid_value_is_rejected_and_reported(self):
        cfg = dict(config.DEFAULTS)
        out = io.StringIO()
        changed = ui._edit(config.BY_KEY["scan_interval_minutes"], cfg, self.paint, io.StringIO("2 weeks\n"), out)
        self.assertFalse(changed)
        self.assertEqual(cfg["scan_interval_minutes"], config.DEFAULTS["scan_interval_minutes"])
        self.assertIn("✗", out.getvalue())

    def test_blank_input_keeps_value_unchanged(self):
        cfg = dict(config.DEFAULTS)
        out = io.StringIO()
        changed = ui._edit(config.BY_KEY["scan_interval_minutes"], cfg, self.paint, io.StringIO("\n"), out)
        self.assertFalse(changed)

    def test_eof_on_stdin_keeps_value_unchanged(self):
        cfg = dict(config.DEFAULTS)
        out = io.StringIO()
        changed = ui._edit(config.BY_KEY["scan_interval_minutes"], cfg, self.paint, io.StringIO(""), out)
        self.assertFalse(changed)


class ConfigMenuTests(unittest.TestCase):
    """Exercises the menu loop end-to-end against an isolated config file.

    ``scheduler.apply`` is mocked in every test here: a schedule-relevant
    change makes the menu re-apply the OS schedule on quit BY DESIGN (that's
    the whole point of `crw config`), and `scheduler.apply` shells out to the
    real launchctl/systemctl/schtasks — a unit test must never do that to
    whatever this machine actually has registered.
    """

    def setUp(self):
        import os
        import pathlib
        import tempfile
        import unittest.mock as mock

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg_path = pathlib.Path(self._tmp.name) / "config.json"
        self._old_config = os.environ.get("CRW_CONFIG")
        os.environ["CRW_CONFIG"] = str(self.cfg_path)
        self.addCleanup(self._restore_env)
        patcher = mock.patch("codex_reset_watch.ui.scheduler.apply", return_value=("launchd", ()))
        self.mock_apply = patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_env(self):
        import os
        if self._old_config is None:
            os.environ.pop("CRW_CONFIG", None)
        else:
            os.environ["CRW_CONFIG"] = self._old_config

    def test_quit_immediately_makes_no_changes(self):
        out = io.StringIO()
        code = ui.config_menu(io.StringIO("q\n"), out)
        self.assertEqual(code, 0)
        self.assertFalse(self.cfg_path.exists())

    def test_toggling_a_bool_row_saves_immediately(self):
        # Row 1 is daily_enabled (a toggle: no value prompt), then quit.
        out = io.StringIO()
        ui.config_menu(io.StringIO("1\nq\n"), out)
        self.assertTrue(self.cfg_path.exists())
        saved = config.load()
        self.assertFalse(saved["daily_enabled"])

    def test_schedule_relevant_change_reapplies_on_quit_via_the_mock_only(self):
        out = io.StringIO()
        ui.config_menu(io.StringIO("1\nq\n"), out)  # daily_enabled is a SCHEDULE_KEYS member
        self.mock_apply.assert_called_once()
        self.assertIn("✓ Re-applied the launchd schedule", out.getvalue())

    def test_the_reapply_notice_follows_the_configured_language(self):
        import os
        with mock.patch.dict(os.environ, {"CRW_LANG": "zh-TW"}):
            out = io.StringIO()
            ui.config_menu(io.StringIO("1\nq\n"), out)
        self.assertIn("✓ 已重新套用 launchd 排程", out.getvalue())

    def test_non_schedule_change_does_not_reapply(self):
        out = io.StringIO()
        # Row 6 (notify_new_reset_events) is a bool but not a SCHEDULE_KEYS member.
        ui.config_menu(io.StringIO("6\nq\n"), out)
        self.mock_apply.assert_not_called()

    def test_invalid_choice_reports_error_and_continues(self):
        out = io.StringIO()
        code = ui.config_menu(io.StringIO("999\nq\n"), out)
        self.assertEqual(code, 0)
        self.assertIn("✗", out.getvalue())

    def test_restore_defaults_resets_and_saves(self):
        out = io.StringIO()
        ui.config_menu(io.StringIO("1\nd\nq\n"), out)  # toggle, then restore defaults
        saved = config.load()
        self.assertEqual(saved, config.DEFAULTS)


class UpdatePromptTests(unittest.TestCase):
    """The interactive update prompt — the settings menu's own cursor
    (▸, the accent colour, the selected-row band), driven by a fake key
    source exactly like RunMenuTests drives run_menu."""

    def _pick(self, events):
        source = iter(events)
        out = io.StringIO()
        answer = ui.update_prompt("0.11.0", "v0.12.0", read=lambda: next(source), out=out)
        return answer, out.getvalue()

    def test_enter_on_the_first_row_is_update_now(self):
        answer, _ = self._pick([keys.KeyEvent(keys.Key.ENTER)])
        self.assertEqual(answer, ui.UPDATE_NOW)

    def test_down_moves_to_skip(self):
        answer, _ = self._pick([keys.KeyEvent(keys.Key.DOWN), keys.KeyEvent(keys.Key.ENTER)])
        self.assertEqual(answer, ui.SKIP)

    def test_up_from_the_top_wraps_to_skip_version(self):
        answer, _ = self._pick([keys.KeyEvent(keys.Key.UP), keys.KeyEvent(keys.Key.ENTER)])
        self.assertEqual(answer, ui.SKIP_VERSION)

    def test_q_backs_out_as_skip(self):
        answer, _ = self._pick([keys.KeyEvent(keys.Key.CHAR, "q")])
        self.assertEqual(answer, ui.SKIP)

    def test_ctrl_c_backs_out_as_skip(self):
        answer, _ = self._pick([keys.KeyEvent(keys.Key.CTRL_C)])
        self.assertEqual(answer, ui.SKIP)

    def test_an_exhausted_key_source_backs_out_as_skip(self):
        answer, _ = self._pick([])
        self.assertEqual(answer, ui.SKIP)

    def test_shows_versions_and_the_projects_own_cursor_glyph(self):
        _, text = self._pick([keys.KeyEvent(keys.Key.ENTER)])
        plain = ui.strip_ansi(text)
        self.assertIn("0.12.0", plain)
        self.assertIn("0.11.0", plain)
        self.assertIn(f"{ui.GLYPH_CURSOR} 1)", plain)


if __name__ == "__main__":
    unittest.main()
