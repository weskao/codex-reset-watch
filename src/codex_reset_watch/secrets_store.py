"""This app's credential store: :class:`telegram_kit.CredentialStore` under the
``codex-reset-watch`` service, with DPAPI files in the app config folder.

The mechanism, and why there is no plaintext fallback, is documented in
:mod:`telegram_kit`. This module keeps the module-level verbs the rest of the
app (and its tests) call and patch.
"""
from __future__ import annotations

import os

import telegram_kit
from telegram_kit import BACKEND_LABELS, IS_MACOS, IS_WINDOWS  # noqa: F401 - re-exported

#: Keychain service / Secret Service attribute identifying this program's items.
SERVICE = "codex-reset-watch"

# Launched from PowerShell 7, we inherit pwsh's PSModulePath; the DPAPI helper
# (powershell.exe 5.1) then can't autoload ConvertTo-SecureString, so every
# token save failed and took the whole config save down with it. Unset, 5.1
# rebuilds its own default path. ponytail: belongs upstream in telegram_kit._run.
if IS_WINDOWS:
    os.environ.pop("PSModulePath", None)


def _config_dir():
    from .paths import app_config_dir  # local: keeps this module import-light
    return app_config_dir()


_store = telegram_kit.CredentialStore(SERVICE, dpapi_dir=_config_dir)


def available() -> bool:
    return telegram_kit.available()


def backend_label() -> str:
    return telegram_kit.backend_label()


def legacy_windows_env_token_present() -> bool:
    return telegram_kit.legacy_windows_env_token_present()


def get(key: str) -> str:
    return _store.get(key)


def set(key: str, value: str) -> bool:  # noqa: A001 - the store's verb, not the builtin
    return _store.set(key, value)


def delete(key: str) -> bool:
    return _store.delete(key)
