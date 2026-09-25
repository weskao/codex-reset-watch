import sys, datetime as dt, importlib.util, os, pathlib, unittest
import codex_reset_watch as crw
class FormattingTests(unittest.TestCase):
    def test_utc8(self):
        t = dt.datetime(2026,9,19,13,0,tzinfo=dt.timezone.utc)
        self.assertEqual(crw.fmt_local(t), "2026-09-19 21:00 UTC+8")
    def test_remaining_day_to_minutes(self):
        now = dt.datetime(2026,9,19,0,0,tzinfo=dt.timezone.utc)
        target = now + dt.timedelta(days=2,hours=3,minutes=7,seconds=59)
        self.assertEqual(crw.fmt_remaining(target, now), "2 Days 3 hours 7 minutes")
    def test_remaining_minimum_unit_minutes(self):
        now = dt.datetime(2026,9,19,0,0,tzinfo=dt.timezone.utc)
        self.assertEqual(crw.fmt_remaining(now + dt.timedelta(seconds=30), now), "0 minutes")
    def test_display_path_uses_tilde_for_home(self):
        original = crw.home
        try:
            # Built with pathlib/os.sep rather than literal "/" so the fixture
            # and the expectation use the same separator on every OS.
            root = pathlib.Path(pathlib.Path.cwd().anchor)
            fake_home = root / "Users" / "example"
            crw.home = lambda: fake_home
            self.assertEqual(crw.display_path(fake_home), "~")
            nested = fake_home / "Library" / "Logs" / "codex-reset-watch"
            self.assertEqual(crw.display_path(nested), "~" + os.sep + os.path.join("Library", "Logs", "codex-reset-watch"))
            outside = root / "opt" / "homebrew" / "bin" / "python3"
            self.assertEqual(crw.display_path(outside), str(outside))
        finally:
            crw.home = original
    def test_manual_output_shows_scheduled_reset_even_without_timestamp(self):
        checked = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.timezone.utc)
        upcoming = crw.Upcoming(
            timestamp=None,
            timing_kind="scheduled_tba",
            event_type="banked",
            status="scheduled",
            title="Banked reset scheduled",
            time_text="Time to be announced",
            source_url="https://x.com/thsottiaux/status/2101352781219258527",
        )
        snapshot = crw.Snapshot(checked, None, upcoming, True, True)
        text = crw.format_manual(snapshot, {"language": "zh-TW"})
        self.assertIn("🚦 狀態：Banked reset scheduled", text)
        self.assertIn("🏷️ 類型：banked", text)
        self.assertIn("🕒 時間：尚未公布（Time to be announced）", text)
        self.assertNotIn("⏳ 距離現在：", text)
        self.assertIn("🔗 公告：https://x.com/thsottiaux/status/2101352781219258527", text)
        self.assertIn("🌐 Codex Resets：https://codex-resets.com/", text)

    def test_fmt_local_honours_configured_timezone(self):
        t = dt.datetime(2026, 9, 19, 13, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(crw.fmt_local(t, {"timezone": "UTC"}), "2026-09-19 13:00 UTC")
        self.assertEqual(crw.fmt_local(t, {"timezone": "UTC-05:30"}), "2026-09-19 07:30 UTC-05:30")

    def test_fmt_local_defaults_to_utc8_without_cfg(self):
        t = dt.datetime(2026, 9, 19, 13, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(crw.fmt_local(t), crw.fmt_local(t, {"timezone": "UTC+8"}))

    def test_format_manual_uses_configured_timezone(self):
        checked = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.timezone.utc)
        snapshot = crw.Snapshot(checked, None, None, True, True)
        text = crw.format_manual(snapshot, {"timezone": "UTC", "language": "zh-TW"})
        self.assertIn("2026-09-20 00:00 UTC", text)
        self.assertNotIn("UTC+8", text)

    def test_format_new_event_notice_uses_configured_timezone(self):
        checked = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.timezone.utc)
        event = crw.Event(event_id="e1", timestamp=checked, event_type="reset")
        text = crw.format_new_event_notice(event, checked, {"timezone": "UTC", "language": "zh-TW"})
        self.assertIn("2026-09-20 00:00 UTC", text)

    def test_tba_upcoming_notice_has_no_fake_countdown(self):
        checked = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.timezone.utc)
        upcoming = crw.Upcoming(
            timing_kind="scheduled_tba",
            event_type="banked",
            status="scheduled",
            title="Banked reset scheduled",
            time_text="Time to be announced",
            source_url="https://x.com/thsottiaux/status/2101352781219258527",
        )
        text = crw.format_upcoming_notice(upcoming, checked, {"language": "zh-TW"})
        self.assertIn("Banked reset scheduled", text)
        self.assertIn("Time to be announced", text)
        self.assertNotIn("距離現在", text)

    def test_no_signal_notice_shows_latest_reset_like_manual(self):
        checked = dt.datetime(2026, 9, 25, 3, 0, tzinfo=dt.timezone.utc)
        latest = crw.Event(
            event_id="e0",
            timestamp=checked - dt.timedelta(days=1),
            event_type="banked",
            message="範例：帳戶額度已重置",
        )
        cfg = {"language": "zh-TW"}
        manual_text = crw.format_manual(crw.Snapshot(checked, latest, None, True, True), cfg)
        notice_text = crw.format_no_signal_notice(checked, latest, cfg)
        for expected in (
            "✅ 最近一次 Reset",
            "🕒 時間：2026-09-24 11:00 UTC+8",
            "🏷️ 類型：banked",
            "📝 公告：範例：帳戶額度已重置",
        ):
            self.assertIn(expected, manual_text)
            self.assertIn(expected, notice_text)
        self.assertIn("尚未偵測到未來 Reset 訊號，內容無變化。", notice_text)
        self.assertIn(crw.tracker_line(), notice_text)

    def test_no_signal_notice_without_latest_shows_fallback(self):
        checked = dt.datetime(2026, 9, 25, 3, 0, tzinfo=dt.timezone.utc)
        text = crw.format_no_signal_notice(checked, None, {"language": "zh-TW"})
        self.assertIn("ℹ️ 最近一次 Reset：API 未提供可解析資料", text)
        self.assertIn("尚未偵測到未來 Reset 訊號，內容無變化。", text)
        self.assertIn(crw.tracker_line(), text)

    def test_no_signal_notice_defaults_to_english(self):
        checked = dt.datetime(2026, 9, 25, 3, 0, tzinfo=dt.timezone.utc)
        text = crw.format_no_signal_notice(checked, None)
        self.assertIn("no parseable data from the API", text)
        self.assertIn("No upcoming reset signal; nothing changed.", text)

    def test_all_notices_include_tracker_link(self):
        checked = dt.datetime(2026, 9, 20, 0, 0, tzinfo=dt.timezone.utc)
        event = crw.Event(event_id="e1", timestamp=checked, event_type="reset")
        upcoming = crw.Upcoming(
            timing_kind="scheduled_tba",
            event_type="banked",
            status="scheduled",
            title="Banked reset scheduled",
            time_text="Time to be announced",
            source_url="https://x.com/thsottiaux/status/2101352781219258527",
        )
        snapshot = crw.Snapshot(checked, event, upcoming, True, True)
        for text in (
            crw.format_manual(snapshot),
            crw.format_upcoming_notice(upcoming, checked),
            crw.format_no_signal_notice(checked, event),
            crw.format_new_event_notice(event, checked),
        ):
            self.assertIn(crw.tracker_line(), text)

if __name__ == "__main__": unittest.main()
