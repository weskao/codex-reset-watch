"""Tell an interactive user when a newer codex-reset-watch release exists.

The check itself (git tags, cache, hint wording) lives in ``update_kit``. This
module only decides *when* it runs: in a background thread started with the
command so it overlaps the command's own work, only when stderr is a TTY (the
launchd / systemd / schtasks runs never pay for it) and the ``update_check``
setting is on. Every failure is silence: a hint must never slow a command
noticeably or change its exit code.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
from typing import Optional

import update_kit

from . import config as cfgmod
from . import i18n, ui
from .console import print_console as print

REPO = "weskao/codex-reset-watch"
DIST = "codex-reset-watch"
#: update_kit.check()'s own network timeout is 1.5 s; wait a little longer than that.
JOIN_SECONDS = 2.0
_check: Optional[threading.Thread] = None
_latest: list = []


def _read_json(path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def prog_name() -> str:
    """The command the user typed (``crw`` or ``codex-reset-watch``), for hints and `update`."""
    name = os.path.basename(sys.argv[0] if sys.argv else "")
    name = name[:-4] if name.lower().endswith(".exe") else name
    return name if name in ("crw", DIST) else DIST


def start_check() -> None:
    """Begin the check in a daemon thread so it overlaps the command. Never raises."""
    global _check
    try:
        if not update_kit.should_hint(sys.stderr):
            return
        # The file alone: cfgmod.load() would also read the keychain.
        cfg = {**cfgmod.DEFAULTS, **_read_json(cfgmod.config_path())}
        if cfg.get("update_check") is not True:
            return
        current = ui.package_version()

        def run() -> None:
            try:
                _latest.append(update_kit.check(repo=REPO, current=current))
            except Exception:  # noqa: BLE001 - a hint must never fail the command
                pass

        _check = threading.Thread(target=run, name="crw-update-check", daemon=True)
        _check.start()
    except Exception:  # noqa: BLE001 - a hint must never fail the command
        return


def maybe_hint() -> None:
    """Print the started check's result as one stderr line. Never raises."""
    try:
        if _check is None:
            return
        _check.join(JOIN_SECONDS)  # usually already done: it ran alongside the command
        latest = _latest[0] if _latest else None
        if latest is None:
            return
        paint = ui.Paint(ui.colour_enabled(sys.stderr))
        line = update_kit.hint(prog_name(), latest, ui.package_version(), i18n.current_language())
        print(f"{paint.warn}{line}{paint.reset}", file=sys.stderr)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - a hint must never fail the command
        return
