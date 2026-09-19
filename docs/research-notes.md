# Research notes

Reviewed on 2026-09-19.

## Primary requested sources

1. Codex Resets: https://codex-resets.com/
   - Tracks public Codex reset announcements associated with `@thsottiaux`.
   - Shows latest reset, historical announcements, regular/banked reset categories and aggregate history.
   - At review time, the homepage showed the latest public reset at `2026-09-12 08:09 UTC` and 53 reset records.

2. 電腦王阿達 article: https://www.kocpc.com.tw/archives/669039
   - Published 2026-09-15.
   - Describes browser / Telegram / Email notifications.
   - Describes Codex Resets as offering a free public API and MCP in addition to the website.

3. API docs: https://codex-resets.com/api/docs
   - Public documentation page referenced by this project.
   - The core endpoint used here is `GET https://codex-resets.com/api/v1/status`.
   - Historical records are best-effort fetched from `GET /api/v1/resets?limit=20&order=desc`.

## Cross-checks used for implementation

- A public Codex Cache Meter project documents that it makes an unauthenticated, read-only request to `https://codex-resets.com/api/v1/status`, and its example output includes a third-party next-reset forecast with chance/confidence/window semantics:
  https://github.com/ivkiwi/codex-cache-meter

- Apple launchd scheduling behavior:
  https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/ScheduledJobs.html
  https://keith.github.io/xcode-man-pages/launchd.plist.5.html

  Key behavior used by this project:
  - `StartCalendarInterval` that is missed while the Mac sleeps is triggered after wake (coalesced if multiple were missed).
  - A job missed while the machine is powered off is not automatically recovered solely by `StartCalendarInterval`.
  - Therefore the daily job combines `StartCalendarInterval=10:00` with `RunAtLoad` and an application-level daily gate to provide an after-10:00 boot/login catch-up.

## Reliability note

`codex-resets.com` is a third-party public tracker, not an OpenAI account-usage API. Public/global reset announcements and forecasts are different from an individual account's natural 5-hour/weekly reset timer. The monitor therefore labels future timing as a third-party signal/forecast and preserves raw API responses in rotating logs to make schema changes diagnosable.
