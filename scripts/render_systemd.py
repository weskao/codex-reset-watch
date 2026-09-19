#!/usr/bin/env python3
"""Render systemd --user service+timer unit pairs for Linux.

Analogous to render_launchd.py for macOS. systemd --user has no equivalent of
launchd's EnvironmentVariables plist key inheriting the login env either, so
TG_BOT_TOKEN/TG_CHAT_ID are baked in as Environment= lines at render time.
"""
import argparse
import os
import pathlib


def service_unit(description, exec_start, stdout, stderr, env):
    lines = [
        "[Unit]",
        f"Description={description}",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={exec_start}",
    ]
    for k, v in env.items():
        lines.append(f"Environment={k}={v}")
    lines += [
        f"StandardOutput=append:{stdout}",
        f"StandardError=append:{stderr}",
        "",
    ]
    return "\n".join(lines)


def timer_unit(description, on_calendar):
    return "\n".join([
        "[Unit]",
        f"Description={description}",
        "",
        "[Timer]",
        f"OnCalendar={on_calendar}",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--log-dir", required=True)
    a = p.parse_args()
    program = os.path.abspath(os.path.expanduser(a.program))
    out = pathlib.Path(os.path.expanduser(a.out_dir)); out.mkdir(parents=True, exist_ok=True)
    log = pathlib.Path(os.path.expanduser(a.log_dir)); log.mkdir(parents=True, exist_ok=True)
    env = {k: os.environ[k] for k in ("TG_BOT_TOKEN", "TG_CHAT_ID") if os.environ.get(k)}

    units = {
        "codex-reset-watch-daily.service": service_unit(
            "Codex Reset Watch (daily)", f"{program} daily",
            log / "systemd-daily.out.log", log / "systemd-daily.err.log", env),
        "codex-reset-watch-daily.timer": timer_unit(
            "Codex Reset Watch (daily) timer", "*-*-* 10:00:00"),
        "codex-reset-watch-monitor.service": service_unit(
            "Codex Reset Watch (monitor)", f"{program} monitor",
            log / "systemd-monitor.out.log", log / "systemd-monitor.err.log", env),
        "codex-reset-watch-monitor.timer": timer_unit(
            "Codex Reset Watch (monitor) timer", "*-*-* 0/2:05:00"),
    }
    for name, content in units.items():
        (out / name).write_text(content, encoding="utf-8")
        os.chmod(out / name, 0o644)


if __name__ == "__main__":
    main()
