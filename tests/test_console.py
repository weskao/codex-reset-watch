"""Console compatibility without changing notification or export payloads."""
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

import codex_reset_watch as crw
from codex_reset_watch import config, console, ui


class ConsoleTests(unittest.TestCase):
    def test_host_identity_script_still_runs_directly(self):
        script = pathlib.Path(crw.__file__).with_name("host_identity.py")
        result = subprocess.run([sys.executable, str(script), "--emoji"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {"[PC]", "[PHONE]"} if sys.platform == "win32" else {"💻", "🖥️", "📱"}
        self.assertIn(result.stdout.strip(), expected)

    def test_legacy_codepage_keeps_chinese_and_readable_status(self):
        for encoding in ("cp950", "cp1252", "utf-8"):
            with self.subTest(encoding=encoding), mock.patch("sys.platform", "win32"):
                data = io.BytesIO()
                out = io.TextIOWrapper(data, encoding=encoding)
                console.print_console("🛰️ 中文 ✅ ❌ 🪐 \ue0b6", file=out, flush=True)
                text = data.getvalue().decode(encoding)
                self.assertIn("[CHECKED]", text)
                self.assertIn("[OK] [ERROR] * *", text)
                self.assertIn("中文" if encoding != "cp1252" else r"\u4e2d\u6587", text)

    def test_macos_and_linux_keep_exact_original_message(self):
        text = "🛰️ 中文 ✅ ◆ ━━ ←→ \033[32m✓\033[0m"
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform), mock.patch("sys.platform", platform):
                out = io.StringIO()
                console.print_console(text, file=out)
                self.assertEqual(out.getvalue(), text + "\n")

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

    def test_windows_long_menu_value_keeps_edit_affordance(self):
        cfg = dict(config.DEFAULTS, api_base="https://example.test/" + "a" * 150)
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(ui, "panel_width", return_value=72), \
                mock.patch.object(ui.shutil, "get_terminal_size", return_value=os.terminal_size((74, 30))):
            row = ui._row(ui.Paint(False), 1, config.BY_KEY["api_base"], cfg["api_base"], "en", selected=True)
            out = io.StringIO()
            ui._draw([row], out, 0)
            visible = ui.strip_ansi(out.getvalue()).split("\033[K")[0].rstrip()
            self.assertTrue(visible.endswith("<"), visible)
            self.assertLessEqual(len(visible), 72)

    def test_windows_json_export_preserves_unicode(self):
        cfg = dict(config.DEFAULTS)
        cfg["timezone"] = "UTC"
        with mock.patch("sys.platform", "win32"), \
                mock.patch.object(config, "export_payload", return_value={"label": "🛰️ 中文"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(ui.export_settings(cfg, "-", "en")[0])
            self.assertEqual(json.loads(out.getvalue()), {"label": "🛰️ 中文"})
