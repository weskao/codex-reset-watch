#!/usr/bin/env python3
import argparse, os, pathlib, plistlib


def build(program, args, label, stdout, stderr, calendar=None, run_at_load=True, env=None):
    d = {
        "Label": label,
        "ProgramArguments": [program] + args,
        "RunAtLoad": bool(run_at_load),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": stdout,
        "StandardErrorPath": stderr,
    }
    if calendar is not None:
        d["StartCalendarInterval"] = calendar
    if env:
        d["EnvironmentVariables"] = env
    return d


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--program", required=True,
                   help="Absolute executable path installed by uv tool, e.g. ~/scripts/codex-reset-watch expanded")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--log-dir", required=True)
    a = p.parse_args()
    program = os.path.abspath(os.path.expanduser(a.program))
    out = pathlib.Path(os.path.expanduser(a.out_dir)); out.mkdir(parents=True, exist_ok=True)
    log = pathlib.Path(os.path.expanduser(a.log_dir)); log.mkdir(parents=True, exist_ok=True)
    # launchd jobs don't inherit the login shell env, so TG_BOT_TOKEN/TG_CHAT_ID must be
    # baked into the plist at render time (from whatever env install.sh was run with).
    env = {k: os.environ[k] for k in ("TG_BOT_TOKEN", "TG_CHAT_ID") if os.environ.get(k)}
    daily = build(program, ["daily"], "com.wes.codex-reset-watch.daily",
                  str(log / "launchd-daily.out.log"), str(log / "launchd-daily.err.log"),
                  {"Hour": 10, "Minute": 0}, True, env)
    every2 = [{"Hour": h, "Minute": 5} for h in range(0,24,2)]
    monitor = build(program, ["monitor"], "com.wes.codex-reset-watch.monitor",
                    str(log / "launchd-monitor.out.log"), str(log / "launchd-monitor.err.log"),
                    every2, True, env)
    for name, data in [
        ("com.wes.codex-reset-watch.daily.plist", daily),
        ("com.wes.codex-reset-watch.monitor.plist", monitor),
    ]:
        with (out / name).open("wb") as f:
            plistlib.dump(data, f, fmt=plistlib.FMT_XML, sort_keys=False)
        os.chmod(out / name, 0o644)

if __name__ == "__main__":
    main()
