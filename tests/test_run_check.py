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
import io
import json
import os
import pathlib
import re
import socketserver
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import codex_reset_watch as crw
from codex_reset_watch import console

FIX = pathlib.Path(__file__).parent / "fixtures"


_STATUS_FIXTURE = "status_upcoming.json"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/status"):
            body, code = (FIX / _STATUS_FIXTURE).read_bytes(), 200
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
def isolated_run(cfg_overrides=None, status_fixture="status_upcoming.json"):
    global _STATUS_FIXTURE
    _STATUS_FIXTURE = status_fixture
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


class BootCatchUpTests(unittest.TestCase):
    def test_monitor_waits_for_boot_lock_then_notifies_missed_reset_once(self):
        cfg = {"language": "zh-TW", "monitor_notify_when_unchanged": False}
        with isolated_run(cfg, status_fixture="status_no_upcoming.json") as (state_dir, log_dir), \
                mock.patch.object(crw, "send_telegram", return_value=True) as send, \
                contextlib.redirect_stdout(io.StringIO()):
            store = crw.StateStore(state_dir=state_dir)
            store.save({"initialized_at": "2026-09-01T00:00:00Z", "latest_event_key": "old"})
            attempted = threading.Event()
            finished = threading.Event()
            errors = []
            real_lock = crw.StateStore.lock

            @contextlib.contextmanager
            def observed_lock(owner, blocking=False):
                attempted.set()
                with real_lock(owner, blocking=blocking) as acquired:
                    yield acquired

            def monitor():
                try:
                    crw.run_check("monitor", notify=True)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    finished.set()

            with mock.patch.object(crw.StateStore, "lock", observed_lock):
                with real_lock(store, blocking=True) as acquired:
                    self.assertTrue(acquired)
                    worker = threading.Thread(target=monitor, daemon=True)
                    worker.start()
                    self.assertTrue(attempted.wait(2))
                    skipped = finished.wait(0.2)
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
            self.assertFalse(skipped, "monitor skipped the boot-time lock instead of waiting")
            self.assertEqual(len(send.call_args_list), 1)
            text = send.call_args.args[1]
            self.assertIn("🏷️ 類型：全域重設", text)
            self.assertIn("📝 公告：Reset all propagated. Sweet dreams.", text)
            crw.run_check("monitor", notify=True)
            self.assertEqual(len(send.call_args_list), 1)
            self.assertNotIn("skipped_locked", (log_dir / "events.jsonl").read_text())


class NotifyWhenUnchangedTests(unittest.TestCase):
    """`daily_notify_when_unchanged`/`monitor_notify_when_unchanged` must still push
    on a run with no upcoming signal at all — not only when an existing upcoming
    signal is unchanged. Detected via the telegram send attempt log line, since
    these tests run with no Telegram credentials configured."""

    def _attempted_send(self, log_dir):
        events = (log_dir / "events.jsonl").read_text(encoding="utf-8")
        return "telegram_credentials_missing" in events

    def test_no_upcoming_signal_still_pushes_when_unchanged_notify_is_on(self):
        with isolated_run({"daily_time": "00:00", "timezone": "UTC", "daily_notify_when_unchanged": True},
                           status_fixture="status_no_upcoming.json") as (_state_dir, log_dir):
            crw.run_check("daily", notify=True)
            self.assertTrue(self._attempted_send(log_dir))

    def test_no_upcoming_signal_sends_nothing_when_unchanged_notify_is_off(self):
        with isolated_run({"daily_time": "00:00", "timezone": "UTC", "daily_notify_when_unchanged": False},
                           status_fixture="status_no_upcoming.json") as (_state_dir, log_dir):
            crw.run_check("daily", notify=True)
            self.assertFalse(self._attempted_send(log_dir))


class NoticeImageTests(unittest.TestCase):
    """A new reset carries a random reset image, an upcoming signal the upcoming
    image, and every other notice goes out as plain text."""

    def _sent(self, cfg, state=None, status_fixture="status_upcoming.json"):
        with isolated_run(cfg, status_fixture=status_fixture) as (state_dir, _log_dir):
            if state:
                state_dir.mkdir(parents=True, exist_ok=True)
                (state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(crw, "send_telegram", return_value=True) as send, \
                    mock.patch.object(console, "sys", SimpleNamespace(platform="darwin", stdout=out)):
                with contextlib.redirect_stdout(out):
                    crw.run_check("monitor", notify=True)
                self.assertEqual(out.getvalue(), "".join(c.args[1] + "\n" for c in send.call_args_list))
        return [(c.args[1], c.kwargs.get("image")) for c in send.call_args_list]

    def test_new_reset_and_upcoming_each_carry_their_own_image(self):
        sent = self._sent({"timezone": "UTC"}, {"initialized_at": "2026-01-01T00:00:00Z", "latest_event_key": "old"},
                          status_fixture="status_scheduled_tba.json")
        self.assertEqual([image.parent.name for _text, image in sent], ["reset", "upcoming"])

    def test_scheduled_notice_names_the_os_job_and_log(self):
        with mock.patch.object(crw.scheduler.platform, "system", return_value="Linux"):
            sent = self._sent({"timezone": "UTC"})
        self.assertTrue(sent)
        for text, _image in sent:
            self.assertIn(f"\n{crw.host_identity.device_label()}\nsystemd: codex-reset-watch-monitor.timer\nlog: ", text)
            self.assertTrue(text.endswith("events.jsonl"))

    def test_custom_device_label_replaces_the_automatic_one(self):
        sent = self._sent({"timezone": "UTC", "device_label": "Office Mac"})
        self.assertTrue(sent)
        for text, _image in sent:
            self.assertIn("\nOffice Mac\n", text)
            self.assertNotIn(crw.host_identity.device_label(), text)

    def test_the_no_signal_notice_stays_text_only(self):
        sent = self._sent({"timezone": "UTC", "monitor_notify_when_unchanged": True},
                          status_fixture="status_no_upcoming.json")
        self.assertTrue(sent)
        self.assertEqual([image for _text, image in sent], [None])

    def test_manual_notify_stays_text_only(self):
        for colour in (False, True):
            out = io.StringIO()
            with self.subTest(colour=colour), isolated_run({"timezone": "UTC"}), \
                    mock.patch.object(console, "sys", SimpleNamespace(platform="darwin", stdout=out)), \
                    mock.patch.object(crw, "send_telegram") as send, \
                    mock.patch.object(crw.ui, "colour_enabled", return_value=colour), \
                    mock.patch.object(crw.host_identity, "device_label", return_value="💻 Test laptop · TEST******"), \
                    contextlib.redirect_stdout(out):
                crw.run_check("manual", notify=True)
            send.assert_called_once()
            self.assertNotIn("image", send.call_args.kwargs)
            self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue()), send.call_args.args[1] + "\n")
            self.assertTrue(out.getvalue().endswith("💻 Test laptop · TEST******\n"))

    def test_windows_manual_notify_converts_console_only(self):
        out = io.StringIO()
        with isolated_run({"timezone": "UTC", "language": "zh-TW"}), \
                mock.patch.dict(os.environ, {"CRW_LANG": "zh-TW"}), \
                mock.patch.object(console, "sys", SimpleNamespace(platform="win32", stdout=out)), \
                mock.patch.object(crw, "send_telegram") as send, \
                mock.patch.object(crw.ui, "colour_enabled", return_value=False), \
                mock.patch.object(crw.host_identity, "device_label", return_value="💻 Test laptop · TEST******"), \
                contextlib.redirect_stdout(out):
            code = crw.run_check("manual", notify=True)

        self.assertEqual(code, 0)
        send.assert_called_once()
        console_text = out.getvalue()
        telegram_text = send.call_args.args[1]
        self.assertIn("[CHECK] Codex Reset 即時查詢", console_text)
        self.assertTrue(console_text.endswith("[PC] Test laptop . TEST******\n"))
        self.assertNotIn("🔎", console_text)
        self.assertIn("🔎 Codex Reset 即時查詢", telegram_text)
        self.assertTrue(telegram_text.endswith("💻 Test laptop · TEST******"))


class BlindAlertTests(unittest.TestCase):
    """A monitor that cannot see the API must not look like "no reset": after
    `blind_alert_after` scheduled scans in a row see nothing usable, exactly one
    notice goes out, and a healthy scan re-arms it."""

    DOWN = {"api_base": "http://127.0.0.1:1", "blind_alert_after": 3,
            "monitor_notify_when_unchanged": False}

    def _blind_sends(self, send):
        title = crw.i18n.t("notice.blind.title", "en")
        return [c for c in send.call_args_list if c.args[1].startswith(title)]

    def _scan(self, send, mode="monitor"):
        with contextlib.redirect_stdout(io.StringIO()):
            crw.run_check(mode, notify=True)
        return len(self._blind_sends(send))

    def test_one_notice_after_n_consecutive_failures(self):
        with isolated_run(self.DOWN), mock.patch.object(crw, "send_telegram") as send:
            self.assertEqual([self._scan(send) for _ in range(4)], [0, 0, 1, 1])
            self.assertIn("3", self._blind_sends(send)[0].args[1])

    def test_an_unrecognized_payload_counts_as_blind(self):
        cfg = {"blind_alert_after": 1, "resets_path": "/missing", "monitor_notify_when_unchanged": False}
        with isolated_run(cfg, status_fixture="status_unrecognized.json"), \
                mock.patch.object(crw, "send_telegram") as send:
            self.assertEqual(self._scan(send), 1)

    def test_a_healthy_scan_rearms_the_alert(self):
        with isolated_run({"blind_alert_after": 1, "monitor_notify_when_unchanged": False}) as (state_dir, _), \
                mock.patch.object(crw, "send_telegram") as send:
            cfg_path = pathlib.Path(os.environ["CRW_CONFIG"])
            healthy = cfg_path.read_text(encoding="utf-8")
            down = json.dumps(dict(json.loads(healthy), api_base=self.DOWN["api_base"]))
            cfg_path.write_text(down, encoding="utf-8")
            self.assertEqual(self._scan(send), 1)
            cfg_path.write_text(healthy, encoding="utf-8")
            self._scan(send)
            self.assertEqual(json.loads((state_dir / "state.json").read_text())["blind_streak"], 0)
            cfg_path.write_text(down, encoding="utf-8")
            self.assertEqual(self._scan(send), 2)

    def test_recovery_does_not_reannounce_the_old_reset(self):
        global _STATUS_FIXTURE
        cfg = {"resets_path": "/missing", "monitor_notify_when_unchanged": False, "timezone": "UTC"}
        with isolated_run(cfg) as (state_dir, _), mock.patch.object(crw, "send_telegram") as send:
            self._scan(send)
            known = json.loads((state_dir / "state.json").read_text())["latest_event_key"]
            _STATUS_FIXTURE = "status_unrecognized.json"
            self._scan(send)
            self.assertEqual(json.loads((state_dir / "state.json").read_text())["latest_event_key"], known)
            _STATUS_FIXTURE = "status_upcoming.json"
            send.reset_mock()
            self._scan(send)
        heading = crw.i18n.t("notice.new_event.title", "en")
        self.assertFalse([c for c in send.call_args_list if c.args[1].startswith(heading)])

    def test_zero_turns_the_alert_off(self):
        with isolated_run(dict(self.DOWN, blind_alert_after=0)), \
                mock.patch.object(crw, "send_telegram") as send:
            self.assertEqual([self._scan(send) for _ in range(3)], [0, 0, 0])

    def test_a_failed_daily_counts_but_stays_due(self):
        with isolated_run(dict(self.DOWN, daily_time="00:00", timezone="UTC")) as (state_dir, _), \
                mock.patch.object(crw, "send_telegram") as send:
            self._scan(send, mode="daily")
            state = json.loads((state_dir / "state.json").read_text())
        self.assertEqual(state["blind_streak"], 1)
        self.assertNotIn("last_daily_date", state)

    def test_manual_check_leaves_the_streak_alone(self):
        with isolated_run(self.DOWN) as (state_dir, _), mock.patch.object(crw, "send_telegram"), \
                contextlib.redirect_stdout(io.StringIO()):
            crw.run_check("manual", notify=False)
            self.assertFalse((state_dir / "state.json").exists())


class ManualCheckTests(unittest.TestCase):
    def test_manual_check_prints_configured_timezone(self):
        import contextlib as _c
        import io

        out = io.StringIO()
        with isolated_run({"timezone": "UTC"}) as (_state_dir, _log_dir), \
                mock.patch.object(console, "sys", SimpleNamespace(platform="darwin", stdout=out)), \
                mock.patch.object(crw.host_identity, "device_label", return_value="💻 Test laptop · TEST******"):
            with _c.redirect_stdout(out):
                code = crw.run_check("manual", notify=False)
            self.assertEqual(code, 0)
            self.assertIn("UTC", out.getvalue())
            self.assertNotIn("UTC+8", out.getvalue())
            self.assertTrue(out.getvalue().endswith("💻 Test laptop · TEST******\n"))


if __name__ == "__main__":
    unittest.main()
