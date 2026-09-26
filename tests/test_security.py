"""Security boundaries, using synthetic credentials and isolated files only."""
import contextlib
import io
import json
import os
import pathlib
import plistlib
import stat
import subprocess
import tempfile
import unittest
import warnings
import getpass
from unittest import mock

import codex_reset_watch as crw
from codex_reset_watch import config, paths, scheduler, secrets_store, ui
from scripts import install, render_launchd, render_systemd


class SecurityTests(unittest.TestCase):
    def test_portable_settings_cannot_disclose_or_replace_local_identity(self):
        local = dict(telegram_bot_token="fake-token", telegram_chat_id="111",
                     log_dir="/private/logs", state_dir="/private/state",
                     api_base="https://example.com", status_path="/status",
                     resets_path="/resets", user_agent="private-agent")
        self.assertFalse(set(local) & config.export_payload(dict(config.DEFAULTS, **local)).keys())
        updates, skipped = config.import_updates(dict(local, daily_time="09:00"))
        self.assertEqual(updates, {"daily_time": "09:00"})
        self.assertEqual(set(skipped), set(local))

    def test_settings_reject_embedded_controls_without_echoing_input(self):
        for key in ("telegram_bot_token", "telegram_chat_id", "log_dir", "api_base", "timezone"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as caught:
                    config.coerce(config.BY_KEY[key], "fake\nExecStartPre=/usr/bin/true")
                self.assertNotIn("fake", str(caught.exception))

    def test_scheduler_rejects_injection_even_outside_config_parser(self):
        with self.assertRaises(ValueError):
            scheduler.systemd_units("/usr/bin/true", pathlib.Path("/logs\nExecStartPre=/usr/bin/true\n#"), config.DEFAULTS)

    def test_schedulers_never_embed_credentials(self):
        for render in (scheduler.launchd_plists, scheduler.systemd_units):
            with self.subTest(render=render.__name__), self.assertRaises(ValueError):
                render("/bin/crw", pathlib.Path("/logs"), config.DEFAULTS, {"TG_BOT_TOKEN": "fake-token"})

    def test_systemd_rejects_injected_environment_lines(self):
        with self.assertRaises(ValueError):
            scheduler.systemd_units("/bin/crw", pathlib.Path("/logs"), config.DEFAULTS,
                                    {"OTHER": "value\nExecStartPre=/usr/bin/true"})

    def test_missing_store_cannot_fall_back_to_scheduler_plaintext(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(config, "load", return_value=dict(config.DEFAULTS)):
            with self.assertRaises(ValueError):
                scheduler.job_env({"TG_BOT_TOKEN": "fake-token"}, {})

    def test_failed_launchd_migration_stops_job_and_scrubs_legacy_plist(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "com.wes.codex-reset-watch.daily.plist"
            old.write_bytes(plistlib.dumps({"EnvironmentVariables": {"TG_BOT_TOKEN": "fake-token"}}))
            calls = []
            with mock.patch.object(scheduler, "launch_agents_dir", return_value=root), mock.patch.object(scheduler, "_launchctl", side_effect=lambda *args, **kwargs: calls.append(args)), mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ValueError):
                    scheduler.apply_launchd("/bin/crw", root / "logs", dict(config.DEFAULTS))
            self.assertNotIn("fake-token", old.read_text())
            self.assertTrue(any(call[0] == "bootout" for call in calls))

    def test_failed_systemd_migration_stops_timer_and_scrubs_legacy_unit(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "codex-reset-watch-daily.service"
            old.write_text("[Service]\nEnvironment=TG_BOT_TOKEN=fake-token\n")
            calls = []
            with mock.patch.object(scheduler, "systemd_user_dir", return_value=root), mock.patch.object(scheduler.subprocess, "run", side_effect=lambda argv, **kwargs: calls.append(argv)), mock.patch.object(scheduler.subprocess, "check_call"), mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ValueError):
                    scheduler.apply_systemd("/bin/crw", root / "logs", dict(config.DEFAULTS))
            self.assertNotIn("fake-token", old.read_text())
            self.assertTrue(any("disable" in call for call in calls))

    def test_launchd_migrates_legacy_token_to_store_before_rewriting(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "com.wes.codex-reset-watch.daily.plist"
            old.write_bytes(plistlib.dumps({"EnvironmentVariables": {"TG_BOT_TOKEN": "fake-token", "TG_CHAT_ID": "111"}}))
            vault = {}
            cfg = dict(config.DEFAULTS)
            with mock.patch.object(scheduler, "launch_agents_dir", return_value=root), mock.patch.object(scheduler, "_launchctl"), mock.patch.dict(os.environ, {"CRW_CONFIG": str(root / "config.json")}, clear=True), mock.patch.object(secrets_store, "available", return_value=True), mock.patch.object(secrets_store, "set", side_effect=lambda key, value: vault.__setitem__(key, value) or True):
                scheduler.apply_launchd("/bin/crw", root / "logs", cfg)
            self.assertEqual(vault["telegram_bot_token"], "fake-token")
            self.assertEqual(json.loads((root / "config.json").read_text())["telegram_chat_id"], "111")
            self.assertNotIn("fake-token", old.read_text())

    def test_launchd_migrates_credentials_split_between_plists(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            for job, env in (("daily", {"TG_CHAT_ID": "111"}),
                             ("monitor", {"TG_BOT_TOKEN": "fake-token"})):
                (root / f"com.wes.codex-reset-watch.{job}.plist").write_bytes(
                    plistlib.dumps({"EnvironmentVariables": env}))
            vault = {}
            cfg = dict(config.DEFAULTS)
            with mock.patch.object(scheduler, "launch_agents_dir", return_value=root), mock.patch.object(scheduler, "_launchctl"), mock.patch.dict(os.environ, {"CRW_CONFIG": str(root / "config.json")}, clear=True), mock.patch.object(secrets_store, "available", return_value=True), mock.patch.object(secrets_store, "set", side_effect=lambda key, value: vault.__setitem__(key, value) or True):
                scheduler.apply_launchd("/bin/crw", root / "logs", cfg)
            self.assertEqual(vault["telegram_bot_token"], "fake-token")
            self.assertEqual(json.loads((root / "config.json").read_text())["telegram_chat_id"], "111")

    def test_launchd_failed_stop_is_reported_after_disk_scrub(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "com.wes.codex-reset-watch.daily.plist"
            old.write_bytes(plistlib.dumps({"EnvironmentVariables": {"TG_BOT_TOKEN": "fake-token"}}))
            def launchctl(*args, **kwargs):
                if args[0] == "bootout":
                    raise OSError("loaded job could not be stopped")
            with mock.patch.object(scheduler, "launch_agents_dir", return_value=root), mock.patch.object(scheduler, "_launchctl", side_effect=launchctl), mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(OSError, "could not be stopped"):
                    scheduler.apply_launchd("/bin/crw", root / "logs", dict(config.DEFAULTS))
            self.assertNotIn("fake-token", old.read_text())

    def test_systemd_failed_stop_is_reported_after_disk_scrub(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "codex-reset-watch-daily.service"
            old.write_text("[Service]\nEnvironment=TG_BOT_TOKEN=fake-token\n")
            def systemctl(argv, **kwargs):
                if "disable" in argv:
                    raise OSError("loaded timer could not be stopped")
            with mock.patch.object(scheduler, "systemd_user_dir", return_value=root), mock.patch.object(scheduler.subprocess, "run", side_effect=systemctl), mock.patch.object(scheduler.subprocess, "check_call"), mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(OSError, "could not be stopped"):
                    scheduler.apply_systemd("/bin/crw", root / "logs", dict(config.DEFAULTS))
            self.assertNotIn("fake-token", old.read_text())

    def test_launchd_failed_bootout_detects_still_loaded_job(self):
        with mock.patch.object(scheduler.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 1, b"", b"failed"),
            subprocess.CompletedProcess([], 0, b"loaded", b""),
        ]):
            with self.assertRaisesRegex(OSError, "may retain old credentials"):
                scheduler._launchctl("bootout", "gui/1/com.wes.codex-reset-watch.daily")

    def test_systemd_failed_disable_detects_active_timer(self):
        with mock.patch.object(scheduler.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 1, b"", b"failed"),
            subprocess.CompletedProcess([], 0, "active", ""),
            subprocess.CompletedProcess([], 0, b"", b""),
        ]):
            with self.assertRaisesRegex(OSError, "may retain old credentials"):
                scheduler._stop_systemd_timer("daily")

    def test_systemd_attempts_service_stop_even_if_timer_stop_fails(self):
        calls = []
        def systemctl(argv, **kwargs):
            calls.append(argv)
            if "disable" in argv:
                return subprocess.CompletedProcess(argv, 1, b"", b"failed")
            if "is-active" in argv:
                return subprocess.CompletedProcess(argv, 0, "active", "")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(scheduler.subprocess, "run", side_effect=systemctl):
            with self.assertRaises(OSError):
                scheduler._stop_systemd_timer("daily")
        self.assertTrue(any("stop" in call and "codex-reset-watch-daily.service" in call
                            for call in calls))

    def test_systemd_stop_also_stops_a_running_service(self):
        calls = []
        with mock.patch.object(scheduler.subprocess, "run", side_effect=lambda argv, **kwargs: calls.append(argv)):
            scheduler._stop_systemd_timer("daily")
        self.assertTrue(any("stop" in call and "codex-reset-watch-daily.service" in call
                            for call in calls))

    def test_systemd_migrates_legacy_token_to_store_before_rewriting(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            old = root / "codex-reset-watch-daily.service"
            old.write_text("[Service]\nEnvironment=TG_BOT_TOKEN=fake-token\nEnvironment=TG_CHAT_ID=111\n")
            vault = {}
            cfg = dict(config.DEFAULTS)
            with mock.patch.object(scheduler, "systemd_user_dir", return_value=root), mock.patch.object(scheduler.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), mock.patch.object(scheduler.subprocess, "check_call"), mock.patch.dict(os.environ, {"CRW_CONFIG": str(root / "config.json")}, clear=True), mock.patch.object(secrets_store, "available", return_value=True), mock.patch.object(secrets_store, "set", side_effect=lambda key, value: vault.__setitem__(key, value) or True):
                scheduler.apply_systemd("/bin/crw", root / "logs", cfg)
            self.assertEqual(vault["telegram_bot_token"], "fake-token")
            self.assertEqual(json.loads((root / "config.json").read_text())["telegram_chat_id"], "111")
            self.assertNotIn("fake-token", old.read_text())

    def test_systemd_successful_migration_stops_legacy_running_service(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root / "codex-reset-watch-daily.service").write_text(
                "[Service]\nEnvironment=TG_BOT_TOKEN=fake-token\n")
            calls = []
            with mock.patch.object(scheduler, "systemd_user_dir", return_value=root), mock.patch.object(scheduler.subprocess, "run", side_effect=lambda argv, **kwargs: calls.append(argv)), mock.patch.object(scheduler.subprocess, "check_call"), mock.patch.dict(os.environ, {"CRW_CONFIG": str(root / "config.json")}, clear=True), mock.patch.object(secrets_store, "available", return_value=True), mock.patch.object(secrets_store, "set", return_value=True):
                scheduler.apply_systemd("/bin/crw", root / "logs", dict(config.DEFAULTS))
            self.assertTrue(any("stop" in call and "codex-reset-watch-daily.service" in call
                                for call in calls))

    def test_render_wrappers_scrub_old_files_when_vault_is_missing(self):
        for wrapper, name, content in (
            (render_launchd, "com.wes.codex-reset-watch.daily.plist",
             plistlib.dumps({"EnvironmentVariables": {"TG_BOT_TOKEN": "fake-token"}})),
            (render_systemd, "codex-reset-watch-daily.service",
             b"[Service]\nEnvironment=TG_BOT_TOKEN=fake-token\n"),
        ):
            with self.subTest(wrapper=wrapper.__name__), tempfile.TemporaryDirectory() as d:
                root = pathlib.Path(d)
                out = root / "jobs"
                out.mkdir()
                old = out / name
                old.write_bytes(content)
                args = ["render", "--program", "/bin/crw", "--out-dir", str(out),
                        "--log-dir", str(root / "logs")]
                with mock.patch("sys.argv", args), mock.patch.object(wrapper.config, "load", return_value=dict(config.DEFAULTS)), mock.patch.object(secrets_store, "available", return_value=False), mock.patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValueError):
                        wrapper.main()
                self.assertNotIn("fake-token", old.read_text())

    def test_failed_secret_save_preserves_existing_config(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            target.write_text('{"daily_time":"08:00"}')
            with mock.patch.object(secrets_store, "set", return_value=False):
                with self.assertRaises(OSError):
                    config.save(dict(config.DEFAULTS, telegram_bot_token="fake-token"), target)
            self.assertEqual(json.loads(target.read_text()), {"daily_time": "08:00"})

    def test_failed_plaintext_migration_does_not_destroy_only_copy(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            target.write_text('{"telegram_bot_token":"fake-token"}')
            with mock.patch.dict(os.environ, {"CRW_CONFIG": str(target)}), mock.patch.object(secrets_store, "set", return_value=False):
                with self.assertRaises(OSError):
                    config.load()
            self.assertEqual(json.loads(target.read_text())["telegram_bot_token"], "fake-token")

    def test_installer_reports_failed_token_storage(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            answers = iter(["y", "111"])
            with mock.patch.dict(os.environ, {"CRW_CONFIG": str(target)}), mock.patch.object(secrets_store, "set", return_value=False):
                out = io.StringIO()
                ok = install.prompt_telegram_setup(dict(config.DEFAULTS), ask=lambda _: next(answers),
                                                   ask_secret=lambda _: "fake-token", out=out)
            self.assertFalse(ok)
            self.assertNotIn("✅", out.getvalue())

    def test_installer_does_not_accept_token_when_terminal_cannot_hide_input(self):
        def unsafe(_):
            warnings.warn("terminal echo unavailable", getpass.GetPassWarning)
            return "fake-token"
        answers = iter(["y", "111"])
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"CRW_CONFIG": str(pathlib.Path(d) / "config.json")}), mock.patch.object(secrets_store, "set", return_value=True):
            out = io.StringIO()
            with warnings.catch_warnings(record=True):
                result = install.prompt_telegram_setup(dict(config.DEFAULTS), ask=lambda _: next(answers),
                                                       ask_secret=unsafe, out=out)
            self.assertFalse(result)
            self.assertNotIn("fake-token", out.getvalue())

    def test_failed_deletion_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"CRW_CONFIG": str(pathlib.Path(d) / "config.json")}), mock.patch.object(secrets_store, "delete", return_value=False):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = crw.config_cmd(crw.build_parser().parse_args(["config", "--set", "telegram_bot_token="]))
            self.assertNotEqual(code, 0)
            self.assertNotIn("removed from", out.getvalue())

    def test_token_is_accepted_from_stdin_without_echo(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            saved = {}
            with mock.patch.dict(os.environ, {"CRW_CONFIG": str(target)}), mock.patch("sys.stdin", io.StringIO("fake-token\n")), mock.patch.object(secrets_store, "set", side_effect=lambda key, value: saved.__setitem__(key, value) or True):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = crw.config_cmd(crw.build_parser().parse_args(["config", "--token-stdin"]))
            self.assertEqual(code, 0)
            self.assertEqual(saved["telegram_bot_token"], "fake-token")
            self.assertNotIn("fake-token", out.getvalue() + target.read_text())

    def test_token_argument_is_rejected_before_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            with mock.patch.dict(os.environ, {"CRW_CONFIG": str(target)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = crw.config_cmd(crw.build_parser().parse_args(["config", "--set", "telegram_bot_token=fake-token"]))
            self.assertNotEqual(code, 0)
            self.assertFalse(target.exists())

    def test_numbered_menu_uses_hidden_input_for_terminal_token(self):
        cfg = dict(config.DEFAULTS)
        with mock.patch("sys.stdin") as terminal, mock.patch("getpass.getpass", return_value="fake-token") as hidden:
            terminal.isatty.return_value = True
            changed = ui._edit(config.BY_KEY["telegram_bot_token"], cfg, ui.Paint(False),
                               terminal, io.StringIO())
        self.assertTrue(changed)
        hidden.assert_called_once()
        self.assertEqual(cfg["telegram_bot_token"], "fake-token")

    def test_chat_id_can_be_read_in_settings(self):
        rendered = ui.render_settings(dict(config.DEFAULTS, telegram_chat_id="123456789"), paint=ui.Paint(False))
        self.assertIn("123456789", rendered)

    def test_export_stdout_is_valid_json(self):
        with mock.patch.object(crw, "load_config", return_value=dict(config.DEFAULTS)):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(crw.config_cmd(crw.build_parser().parse_args(["config", "--export", "-"])), 0)
        self.assertIsInstance(json.loads(out.getvalue()), dict)

    @unittest.skipIf(os.name == "nt", "POSIX permissions and symlinks")
    def test_export_does_not_follow_symlink_or_truncate_target(self):
        with tempfile.TemporaryDirectory() as d:
            victim = pathlib.Path(d) / "victim"
            victim.write_text("untouched")
            link = pathlib.Path(d) / "export.json"
            link.symlink_to(victim)
            ok, _ = ui.export_settings(dict(config.DEFAULTS), str(link), "en")
            self.assertFalse(ok)
            self.assertEqual(victim.read_text(), "untouched")

    def test_private_write_keeps_previous_file_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            target.write_text("before")
            with mock.patch("os.replace", side_effect=OSError("simulated failure")):
                with self.assertRaises(OSError):
                    paths.write_private(target, "after")
            self.assertEqual(target.read_text(), "before")
            self.assertEqual(list(pathlib.Path(d).iterdir()), [target])

    @unittest.skipIf(os.name == "nt", "POSIX mode")
    def test_private_write_has_owner_permissions_before_content(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            original = os.fdopen
            def inspect(fd, *args, **kwargs):
                self.assertEqual(stat.S_IMODE(os.fstat(fd).st_mode), 0o600)
                return original(fd, *args, **kwargs)
            with mock.patch("os.fdopen", side_effect=inspect):
                paths.write_private(target, "private")
            self.assertEqual(target.read_text(), "private")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    @unittest.skipUnless(os.name == "nt", "Windows ACL")
    def test_private_write_has_protected_owner_only_windows_acl(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "config.json"
            paths.write_private(target, "private")
            check = (
                "$a=Get-Acl -LiteralPath ([Console]::In.ReadToEnd()); "
                "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                "if (!$a.AreAccessRulesProtected -or $a.Access.Count -ne 1 -or "
                "$a.Access[0].IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -ne $sid) { exit 1 }"
            )
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", check],
                                    input=str(target), text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
