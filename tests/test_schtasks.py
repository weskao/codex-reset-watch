import unittest
from scripts import schtasks

class SchtasksTests(unittest.TestCase):
    def test_daily_task_runs_once_a_day_at_ten(self):
        cmd = schtasks.daily_task_command(r"C:\bin\codex-reset-watch.exe")
        self.assertEqual(cmd, [
            "schtasks", "/Create", "/F", "/TN", "CodexResetWatchDaily",
            "/TR", r'"C:\bin\codex-reset-watch.exe" daily',
            "/SC", "DAILY", "/ST", "10:00",
        ])

    def test_monitor_task_runs_every_two_hours(self):
        cmd = schtasks.monitor_task_command(r"C:\bin\codex-reset-watch.exe")
        self.assertEqual(cmd, [
            "schtasks", "/Create", "/F", "/TN", "CodexResetWatchMonitor",
            "/TR", r'"C:\bin\codex-reset-watch.exe" monitor',
            "/SC", "HOURLY", "/MO", "2", "/ST", "00:05",
        ])

    def test_delete_task_command(self):
        self.assertEqual(
            schtasks.delete_task_command("CodexResetWatchDaily"),
            ["schtasks", "/Delete", "/F", "/TN", "CodexResetWatchDaily"],
        )

if __name__ == "__main__":
    unittest.main()
