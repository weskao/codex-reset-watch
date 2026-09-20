#!/usr/bin/env python3
"""Cross-platform installer: uv tool install + native OS scheduling.

macOS -> launchd, Linux -> systemd --user timers, Windows -> Task Scheduler.
Replaces the old install.sh: bash isn't native on Windows, Python already is
(uv itself requires it), so one script covers all three OSes.

Scheduling is config-driven (see ``codex_reset_watch.config``/``.scheduler``):
this script renders/registers whichever jobs `daily_enabled`/`monitor_enabled`
turn on, at the times/interval the config says. Re-run it (or `crw
apply-schedule` / `crw config`) after changing those settings.
"""
from __future__ import annotations

import os
import pathlib
import platform
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from codex_reset_watch import config, paths, scheduler  # noqa: E402

backend_for = scheduler.backend_for


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


def trim_launchd_logs(log_dir: pathlib.Path, max_bytes: int = 512 * 1024, keep_bytes: int = 256 * 1024) -> None:
    for f in log_dir.glob("launchd-*.log"):
        if f.stat().st_size > max_bytes:
            tail = f.read_bytes()[-keep_bytes:]
            f.write_bytes(tail)


def install_schtasks(program: pathlib.Path, cfg) -> None:
    # Task Scheduler has no per-task env-var slot; persist into the user's
    # environment instead of the task args, or the tokens would leak into
    # `schtasks /query /v` output.
    for key in scheduler.SECRET_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            subprocess.run(["setx", key, value], capture_output=True)
    scheduler.apply_schtasks(str(program), cfg)


def main() -> int:
    uv = uv_bin()
    python_version = os.environ.get("CRW_UV_PYTHON", "3.13")
    dest = pathlib.Path(os.environ.get("CRW_INSTALL_DIR", str(REPO_ROOT))).expanduser()
    bin_dir = pathlib.Path(os.environ.get("CRW_BIN_DIR", str(pathlib.Path.home() / "scripts"))).expanduser()
    config_dir = paths.app_config_dir()
    log_dir = config.log_dir(config.load())  # config file may not exist yet: defaults apply
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

    cfg = config.load()  # re-read: config_path may have just been created above
    backend = backend_for(platform.system())
    if backend == "launchd":
        scheduler.apply_launchd(str(program), log_dir, cfg)
        trim_launchd_logs(log_dir)
    elif backend == "systemd":
        scheduler.apply_systemd(str(program), log_dir, cfg)
    else:
        install_schtasks(program, cfg)

    jobs = scheduler.enabled_jobs(cfg) or ("none",)
    print(f"✅ Codex Reset Watch installed with uv tool ({backend} scheduling: {', '.join(jobs)}).")
    print(f"CLI:    {program}")
    print(f"Config: {config_path}")
    print(f"Logs:   {log_dir}")
    print("Adjust timing/notifications anytime: crw config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
