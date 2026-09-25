"""Tell an interactive user when a newer codex-reset-watch release is on GitHub.

This tool is installed from git tags, not PyPI, so the source of truth is the
repo's latest GitHub release. The request starts in a background thread when
the command starts, so it overlaps the command's own work; at most one per
``TTL_SECONDS`` (cached in the state folder, short because several releases
can land in one day), a sub-second timeout, and every failure — offline,
rate-limited, junk payload — is silence: a hint must never slow a command
noticeably or change its exit code. Only a human sees it: stderr has to be a
TTY, so the launchd / systemd / schtasks runs never pay for the request.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional, Tuple

from . import config as cfgmod
from . import i18n, keys, ui

REPO = "weskao/codex-reset-watch"
#: Unauthenticated GitHub API allows 60 requests/hour per IP; 6/hour is polite.
TTL_SECONDS = 600
TIMEOUT_SECONDS = 0.8
_check: Optional[threading.Thread] = None
_latest: list = []


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
    # offline costs one timeout per TTL, not one per command.
    if not isinstance(checked_at, (int, float)) or stamp - checked_at >= TTL_SECONDS:
        try:
            fetched = fetch()
        except (OSError, ValueError):
            fetched = None
        latest = fetched or latest
        # Merge, not overwrite: a "skipped" version set by skip_version()
        # must survive the next refetch.
        _write_json(cache_path, {**cached, "checked_at": stamp, "latest": latest})
    if latest is not None and latest == cached.get("skipped"):
        return None
    latest_parts = version_tuple(latest) if latest else None
    if latest_parts is None or latest_parts <= current_parts:
        return None
    return latest


def skip_version(tag: str, cfg: Optional[dict] = None) -> None:
    """Stop reporting *tag*; a newer release is reported again. Never raises."""
    cache_path = cfgmod.state_dir(cfg) / "update-check.json"
    data = _read_json(cache_path)
    data["skipped"] = tag
    _write_json(cache_path, data)


def _write_json(path, data: dict) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def start_check() -> None:
    """Begin the check in a daemon thread so it overlaps the command. Never raises."""
    global _check
    try:
        if not sys.stderr.isatty():
            return
        # The file alone: cfgmod.load() would also read the keychain.
        cfg = {**cfgmod.DEFAULTS, **_read_json(cfgmod.config_path())}
        if cfg.get("update_check") is not True:
            return
        current = ui.package_version()

        def run() -> None:
            try:
                _latest.append(newer_release(current, cfg))
            except Exception:  # noqa: BLE001 - http.client errors are not all OSError
                pass

        _check = threading.Thread(target=run, name="crw-update-check", daemon=True)
        _check.start()
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return


def _upgrade_steps(latest: str) -> Tuple[Tuple[str, ...], ...]:
    return (
        ("uv", "tool", "install", "--force", "--from",
         f"git+https://github.com/{REPO}.git@{latest}", "codex-reset-watch"),
        ("crw", "apply-schedule"),
    )


def _run_upgrade(latest: str) -> None:
    """Run each step in order, stopping at the first failure. Reports a
    failed/missing step but never raises — Update Now must not crash the
    command it runs after."""
    lang = i18n.current_language()
    paint = ui.Paint(ui.colour_enabled(sys.stderr))
    for step in _upgrade_steps(latest):
        try:
            done = subprocess.run(list(step), check=False)
        except OSError:
            done = None
        if done is None or done.returncode != 0:
            print(f"{paint.warn}{i18n.t('update.failed', lang)}{paint.reset}", file=sys.stderr)
            return


def maybe_hint() -> None:
    """Report the started check's result. Never raises.

    On a keyboard-capable terminal (stdin AND stdout both real TTYs) this is
    the settings menu's own interactive prompt — Update now / Skip / Skip
    until next version. Otherwise (redirected stdin/stdout, still a TTY
    stderr) it stays the one-shot hint: a question nobody can answer must
    never block.
    """
    try:
        if _check is None:
            return
        # Usually already done: it ran alongside the command.
        _check.join(TIMEOUT_SECONDS)
        latest = _latest[0] if _latest else None
        if latest is None:
            return
        current = ui.package_version()
        if keys.is_interactive_tty():
            answer = ui.update_prompt(current, latest)
            if answer == ui.SKIP_VERSION:
                skip_version(latest)
            elif answer == ui.UPDATE_NOW:
                _run_upgrade(latest)
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
