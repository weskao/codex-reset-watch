import sys, datetime as dt, importlib.util, json, pathlib, unittest
import codex_reset_watch as crw

class ParserTests(unittest.TestCase):
    def fixture(self, name):
        return json.loads((pathlib.Path(__file__).parent / "fixtures" / name).read_text())

    def test_latest_event(self):
        e = crw.latest_event(self.fixture("status_upcoming.json"), self.fixture("resets.json"))
        self.assertEqual(e.event_id, "evt-53")
        self.assertEqual(e.event_type, "regular")
        self.assertEqual(crw.iso_utc(e.timestamp), "2026-09-12T08:09:00Z")

    def test_upcoming_forecast_window(self):
        now = dt.datetime(2026,9,19,13,25,tzinfo=dt.timezone.utc)
        u = crw.upcoming_from_status(self.fixture("status_upcoming.json"), now=now)
        self.assertIsNotNone(u)
        self.assertEqual(u.chance_percent, 45)
        self.assertEqual(u.timing_kind, "forecast_window")
        self.assertEqual(crw.iso_utc(u.timestamp), "2026-09-21T15:09:00Z")

    def test_exact_eta_beats_window(self):
        data = {"upcoming_reset": {"eta": "2026-09-20T00:00:00Z", "window_end":"2026-09-21T00:00:00Z"}}
        u = crw.upcoming_from_status(data, now=dt.datetime(2026,9,19,tzinfo=dt.timezone.utc))
        self.assertEqual(u.timing_kind, "announced_or_estimated_time")
        self.assertIn("eta", u.raw_field)

    def test_nested_forecast_schema(self):
        data = {"forecast": {"window": {"window_end_at": "2026-09-20T02:00:00Z"}, "score": {"chance_percent": 0.6}, "confidence": "medium"}}
        u = crw.upcoming_from_status(data, now=dt.datetime(2026,9,19,tzinfo=dt.timezone.utc))
        self.assertIsNotNone(u)
        self.assertEqual(u.chance_percent, 60)
        self.assertIn("window_end_at", u.raw_field)

    def test_past_upcoming_is_ignored(self):
        data = {"forecast": {"window_end": "2026-09-18T00:00:00Z"}}
        u = crw.upcoming_from_status(data, now=dt.datetime(2026,9,19,tzinfo=dt.timezone.utc))
        self.assertIsNone(u)

    def test_epoch_millis(self):
        t = crw.parse_time(1789824000000)
        self.assertIsNotNone(t)
        self.assertEqual(t.tzinfo, dt.timezone.utc)

if __name__ == "__main__": unittest.main()
