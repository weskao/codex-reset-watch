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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from codex_reset_watch import config, scheduler  # noqa: E402

backend_for = scheduler.backend_for


def main() -> int:
    backend = scheduler.remove()

    bin_dir = scheduler.bin_dir()
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
    cfg = config.load()
    print(f"  rm -rf '{config.state_dir(cfg)}' '{config.log_dir(cfg)}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
