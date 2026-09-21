LaunchAgent plist files are generated at install time by `scripts/render_launchd.py` because macOS requires absolute paths to the actual Python interpreter, project directory, HOME and log directory.

Installed files:
- `~/Library/LaunchAgents/com.wes.codex-reset-watch.daily.plist`
- `~/Library/LaunchAgents/com.wes.codex-reset-watch.monitor.plist`

Both invoke the absolute path of the installed CLI, which is uv's own bin directory — `~/.local/bin/codex-reset-watch` unless `$UV_TOOL_BIN_DIR`/`$XDG_BIN_HOME`/`$CRW_BIN_DIR` says otherwise (`scheduler.bin_dir()`). Installing anywhere else is what once broke these jobs: uv deletes the entrypoints recorded in its receipt on every reinstall, so a plain `uv tool install` relocated the CLI and left both plists pointing at a path that no longer existed — silently, since launchd reports nothing for a program it cannot find.

`crw doctor` guards against that: its **Scheduled CLI** line fails when the binary these plists invoke is gone. Re-run `uv run python scripts/install.py` to re-render and re-bootstrap them.

Inspect the live jobs with:

```bash
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.daily
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.monitor
```
