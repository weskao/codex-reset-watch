LaunchAgent plist files are generated at install time by `scripts/render_launchd.py` because macOS requires absolute paths to the actual Python interpreter, project directory, HOME and log directory.

Installed files:
- `~/Library/LaunchAgents/com.wes.codex-reset-watch.daily.plist`
- `~/Library/LaunchAgents/com.wes.codex-reset-watch.monitor.plist`
