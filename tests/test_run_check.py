"""End-to-end `run_check` tests against a local HTTP fixture server, fully
isolated from the real machine: CRW_CONFIG/CRW_STATE_DIR/CRW_LOG_DIR all point
into a temp dir, and Telegram is never actually contacted (no TG_* env vars,
and these tests never pass notify=True with real credentials present).

All assertions that touch state_dir/log_dir live INSIDE the `with
isolated_run(...):` block — `tempfile.TemporaryDirectory()` deletes that
directory the moment the block exits, so an assertion placed after it would
silently check a path that no longer exists (an `exists()` check would then
pass "vacuously" whether or not the code under test ever created the file)."""
import contextlib
import http.server
import json
import os
import pathlib
import socketserver
import tempfile
import threading
import unittest

import codex_reset_watch as crw

FIX = pathlib.Path(__file__).parent / "fixtures"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/status"):
            body, code = (FIX / "status_upcoming.json").read_bytes(), 200
        elif self.path.startswith("/api/v1/resets"):
            body, code = (FIX / "resets.json").read_bytes(), 200
        else:
            body, code = b"{}", 404
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@contextlib.contextmanager
def isolated_run(cfg_overrides=None):
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv, tempfile.TemporaryDirectory() as d:
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        root = pathlib.Path(d)
        cfg_path = root / "config.json"
        state_dir = root / "state"
        log_dir = root / "log"
        payload = {"api_base": f"http://127.0.0.1:{srv.server_address[1]}", "request_retries": 1}
        payload.update(cfg_overrides or {})
        cfg_path.write_text(json.dumps(payload), encoding="utf-8")

        saved = {}
        for key, value in {
            "CRW_CONFIG": str(cfg_path), "CRW_STATE_DIR": str(state_dir), "CRW_LOG_DIR": str(log_dir),
            "TG_BOT_TOKEN": None, "TG_CHAT_ID": None,  # never real-notify in a test
        }.items():
            saved[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            yield state_dir, log_dir
        finally:
            srv.shutdown()
            th.join(timeout=2)
            for key, old in saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old


class DailyGatingTests(unittest.TestCase):
    def test_disabled_daily_skips_without_hitting_the_api_or_writing_state(self):
        with isolated_run({"daily_enabled": False}) as (state_dir, _):
            code = crw.run_check("daily", notify=False)
            self.assertEqual(code, 0)
            self.assertFalse((state_dir / "state.json").exists())

    def test_daily_time_in_the_future_is_not_due(self):
        with isolated_run({"daily_time": "23:59", "timezone": "UTC"}) as (state_dir, _):
            code = crw.run_check("daily", notify=False)
            self.assertEqual(code, 0)
            self.assertFalse((state_dir / "state.json").exists())

    def test_daily_time_in_the_past_is_due_and_runs(self):
        with isolated_run({"daily_time": "00:00", "timezone": "UTC"}) as (state_dir, _):
            code = crw.run_check("daily", notify=False)
            self.assertEqual(code, 0)
            state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["last_daily_date"], state["last_check_at"][:10])

    def test_force_bypasses_both_the_enabled_flag_and_the_time_gate(self):
        with isolated_run({"daily_enabled": False, "daily_time": "23:59"}) as (state_dir, _):
            code = crw.run_check("daily", notify=False, force_daily=True)
            self.assertEqual(code, 0)
            self.assertTrue((state_dir / "state.json").exists())

    def test_second_daily_run_same_day_is_a_no_op(self):
        with isolated_run({"daily_time": "00:00", "timezone": "UTC"}) as (state_dir, _):
            crw.run_check("daily", notify=False)
            first = (state_dir / "state.json").read_text(encoding="utf-8")
            crw.run_check("daily", notify=False)
            second = (state_dir / "state.json").read_text(encoding="utf-8")
            self.assertEqual(first, second)


class DirectoryHonouredTests(unittest.TestCase):
    def test_state_and_log_dirs_come_from_config_not_the_platform_default(self):
        with isolated_run() as (state_dir, log_dir):
            crw.run_check("monitor", notify=False)
            self.assertTrue((state_dir / "state.json").exists())
            self.assertTrue((log_dir / "events.jsonl").exists())
            self.assertTrue((log_dir / "api.jsonl").exists())


class ManualCheckTests(unittest.TestCase):
    def test_manual_check_prints_configured_timezone(self):
        import contextlib as _c
        import io

        with isolated_run({"timezone": "UTC"}) as (_state_dir, _log_dir):
            out = io.StringIO()
            with _c.redirect_stdout(out):
                code = crw.run_check("manual", notify=False)
            self.assertEqual(code, 0)
            self.assertIn("UTC", out.getvalue())
            self.assertNotIn("UTC+8", out.getvalue())


if __name__ == "__main__":
    unittest.main()
