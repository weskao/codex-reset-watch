"""Console compatibility without changing notification or export payloads."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

import codex_reset_watch as crw
from codex_reset_watch import config, ui


class ConsoleTests(unittest.TestCase):
    def test_schedule_output_uses_ascii_on_windows_only(self):
        for platform, marker in (("win32", "[OK]"), ("darwin", "✅")):
            with self.subTest(platform=platform), \
                    mock.patch("sys.platform", platform), \
                    mock.patch.object(crw, "load_config", return_value=dict(config.DEFAULTS)), \
                    mock.patch.object(crw.scheduler, "apply", return_value=("test", [])), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(crw.apply_schedule_cmd(), 0)
                self.assertIn(marker, out.getvalue())
                if platform == "win32":
                    self.assertNotIn("✅", out.getvalue())
                    self.assertNotIn("◆", out.getvalue())

    def test_windows_stderr_reports_readable_error(self):
        args = crw.build_parser().parse_args(["check"])
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(crw, "run_check", side_effect=OSError("test error")), \
                contextlib.redirect_stderr(io.StringIO()) as out:
            self.assertEqual(crw._dispatch(args), 1)
            self.assertEqual(out.getvalue(), "[ERROR] test error\n")

    def test_menu_converts_before_clipping_and_preserves_ansi(self):
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(ui.shutil, "get_terminal_size", return_value=os.terminal_size((12, 30))):
            out = io.StringIO()
            ui._draw(["\033[32m✅ ready\033[0m", "▸ ━━ ◆ ←→ ⏎"], out, 0)
            text = out.getvalue()
            self.assertIn("\033[32m[OK] ready", text)
            self.assertNotIn("✅", text)
            self.assertIn("> == *", text)

    def test_windows_json_export_preserves_unicode(self):
        cfg = dict(config.DEFAULTS)
        cfg["timezone"] = "UTC"
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(config, "export_payload", return_value={"label": "🛰️ 中文"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(ui.export_settings(cfg, "-", "en")[0])
            self.assertEqual(json.loads(out.getvalue()), {"label": "🛰️ 中文"})
