## [Unreleased]

### 🐛 Bug Fixes

- **telegram:** Use the configured credentials ahead of `TG_BOT_TOKEN`/`TG_CHAT_ID`, so a token
  left in a shell profile or baked into an older launchd plist can no longer keep notifying
  through a bot `crw config` already replaced
- **scheduler:** Stop baking the token into the generated plist/unit on a machine that has a
  credential store — the job reads it itself, so it is no longer written to a 0644 file nor
  carried forward, stale, on every re-apply

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
