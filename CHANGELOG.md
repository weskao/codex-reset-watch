## [0.4.2] - 2026-09-22

### 🐛 Bug Fixes

- Preserve stored tokens during config saves
- **tests:** Stop the integration test fixture from expiring
- **ui:** Stop windows CI hanging in run_menu
- **install:** Detect PATH shadows with empty PATHEXT

### 📚 Documentation

- Clarify reset notification docs

### 🧪 Testing

- **ui:** Isolate colour tests from NO_COLOR

### ⚙️ Miscellaneous Tasks

- Redact live telegram chat id from examples
## [0.4.1] - 2026-09-21

### 🐛 Bug Fixes

- **ui:** Refit cursor whenever ui_mode changes

### 📚 Documentation

- **changelog:** Release v0.4.1
## [0.4.0] - 2026-09-21

### 🚀 Features

- **ui:** Add Basic/Advanced settings mode with tab switching

### 🐛 Bug Fixes

- Detect shadowed cli commands
- Notify on unchanged daily runs
- Only re-apply schedule when a value actually changes

### 📚 Documentation

- **readme:** Add crw --config telegram notification example
- **readme:** Move telegram example nearer the top
- **changelog:** Release v0.4.0
## [0.3.1] - 2026-09-21

### 🐛 Bug Fixes

- Align scheduler with uv bin path
- **ui:** Enable ansi color on windows console

### 📚 Documentation

- **changelog:** Release v0.3.1

### ⚙️ Miscellaneous Tasks

- **config:** Default unchanged-scan notify to on
## [0.3.0] - 2026-09-21

### 🐛 Bug Fixes

- **install:** Import readline so arrow keys work in prompts
- **telegram:** [**breaking**] Config credentials outrank TG_BOT_TOKEN/TG_CHAT_ID
- **ui:** Quit menu on ctrl-c, clear stale error
- **cli:** Cancel gracefully on ctrl-c

### 📚 Documentation

- **changelog:** Release v0.3.0

### 🎨 Styling

- **cli:** Visually separate reset blocks in check output
## [0.2.0] - 2026-09-20

### 🚀 Features

- Add cross-platform scheduler support
- Add codex reset watch scheduler and config
- Add secure telegram credentials
- Add telegram setup prompts
- **ui:** Animate selected row cursor

### 🐛 Bug Fixes

- Correct windows file locking
- **ui:** Wrap long footer messages
- **secrets:** Store the bot token via security batch mode, not -w

### 📚 Documentation

- **changelog:** Release v0.2.0

### 🧪 Testing

- **scheduler:** Pin timezone in the two schtasks daily-time tests
- Drop POSIX-only path and mode assumptions from seven tests

### ⚙️ Miscellaneous Tasks

- Add readme field to pyproject.toml
- Ignore local test artifacts
## [0.1.0] - 2026-09-19

### 🚀 Features

- Implement codex-reset-watch monitor

### 🐛 Bug Fixes

- **parser:** Tolerate nested and camelCase event fields
- **parser:** Keep upcoming resets without eta

### 🚜 Refactor

- **telegram:** [**breaking**] Drop tg-send.sh dependency for notifications

### 📚 Documentation

- Add readme and research notes
- Document event parsing resilience behavior
- **changelog:** Release v0.1.0

### 🧪 Testing

- Add test suite for codex-reset-watch
- **parser:** Cover scheduled reset without eta

### ⚙️ Miscellaneous Tasks

- Add .gitignore
- Expand .gitignore for python/uv project
- Track uv.lock
