# Codex Reset Watch

Cross-platform (macOS/Linux/Windows) monitor for `codex-resets.com`, with Telegram notifications sent directly via the Bot API (`src/codex_reset_watch/telegram_notify.py`, stdlib-only).

This version is **uv-native**:

- project/dependency metadata: `pyproject.toml`
- locked resolution: `uv.lock`
- preferred Python: `.python-version` (`3.13`)
- runtime: uv-managed Python
- installed CLI: `uv tool install`
- CLI entry points: `codex-reset-watch` and `crw`
- the OS scheduler calls the installed uv-tool executable directly; it does **not** depend on shell activation or `.venv`

Native OS scheduling backend, chosen automatically by `scripts/install.py`:

| OS | Scheduler | Renderer |
|---|---|---|
| macOS | `launchd` | `scripts/render_launchd.py` |
| Linux | `systemd --user` timers | `scripts/render_systemd.py` |
| Windows | Task Scheduler (`schtasks`) | `scripts/schtasks.py` |

## Installed paths

| Purpose | macOS | Linux | Windows |
|---|---|---|---|
| Config | `~/Library/Application Support/codex-reset-watch/config.json` | `$XDG_CONFIG_HOME/codex-reset-watch/config.json` (default `~/.config/...`) | `%APPDATA%\codex-reset-watch\config.json` |
| State | `~/Library/Application Support/codex-reset-watch/state.json` | `$XDG_STATE_HOME/codex-reset-watch/state.json` (default `~/.local/state/...`) | `%LOCALAPPDATA%\codex-reset-watch\state.json` |
| Logs | `~/Library/Logs/codex-reset-watch/` | `$XDG_STATE_HOME/codex-reset-watch/log/` | `%LOCALAPPDATA%\codex-reset-watch\Logs\` |
| Scheduler units | `~/Library/LaunchAgents/com.wes.codex-reset-watch.{daily,monitor}.plist` | `~/.config/systemd/user/codex-reset-watch-{daily,monitor}.{service,timer}` | Task Scheduler tasks `CodexResetWatchDaily` / `CodexResetWatchMonitor` |
| CLI | `~/scripts/codex-reset-watch` (symlinked to `/opt/homebrew/bin/codex-reset-watch`) | `~/scripts/codex-reset-watch` | `%USERPROFILE%\scripts\codex-reset-watch.exe` |

Resolution logic lives in `src/codex_reset_watch/paths.py`; override any of the three with `CRW_CONFIG`, `CRW_STATE_DIR`, `CRW_LOG_DIR`.

> `~` is used in documentation and user-facing output. Scheduler job files/tasks contain expanded absolute paths — none of `launchd`/`systemd`/Task Scheduler expand `~`.

## 1. Install uv

If `uv` is already installed, skip this section.

macOS (Homebrew):

```bash
brew install uv
```

Linux/macOS (Astral's official installer):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify:

```bash
uv --version
```

## 2. Install Codex Reset Watch

Export Telegram credentials first — `scripts/install.py` bakes them into the generated scheduler job (macOS/Linux) or persists them into your user environment via `setx` (Windows), since none of the three schedulers inherit your shell env:

```bash
export TG_BOT_TOKEN="..."
export TG_CHAT_ID="..."
```

```powershell
$env:TG_BOT_TOKEN = "..."
$env:TG_CHAT_ID = "..."
```

From the extracted project folder, macOS/Linux:

```bash
make test
make install
```

Windows (no `make` required):

```powershell
uv run python -m unittest discover -s tests -v
uv run python scripts/install.py
```

`scripts/install.py` will:

1. copy the project to `$CRW_INSTALL_DIR` (default `~/Documents/Workspace/codex-reset-watch`)
2. run `uv python install 3.13`
3. install the package as a persistent isolated uv tool
4. put `codex-reset-watch` and `crw` in `~/scripts` (`%USERPROFILE%\scripts` on Windows); on macOS, also symlink both into `/opt/homebrew/bin` (already on `PATH`, no `~/.zshrc` edit needed)
5. create config/state/log directories
6. register the native scheduler job for the current OS (launchd / systemd --user timers / Task Scheduler)

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

`make install` (or `uv run python scripts/install.py` on Windows) is preferred when scheduler configuration or installer files also changed.

## 6. Scheduler jobs

Both jobs run at the same wall-clock times on every OS; only the mechanism differs.

### Daily job

- runs at 10:00 local time
- runs immediately at login/boot too (`RunAtLoad`/`Persistent`), so a missed 10:00 (machine asleep/off) catches up
- Python-side daily gate (`last_daily_date` in state) prevents duplicate daily work even if the OS runs it more than once

### Monitor job

Runs every two hours at minute `05` (`00:05`, `02:05`, ... `22:05`).

The program uses a cross-platform file lock (`src/codex_reset_watch/filelock.py`) so overlapping daily/monitor invocations do not corrupt state or duplicate work.

### macOS — launchd

- `~/Library/LaunchAgents/com.wes.codex-reset-watch.daily.plist` (`StartCalendarInterval`)
- `~/Library/LaunchAgents/com.wes.codex-reset-watch.monitor.plist` (12 `StartCalendarInterval` entries)
- installer runs `launchctl bootstrap`/`enable` for both

### Linux — systemd --user timers

- `~/.config/systemd/user/codex-reset-watch-daily.{service,timer}` (`OnCalendar=*-*-* 10:00:00`)
- `~/.config/systemd/user/codex-reset-watch-monitor.{service,timer}` (`OnCalendar=*-*-* 0/2:05:00`)
- installer runs `systemctl --user daemon-reload` then `enable --now` on both timers
- requires a systemd user instance (lingering, if you want jobs to run without an active login session: `loginctl enable-linger $USER`)

### Windows — Task Scheduler

- tasks `CodexResetWatchDaily` (`/SC DAILY /ST 10:00`) and `CodexResetWatchMonitor` (`/SC HOURLY /MO 2 /ST 00:05`), created via `schtasks /Create`
- `TG_BOT_TOKEN`/`TG_CHAT_ID` are persisted with `setx` into your user environment rather than passed as task arguments, since `schtasks /query /v` output is not a safe place for secrets

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

launchd stdout/stderr logs are separately trimmed to 256 KiB by the installer when they exceed 512 KiB (macOS only — `install.trim_launchd_logs`). systemd/journald and Windows Task Scheduler manage their own job-output retention.

## 11. Tests

Run everything through uv (macOS/Linux):

```bash
make test
```

Windows:

```powershell
uv run python -m unittest discover -s tests -v
```

Individual groups (macOS/Linux):

```bash
make test-unit
make test-integration
```

The integration test starts a local HTTP server and exercises the real HTTP client and response normalization path without contacting the production API. The full suite runs unmodified on macOS, Linux, and Windows in CI (see `.github/workflows/ci.yml`).

## 12. Scheduler status

macOS:

```bash
make launch-status
# or
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.daily
launchctl print gui/$(id -u)/com.wes.codex-reset-watch.monitor
```

Linux:

```bash
systemctl --user status codex-reset-watch-daily.timer codex-reset-watch-monitor.timer
systemctl --user list-timers 'codex-reset-watch-*'
```

Windows:

```powershell
schtasks /Query /TN CodexResetWatchDaily /V /FO LIST
schtasks /Query /TN CodexResetWatchMonitor /V /FO LIST
```

## 13. Uninstall

macOS/Linux:

```bash
cd ~/Documents/Workspace/codex-reset-watch
make uninstall
```

Windows:

```powershell
uv run python scripts/uninstall.py
```

This removes the scheduler job(s) for the current OS and uninstalls the uv tool. Project files, config, state, and logs are intentionally retained (path printed by the uninstaller).

## 14. CI notifications

`.github/workflows/ci.yml` runs the test matrix (macOS/Linux/Windows) on every push and PR, then a separate `notify-telegram` job sends a Telegram message only when the test job fails on a `push` (never on green runs, never on `pull_request`, to avoid pinging on forks/external PRs). Configure it once per repo:

```bash
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

Both are independent of this project's own runtime `TG_BOT_TOKEN`/`TG_CHAT_ID` — the CI ones only ever see a failure alert with the repo/branch/commit and a link to the run; they never touch the app's monitoring data.

## Why the installer does not call `uv run` for scheduled jobs

`uv run` is ideal for project development because it discovers the project, syncs the project environment when needed, and executes within that environment. For a long-running background setup, this project instead installs the CLI with `uv tool install` and lets the OS scheduler call the installed executable directly. That keeps each scheduled invocation small and avoids depending on the current working directory, shell startup files, PATH activation, or project `.venv` state.
