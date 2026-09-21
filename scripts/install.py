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

import getpass
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
from typing import Any, Callable, Dict, IO, List

try:  # stdlib on macOS/Linux; absent on Windows, where the console
    import readline  # noqa: F401  (imported for its input() line-editing side effect)
except ImportError:
    pass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from codex_reset_watch import config, paths, scheduler  # noqa: E402

backend_for = scheduler.backend_for

CLI_NAMES = ("crw", "codex-reset-watch")

# A shell alias/function, or another file of the same name earlier on PATH, wins
# over the entrypoints uv just wrote — so `crw` can keep resolving to a path this
# installer never touched (classically a stale alias pointing at a hand-made
# symlink from an older install, which then dies with "no such file or
# directory"). Install time is the only place to catch it: once the name is
# shadowed, `crw doctor` never reaches our code to report anything.
_SHADOW_PATTERNS = (
    re.compile(r"^\s*alias\s+(?:-\S+\s+)*(crw|codex-reset-watch)\s*="),      # sh/bash/zsh alias
    re.compile(r"^\s*(?:function\s+)?(crw|codex-reset-watch)\s*\(\s*\)"),    # sh/bash/zsh function
    re.compile(r"^\s*function\s+(crw|codex-reset-watch)\b"),                 # fish / PowerShell
    re.compile(r"^\s*Set-Alias\s+(?:-Name\s+)?(crw|codex-reset-watch)\b", re.IGNORECASE),
)

# Read as plain files, never by sourcing a shell: keeps the check identical on
# Windows (PowerShell profiles) and on POSIX, and runs no user code.
_RC_FILES = (
    ".zshrc", ".zshenv", ".zprofile", ".bashrc", ".bash_profile", ".profile",
    ".config/fish/config.fish",
    "Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
    "Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1",
)


def path_shadows(bin_dir: pathlib.Path, environ: Dict[str, str] | None = None) -> List[pathlib.Path]:
    """Same-named executables that PATH reaches before ``bin_dir``.

    The venv running this installer is skipped: ``uv run`` puts its own shims on
    PATH, and they vanish with the venv — not a shadow the user has to act on.
    """
    environ = os.environ if environ is None else environ
    exts = [e for e in environ.get("PATHEXT", "").split(os.pathsep) if e] if os.name == "nt" else [""]
    own = pathlib.Path(sysconfig.get_path("scripts"))
    entries = [
        d for d in (pathlib.Path(p).expanduser() for p in environ.get("PATH", "").split(os.pathsep) if p)
        if d != own
    ]
    found = []
    for name in CLI_NAMES:
        for d in entries:
            # is_symlink() too: a broken symlink is exactly the failure mode here.
            hit = next((c for c in (d / (name + e) for e in exts) if c.is_file() or c.is_symlink()), None)
            if hit is None:
                continue
            if d != bin_dir:
                found.append(hit)
            break
    return found


def rc_shadows(home: pathlib.Path | None = None) -> List[str]:
    """``file:line: text`` for each shell alias/function that would hide the CLI."""
    home = pathlib.Path.home() if home is None else home
    hits = []
    for rel in _RC_FILES:
        rc = home / rel
        try:
            text = rc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in _SHADOW_PATTERNS):
                hits.append(f"{rc}:{n}: {line.strip()}")
    return hits


# ── first-run Telegram setup ─────────────────────────────────────────────────
# Hidden-input entry uses stdlib ``getpass.getpass`` rather than a hand-rolled
# ``stty -echo`` / ``read -s`` dance: it already suppresses terminal echo the
# same way on macOS, Linux *and* Windows (via ``msvcrt`` there), restores the
# terminal on any exit path including Ctrl-C, and needs no trap/cleanup code
# of our own to get wrong. One function call replaces the whole shell pattern.

def telegram_already_configured(cfg: Dict[str, Any]) -> bool:
    """True when a token *and* a chat id are already reachable — env or config."""
    token, chat_id = config.telegram_credentials(cfg)
    return bool(token and chat_id)


def prompt_telegram_setup(cfg: Dict[str, Any], *, ask: Callable[[str], str] = input,
                          ask_secret: Callable[[str], str] = getpass.getpass,
                          out: IO[str] = sys.stdout) -> bool:
    """Ask for the bot token and chat id, store them, report what happened.

    *ask_secret* never echoes what is typed. The token is written through
    :func:`config.save`, which routes it to the OS keychain rather than the
    config file (see :mod:`codex_reset_watch.secrets_store`) — this function
    never sees or logs the value again once it hands it over.
    """
    print("\nSet up Telegram notifications now?", file=out)
    print("  You'll need a bot token from @BotFather and the chat id it should message.",
          file=out)
    answer = ask("  Set up now? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        print("  Skipped — configure later with `crw config` (or TG_BOT_TOKEN/TG_CHAT_ID).",
              file=out)
        return False

    chat_id = ask("  Telegram chat id: ").strip()
    token = ask_secret("  Telegram bot token (hidden): ").strip()
    if not chat_id or not token:
        print("  Skipped — configure later with `crw config`.", file=out)
        return False

    config.set_value(cfg, "telegram_chat_id", chat_id)
    config.set_value(cfg, "telegram_bot_token", token)
    config.save(cfg)
    print(f"  ✅ Saved. Bot token stored in {config.secrets_store.backend_label()}; "
          f"chat id in {config.config_path()}.", file=out)
    return True


def maybe_setup_telegram(cfg: Dict[str, Any], *, ask: Callable[[str], str] = input,
                         ask_secret: Callable[[str], str] = getpass.getpass,
                         out: IO[str] = sys.stdout) -> bool:
    """Run :func:`prompt_telegram_setup` only when it can and needs to.

    Skipped on a non-interactive stdin (there is no one to answer the prompt —
    a CI run or a piped install must never block on it) and whenever
    credentials are already reachable, so re-running the installer is silent.
    """
    if not sys.stdin.isatty() or telegram_already_configured(cfg):
        return False
    return prompt_telegram_setup(cfg, ask=ask, ask_secret=ask_secret, out=out)


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
    bin_dir = scheduler.bin_dir()
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

    # Put uv's bin dir on PATH for future shells, so `crw ...` works right after
    # a first install without the user hand-editing a profile. Best effort: uv
    # older than 0.4 has no such subcommand; failure is surfaced below instead
    # of promising a shell restart that wouldn't actually fix anything.
    shell_updated = subprocess.run([uv, "tool", "update-shell"], env=env, capture_output=True).returncode == 0

    program = scheduler.cli_path()
    if not program.exists():
        sys.exit(f"ERROR: uv tool install completed but executable was not found at {program}")

    cfg = config.load()  # re-read: config_path may have just been created above
    maybe_setup_telegram(cfg)
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
    # Resolve against PATH entries, not shutil.which: under `uv run` the latter
    # finds the project venv's own shim and would wrongly report "already set up".
    on_path = any(
        pathlib.Path(p).expanduser() == bin_dir
        for p in os.environ.get("PATH", "").split(os.pathsep) if p
    )
    print("Next:   crw doctor  →  crw check      (settings: crw config)")
    if not on_path:
        if shell_updated:
            print(f"        ↳ restart your shell first: {bin_dir} was just added to PATH")
        else:
            print(f"        ↳ {bin_dir} is not on PATH and `uv tool update-shell` failed;")
            print(f"          add it yourself, e.g.: export PATH=\"{bin_dir}:$PATH\"")
    for hit in path_shadows(bin_dir):
        print(f"        ⚠️  `{hit.name}` earlier on PATH shadows this install: {hit}")
        print(f"          remove it, or call {bin_dir / hit.name} directly")
    for hit in rc_shadows():
        print(f"        ⚠️  a shell alias/function shadows the CLI — delete this line:")
        print(f"          {hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
