"""This app's credential store: :class:`telegram_kit.CredentialStore` under the
``codex-reset-watch`` service, with DPAPI files in the app config folder.

The mechanism, and why there is no plaintext fallback, is documented in
:mod:`telegram_kit`. This module keeps the module-level verbs the rest of the
app (and its tests) call and patch.
"""
from __future__ import annotations

import telegram_kit
from telegram_kit import BACKEND_LABELS, IS_MACOS, IS_WINDOWS  # noqa: F401 - re-exported

#: Keychain service / Secret Service attribute identifying this program's items.
SERVICE = "codex-reset-watch"


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
