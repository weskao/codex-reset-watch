"""Machine labels for Telegram notices.

Standalone copy of ~/.claude/scripts/host_identity.py — kept in sync by hand
rather than imported across projects.
"""

import functools
import socket
import subprocess
import uuid


@functools.cache
def host_name() -> str:
    try:
        result = subprocess.run(
            ["/usr/sbin/scutil", "--get", "ComputerName"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    # scutil is macOS-only and unavailable in headless launchd runs (e.g. at
    # the lock screen); gethostname() is the cross-platform fallback, but on
    # any OS it can carry an mDNS ".local" suffix that isn't a display name.
    return (socket.gethostname() or "unknown-host").removesuffix(".local")


def host_emoji(name: str) -> str:
    return "🖥️" if "mini" in name.lower() else "💻"


def device_code() -> str:
    """Stable per-device id derived from the primary network MAC address.

    uuid.getnode() is stdlib and identical across macOS/Windows/Linux; it
    only falls back to a random 48-bit value when no real MAC is found.
    """
    return f"{uuid.getnode():012x}"


def masked_device_code() -> str:
    """Device code with only the first 4 hex chars shown — it's derived from
    a MAC address, so treat it like a secret."""
    code = device_code()
    return f"{code[:4]}{'*' * (len(code) - 4)}"


def device_label() -> str:
    """Full display label for notifications, e.g. '🖥️ Mac mini · a1b2****'."""
    name = host_name()
    return f"{host_emoji(name)} {name} · {masked_device_code()}"
