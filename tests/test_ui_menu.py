"""The keyboard-driven settings menu.

``step`` is a pure function of (state, keypress) — every interaction test here
drives it directly with synthetic ``KeyEvent``s and never touches a terminal.
The thin shell around it (``run_menu``) is exercised separately with a fake key
source and a StringIO, so the redraw and the side effects are covered too.
"""
import io
import unittest
import unittest.mock as mock

from codex_reset_watch import config, keys, ui

SETTINGS = config.SETTINGS


def press(key, char=None):
    return keys.KeyEvent(key, char)


def char(c):
    return keys.KeyEvent(keys.Key.CHAR, c)


def fresh(**overrides):
    overrides.setdefault("values", dict(config.DEFAULTS))
    return ui.MenuState(**overrides)


class NavigationTests(unittest.TestCase):
    def test_down_moves_the_cursor(self):
        state = ui.step(fresh(), press(keys.Key.DOWN), SETTINGS)
        self.assertEqual(state.cursor, 1)

    def test_up_from_the_top_wraps_to_the_bottom(self):
        state = ui.step(fresh(), press(keys.Key.UP), SETTINGS)
        self.assertEqual(state.cursor, len(SETTINGS) - 1)

    def test_down_from_the_bottom_wraps_to_the_top(self):
        state = ui.step(fresh(cursor=len(SETTINGS) - 1), press(keys.Key.DOWN), SETTINGS)
        self.assertEqual(state.cursor, 0)

    def test_q_quits(self):
        self.assertTrue(ui.step(fresh(), char("q"), SETTINGS).quitting)

    def test_escape_quits_while_browsing(self):
        self.assertTrue(ui.step(fresh(), press(keys.Key.ESCAPE), SETTINGS).quitting)

    def test_ctrl_c_quits(self):
        self.assertTrue(ui.step(fresh(), press(keys.Key.CTRL_C), SETTINGS).quitting)

    def test_an_undecoded_key_changes_nothing(self):
        before = fresh(cursor=3)
        self.assertEqual(ui.step(before, press(keys.Key.UNKNOWN), SETTINGS), before)


class ToggleTests(unittest.TestCase):
    def test_right_toggles_a_bool_row(self):
        state = ui.step(fresh(), press(keys.Key.RIGHT), SETTINGS)  # row 0: daily_enabled
        self.assertFalse(state.values["daily_enabled"])
        self.assertTrue(state.pending_save)

    def test_left_toggles_back(self):
        state = ui.step(fresh(), press(keys.Key.RIGHT), SETTINGS)
        state = ui.step(state, press(keys.Key.LEFT), SETTINGS)
        self.assertTrue(state.values["daily_enabled"])

    def test_enter_on_a_bool_toggles_rather_than_opening_an_editor(self):
        state = ui.step(fresh(), press(keys.Key.ENTER), SETTINGS)
        self.assertFalse(state.editing)
        self.assertFalse(state.values["daily_enabled"])

    def test_a_choice_row_cycles_through_its_choices(self):
        index = SETTINGS.index(config.BY_KEY["language"])
        state = ui.step(fresh(cursor=index), press(keys.Key.RIGHT), SETTINGS)
        self.assertEqual(state.values["language"], "en")
        state = ui.step(state, press(keys.Key.RIGHT), SETTINGS)
        self.assertEqual(state.values["language"], "zh-TW")
        state = ui.step(state, press(keys.Key.RIGHT), SETTINGS)
        self.assertEqual(state.values["language"], "auto")  # wraps

    def test_toggling_a_schedule_key_marks_the_schedule_dirty(self):
        state = ui.step(fresh(), press(keys.Key.RIGHT), SETTINGS)  # daily_enabled
        self.assertTrue(state.schedule_dirty)

    def test_toggling_a_non_schedule_key_leaves_the_schedule_clean(self):
        index = SETTINGS.index(config.BY_KEY["notify_upcoming_reset"])
        state = ui.step(fresh(cursor=index), press(keys.Key.RIGHT), SETTINGS)
        self.assertFalse(state.schedule_dirty)

    def test_a_typed_row_does_not_cycle(self):
        index = SETTINGS.index(config.BY_KEY["daily_time"])
        state = ui.step(fresh(cursor=index), press(keys.Key.RIGHT), SETTINGS)
        self.assertEqual(state.values["daily_time"], config.DEFAULTS["daily_time"])


class EditingTests(unittest.TestCase):
    def _open(self, key):
        index = SETTINGS.index(config.BY_KEY[key])
        return ui.step(fresh(cursor=index), press(keys.Key.ENTER), SETTINGS)

    def test_enter_on_a_typed_row_opens_an_editor_seeded_with_the_value(self):
        state = self._open("daily_time")
        self.assertTrue(state.editing)
        self.assertEqual(state.edit_buffer, "10:00")

    def test_typing_appends_to_the_buffer(self):
        state = self._open("daily_time")
        state = ui.step(state, char("9"), SETTINGS)
        self.assertEqual(state.edit_buffer, "10:009")

    def test_backspace_deletes_the_last_character(self):
        state = self._open("daily_time")
        state = ui.step(state, press(keys.Key.BACKSPACE), SETTINGS)
        self.assertEqual(state.edit_buffer, "10:0")

    def test_escape_cancels_without_changing_the_value(self):
        state = self._open("daily_time")
        state = ui.step(state, char("x"), SETTINGS)
        state = ui.step(state, press(keys.Key.ESCAPE), SETTINGS)
        self.assertFalse(state.editing)
        self.assertEqual(state.values["daily_time"], "10:00")
        self.assertFalse(state.pending_save)

    def test_enter_commits_a_valid_value(self):
        state = self._open("daily_time")
        for _ in range(5):
            state = ui.step(state, press(keys.Key.BACKSPACE), SETTINGS)
        for c in "07:30":
            state = ui.step(state, char(c), SETTINGS)
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertEqual(state.values["daily_time"], "07:30")
        self.assertFalse(state.editing)
        self.assertTrue(state.pending_save)

    def test_an_invalid_value_keeps_the_editor_open_and_shows_the_error(self):
        state = self._open("scan_interval_minutes")
        state = ui.step(state, press(keys.Key.ESCAPE), SETTINGS)
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        for _ in range(10):
            state = ui.step(state, press(keys.Key.BACKSPACE), SETTINGS)
        for c in "9 weeks":
            state = ui.step(state, char(c), SETTINGS)
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertTrue(state.editing)
        self.assertIsNotNone(state.error)
        self.assertEqual(state.values["scan_interval_minutes"],
                         config.DEFAULTS["scan_interval_minutes"])

    def test_committing_the_same_value_is_not_a_change(self):
        state = self._open("daily_time")
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertFalse(state.pending_save)
        self.assertFalse(state.schedule_dirty)

    def test_a_secret_row_opens_with_an_empty_buffer(self):
        values = dict(config.DEFAULTS, telegram_bot_token="123456789:REALTOKEN")
        index = SETTINGS.index(config.BY_KEY["telegram_bot_token"])
        state = ui.step(ui.MenuState(values=values, cursor=index), press(keys.Key.ENTER), SETTINGS)
        self.assertTrue(state.editing)
        self.assertEqual(state.edit_buffer, "")

    def test_committing_an_empty_secret_keeps_the_stored_token(self):
        values = dict(config.DEFAULTS, telegram_bot_token="123456789:REALTOKEN")
        index = SETTINGS.index(config.BY_KEY["telegram_bot_token"])
        state = ui.MenuState(values=values, cursor=index, editing=True, edit_buffer="")
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertEqual(state.values["telegram_bot_token"], "123456789:REALTOKEN")
        self.assertFalse(state.pending_save)

    def test_arrows_are_ignored_while_editing(self):
        state = self._open("daily_time")
        moved = ui.step(state, press(keys.Key.DOWN), SETTINGS)
        self.assertEqual(moved.cursor, state.cursor)
        self.assertEqual(moved.edit_buffer, state.edit_buffer)


class RestoreDefaultsTests(unittest.TestCase):
    def test_d_asks_for_confirmation_first(self):
        state = ui.step(fresh(), char("d"), SETTINGS)
        self.assertTrue(state.confirm_defaults)

    def test_y_restores_every_default(self):
        state = fresh(values=dict(config.DEFAULTS, daily_time="03:00"), confirm_defaults=True)
        state = ui.step(state, char("y"), SETTINGS)
        self.assertEqual(state.values["daily_time"], config.DEFAULTS["daily_time"])
        self.assertTrue(state.pending_save)
        self.assertFalse(state.confirm_defaults)

    def test_any_other_key_cancels_the_reset(self):
        state = fresh(values=dict(config.DEFAULTS, daily_time="03:00"), confirm_defaults=True)
        state = ui.step(state, char("n"), SETTINGS)
        self.assertEqual(state.values["daily_time"], "03:00")
        self.assertFalse(state.confirm_defaults)
        self.assertFalse(state.pending_save)

    def test_a_restore_marks_the_schedule_dirty(self):
        state = fresh(values=dict(config.DEFAULTS, daily_time="03:00"), confirm_defaults=True)
        self.assertTrue(ui.step(state, char("y"), SETTINGS).schedule_dirty)


class ActionTests(unittest.TestCase):
    def test_a_requests_a_schedule_apply(self):
        self.assertEqual(ui.step(fresh(), char("a"), SETTINGS).pending_action, "apply")

    def test_e_opens_the_export_path_prompt(self):
        state = ui.step(fresh(), char("e"), SETTINGS)
        self.assertEqual(state.prompt, "export")
        self.assertIsNone(state.pending_action)

    def test_i_opens_the_import_path_prompt(self):
        self.assertEqual(ui.step(fresh(), char("i"), SETTINGS).prompt, "import")

    def test_typing_a_path_then_enter_requests_the_action(self):
        state = ui.step(fresh(), char("e"), SETTINGS)
        for c in "/tmp/a.json":
            state = ui.step(state, char(c), SETTINGS)
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertEqual(state.pending_action, "export")
        self.assertEqual(state.prompt_buffer, "/tmp/a.json")
        self.assertIsNone(state.prompt)

    def test_escape_cancels_the_path_prompt(self):
        state = ui.step(fresh(), char("e"), SETTINGS)
        state = ui.step(state, press(keys.Key.ESCAPE), SETTINGS)
        self.assertIsNone(state.prompt)
        self.assertIsNone(state.pending_action)

    def test_an_empty_path_does_not_request_an_action(self):
        state = ui.step(fresh(), char("i"), SETTINGS)
        state = ui.step(state, press(keys.Key.ENTER), SETTINGS)
        self.assertIsNone(state.pending_action)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.plain = ui.Paint(False)
        self.cfg = dict(config.DEFAULTS)

    def test_the_frame_has_no_box_drawing_borders(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en")
        text = "\n".join(lines)
        for glyph in "╭╮╰╯│┌┐└┘":
            self.assertNotIn(glyph, text)

    def test_the_selected_row_is_marked(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en")
        marked = [line for line in lines if ui.GLYPH_CURSOR in line]
        self.assertEqual(len(marked), 1)
        self.assertIn("Daily notification", marked[0])

    def test_the_selected_rows_help_is_shown(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en")
        self.assertTrue(any("Send one overview" in line for line in lines))

    def test_the_running_version_is_shown(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en")
        self.assertTrue(any(ui.package_version() in line for line in lines))

    def test_labels_follow_the_language(self):
        english = "\n".join(ui.render_menu(self.cfg, 0, paint=self.plain, lang="en"))
        chinese = "\n".join(ui.render_menu(self.cfg, 0, paint=self.plain, lang="zh-TW"))
        self.assertIn("Daily notification", english)
        self.assertIn("每日通知", chinese)

    def test_a_secret_is_never_rendered_in_the_clear(self):
        cfg = dict(self.cfg, telegram_bot_token="123456789:SUPERSECRETVALUE")
        text = "\n".join(ui.render_menu(cfg, 0, paint=self.plain, lang="en"))
        self.assertNotIn("SUPERSECRETVALUE", text)
        self.assertNotIn("123456789", text)

    def test_the_edit_buffer_is_shown_while_editing(self):
        index = SETTINGS.index(config.BY_KEY["daily_time"])
        lines = ui.render_menu(self.cfg, index, paint=self.plain, lang="en",
                               editing=True, edit_buffer="08:1")
        self.assertTrue(any("08:1" in line for line in lines))

    def test_an_error_is_shown(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en", error="nope")
        self.assertTrue(any("nope" in line for line in lines))

    def test_the_path_prompt_is_shown(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en",
                               prompt="export", prompt_buffer="/tmp/x.json")
        self.assertTrue(any("/tmp/x.json" in line for line in lines))

    def test_no_line_overflows_the_panel_width(self):
        cfg = dict(self.cfg, user_agent="codex-reset-watch/9.9 (+https://example.invalid/a/very/"
                                        "long/user/agent/string/that/keeps/going)")
        for line in ui.render_menu(cfg, 16, paint=self.plain, lang="en", height=44):
            self.assertLessEqual(ui.width(line), ui.PANEL_WIDTH + 1, repr(line))

    def test_the_home_directory_is_shortened_in_the_path_line(self):
        import pathlib
        with mock.patch.object(ui.config, "config_path",
                               return_value=pathlib.Path.home() / "cfg" / "config.json"):
            lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en")
        self.assertTrue(any("~/cfg/config.json" in line for line in lines))
        self.assertFalse(any(str(pathlib.Path.home()) in line for line in lines))

    def test_the_token_row_names_the_store_holding_it(self):
        index = SETTINGS.index(config.BY_KEY["telegram_bot_token"])
        with mock.patch.object(ui.secrets_store, "available", return_value=True), \
                mock.patch.object(ui.secrets_store, "backend_label", return_value="macOS Keychain"):
            lines = ui.render_menu(self.cfg, index, paint=self.plain, lang="en")
        self.assertTrue(any("macOS Keychain" in line for line in lines))

    def test_the_token_row_warns_when_there_is_no_store(self):
        index = SETTINGS.index(config.BY_KEY["telegram_bot_token"])
        with mock.patch.object(ui.secrets_store, "available", return_value=False):
            lines = ui.render_menu(self.cfg, index, paint=self.plain, lang="en")
        self.assertTrue(any("TG_BOT_TOKEN" in line for line in lines))

    def test_the_view_fits_the_terminal_height(self):
        lines = ui.render_menu(self.cfg, 0, paint=self.plain, lang="en", height=20)
        self.assertLessEqual(len(lines), 20)

    def test_the_cursor_row_stays_visible_in_a_short_terminal(self):
        last = len(SETTINGS) - 1
        lines = ui.render_menu(self.cfg, last, paint=self.plain, lang="en", height=20)
        self.assertTrue(any(ui.GLYPH_CURSOR in line for line in lines))


class RunMenuTests(unittest.TestCase):
    """The shell: fake keys in, StringIO out, every side effect mocked."""

    def _run(self, events, cfg=None, **mocks):
        source = iter(events)
        out = io.StringIO()
        saved = []
        with mock.patch.object(ui.config, "save", side_effect=lambda c, *a: saved.append(dict(c))), \
                mock.patch.object(ui.scheduler, "apply", return_value=("launchd", ("daily",))) as apply_mock:
            for name, value in mocks.items():
                patcher = mock.patch.object(ui, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
            code = ui.run_menu(cfg=dict(cfg or config.DEFAULTS), read=lambda: next(source),
                               out=out, height=40)
        return code, out.getvalue(), saved, apply_mock

    def test_quitting_immediately_saves_nothing(self):
        code, _, saved, apply_mock = self._run([char("q")])
        self.assertEqual(code, 0)
        self.assertEqual(saved, [])
        apply_mock.assert_not_called()

    def test_a_toggle_is_saved(self):
        _, _, saved, _ = self._run([press(keys.Key.RIGHT), char("q")])
        self.assertEqual(len(saved), 1)
        self.assertFalse(saved[-1]["daily_enabled"])

    def test_a_schedule_change_reapplies_on_quit(self):
        _, _, _, apply_mock = self._run([press(keys.Key.RIGHT), char("q")])
        apply_mock.assert_called_once()

    def test_a_non_schedule_change_does_not_reapply(self):
        index = SETTINGS.index(config.BY_KEY["notify_upcoming_reset"])
        source = iter([press(keys.Key.RIGHT), char("q")])
        out = io.StringIO()
        with mock.patch.object(ui.config, "save"), \
                mock.patch.object(ui.scheduler, "apply") as apply_mock:
            ui.run_menu(cfg=dict(config.DEFAULTS), read=lambda: next(source), out=out,
                        cursor=index, height=40)
        apply_mock.assert_not_called()

    def test_a_scheduler_failure_is_reported_not_raised(self):
        source = iter([char("a"), char("q")])
        out = io.StringIO()
        with mock.patch.object(ui.config, "save"), \
                mock.patch.object(ui.scheduler, "apply", side_effect=RuntimeError("launchctl exploded")):
            code = ui.run_menu(cfg=dict(config.DEFAULTS), read=lambda: next(source),
                               out=out, height=40)
        self.assertEqual(code, 0)
        self.assertIn("launchctl exploded", out.getvalue())

    def test_keyboard_interrupt_exits_cleanly(self):
        def boom():
            raise KeyboardInterrupt

        out = io.StringIO()
        with mock.patch.object(ui.config, "save"), mock.patch.object(ui.scheduler, "apply"):
            self.assertEqual(ui.run_menu(cfg=dict(config.DEFAULTS), read=boom, out=out, height=40), 0)

    def test_an_exhausted_key_source_exits_instead_of_looping(self):
        source = iter([])

        def read():
            return next(source)

        out = io.StringIO()
        with mock.patch.object(ui.config, "save"), mock.patch.object(ui.scheduler, "apply"):
            self.assertEqual(ui.run_menu(cfg=dict(config.DEFAULTS), read=read, out=out, height=40), 0)


class DispatchTests(unittest.TestCase):
    def test_a_real_tty_gets_the_keyboard_menu(self):
        with mock.patch.object(ui.keys, "is_interactive_tty", return_value=True), \
                mock.patch.object(ui, "run_menu", return_value=0) as run, \
                mock.patch.object(ui, "fallback_menu") as fallback:
            ui.config_menu()
        run.assert_called_once()
        fallback.assert_not_called()

    def test_a_pipe_gets_the_numbered_fallback(self):
        with mock.patch.object(ui.keys, "is_interactive_tty", return_value=False), \
                mock.patch.object(ui, "run_menu") as run, \
                mock.patch.object(ui, "fallback_menu", return_value=0) as fallback:
            ui.config_menu(io.StringIO("q\n"), io.StringIO())
        fallback.assert_called_once()
        run.assert_not_called()


class VersionTests(unittest.TestCase):
    def test_version_is_a_string(self):
        self.assertIsInstance(ui.package_version(), str)
        self.assertTrue(ui.package_version())

    def test_an_uninstalled_package_does_not_crash_the_header(self):
        import importlib.metadata as md

        with mock.patch.object(md, "version", side_effect=md.PackageNotFoundError):
            ui.package_version.cache_clear()
            self.assertTrue(ui.package_version())
        ui.package_version.cache_clear()


if __name__ == "__main__":
    unittest.main()
