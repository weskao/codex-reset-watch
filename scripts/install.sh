#!/usr/bin/env bash
set -euo pipefail

SRC_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
DEST="${CRW_INSTALL_DIR:-$HOME/Documents/Workspace/codex-reset-watch}"
CONFIG_DIR="$HOME/Library/Application Support/codex-reset-watch"
LOG_DIR="$HOME/Library/Logs/codex-reset-watch"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
BIN_DIR="${CRW_BIN_DIR:-$HOME/scripts}"
UV_PYTHON_VERSION="${CRW_UV_PYTHON:-3.13}"
UID_NUM="$(id -u)"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  cat >&2 <<'MSG'
ERROR: uv not found.
Install it first, for example:
  brew install uv
or use Astral's official installer:
  curl -LsSf https://astral.sh/uv/install.sh | sh
MSG
  exit 1
fi

mkdir -p "$DEST" "$CONFIG_DIR" "$LOG_DIR" "$LAUNCH_DIR" "$BIN_DIR"

# Ensure the requested runtime is managed by uv, not Homebrew/system Python.
"$UV_BIN" python install "$UV_PYTHON_VERSION"
UV_PYTHON_BIN="$("$UV_BIN" python find --managed-python "$UV_PYTHON_VERSION")"
if [[ "$SRC_ROOT" != "$DEST" ]]; then
  /usr/bin/ditto "$SRC_ROOT" "$DEST"
fi
chmod +x "$DEST/scripts/install.sh" "$DEST/scripts/uninstall.sh" "$DEST/scripts/render_launchd.py"

if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  cp "$DEST/config.example.json" "$CONFIG_DIR/config.json"
  chmod 600 "$CONFIG_DIR/config.json"
fi

# Install the application as a persistent, isolated uv tool environment.
# Pin the runtime explicitly because uv tool environments ignore local .python-version files.
UV_TOOL_BIN_DIR="$BIN_DIR" "$UV_BIN" tool install --force --managed-python --python "$UV_PYTHON_VERSION" "$DEST"

PROGRAM="$BIN_DIR/codex-reset-watch"
if [[ ! -x "$PROGRAM" ]]; then
  echo "ERROR: uv tool install completed but executable was not found at $PROGRAM" >&2
  echo "uv tool executable directory: $(UV_TOOL_BIN_DIR="$BIN_DIR" "$UV_BIN" tool dir --bin)" >&2
  exit 1
fi

# Symlink into Homebrew's bin so `crw`/`codex-reset-watch` work from any shell
# without editing PATH/~/.zshrc. /opt/homebrew/bin is unconditionally on PATH
# already (see ~/.zshrc), same guard used there.
if [[ -d /opt/homebrew/bin && -w /opt/homebrew/bin ]]; then
  ln -sf "$BIN_DIR/codex-reset-watch" /opt/homebrew/bin/codex-reset-watch
  ln -sf "$BIN_DIR/crw" /opt/homebrew/bin/crw
fi

# Render launchd jobs with the absolute installed executable path. launchd does not expand '~'.
"$UV_PYTHON_BIN" "$DEST/scripts/render_launchd.py" \
  --program "$PROGRAM" \
  --out-dir "$LAUNCH_DIR" \
  --log-dir "$LOG_DIR"

for label in com.wes.codex-reset-watch.daily com.wes.codex-reset-watch.monitor; do
  launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 || true
done
launchctl bootstrap "gui/$UID_NUM" "$LAUNCH_DIR/com.wes.codex-reset-watch.daily.plist"
launchctl bootstrap "gui/$UID_NUM" "$LAUNCH_DIR/com.wes.codex-reset-watch.monitor.plist"
launchctl enable "gui/$UID_NUM/com.wes.codex-reset-watch.daily" || true
launchctl enable "gui/$UID_NUM/com.wes.codex-reset-watch.monitor" || true

# Keep launchd stdio logs tiny. Application/API logs are rotated separately by Python.
for f in "$LOG_DIR"/launchd-*.log; do
  [[ -f "$f" ]] || continue
  size=$(stat -f%z "$f" 2>/dev/null || echo 0)
  if (( size > 524288 )); then
    tail -c 262144 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  fi
done

display_path() {
  local p="$1"
  if [[ "$p" == "$HOME" ]]; then
    printf '~'
  elif [[ "$p" == "$HOME/"* ]]; then
    printf '~/%s' "${p#"$HOME/"}"
  else
    printf '%s' "$p"
  fi
}

DISPLAY_DEST="$(display_path "$DEST")"
DISPLAY_BIN="$(display_path "$BIN_DIR")"
DISPLAY_CONFIG="$(display_path "$CONFIG_DIR")"
DISPLAY_LOG="$(display_path "$LOG_DIR")"
DISPLAY_LAUNCH="$(display_path "$LAUNCH_DIR")"
DISPLAY_UV="$(display_path "$UV_BIN")"

cat <<EOF2
✅ Codex Reset Watch installed with uv tool.

Project:      $DISPLAY_DEST
CLI:          $DISPLAY_BIN/codex-reset-watch
Alias:        $DISPLAY_BIN/crw
Config:       $DISPLAY_CONFIG/config.json
Logs:         $DISPLAY_LOG
LaunchAgents: $DISPLAY_LAUNCH/com.wes.codex-reset-watch.{daily,monitor}.plist
uv:           $DISPLAY_UV
Python:       $UV_PYTHON_VERSION (uv-managed tool runtime)

Verify:
  uv tool list
  $DISPLAY_BIN/crw doctor
  $DISPLAY_BIN/crw check
EOF2
