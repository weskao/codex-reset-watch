"""Render and apply the native OS scheduler entries from the current config.

macOS → launchd, Linux → systemd --user timers, Windows → Task Scheduler.

This lives in the package rather than in ``scripts/`` so the *installed* CLI can
re-apply the schedule by itself after ``crw config`` changes a timing setting —
the scripts directory is not shipped in the uv-tool install. ``scripts/*.py``
are thin wrappers around these functions.

The monitor job is interval-driven (``StartInterval`` / ``OnUnitActiveSec`` /
``/SC MINUTE``) rather than a list of wall-clock slots, because the configured
interval may be any value from 1 minute to 1 day and enumerating slots for the
small ones would mean a 1440-entry plist. The daily job stays wall-clock.
"""
from __future__ import annotations

import contextlib
import os
import pathlib
import platform
import plistlib
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import config, secrets_store

BACKENDS = {"Darwin": "launchd", "Linux": "systemd", "Windows": "schtasks"}
JOBS = ("daily", "monitor")
SECRET_ENV_KEYS = ("TG_BOT_TOKEN", "TG_CHAT_ID")

LAUNCHD_LABELS = {job: f"com.wes.codex-reset-watch.{job}" for job in JOBS}
SYSTEMD_UNITS = {job: f"codex-reset-watch-{job}" for job in JOBS}
SCHTASKS_NAMES = {"daily": "CodexResetWatchDaily", "monitor": "CodexResetWatchMonitor"}
DAILY_TASK_NAME = SCHTASKS_NAMES["daily"]
MONITOR_TASK_NAME = SCHTASKS_NAMES["monitor"]


def backend_for(system: str) -> str:
    try:
        return BACKENDS[system]
    except KeyError:
        raise ValueError(f"unsupported platform: {system}") from None


def current_backend() -> str:
    return backend_for(platform.system())


def enabled_jobs(cfg: Dict[str, Any]) -> Tuple[str, ...]:
    return tuple(job for job in JOBS if cfg.get(f"{job}_enabled", True))


def launch_agents_dir() -> pathlib.Path:
    return pathlib.Path.home() / "Library" / "LaunchAgents"


def systemd_user_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "systemd" / "user"


# ── launchd ──────────────────────────────────────────────────────────────────

def _plist(program: str, args: List[str], label: str, stdout: str, stderr: str,
           *, calendar: Any = None, interval: Optional[int] = None,
           run_at_load: bool = True, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {
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
    if interval is not None:
        d["StartInterval"] = int(interval)
    if env:
        d["EnvironmentVariables"] = env
    return d


def launchd_plists(program: str, log_dir: pathlib.Path, cfg: Dict[str, Any],
                   env: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, Any]]:
    """``{plist filename: plist dict}`` for the jobs enabled in ``cfg``."""
    log = pathlib.Path(log_dir)
    hour, minute = config.daily_os_local_hm(cfg)
    built = {
        "daily": _plist(
            program, ["daily"], LAUNCHD_LABELS["daily"],
            str(log / "launchd-daily.out.log"), str(log / "launchd-daily.err.log"),
            calendar={"Hour": hour, "Minute": minute}, env=env,
        ),
        "monitor": _plist(
            program, ["monitor"], LAUNCHD_LABELS["monitor"],
            str(log / "launchd-monitor.out.log"), str(log / "launchd-monitor.err.log"),
            interval=int(cfg.get("scan_interval_minutes", 120)) * 60, env=env,
        ),
    }
    return {f"{LAUNCHD_LABELS[job]}.plist": built[job] for job in enabled_jobs(cfg)}


def write_launchd(out_dir: pathlib.Path, plists: Dict[str, Dict[str, Any]]) -> List[pathlib.Path]:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in plists.items():
        target = out / name
        with target.open("wb") as f:
            plistlib.dump(data, f, fmt=plistlib.FMT_XML, sort_keys=False)
        os.chmod(target, 0o644)
        written.append(target)
    # A job switched off must lose its plist, not keep a stale one.
    for job in JOBS:
        stale = out / f"{LAUNCHD_LABELS[job]}.plist"
        if stale.name not in plists:
            stale.unlink(missing_ok=True)
    return written


def launchd_existing_env(out_dir: pathlib.Path) -> Dict[str, str]:
    for job in JOBS:
        path = pathlib.Path(out_dir) / f"{LAUNCHD_LABELS[job]}.plist"
        with contextlib.suppress(OSError, ValueError, plistlib.InvalidFileException):
            data = plistlib.loads(path.read_bytes())
            env = data.get("EnvironmentVariables")
            if isinstance(env, dict) and env:
                return {k: str(v) for k, v in env.items() if k in SECRET_ENV_KEYS}
    return {}


def _launchctl(*args: str, check: bool = False) -> None:
    if check:
        subprocess.check_call(["launchctl", *args])
    else:
        subprocess.run(["launchctl", *args], capture_output=True)


def apply_launchd(program: str, log_dir: pathlib.Path, cfg: Dict[str, Any],
                  env: Optional[Dict[str, str]] = None) -> Tuple[str, ...]:
    out_dir = launch_agents_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = job_env(env, launchd_existing_env(out_dir))
    plists = launchd_plists(program, log_dir, cfg, env)
    uid = str(os.getuid())
    for job in JOBS:  # bootout everything first: a disabled job must stop running
        _launchctl("bootout", f"gui/{uid}/{LAUNCHD_LABELS[job]}")
    write_launchd(out_dir, plists)
    for job in enabled_jobs(cfg):
        _launchctl("bootstrap", f"gui/{uid}", str(out_dir / f"{LAUNCHD_LABELS[job]}.plist"), check=True)
        _launchctl("enable", f"gui/{uid}/{LAUNCHD_LABELS[job]}")
    return enabled_jobs(cfg)


def remove_launchd() -> None:
    uid = str(os.getuid())
    out_dir = launch_agents_dir()
    for job in JOBS:
        _launchctl("bootout", f"gui/{uid}/{LAUNCHD_LABELS[job]}")
        (out_dir / f"{LAUNCHD_LABELS[job]}.plist").unlink(missing_ok=True)


# ── systemd --user ───────────────────────────────────────────────────────────

def _service_unit(description: str, exec_start: str, stdout: Any, stderr: Any,
                  env: Dict[str, str]) -> str:
    lines = ["[Unit]", f"Description={description}", "", "[Service]", "Type=oneshot",
             f"ExecStart={exec_start}"]
    lines += [f"Environment={k}={v}" for k, v in env.items()]
    lines += [f"StandardOutput=append:{stdout}", f"StandardError=append:{stderr}", ""]
    return "\n".join(lines)


def _timer_unit(description: str, body: Iterable[str]) -> str:
    return "\n".join(["[Unit]", f"Description={description}", "", "[Timer]", *body,
                      "", "[Install]", "WantedBy=timers.target", ""])


def systemd_units(program: str, log_dir: pathlib.Path, cfg: Dict[str, Any],
                  env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """``{unit filename: contents}`` for the jobs enabled in ``cfg``."""
    log = pathlib.Path(log_dir)
    env = env or {}
    hour, minute = config.daily_os_local_hm(cfg)
    minutes = int(cfg.get("scan_interval_minutes", 120))
    units: Dict[str, str] = {}
    if "daily" in enabled_jobs(cfg):
        units[f"{SYSTEMD_UNITS['daily']}.service"] = _service_unit(
            "Codex Reset Watch (daily)", f"{program} daily",
            log / "systemd-daily.out.log", log / "systemd-daily.err.log", env)
        units[f"{SYSTEMD_UNITS['daily']}.timer"] = _timer_unit(
            "Codex Reset Watch (daily) timer",
            [f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00", "Persistent=true"])
    if "monitor" in enabled_jobs(cfg):
        units[f"{SYSTEMD_UNITS['monitor']}.service"] = _service_unit(
            "Codex Reset Watch (monitor)", f"{program} monitor",
            log / "systemd-monitor.out.log", log / "systemd-monitor.err.log", env)
        units[f"{SYSTEMD_UNITS['monitor']}.timer"] = _timer_unit(
            "Codex Reset Watch (monitor) timer",
            [f"OnBootSec={minutes}min", f"OnUnitActiveSec={minutes}min"])
    return units


def write_systemd(out_dir: pathlib.Path, units: Dict[str, str]) -> List[pathlib.Path]:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in units.items():
        (out / name).write_text(content, encoding="utf-8")
        os.chmod(out / name, 0o644)
        written.append(out / name)
    for job in JOBS:
        for suffix in (".service", ".timer"):
            stale = out / f"{SYSTEMD_UNITS[job]}{suffix}"
            if stale.name not in units:
                stale.unlink(missing_ok=True)
    return written


def systemd_existing_env(out_dir: pathlib.Path) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for job in JOBS:
        path = pathlib.Path(out_dir) / f"{SYSTEMD_UNITS[job]}.service"
        with contextlib.suppress(OSError):
            for line in path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^Environment=([A-Z0-9_]+)=(.*)$", line.strip())
                if m and m.group(1) in SECRET_ENV_KEYS:
                    found.setdefault(m.group(1), m.group(2))
    return found


def apply_systemd(program: str, log_dir: pathlib.Path, cfg: Dict[str, Any],
                  env: Optional[Dict[str, str]] = None) -> Tuple[str, ...]:
    out_dir = systemd_user_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    env = job_env(env, systemd_existing_env(out_dir))
    units = systemd_units(program, log_dir, cfg, env)
    for job in JOBS:
        if job not in enabled_jobs(cfg):
            subprocess.run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNITS[job]}.timer"],
                           capture_output=True)
    write_systemd(out_dir, units)
    subprocess.check_call(["systemctl", "--user", "daemon-reload"])
    for job in enabled_jobs(cfg):
        subprocess.check_call(["systemctl", "--user", "enable", "--now", f"{SYSTEMD_UNITS[job]}.timer"])
    return enabled_jobs(cfg)


def remove_systemd() -> None:
    out_dir = systemd_user_dir()
    for job in JOBS:
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNITS[job]}.timer"],
                       capture_output=True)
        for suffix in (".service", ".timer"):
            (out_dir / f"{SYSTEMD_UNITS[job]}{suffix}").unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


# ── Windows Task Scheduler ───────────────────────────────────────────────────
# Secrets are never task arguments: `schtasks /query /v` prints those. install.py
# persists TG_BOT_TOKEN/TG_CHAT_ID with `setx` into the user environment instead.

def daily_task_command(program: str, cfg: Optional[Dict[str, Any]] = None,
                       task_name: str = DAILY_TASK_NAME) -> List[str]:
    hour, minute = config.daily_os_local_hm(cfg or config.DEFAULTS)
    return ["schtasks", "/Create", "/F", "/TN", task_name, "/TR", f'"{program}" daily',
            "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}"]


def monitor_task_command(program: str, cfg: Optional[Dict[str, Any]] = None,
                         task_name: str = MONITOR_TASK_NAME) -> List[str]:
    minutes = int((cfg or config.DEFAULTS).get("scan_interval_minutes", 120))
    head = ["schtasks", "/Create", "/F", "/TN", task_name, "/TR", f'"{program}" monitor']
    # HOURLY/DAILY read more naturally than a big /MO count and are what a human
    # would type by hand; MINUTE is the fallback for anything not hour-aligned.
    if minutes % config.MAX_INTERVAL_MINUTES == 0:
        return head + ["/SC", "DAILY", "/ST", "00:05"]
    if minutes % 60 == 0:
        return head + ["/SC", "HOURLY", "/MO", str(minutes // 60), "/ST", "00:05"]
    return head + ["/SC", "MINUTE", "/MO", str(minutes)]


def delete_task_command(task_name: str) -> List[str]:
    return ["schtasks", "/Delete", "/F", "/TN", task_name]


def apply_schtasks(program: str, cfg: Dict[str, Any]) -> Tuple[str, ...]:
    builders = {"daily": daily_task_command, "monitor": monitor_task_command}
    for job in JOBS:
        if job in enabled_jobs(cfg):
            subprocess.check_call(builders[job](program, cfg))
        else:
            subprocess.run(delete_task_command(SCHTASKS_NAMES[job]), capture_output=True)
    return enabled_jobs(cfg)


def remove_schtasks() -> None:
    for name in SCHTASKS_NAMES.values():
        subprocess.run(delete_task_command(name), capture_output=True)


# ── entry points ─────────────────────────────────────────────────────────────

def merged_env(process_env: Optional[Dict[str, str]], existing: Dict[str, str]) -> Dict[str, str]:
    """Credentials for the generated job.

    A re-apply triggered from ``crw config`` usually runs in a shell with no
    TG_* exported; without this merge, re-rendering would silently strip the
    credentials the installer baked in and the job would go quiet.
    """
    env = dict(existing)
    for key in SECRET_ENV_KEYS:
        value = (process_env or {}).get(key) or os.environ.get(key)
        if value:
            env[key] = value
    return env


def job_env(process_env: Optional[Dict[str, str]], existing: Dict[str, str]) -> Dict[str, str]:
    """What to bake into the generated job — nothing, when there is a keychain.

    The job resolves its own credentials through :func:`config.telegram_credentials`,
    which reads the keychain-backed config, so baking a copy into a 0644 plist
    or unit file would only put the token on disk in plaintext *and* pin it:
    whatever was in the installing shell's ``TG_BOT_TOKEN`` used to be carried
    forward on every re-apply and outranked the token the user later set with
    ``crw config``. Without a credential store the config cannot hold a token
    at all and the environment is the only channel left, so there it is still
    baked in — launchd and systemd jobs inherit no login shell.
    """
    if secrets_store.available():
        return {}
    return merged_env(process_env, existing)


def bin_dir() -> pathlib.Path:
    """Directory uv puts the ``codex-reset-watch``/``crw`` entrypoints in.

    Mirrors uv's own resolution order so that a plain ``uv tool install`` lands
    in the same place install.py does. Overriding it to a private directory is
    what broke the scheduler once: uv rewrites its receipt on every reinstall
    and deletes the entrypoints it previously recorded, so any later bare
    ``uv tool install``/``upgrade`` silently moved the CLI out from under the
    launchd/systemd/schtasks job pointing at the old location.
    """
    configured = (
        os.environ.get("CRW_BIN_DIR")
        or os.environ.get("UV_TOOL_BIN_DIR")
        or os.environ.get("XDG_BIN_HOME")
    )
    if configured:
        return pathlib.Path(configured).expanduser()
    return pathlib.Path.home() / ".local" / "bin"


def cli_path(name: str = "codex-reset-watch") -> pathlib.Path:
    """Where :func:`bin_dir` holds the given entrypoint on this OS."""
    suffix = ".exe" if platform.system() == "Windows" else ""
    return bin_dir() / f"{name}{suffix}"


def program_path() -> str:
    """Absolute path of the installed CLI the scheduler should invoke.

    Prefers the stable ``uv tool install`` location install.py itself uses
    (see :func:`bin_dir`) over both ``shutil.which`` and ``sys.argv[0]``:
    either of those resolves to whatever "codex-reset-watch" is active in the
    CURRENT process context, which inside ``uv run``/a project venv (e.g. a
    developer running `crw config` from a checkout) is that ephemeral venv's
    shim — a scheduler job pointed there stops working the moment that venv is
    rebuilt or removed.
    """
    import shutil
    import sys

    for name in ("codex-reset-watch", "crw"):
        candidate = cli_path(name)
        if candidate.exists():  # keep the symlink path itself (README's documented location)
            return str(candidate)
    found = shutil.which("codex-reset-watch") or shutil.which("crw")
    if found:
        return os.path.abspath(found)
    argv0 = sys.argv[0] if sys.argv else ""
    return os.path.abspath(argv0 or "codex-reset-watch")


def apply(*, program: Optional[str] = None, cfg: Optional[Dict[str, Any]] = None,
          env: Optional[Dict[str, str]] = None) -> Tuple[str, Tuple[str, ...]]:
    """Re-render and (re)register the scheduler entries. Returns (backend, jobs)."""
    cfg = cfg if cfg is not None else config.load()
    program = program or program_path()
    log_dir = config.log_dir(cfg)
    log_dir.mkdir(parents=True, exist_ok=True)
    backend = current_backend()
    if backend == "launchd":
        return backend, apply_launchd(program, log_dir, cfg, env)
    if backend == "systemd":
        return backend, apply_systemd(program, log_dir, cfg, env)
    return backend, apply_schtasks(program, cfg)


def remove() -> str:
    backend = current_backend()
    {"launchd": remove_launchd, "systemd": remove_systemd, "schtasks": remove_schtasks}[backend]()
    return backend


def demo() -> None:
    cfg = dict(config.DEFAULTS)
    cfg["scan_interval_minutes"] = 15
    plists = launchd_plists("/bin/crw", pathlib.Path("/tmp/logs"), cfg)
    assert plists["com.wes.codex-reset-watch.monitor.plist"]["StartInterval"] == 900
    assert "StartCalendarInterval" in plists["com.wes.codex-reset-watch.daily.plist"]

    cfg["daily_enabled"] = False
    assert enabled_jobs(cfg) == ("monitor",)
    assert "com.wes.codex-reset-watch.daily.plist" not in launchd_plists("/bin/crw", pathlib.Path("/tmp"), cfg)
    units = systemd_units("/bin/crw", pathlib.Path("/tmp"), cfg)
    assert set(units) == {"codex-reset-watch-monitor.service", "codex-reset-watch-monitor.timer"}
    assert "OnUnitActiveSec=15min" in units["codex-reset-watch-monitor.timer"]

    assert monitor_task_command("C:/crw.exe", cfg)[-3:] == ["/SC", "MINUTE", "/MO", "15"][-3:]
    full_day = dict(config.DEFAULTS, scan_interval_minutes=1440)
    assert "/SC" in monitor_task_command("C:/crw.exe", full_day)
    assert monitor_task_command("C:/crw.exe", full_day)[-3:] == ["/SC", "DAILY", "/ST", "00:05"][-3:]

    assert merged_env({"TG_BOT_TOKEN": "new"}, {"TG_BOT_TOKEN": "old", "TG_CHAT_ID": "42"}) == {
        "TG_BOT_TOKEN": "new", "TG_CHAT_ID": "42"}

    import unittest.mock as mock
    with mock.patch.object(secrets_store, "available", lambda: True):
        assert job_env({"TG_BOT_TOKEN": "new"}, {"TG_CHAT_ID": "42"}) == {}   # keychain: bake nothing
    with mock.patch.object(secrets_store, "available", lambda: False):
        assert job_env({"TG_BOT_TOKEN": "new"}, {"TG_CHAT_ID": "42"}) == {
            "TG_BOT_TOKEN": "new", "TG_CHAT_ID": "42"}                        # no store: still baked
    print("scheduler.demo: ok")


if __name__ == "__main__":
    demo()
