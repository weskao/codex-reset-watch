"""Basic/Advanced menu mode: which settings ``crw config`` shows by default,
and how a user switches between the curated subset and the full schema.
"""
import unittest

from codex_reset_watch import config, keys, ui


def press(key, char=None):
    return keys.KeyEvent(key, char)


def fresh(**overrides):
    overrides.setdefault("values", dict(config.DEFAULTS))
    return ui.MenuState(**overrides)


class ConfigModeTests(unittest.TestCase):
    def test_ui_mode_defaults_to_basic(self):
        self.assertEqual(config.DEFAULTS["ui_mode"], "basic")

    def test_ui_mode_is_a_declared_setting(self):
        self.assertIn("ui_mode", config.BY_KEY)
        self.assertEqual(config.BY_KEY["ui_mode"].choices, config.UI_MODES)

    def test_basic_mode_shows_only_basic_tier_settings(self):
        visible = config.visible_settings("basic")
        self.assertTrue(visible)
        self.assertTrue(all(s.tier == "basic" for s in visible))
        self.assertLess(len(visible), len(config.SETTINGS))

    def test_advanced_mode_shows_every_setting(self):
        self.assertEqual(config.visible_settings("advanced"), config.SETTINGS)

    def test_unknown_mode_falls_back_to_basic(self):
        self.assertEqual(config.visible_settings("bogus"), config.visible_settings("basic"))

    def test_daily_and_telegram_settings_are_basic_tier(self):
        for key in ("daily_enabled", "daily_time", "monitor_enabled",
                    "scan_interval_minutes", "telegram_bot_token", "telegram_chat_id"):
            self.assertEqual(config.BY_KEY[key].tier, "basic", key)

    def test_api_and_storage_settings_are_advanced_only(self):
        for key in ("api_base", "request_retries", "state_dir", "max_log_bytes"):
            self.assertEqual(config.BY_KEY[key].tier, "advanced", key)

    def test_current_ui_mode_reads_from_cfg(self):
        self.assertEqual(config.current_ui_mode({"ui_mode": "advanced"}), "advanced")
        self.assertEqual(config.current_ui_mode({}), "basic")
        self.assertEqual(config.current_ui_mode({"ui_mode": "nonsense"}), "basic")

    def test_render_shows_a_translated_mode_name(self):
        self.assertEqual(config.render(config.BY_KEY["ui_mode"], "basic", "en"), "Basic")
        self.assertEqual(config.render(config.BY_KEY["ui_mode"], "advanced", "zh-TW"), "進階")


class KeyDecodingTests(unittest.TestCase):
    def test_tab_byte_decodes_to_tab_key(self):
        import unittest.mock as mock

        def source(*chunks):
            queue = list(chunks)
            return lambda timeout=None: queue.pop(0) if queue else None

        with mock.patch.object(keys, "_read_byte", source(b"\t")):
            self.assertEqual(keys.read_key().key, keys.Key.TAB)


class ModeToggleStepTests(unittest.TestCase):
    def test_tab_flips_basic_to_advanced(self):
        state = fresh()
        settings = config.visible_settings(config.current_ui_mode(state.values))
        state = ui.step(state, press(keys.Key.TAB), settings)
        self.assertEqual(state.values["ui_mode"], "advanced")
        self.assertTrue(state.pending_save)

    def test_tab_flips_advanced_back_to_basic(self):
        state = fresh(values={**config.DEFAULTS, "ui_mode": "advanced"})
        settings = config.visible_settings(config.current_ui_mode(state.values))
        state = ui.step(state, press(keys.Key.TAB), settings)
        self.assertEqual(state.values["ui_mode"], "basic")

    def test_tab_does_not_mark_the_schedule_dirty(self):
        state = fresh()
        settings = config.visible_settings(config.current_ui_mode(state.values))
        state = ui.step(state, press(keys.Key.TAB), settings)
        self.assertFalse(state.schedule_dirty)

    def test_tab_keeps_the_same_row_selected_when_it_stays_visible(self):
        # daily_time is tier=basic, at the same relative position in both lists.
        basic = config.visible_settings("basic")
        index = basic.index(config.BY_KEY["daily_time"])
        state = fresh(cursor=index)
        state = ui.step(state, press(keys.Key.TAB), basic)
        advanced = config.visible_settings("advanced")
        self.assertEqual(advanced[state.cursor].key, "daily_time")

    def test_tab_clamps_the_cursor_when_the_row_disappears_in_basic_mode(self):
        # api_base is advanced-only: switching to basic must not leave the
        # cursor pointing past the end of the shorter, filtered list.
        advanced = config.visible_settings("advanced")
        index = advanced.index(config.BY_KEY["api_base"])
        state = fresh(cursor=index, values={**config.DEFAULTS, "ui_mode": "advanced"})
        state = ui.step(state, press(keys.Key.TAB), advanced)
        basic = config.visible_settings("basic")
        self.assertLess(state.cursor, len(basic))


class RenderModeTests(unittest.TestCase):
    def test_render_menu_defaults_to_the_filtered_settings_for_the_cfg_mode(self):
        cfg = dict(config.DEFAULTS)  # basic
        frame = ui.render_menu(cfg, 0, paint=ui.Paint(False), lang="en", height=200)
        text = "\n".join(frame)
        self.assertNotIn("API base", text)

    def test_the_list_view_keeps_a_badge_rather_than_a_tab_bar(self):
        # `--list` is not a keyboard surface: it states which view it printed,
        # and never offers a key that cannot be pressed there.
        text = ui.render_settings(dict(config.DEFAULTS), paint=ui.Paint(False), lang="en")
        self.assertIn("Advanced mode", text)
        self.assertNotIn("Tab", text)


class TabBarTests(unittest.TestCase):
    """Both modes are shown as two tabs; the rule underlines the active one."""

    def _frame(self, mode):
        cfg = {**config.DEFAULTS, "ui_mode": mode}
        return ui.render_menu(cfg, 0, paint=ui.Paint(False), lang="en", height=40)

    def _tab_and_rule(self, mode):
        frame = self._frame(mode)
        index = next(i for i, line in enumerate(frame) if "Basic" in line)
        return frame[index], frame[index + 1]

    def test_both_tabs_are_always_visible(self):
        tabs, _ = self._tab_and_rule("basic")
        self.assertIn("Basic", tabs)
        self.assertIn("Advanced", tabs)

    def test_each_tab_carries_its_row_count(self):
        tabs, _ = self._tab_and_rule("basic")
        self.assertIn(f"Basic {len(config.visible_settings('basic'))}", tabs)
        self.assertIn(f"Advanced {len(config.visible_settings('advanced'))}", tabs)

    def test_the_tab_row_says_which_key_switches(self):
        tabs, _ = self._tab_and_rule("basic")
        self.assertIn("Tab", tabs)

    def test_the_rule_underlines_the_active_tab(self):
        tabs, rule = self._tab_and_rule("basic")
        label = f"Basic {len(config.visible_settings('basic'))}"
        self.assertEqual(rule.index("━"), tabs.index(label))
        self.assertEqual(rule.count("━"), len(label))

    def test_switching_moves_the_underline_to_the_other_tab(self):
        tabs, rule = self._tab_and_rule("advanced")
        label = f"Advanced {len(config.visible_settings('advanced'))}"
        self.assertEqual(rule.index("━"), tabs.index(label))
        self.assertEqual(rule.count("━"), len(label))

    def test_the_rule_still_spans_the_whole_panel(self):
        _, rule = self._tab_and_rule("basic")
        self.assertEqual(rule.count("━") + rule.count("─"), ui.PANEL_WIDTH)

    def test_no_tab_bar_line_overflows_the_panel(self):
        for mode in config.UI_MODES:
            tabs, rule = self._tab_and_rule(mode)
            self.assertLessEqual(ui.width(tabs), ui.PANEL_WIDTH + 1, mode)
            self.assertLessEqual(ui.width(rule), ui.PANEL_WIDTH + 1, mode)

    def test_the_tabs_and_the_switch_hint_are_translated(self):
        cfg = dict(config.DEFAULTS)
        frame = ui.render_menu(cfg, 0, paint=ui.Paint(False), lang="zh-TW", height=40)
        text = "\n".join(frame)
        self.assertIn("一般", text)
        self.assertIn("進階", text)
        self.assertIn("切換", text)


if __name__ == "__main__":
    unittest.main()
