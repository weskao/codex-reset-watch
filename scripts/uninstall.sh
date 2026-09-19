#!/usr/bin/env bash
set -euo pipefail
UID_NUM="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
BIN_DIR="${CRW_BIN_DIR:-$HOME/scripts}"
for label in com.wes.codex-reset-watch.daily com.wes.codex-reset-watch.monitor; do
  launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 || true
done
rm -f "$LAUNCH_DIR/com.wes.codex-reset-watch.daily.plist" "$LAUNCH_DIR/com.wes.codex-reset-watch.monitor.plist"
if command -v uv >/dev/null 2>&1; then
  UV_TOOL_BIN_DIR="$BIN_DIR" uv tool uninstall codex-reset-watch >/dev/null 2>&1 || true
fi
# Clean stale wrappers/symlinks from older non-uv versions, if any.
rm -f "$BIN_DIR/codex-reset-watch" "$BIN_DIR/crw"
# Remove the PATH-free Homebrew symlinks installed by install.sh.
rm -f /opt/homebrew/bin/codex-reset-watch /opt/homebrew/bin/crw
echo "✅ LaunchAgents and uv tool removed. Project/config/logs were intentionally kept."
echo "To remove data manually:"
echo "  rm -rf '$HOME/Library/Application Support/codex-reset-watch' '$HOME/Library/Logs/codex-reset-watch'"
