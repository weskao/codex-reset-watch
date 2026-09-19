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
        text = crw.format_manual(snapshot)
        self.assertIn("📌 狀態：Banked reset scheduled", text)
        self.assertIn("🏷️ 類型：banked", text)
        self.assertIn("🕒 時間：尚未公布（Time to be announced）", text)
        self.assertNotIn("⏳ 距離現在：", text)
        self.assertIn("🔗 公告：https://x.com/thsottiaux/status/2101352781219258527", text)
        self.assertIn("🌐 Codex Resets：https://codex-resets.com/", text)

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
        text = crw.format_upcoming_notice(upcoming, checked)
        self.assertIn("Banked reset scheduled", text)
        self.assertIn("Time to be announced", text)
        self.assertNotIn("距離現在", text)

if __name__ == "__main__": unittest.main()
