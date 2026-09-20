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
                   help="Absolute executable path installed by uv tool, e.g. ~/scripts/codex-reset-watch expanded")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--log-dir", required=True)
    a = p.parse_args()
    program = os.path.abspath(os.path.expanduser(a.program))
    out = pathlib.Path(os.path.expanduser(a.out_dir))
    log = pathlib.Path(os.path.expanduser(a.log_dir))
    log.mkdir(parents=True, exist_ok=True)
    cfg = config.load()
    # launchd jobs don't inherit the login shell env, so TG_BOT_TOKEN/TG_CHAT_ID must be
    # baked into the plist at render time (from whatever env this was run with, merged
    # with whatever an existing plist already carries).
    env = scheduler.merged_env(dict(os.environ), scheduler.launchd_existing_env(out))
    plists = scheduler.launchd_plists(program, log, cfg, env)
    scheduler.write_launchd(out, plists)


if __name__ == "__main__":
    main()
