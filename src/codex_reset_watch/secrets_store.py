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
home-rolled obfuscation. Environment-only credentials work for manual runs,
but cannot be persisted in scheduler files. An encoding that anyone can reverse is not
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
import re
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


def _unhex(secret: str) -> str:
    """Undo the hex encoding ``security -w`` applies to "non-clean" secrets.

    It decides that per item, so the shape of the output is the only signal.
    A secret that is itself pure hex stays as-is unless it also decodes to
    valid UTF-8 — Telegram tokens contain ``:`` so they never take that path.
    """
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", secret):
        return secret
    try:
        return bytes.fromhex(secret).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return secret


def _batch_quote(value: str) -> str:
    """Escape a value for a double-quoted argument in ``security -i`` batch mode.

    Rejects newlines outright: batch mode is line-oriented, so an embedded one
    would end the command and let the rest be read as a second one.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("security batch argument contains a newline")
    return value.replace("\\", "\\\\").replace('"', '\\"')


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


def legacy_windows_env_token_present() -> bool:
    """Detect a token left by older `setx` installs without reading it aloud."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, "TG_BOT_TOKEN")
            return bool(value)
    except OSError:
        return False


def _dpapi_path(key: str):
    from .paths import app_config_dir  # local: keeps this module import-light
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError("Invalid credential identifier")
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
            if code == 0:
                return _unhex(out.strip())
        elif active == "libsecret":
            code, out = _run(["secret-tool", "lookup", "service", SERVICE, "account", key])
        else:  # dpapi
            path = _dpapi_path(key)
            if not path.exists():
                return ""
            code, out = _run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$s = [Console]::In.ReadToEnd().Trim() | ConvertTo-SecureString; "
                "[Runtime.InteropServices.Marshal]::PtrToStringAuto("
                "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s))",
            ], stdin=path.read_text(encoding="utf-8"))
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
            # `add-generic-password -w` with no argument does NOT read stdin — it
            # opens /dev/tty and prompts, so piping the secret there stored an
            # empty item and still exited 0. Batch mode (`security -i`) takes the
            # whole command on stdin, which keeps the secret out of every argv
            # (i.e. out of `ps`), and -X hex-encodes it past the tokenizer's
            # quoting and newline rules. -U updates in place rather than stacking
            # duplicate items.
            command = 'add-generic-password -U -s "{}" -a "{}" -X {}\n'.format(
                _batch_quote(SERVICE), _batch_quote(key), value.encode("utf-8").hex())
            code, _ = _run(["security", "-i"], stdin=command)
        elif active == "libsecret":
            code, _ = _run(["secret-tool", "store", "--label", f"{SERVICE} {key}",
                            "service", SERVICE, "account", key], stdin=value)
        else:  # dpapi
            path = _dpapi_path(key)
            code, encrypted = _run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "$in = [Console]::In.ReadToEnd().Trim(); "
                "ConvertTo-SecureString $in -AsPlainText -Force | ConvertFrom-SecureString",
            ], stdin=value)
            if code != 0 or not encrypted.strip():
                return False
            from .paths import write_private
            write_private(path, encrypted.strip())
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
        argv, stdin = calls[-1]
        assert "s3cret" not in " ".join(argv)              # never in argv
        assert argv == ["security", "-i"]                  # batch mode, not -w
        assert "s3cret" not in stdin                       # hex-encoded, not literal
        assert "s3cret".encode().hex() in stdin            # ...but it is in there
    assert _unhex("68690a") == "hi\n"      # security's hex output is decoded
    assert _unhex("nothex") == "nothex"    # ...and anything else is left alone
    print("secrets_store.demo: ok")


if __name__ == "__main__":
    demo()
