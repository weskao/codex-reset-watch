import unittest

from codex_reset_watch import paths


class PathsTests(unittest.TestCase):
    def test_darwin_config_dir(self):
        p = paths.app_config_dir(platform="darwin", home="/Users/user", environ={})
        self.assertEqual(str(p), "/Users/user/Library/Application Support/codex-reset-watch")

    def test_darwin_state_dir_matches_config_dir(self):
        p = paths.app_state_dir(platform="darwin", home="/Users/user", environ={})
        self.assertEqual(str(p), "/Users/user/Library/Application Support/codex-reset-watch")

    def test_darwin_log_dir(self):
        p = paths.app_log_dir(platform="darwin", home="/Users/user", environ={})
        self.assertEqual(str(p), "/Users/user/Library/Logs/codex-reset-watch")

    def test_windows_config_dir_uses_appdata(self):
        p = paths.app_config_dir(platform="win32", home="C:\\Users\\user", environ={"APPDATA": "C:\\Users\\user\\AppData\\Roaming"})
        self.assertEqual(str(p), "C:\\Users\\user\\AppData\\Roaming\\codex-reset-watch")

    def test_windows_config_dir_falls_back_without_appdata(self):
        p = paths.app_config_dir(platform="win32", home="C:\\Users\\user", environ={})
        self.assertEqual(str(p), "C:\\Users\\user\\AppData\\Roaming\\codex-reset-watch")

    def test_windows_state_dir_uses_localappdata(self):
        p = paths.app_state_dir(platform="win32", home="C:\\Users\\user", environ={"LOCALAPPDATA": "C:\\Users\\user\\AppData\\Local"})
        self.assertEqual(str(p), "C:\\Users\\user\\AppData\\Local\\codex-reset-watch")

    def test_windows_log_dir_is_subfolder_of_localappdata(self):
        p = paths.app_log_dir(platform="win32", home="C:\\Users\\user", environ={"LOCALAPPDATA": "C:\\Users\\user\\AppData\\Local"})
        self.assertEqual(str(p), "C:\\Users\\user\\AppData\\Local\\codex-reset-watch\\Logs")

    def test_linux_config_dir_uses_xdg_config_home(self):
        p = paths.app_config_dir(platform="linux", home="/home/user", environ={"XDG_CONFIG_HOME": "/home/user/.config"})
        self.assertEqual(str(p), "/home/user/.config/codex-reset-watch")

    def test_linux_config_dir_falls_back_without_xdg(self):
        p = paths.app_config_dir(platform="linux", home="/home/user", environ={})
        self.assertEqual(str(p), "/home/user/.config/codex-reset-watch")

    def test_linux_state_dir_uses_xdg_state_home(self):
        p = paths.app_state_dir(platform="linux", home="/home/user", environ={"XDG_STATE_HOME": "/home/user/.local/state"})
        self.assertEqual(str(p), "/home/user/.local/state/codex-reset-watch")

    def test_linux_log_dir_is_subfolder_of_state_dir(self):
        p = paths.app_log_dir(platform="linux", home="/home/user", environ={})
        self.assertEqual(str(p), "/home/user/.local/state/codex-reset-watch/log")

    def test_default_platform_uses_sys_platform_and_returns_concrete_path(self):
        import pathlib
        p = paths.app_config_dir()
        self.assertIsInstance(p, pathlib.Path)

if __name__ == "__main__":
    unittest.main()
