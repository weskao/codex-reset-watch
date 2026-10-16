"""Tell an interactive user when a newer codex-reset-watch release exists.

The check itself (git tags, cache) and the install live in ``update_kit``. This
module decides *when* the check runs: before the command, only when stderr is a TTY (the
launchd / systemd / schtasks runs never pay for it) and the ``update_check``
setting is on. A newer release precedes the command with the interactive Update now /
Skip / Skip until next version prompt. Every failure is silence: a check must never
wait more than JOIN_SECONDS or change the command's exit code.
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
from . import i18n, keys, ui
from .console import print_console as print

REPO = "weskao/codex-reset-watch"
DIST = "codex-reset-watch"
#: update_kit.check()'s own network timeout is 1.5 s; wait a little longer than that.
JOIN_SECONDS = 2.0
_check: Optional[threading.Thread] = None
_latest: list = []
_cfg: dict = {}


def _read_json(path) -> dict:
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {}


def _write_json(path, data: dict) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def _skip_path(cfg: Optional[dict] = None):
    return cfgmod.state_dir(cfg) / "update-check.json"


def skip_version(tag: str, cfg: Optional[dict] = None) -> None:
    """Stop reporting *tag*; a newer release is reported again. Never raises."""
    path = _skip_path(cfg)
    _write_json(path, {**_read_json(path), "skipped": tag})


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
        _cfg.update(cfg)
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


def self_update() -> int:
    """Install the newest release tag (update_kit), then re-apply the schedule."""
    prog = prog_name()
    return update_kit.update(
        app=prog, repo=REPO, dist=DIST,
        lang=i18n.current_language(),
        after=[[prog, "apply-schedule"]],  # re-register the schedule with the NEW binary
        out=sys.stderr,
    )


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
        _check.join(JOIN_SECONDS)
        latest = _latest[0] if _latest else None
        if latest is None or latest == _read_json(_skip_path(_cfg)).get("skipped"):
            return
        current = ui.package_version()
        release_url = f"https://github.com/{REPO}/releases/tag/{latest}"
        if keys.is_interactive_tty():
            answer = ui.update_prompt(current, latest, release_url=release_url)
            if answer == ui.SKIP_VERSION:
                skip_version(latest, _cfg)
            elif answer == ui.UPDATE_NOW:
                self_update()  # update_kit reports success or failure itself
            return
        paint = ui.Paint(ui.colour_enabled(sys.stderr))
        lang = i18n.current_language()
        message = i18n.t("update.available", lang, latest=latest.lstrip("vV"), current=current)
        notes = i18n.t("update.release_notes", lang, url=release_url)
        print(f"{paint.warn}{message}{paint.reset}\n"
              f"  {prog_name()} update\n"
              f"  {notes}",
              file=sys.stderr)
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 - a hint must never fail the command
        return
