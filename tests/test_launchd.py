import pathlib, plistlib, subprocess, sys, tempfile, unittest
ROOT = pathlib.Path(__file__).parents[1]

class LaunchdTests(unittest.TestCase):
    def test_render(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d); out = root/"agents"; logs = root/"logs"
            program = root/"bin"/"codex-reset-watch"
            subprocess.check_call([sys.executable, str(ROOT/"scripts/render_launchd.py"),
                                   "--program", str(program), "--out-dir", str(out), "--log-dir", str(logs)])
            daily = plistlib.loads((out/"com.wes.codex-reset-watch.daily.plist").read_bytes())
            monitor = plistlib.loads((out/"com.wes.codex-reset-watch.monitor.plist").read_bytes())
            self.assertEqual(daily["ProgramArguments"], [str(program), "daily"])
            self.assertEqual(daily["StartCalendarInterval"], {"Hour":10,"Minute":0})
            self.assertEqual(monitor["ProgramArguments"], [str(program), "monitor"])
            self.assertEqual(len(monitor["StartCalendarInterval"]), 12)
            self.assertEqual(monitor["StartCalendarInterval"][0], {"Hour":0,"Minute":5})
            self.assertTrue(daily["RunAtLoad"])

if __name__ == "__main__": unittest.main()
