import sys, datetime as dt, importlib.util, pathlib, unittest
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
            crw.home = lambda: pathlib.Path("/Users/example")
            self.assertEqual(crw.display_path("/Users/example"), "~")
            self.assertEqual(crw.display_path("/Users/example/Library/Logs/codex-reset-watch"), "~/Library/Logs/codex-reset-watch")
            self.assertEqual(crw.display_path("/opt/homebrew/bin/python3"), "/opt/homebrew/bin/python3")
        finally:
            crw.home = original
if __name__ == "__main__": unittest.main()
