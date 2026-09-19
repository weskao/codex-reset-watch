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

### 🧪 Testing

- Add test suite for codex-reset-watch
- **parser:** Cover scheduled reset without eta

### ⚙️ Miscellaneous Tasks

- Add .gitignore
- Expand .gitignore for python/uv project
- Track uv.lock
