"""Keep secrets out of the config file: store them in the OS keychain.

The Telegram bot token is the only secret this program has, and it is never
written to ``config.json`` — not even 0600. It goes to whichever native
credential store this machine has:

===========  ==========================================================
macOS        Keychain, via the ``security`` CLI
Linux        the Secret Service (GNOME Keyring / KWallet), via ``secret-tool``
Windows      DPAPI, via PowerShell — user-scoped encryption at rest
none         **nothing is stored**
===========  ==========================================================

That last row is the important one. With no credential store available this
module refuses to write rather than falling back to a plaintext file or to
home-rolled obfuscation, and the caller tells the user to use the
``TG_BOT_TOKEN`` environment variable instead (which the launchd plist and
systemd unit already carry). An encoding that anyone can reverse is not
storage security, it is a comforting lie, so it is not offered.

The secret itself never appears in an argument vector — ``ps`` and shell
history would show it — so every write passes it on **stdin**. Reads strip the
trailing newline the helpers add, and every failure degrades to "no secret"
rather than raising: a scheduled run must not die because a keyring is locked.
"""
from __future__ import annotations

import contextlib
import functools
import platform
import shutil
import subprocess
from typing import Optional, Tuple

IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

#: Keychain service / Secret Service attribute identifying this program's items.
SERVICE = "codex-reset-watch"

#: How long a credential helper may take before we give up on it.
TIMEOUT_SECONDS = 10

BACKEND_LABELS = {
    "keychain": "macOS Keychain",
    "libsecret": "Secret Service (libsecret)",
    "dpapi": "Windows DPAPI",
}


def _run(argv, stdin: Optional[str] = None) -> Tuple[int, str]:
    """The single subprocess funnel: ``(returncode, stdout)``.

    Tests replace this whole function, so nothing above it needs a keychain.
    """
    completed = subprocess.run(
        list(argv), input=stdin, capture_output=True, text=True,
        timeout=TIMEOUT_SECONDS, check=False,
    )
    return completed.returncode, completed.stdout


def _detect_backend() -> Optional[str]:
    if IS_MACOS and shutil.which("security"):
        return "keychain"
    if IS_WINDOWS and shutil.which("powershell.exe"):
        return "dpapi"
    if shutil.which("secret-tool"):
        return "libsecret"
    return None


@functools.lru_cache(maxsize=1)
def backend() -> Optional[str]:
    """Which credential store this machine has, or ``None``. Probed once."""
    return _detect_backend()


def available() -> bool:
    return backend() is not None


def backend_label() -> str:
    """A human name for the active store, for ``doctor`` and the config menu."""
    return BACKEND_LABELS.get(backend() or "", "none")


def _dpapi_path(key: str):
    from .paths import app_config_dir  # local: keeps this module import-light

    return app_config_dir() / f"{key}.dpapi"


def get(key: str) -> str:
    """The stored secret for *key*, or ``""`` when there is none.

    Never raises: a locked keyring, a missing helper or a denied prompt all
    mean "no secret", which the caller already handles.
    """
    active = backend()
    if active is None:
        return ""
    with contextlib.suppress(Exception):
        if active == "keychain":
            code, out = _run(["security", "find-generic-password",
                              "-s", SERVICE, "-a", key, "-w"])
        elif active == "libsecret":
            code, out = _run(["secret-tool", "lookup", "service", SERVICE, "account", key])
        else:  # dpapi
            path = _dpapi_path(key)
            if not path.exists():
                return ""
            code, out = _run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                f"$s = Get-Content -Raw '{path}' | ConvertTo-SecureString; "
                "[Runtime.InteropServices.Marshal]::PtrToStringAuto("
                "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))",
            ])
        if code == 0:
            return out.strip()
    return ""


def set(key: str, value: str) -> bool:  # noqa: A001 - the store's verb, not the builtin
    """Store *value* for *key*. ``False`` when it could not be stored securely.

    An empty *value* deletes the item instead of storing a blank — that is how
    the menu clears a token.
    """
    if not value:
        return delete(key)
    active = backend()
    if active is None:
        return False  # refuse rather than write plaintext; see the module docstring
    with contextlib.suppress(Exception):
        if active == "keychain":
            # -U updates in place instead of stacking duplicate items; -w with
            # no argument makes `security` read the secret from stdin.
            code, _ = _run(["security", "add-generic-password", "-U",
                            "-s", SERVICE, "-a", key, "-w"], stdin=value)
        elif active == "libsecret":
            code, _ = _run(["secret-tool", "store", "--label", f"{SERVICE} {key}",
                            "service", SERVICE, "account", key], stdin=value)
        else:  # dpapi
            path = _dpapi_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            code, _ = _run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$in = [Console]::In.ReadToEnd().Trim(); "
                "ConvertTo-SecureString $in -AsPlainText -Force | ConvertFrom-SecureString | "
                f"Set-Content -NoNewline '{path}'",
            ], stdin=value)
        return code == 0
    return False


def delete(key: str) -> bool:
    """Remove the stored secret for *key*. ``False`` when there was nothing to remove."""
    active = backend()
    if active is None:
        return False
    with contextlib.suppress(Exception):
        if active == "keychain":
            code, _ = _run(["security", "delete-generic-password", "-s", SERVICE, "-a", key])
        elif active == "libsecret":
            code, _ = _run(["secret-tool", "clear", "service", SERVICE, "account", key])
        else:  # dpapi
            path = _dpapi_path(key)
            path.unlink(missing_ok=True)
            return True
        return code == 0
    return False


def demo() -> None:
    import unittest.mock as mock

    calls = []

    def fake(argv, stdin=None):
        calls.append((list(argv), stdin))
        return (0, "stored-token\n")

    with mock.patch.multiple(__import__(__name__, fromlist=["_run"]),
                             _detect_backend=lambda: "keychain", _run=fake):
        assert get("tok") == "stored-token"
        assert set("tok", "s3cret") is True
        assert "s3cret" not in " ".join(calls[-1][0])  # never in argv
        assert calls[-1][1] == "s3cret"                 # always on stdin
    print("secrets_store.demo: ok")


if __name__ == "__main__":
    demo()
