#!/usr/bin/env python3
"""Cross-platform installer: uv tool install + native OS scheduling.

macOS -> launchd, Linux -> systemd --user timers, Windows -> Task Scheduler.
Replaces the old install.sh: bash isn't native on Windows, Python already is
(uv itself requires it), so one script covers all three OSes.
"""
from __future__ import annotations

import os
import pathlib
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import schtasks  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from codex_reset_watch import paths  # noqa: E402

BACKENDS = {"Darwin": "launchd", "Linux": "systemd", "Windows": "schtasks"}


def backend_for(system: str) -> str:
    try:
        return BACKENDS[system]
    except KeyError:
        raise ValueError(f"unsupported platform: {system}")


def uv_bin() -> str:
    found = shutil.which("uv")
    if not found:
        sys.exit(
            "ERROR: uv not found.\n"
            "Install it first: https://docs.astral.sh/uv/getting-started/installation/"
        )
    return found


def sync_dest(dest: pathlib.Path) -> None:
    if dest.resolve() == REPO_ROOT:
        return
    shutil.copytree(
        REPO_ROOT, dest, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"),
    )


def install_launchd(uv: str, program: pathlib.Path, log_dir: pathlib.Path, python_version: str) -> None:
    launch_dir = pathlib.Path.home() / "Library" / "LaunchAgents"
    launch_dir.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([
        uv, "run", "--python", python_version, str(REPO_ROOT / "scripts/render_launchd.py"),
        "--program", str(program), "--out-dir", str(launch_dir), "--log-dir", str(log_dir),
    ])
    uid = str(os.getuid())
    for label in ("com.wes.codex-reset-watch.daily", "com.wes.codex-reset-watch.monitor"):
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    for label in ("daily", "monitor"):
        plist = launch_dir / f"com.wes.codex-reset-watch.{label}.plist"
        subprocess.check_call(["launchctl", "bootstrap", f"gui/{uid}", str(plist)])
        subprocess.run(["launchctl", "enable", f"gui/{uid}/com.wes.codex-reset-watch.{label}"])
    trim_launchd_logs(log_dir)


def trim_launchd_logs(log_dir: pathlib.Path, max_bytes: int = 512 * 1024, keep_bytes: int = 256 * 1024) -> None:
    for f in log_dir.glob("launchd-*.log"):
        if f.stat().st_size > max_bytes:
            tail = f.read_bytes()[-keep_bytes:]
            f.write_bytes(tail)


def install_systemd(uv: str, program: pathlib.Path, log_dir: pathlib.Path, python_version: str) -> None:
    unit_dir = pathlib.Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([
        uv, "run", "--python", python_version, str(REPO_ROOT / "scripts/render_systemd.py"),
        "--program", str(program), "--out-dir", str(unit_dir), "--log-dir", str(log_dir),
    ])
    subprocess.check_call(["systemctl", "--user", "daemon-reload"])
    for name in ("codex-reset-watch-daily.timer", "codex-reset-watch-monitor.timer"):
        subprocess.check_call(["systemctl", "--user", "enable", "--now", name])


def install_schtasks(program: pathlib.Path) -> None:
    # Task Scheduler has no per-task env-var slot; persist into the user's
    # environment instead of the task args, or the tokens would leak into
    # `schtasks /query /v` output.
    for key in ("TG_BOT_TOKEN", "TG_CHAT_ID"):
        value = os.environ.get(key)
        if value:
            subprocess.run(["setx", key, value], capture_output=True)
    subprocess.check_call(schtasks.daily_task_command(str(program)))
    subprocess.check_call(schtasks.monitor_task_command(str(program)))


def main() -> int:
    uv = uv_bin()
    python_version = os.environ.get("CRW_UV_PYTHON", "3.13")
    dest = pathlib.Path(os.environ.get("CRW_INSTALL_DIR", str(REPO_ROOT))).expanduser()
    bin_dir = pathlib.Path(os.environ.get("CRW_BIN_DIR", str(pathlib.Path.home() / "scripts"))).expanduser()
    config_dir = paths.app_config_dir()
    log_dir = paths.app_log_dir()
    for d in (dest, bin_dir, config_dir, log_dir):
        d.mkdir(parents=True, exist_ok=True)

    subprocess.check_call([uv, "python", "install", python_version])
    sync_dest(dest)

    config_path = config_dir / "config.json"
    if not config_path.exists():
        shutil.copy(dest / "config.example.json", config_path)
        os.chmod(config_path, 0o600)

    env = dict(os.environ, UV_TOOL_BIN_DIR=str(bin_dir))
    subprocess.check_call(
        [uv, "tool", "install", "--force", "--python", python_version, str(dest)], env=env,
    )

    exe_suffix = ".exe" if platform.system() == "Windows" else ""
    program = bin_dir / f"codex-reset-watch{exe_suffix}"
    if not program.exists():
        sys.exit(f"ERROR: uv tool install completed but executable was not found at {program}")

    backend = backend_for(platform.system())
    if backend == "launchd":
        install_launchd(uv, program, log_dir, python_version)
    elif backend == "systemd":
        install_systemd(uv, program, log_dir, python_version)
    else:
        install_schtasks(program)

    print(f"✅ Codex Reset Watch installed with uv tool ({backend} scheduling).")
    print(f"CLI:    {program}")
    print(f"Config: {config_path}")
    print(f"Logs:   {log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
