# Codex Reset Watch

macOS `launchd` monitor for `codex-resets.com`, with Telegram notifications sent directly via the Bot API (`src/codex_reset_watch/telegram_notify.py`, stdlib-only).

This version is **uv-native**:

- project/dependency metadata: `pyproject.toml`
- locked resolution: `uv.lock`
- preferred Python: `.python-version` (`3.13`)
- runtime: uv-managed Python
- installed CLI: `uv tool install`
- CLI entry points: `codex-reset-watch` and `crw`
- launchd calls the installed uv-tool executable directly; it does **not** depend on shell activation or `.venv`

## Installed paths

| Purpose | Path |
|---|---|
| Project | `~/Documents/Workspace/codex-reset-watch` |
| CLI | `~/scripts/codex-reset-watch` (symlinked to `/opt/homebrew/bin/codex-reset-watch`) |
| Short CLI | `~/scripts/crw` (symlinked to `/opt/homebrew/bin/crw`) |
| Config | `~/Library/Application Support/codex-reset-watch/config.json` |
| State | `~/Library/Application Support/codex-reset-watch/state.json` |
| Logs | `~/Library/Logs/codex-reset-watch/` |
| Daily LaunchAgent | `~/Library/LaunchAgents/com.wes.codex-reset-watch.daily.plist` |
| 2-hour monitor | `~/Library/LaunchAgents/com.wes.codex-reset-watch.monitor.plist` |

> `~` is used in documentation and user-facing output. `launchd` plist files contain expanded absolute paths because `launchd` does not expand `~`.

## 1. Install uv

If `uv` is already installed, skip this section.

Homebrew:

```bash
brew install uv
```

Or Astral's official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify:

```bash
uv --version
```

## 2. Install Codex Reset Watch

Export Telegram credentials first — `make install` bakes them into the generated LaunchAgent plists, since launchd jobs don't inherit your shell env:

```bash
export TG_BOT_TOKEN="..."
export TG_CHAT_ID="..."
```

From the extracted project folder:

```bash
make test
make install
```

`make install` will:

1. copy the project to `~/Documents/Workspace/codex-reset-watch`
2. run `uv python install 3.13`
3. install the package as a persistent isolated uv tool
4. put `codex-reset-watch` and `crw` in `~/scripts`, and symlink both into `/opt/homebrew/bin` (already on `PATH`, no `~/.zshrc` edit needed)
5. create config/state/log directories
6. generate both LaunchAgent plist files
7. bootstrap/reload both LaunchAgents

Verify:

```bash
uv tool list
crw doctor
crw check
```

Expected `uv tool list` entry:

```text
codex-reset-watch v0.1.0
- codex-reset-watch
- crw
```

## 3. Common commands

Immediate API check + terminal output + Telegram notification:

```bash
crw check
```

Alias of `check`:

```bash
crw update
```

Check without Telegram:

```bash
crw check --no-notify
```

Health check:

```bash
crw doctor
```

Recent logs:

```bash
crw logs -n 50
```

Force the daily path for testing:

```bash
crw daily --force
```

Run monitor path manually:

```bash
crw monitor
```

## 4. uv project commands

For development inside the project:

```bash
cd ~/Documents/Workspace/codex-reset-watch
uv sync
uv run crw --help
uv run crw check --no-notify
uv run python -m unittest discover -s tests -v
```

Inspect uv-managed tool locations:

```bash
uv tool list
uv tool dir
uv tool dir --bin
uv python list --only-installed
```

The installer intentionally sets `UV_TOOL_BIN_DIR=~/scripts` during tool installation so the stable paths used by launchd are:

```text
~/scripts/codex-reset-watch
~/scripts/crw
```

## 5. Reinstall after source changes

Recommended:

```bash
cd ~/Documents/Workspace/codex-reset-watch
make install
```

Or reinstall only the uv tool executable/environment:

```bash
cd ~/Documents/Workspace/codex-reset-watch
make tool-reinstall
```

`make install` is preferred when `launchd` configuration or installer files also changed.

## 6. launchd schedule

### Daily job

`com.wes.codex-reset-watch.daily`

- scheduled for 10:00 local macOS time
- `RunAtLoad=true`
- Python-side daily gate prevents duplicate daily work
- if the Mac sleeps through 10:00, `StartCalendarInterval` is eligible to run after wake
- if the Mac was powered off and starts after 10:00, `RunAtLoad` plus the daily gate provides the catch-up path

### Monitor job

`com.wes.codex-reset-watch.monitor`

Runs every two hours at minute `05`:

```text
00:05
02:05
04:05
06:05
08:05
10:05
12:05
14:05
16:05
18:05
20:05
22:05
```

It also has `RunAtLoad=true`.

The program uses a lock so overlapping daily/monitor invocations do not corrupt state or duplicate work.

## 7. Telegram

Sends directly through the Telegram Bot API via `telegram_notify.py` — no external script dependency. Set both env vars before running:

```bash
export TG_BOT_TOKEN="..."
export TG_CHAT_ID="..."
```

The project does not copy or hardcode credentials; both vars are read at runtime only. `crw doctor` reports whether they're set.

Then:

```bash
crw check
```

## 8. UTC+8 and countdown

Upcoming reset information is rendered in UTC+8 and includes remaining time with:

- maximum unit: Day
- minimum unit: minutes

Example:

```text
🕒 預測窗口截止：2026-09-21 23:09 UTC+8
⏳ 距離現在：2 Days 1 hour 35 minutes
```

## 9. Event parsing resilience

`event_from_dict` tolerates upstream API schema drift instead of assuming one fixed shape:

- Timestamp and source-URL keys match both `snake_case` and `camelCase` variants
  (`created_at`/`createdAt`, `source_url`/`sourceUrl`, etc.), searched up to 3 levels deep so a
  nested `source: {url: ...}` object resolves correctly.
- If no timestamp field is present or parseable, the event ID or source URL is checked for an
  embedded X/Twitter Snowflake post ID, which encodes its own creation time. This keeps
  historical reset times available even if the upstream schema changes or omits its timestamp
  field. API-provided timestamps always take priority over this fallback.

## 10. Logs and disk usage

Application logs:

```text
~/Library/Logs/codex-reset-watch/events.jsonl
~/Library/Logs/codex-reset-watch/api.jsonl
```

Default config limits each rotated application/API log to roughly 2 MiB with 3 backups:

```json
{
  "max_log_bytes": 2097152,
  "log_backups": 3
}
```

Launchd stdout/stderr logs are separately trimmed by the installer when they exceed 512 KiB.

## 11. Tests

Run everything through uv:

```bash
make test
```

Individual groups:

```bash
make test-unit
make test-integration
```

The integration test starts a local HTTP server and exercises the real HTTP client and response normalization path without contacting the production API.

## 12. launchd status

```bash
make launch-status
```

Or:

```bash
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.daily
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.monitor
```

## 13. Uninstall

```bash
cd ~/Documents/Workspace/codex-reset-watch
make uninstall
```

This unloads/removes LaunchAgents and uninstalls the uv tool. Project files, config, state, and logs are intentionally retained.

To remove retained data manually:

```bash
rm -rf ~/Library/Application\ Support/codex-reset-watch
rm -rf ~/Library/Logs/codex-reset-watch
```

## Why launchd does not call `uv run`

`uv run` is ideal for project development because it discovers the project, syncs the project environment when needed, and executes within that environment. For a long-running background setup, this project instead installs the CLI with `uv tool install` and lets launchd call the installed executable directly. That keeps each scheduled invocation small and avoids depending on the current working directory, shell startup files, PATH activation, or project `.venv` state.
