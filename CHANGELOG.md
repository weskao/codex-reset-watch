## [0.20.4] - 2026-10-09

### 🐛 Bug Fixes

- **update-check:** Only offer published pypi versions
- **ui:** Show ctrl+c in config menu
## [0.20.3] - 2026-10-08

### 🐛 Bug Fixes

- **ui:** Show ctrl-c skip shortcut

### 📚 Documentation

- **update-check:** Document skip shortcut
## [0.20.2] - 2026-10-08

### 🐛 Bug Fixes

- Wait for boot lock before reset catch-up
## [0.20.1] - 2026-10-06

### ⚙️ Miscellaneous Tasks

- Add Dependabot config (github-actions, uv)
## [0.20.0] - 2026-10-04

### 🚀 Features

- **update:** Install upgrades from PyPI

### 🐛 Bug Fixes

- **update:** Say upgrades come from PyPI in the setting help

### 📚 Documentation

- **readme:** Document the PyPI install

### ⚙️ Miscellaneous Tasks

- **release:** Publish to PyPI on v* tags
## [0.19.2] - 2026-10-03

### 🐛 Bug Fixes

- **monitor:** Keep the last known reset through unreadable scans

### 💼 Other

- **deps:** Require telegram-kit 0.2.2

### 🚜 Refactor

- **secrets:** Drop PSModulePath workaround

### 📚 Documentation

- Refresh settings sample and upstream workaround note
## [0.19.1] - 2026-10-03

### ⚙️ Miscellaneous Tasks

- Bump setup-uv to v10.2.0
## [0.19.0] - 2026-10-03

### 🚀 Features

- **monitor:** Alert after blind scans

### 🐛 Bug Fixes

- **keys:** Decode utf-8 terminal input

### 💼 Other

- **test:** Add pytest development dependency
## [0.18.0] - 2026-10-03

### 🚀 Features

- **cli:** Support bare help and version
- **config:** Add customizable device label setting

### 🐛 Bug Fixes

- **ui:** Show target value in reset-row prompt

### 📚 Documentation

- **cli:** Note crw is an alias of codex-reset-watch
- **cli:** Say every command works as crw
## [0.17.0] - 2026-10-03

### 🚀 Features

- **console:** Use ascii-safe output on Windows

### 🐛 Bug Fixes

- **ui:** Adapt config menu to terminal size
- **secrets:** Drop PSModulePath for DPAPI helper
- **scheduler:** Report schtasks failure as OSError
- **console:** Preserve windows menu layout
- **ui:** Stop menu repaint stacking logo rows

### 📚 Documentation

- **readme:** Describe adaptive config menu layout

### ⚙️ Miscellaneous Tasks

- **gitignore:** Ignore shell crash dumps
- Set daily_notify_when_unchanged default to true in example config
## [0.16.0] - 2026-10-02

### 🚀 Features

- **ui:** Add banner to menu

### 📚 Documentation

- **images:** Update config and upcoming event screenshots
- Add current reset event result image
- **readme:** Point telegram-kit install at 0.2.1
- **readme:** Drop telegram_kit usage section
- **readme:** Add unofficial disclaimer and acknowledgements section
## [0.15.1] - 2026-10-01

### 🐛 Bug Fixes

- **i18n:** Use official reset terminology
- **ui:** Move reset actions to r shortcut

## [0.15.0] - 2026-09-30

### 🚀 Features

- **host-identity:** Improve device identification
- **cli:** Append host identity footer to output
- **i18n:** Localize reset event types in notices

### 📚 Documentation

- Release 0.15.0

### ⚙️ Miscellaneous Tasks

- **gitignore:** Ignore .codegraph directory
## [0.14.1] - 2026-09-29

### 🐛 Bug Fixes

- **codex-reset-watch:** Restart systemd timers

### ⚙️ Miscellaneous Tasks

- **release:** Bump version to 0.14.1
## [0.14.0] - 2026-09-29

### 🚀 Features

- Identify device in telegram notices

### 📚 Documentation

- **changelog:** Release v0.14.0
## [0.13.0] - 2026-09-29

### 🚀 Features

- **scheduler:** [**breaking**] Standardize scheduler notice references
- **i18n:** Localize confidence levels

### 🐛 Bug Fixes

- **notice:** [**breaking**] Align sources and localize window

### 🚜 Refactor

- **notice:** Use NoticeKind enum for images

### 📚 Documentation

- Add README output examples
- **changelog:** Release v0.13.0

### 🧪 Testing

- **paths:** Anonymize home directory fixtures

### ⚙️ Miscellaneous Tasks

- Add upcoming images
## [0.12.0] - 2026-09-28

### 🚀 Features

- **menu:** [**breaking**] Split d reset-all into d row-reset / D reset-all

### 📚 Documentation

- **changelog:** Release v0.12.0
## [0.11.1] - 2026-09-28

### 📚 Documentation

- **changelog:** Release v0.11.1
## [0.11.0] - 2026-09-28

### 🚀 Features

- **notifications:** Include launchd job and log path
- **config:** Cap free-text setting length

### 📚 Documentation

- **changelog:** Release v0.11.0
## [0.10.1] - 2026-09-27

### 💼 Other

- **deps:** Bump telegram-kit to v0.1.4

### 📚 Documentation

- **changelog:** Release v0.10.1
## [0.10.0] - 2026-09-27

### 🚀 Features

- **check:** Read active_watch and avg reset interval
- **check:** Show how long ago the latest reset was
- **telegram:** Send photo notifications with reset notices

### 🐛 Bug Fixes

- **cli:** Offer the update prompt on every exit path
- Keep scheduled reset announcements until executed
- **telegram:** Upload notice images

### 📚 Documentation

- **changelog:** Release v0.10.0

### ⚙️ Miscellaneous Tasks

- Add "upcoming" images
## [0.9.0] - 2026-09-27

### 🚀 Features

- **config:** [**breaking**] Drop auto language, resolve once at first use
- **cli:** Translate --help text via i18n catalogue

### 📚 Documentation

- **changelog:** Release v0.9.0
## [0.8.0] - 2026-09-27

### 🚀 Features

- **update:** Link release notes in the update prompt

### 📚 Documentation

- **changelog:** Release v0.8.0
## [0.7.3] - 2026-09-26

### 🐛 Bug Fixes

- **ci:** Skip POSIX-only scheduler tests on Windows and check ACL via .NET

### 📚 Documentation

- **changelog:** Release v0.7.3
## [0.7.2] - 2026-09-26

### 🐛 Bug Fixes

- **ci:** Drop --no-index from wheel install check

### 📚 Documentation

- **changelog:** Release v0.7.2
## [0.7.1] - 2026-09-26

### 🐛 Bug Fixes

- Pin telegram-kit and align menu rows

### 💼 Other

- **deps:** Use telegram-kit>=0.1.3 from PyPI

### 📚 Documentation

- **changelog:** Release v0.7.1
## [0.7.0] - 2026-09-26

### 🚀 Features

- **i18n:** [**breaking**] Translate telegram notices via config language
- Interactive update prompts
- **security:** [**breaking**] Harden telegram secret storage and export
- **telegram:** [**breaking**] Extract reusable telegram_kit package

### 🐛 Bug Fixes

- **ui:** Align update prompt layout
- Include unchanged notification settings in basic mode
- **security:** Keep cli and schedules alive on store failure

### 🚜 Refactor

- **telegram:** [**breaking**] Depend on telegram_kit instead of vendoring it

### 📚 Documentation

- Describe telegram credential store and export changes
- Document telegram_kit and credential fallbacks
- **changelog:** Release v0.7.0

### 🧪 Testing

- **security:** Cover credential store and export hardening
## [0.6.0] - 2026-09-25

### 🚀 Features

- **update-check:** Check github in background, not once a day
- Add no-signal tracker notices

### 📚 Documentation

- **changelog:** Release v0.6.0
## [0.5.0] - 2026-09-24

### 🚀 Features

- Hint when a newer GitHub release is available

### 📚 Documentation

- **changelog:** Release v0.5.0
## [0.4.2] - 2026-09-22

### 🐛 Bug Fixes

- Preserve stored tokens during config saves
- **tests:** Stop the integration test fixture from expiring
- **ui:** Stop windows CI hanging in run_menu
- **install:** Detect PATH shadows with empty PATHEXT

### 📚 Documentation

- Clarify reset notification docs
- **changelog:** Release v0.4.2

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
