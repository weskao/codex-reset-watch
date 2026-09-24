"""Tell an interactive user when a newer codex-reset-watch release is on GitHub.

This tool is installed from git tags, not PyPI, so the source of truth is the
repo's latest GitHub release. One request per day at most (cached in the state
folder), a sub-second timeout, and every failure — offline, rate-limited, junk
payload — is silence: a hint must never slow a command noticeably or change its
exit code. Only a human sees it: stderr has to be a TTY, so the launchd /
systemd / schtasks runs never pay for the request.
"""
from __future__ import annotations

import contextlib
import json
import sys
import time
import urllib.request
from typing import Callable, Optional, Tuple

from . import config as cfgmod
from . import i18n, ui

REPO = "weskao/codex-reset-watch"
TTL_SECONDS = 86_400
TIMEOUT_SECONDS = 0.8


def version_tuple(version: str) -> Optional[Tuple[int, ...]]:
    """``v0.4.2`` → ``(0, 4, 2)``; leading dotted integers, at least X.Y."""
    parts = []
    for chunk in version.strip().lstrip("vV").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if len(parts) >= 2 else None


def fetch_latest_tag(timeout: float = TIMEOUT_SECONDS) -> Optional[str]:
    """The latest release's tag name from the GitHub API, or None."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "codex-reset-watch-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    tag = data.get("tag_name") if isinstance(data, dict) else None
    return tag if isinstance(tag, str) and tag else None


def _read_json(path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def newer_release(current: str, cfg: Optional[dict] = None, *, now: Optional[float] = None,
                  fetch: Callable[[], Optional[str]] = fetch_latest_tag) -> Optional[str]:
    """The latest release tag when it is newer than *current*, else None."""
    current_parts = version_tuple(current)
    if current_parts is None:
        return None
    stamp = time.time() if now is None else now
    cache_path = cfgmod.state_dir(cfg) / "update-check.json"
    cached = _read_json(cache_path)
    latest = cached.get("latest") if isinstance(cached.get("latest"), str) else None
    checked_at = cached.get("checked_at")
    # A failed fetch is cached too (as the old answer or None), so being
    # offline costs one timeout per day, not one per command.
    if not isinstance(checked_at, (int, float)) or stamp - checked_at >= TTL_SECONDS:
        try:
            fetched = fetch()
        except (OSError, ValueError):
            fetched = None
        latest = fetched or latest
        with contextlib.suppress(OSError):
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"checked_at": stamp, "latest": latest}) + "\n", encoding="utf-8")
    latest_parts = version_tuple(latest) if latest else None
    if latest_parts is None or latest_parts <= current_parts:
        return None
    return latest


def maybe_hint() -> None:
    """Print the upgrade hint on stderr when one applies. Never raises."""
    try:
        if not sys.stderr.isatty():
            return
        # The file alone: cfgmod.load() would also read the keychain.
        cfg = {**cfgmod.DEFAULTS, **_read_json(cfgmod.config_path())}
        if cfg.get("update_check") is not True:
            return
        current = ui.package_version()
        latest = newer_release(current, cfg)
        if latest is None:
            return
        paint = ui.Paint(ui.colour_enabled(sys.stderr))
        message = i18n.t("update.available", i18n.current_language(),
                         latest=latest.lstrip("vV"), current=current)
        print(f"{paint.warn}{message}{paint.reset}\n"
              f"  uv tool install --force --from git+https://github.com/{REPO}.git@{latest} codex-reset-watch"
              " && crw apply-schedule",
              file=sys.stderr)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - a hint must never fail the command
        return
