"""Test package — and the fence around the machine's real credential store.

The suite calls :func:`codex_reset_watch.config.save` and ``load`` for real,
with only the *config file* redirected to a temp dir. The keychain has no such
redirect: the service name is a constant, so an unmocked save used to write —
and, when the cfg had no token in it, **delete** — the developer's own
``codex-reset-watch`` keychain item. Running ``make test`` before a release is
what kept wiping the bot token, and a passing test run silently depended on
having destroyed it (``load() == DEFAULTS`` only holds when the store is
empty).

So the OS boundary is closed once, here, for every test module: the backend
probe reports "no credential store", and :func:`telegram_kit._run` — the one
subprocess funnel — raises if anything still tries to shell out to
``security``/``secret-tool``/PowerShell. Tests that need a working store mock
``get``/``set``/``delete`` themselves (see ``test_config_extras``), and
``test_secrets_store`` patches this same funnel with its own fake, which wins
inside its own context.
"""
import contextlib
import unittest.mock as mock

import telegram_kit


def _no_credential_helpers(argv, stdin=None):  # pragma: no cover - a tripwire
    raise AssertionError(
        "a test reached the real credential store: " + " ".join(map(str, argv))
    )


# ``backend``, not ``_detect_backend``: the probe itself is what
# test_secrets_store.BackendDetectionTests exercises, and it must stay real.
_REAL_BACKEND, _REAL_RUN = telegram_kit.backend, telegram_kit._run
telegram_kit.backend = lambda: None
telegram_kit._run = _no_credential_helpers


@contextlib.contextmanager
def real_credential_store():
    """Lift the fence for the one test that must use the real store.

    ``test_secrets_store.RoundTripTests`` stores and re-reads a throwaway
    ``_roundtrip_probe`` item, because mocking the subprocess funnel is exactly
    what once hid an inert write (``add-generic-password -w`` prompts
    ``/dev/tty`` instead of reading stdin: nothing stored, exit 0, every mocked
    assertion green). It touches its own account name, never the real token.
    """
    with mock.patch.multiple(telegram_kit, backend=_REAL_BACKEND, _run=_REAL_RUN):
        yield
