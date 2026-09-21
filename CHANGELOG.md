## [0.3.1] - 2026-09-21

### 🐛 Bug Fixes

- Align scheduler with uv bin path
- **ui:** Enable ansi color on windows console

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
