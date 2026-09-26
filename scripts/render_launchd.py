#!/usr/bin/env python3
"""Render launchd plist(s) from the current config. Thin CLI wrapper around
``codex_reset_watch.scheduler`` (the single source of truth, also used by the
installed CLI's own ``crw config``/``crw apply-schedule``).
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from codex_reset_watch import config, scheduler  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program", required=True,
                   help="Absolute executable path installed by uv tool, e.g. ~/.local/bin/codex-reset-watch expanded")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--log-dir", required=True)
    a = p.parse_args()
    program = os.path.abspath(os.path.expanduser(a.program))
    out = pathlib.Path(os.path.expanduser(a.out_dir))
    log = pathlib.Path(os.path.expanduser(a.log_dir))
    log.mkdir(parents=True, exist_ok=True)
    cfg = config.load()
    # Scheduled jobs read local credentials; reject environment-only credentials.
    existing = scheduler.launchd_existing_env(out)
    try:
        scheduler._migrate_legacy_credentials(existing, cfg)
        env = scheduler.job_env(dict(os.environ), existing, cfg)
    except (OSError, ValueError):
        scheduler.write_launchd(out, scheduler.launchd_plists(program, log, cfg))
        raise
    plists = scheduler.launchd_plists(program, log, cfg, env)
    scheduler.write_launchd(out, plists)


if __name__ == "__main__":
    main()
