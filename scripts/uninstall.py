#!/usr/bin/env python3
"""Cross-platform uninstaller counterpart to install.py.

Removes the OS scheduler entries and the uv tool install. Config/state/logs
are kept intentionally, same as the old uninstall.sh.
"""
from __future__ import annotations

import os
import pathlib
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from install import backend_for  # noqa: E402
import schtasks  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from codex_reset_watch import paths  # noqa: E402


def uninstall_launchd() -> None:
    uid = str(os.getuid())
    launch_dir = pathlib.Path.home() / "Library" / "LaunchAgents"
    for label in ("com.wes.codex-reset-watch.daily", "com.wes.codex-reset-watch.monitor"):
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
        (launch_dir / f"{label}.plist").unlink(missing_ok=True)


def uninstall_systemd() -> None:
    unit_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    for name in ("codex-reset-watch-daily", "codex-reset-watch-monitor"):
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{name}.timer"], capture_output=True)
        for suffix in (".service", ".timer"):
            (unit_dir / f"{name}{suffix}").unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def uninstall_schtasks() -> None:
    for task_name in (schtasks.DAILY_TASK_NAME, schtasks.MONITOR_TASK_NAME):
        subprocess.run(schtasks.delete_task_command(task_name), capture_output=True)


def main() -> int:
    backend = backend_for(platform.system())
    if backend == "launchd":
        uninstall_launchd()
    elif backend == "systemd":
        uninstall_systemd()
    else:
        uninstall_schtasks()

    bin_dir = pathlib.Path(os.environ.get("CRW_BIN_DIR", str(pathlib.Path.home() / "scripts"))).expanduser()
    uv = shutil.which("uv")
    if uv:
        subprocess.run(
            [uv, "tool", "uninstall", "codex-reset-watch"],
            env=dict(os.environ, UV_TOOL_BIN_DIR=str(bin_dir)), capture_output=True,
        )
    for name in ("codex-reset-watch", "codex-reset-watch.exe", "crw", "crw.exe"):
        (bin_dir / name).unlink(missing_ok=True)

    print(f"✅ Scheduler entries ({backend}) and uv tool removed. Config/state/logs were intentionally kept.")
    print("To remove data manually:")
    print(f"  rm -rf '{paths.app_state_dir()}' '{paths.app_log_dir()}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
