"""Stdlib-only raw-mode single-keystroke reader for the interactive menu.

Canonical here: decoding one keypress (arrows, Enter, Escape, Backspace,
Ctrl-C, printable characters) into a stable :class:`KeyEvent` so callers never
see raw escape bytes; :func:`raw_mode`, which puts the terminal into cbreak
mode with a *guaranteed* restore (including on ``KeyboardInterrupt``); and
:func:`is_interactive_tty`, the single funnel deciding whether a keyboard menu
is possible at all.

Every OS-specific byte read goes through :func:`_read_byte`, and ``termios`` /
``tty`` are imported lazily so this module stays importable on Windows, where
``msvcrt`` is used instead — and ``msvcrt`` is only touched when
:data:`IS_WINDOWS`, so it stays importable on POSIX too.

Note on Ctrl-C: cbreak (not raw) mode leaves ``ISIG`` enabled, so a real
terminal delivers Ctrl-C as ``SIGINT`` / ``KeyboardInterrupt`` rather than as a
decodable ``\\x03`` byte. :data:`Key.CTRL_C` exists for completeness and for
fake byte sources in tests — callers still catch ``KeyboardInterrupt`` around
:func:`read_key` / :func:`raw_mode`.

Callers must never do their own ``sys.stdin.read()`` or escape-sequence
parsing: routing everything through :func:`read_key` keeps the ambiguity
between a bare Escape and the start of an arrow sequence — and the
POSIX-vs-Windows arrow encodings — in exactly one place.
"""
from __future__ import annotations

import enum
import os
import platform
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

IS_WINDOWS = platform.system() == "Windows"

# How long to wait for the rest of an escape sequence before concluding the
# user pressed a bare Escape. Real terminals deliver a multi-byte arrow
# sequence effectively atomically; a human never types ESC, waits, then [.
_ESCAPE_SEQUENCE_TIMEOUT = 0.05


class Key(enum.Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    ENTER = "enter"
    TAB = "tab"
    ESCAPE = "escape"
    BACKSPACE = "backspace"
    CTRL_C = "ctrl_c"
    CHAR = "char"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class KeyEvent:
    """One decoded keypress. Trivially constructible without a terminal, so the
    menu state machine can be driven from a list of these in a test."""

    key: Key
    char: Optional[str] = None


# ── platform byte-read funnel ────────────────────────────────────────────────

def _read_byte_posix(timeout: Optional[float]) -> Optional[bytes]:
    import select

    try:
        fd = sys.stdin.fileno()
    except (OSError, ValueError):
        return None  # stdin has no real fd (redirected/captured, e.g. in tests)
    if timeout is not None:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
    return os.read(fd, 1)


def _read_byte_windows(timeout: Optional[float]) -> Optional[bytes]:
    import msvcrt

    if timeout is not None:
        deadline = time.monotonic() + timeout
        while not msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.001)
    return msvcrt.getch()


def _read_byte(timeout: Optional[float] = None) -> Optional[bytes]:
    """The single OS-specific funnel: read one raw byte from the keyboard.

    ``timeout=None`` blocks until a byte arrives. A numeric timeout returns
    ``None`` when nothing arrives in time — used only to tell a bare Escape
    from the start of a multi-byte sequence. Tests replace this whole function
    with a fake byte source; nothing above this line needs a real terminal.
    """
    if IS_WINDOWS:
        return _read_byte_windows(timeout)
    return _read_byte_posix(timeout)


def key_ready(timeout: float) -> bool:
    """True if a keypress is already waiting — peeks without consuming a byte.

    Used by :func:`codex_reset_watch.ui.run_menu` to animate the selected
    row's cursor while a real terminal is idle: block for at most *timeout*
    seconds, then let the caller redraw the next animation frame instead of
    read_key()'s indefinite block. A redirected/non-tty stdin (piped input,
    tests) always reports ready immediately, so the animation branch never
    fires — and never adds latency — outside a real keyboard menu.
    """
    if IS_WINDOWS:
        import msvcrt

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                return True
            time.sleep(0.01)
        return False
    import select

    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return True
    except (OSError, ValueError):
        return True
    ready, _, _ = select.select([fd], [], [], timeout)
    return bool(ready)


_POSIX_ARROWS = {b"A": Key.UP, b"B": Key.DOWN, b"C": Key.RIGHT, b"D": Key.LEFT}
_WINDOWS_ARROWS = {b"H": Key.UP, b"P": Key.DOWN, b"K": Key.LEFT, b"M": Key.RIGHT}


def read_key() -> KeyEvent:
    """Block for and decode exactly one keypress."""
    first = _read_byte()
    if first is None:
        return KeyEvent(Key.UNKNOWN)
    if first == b"\x03":
        return KeyEvent(Key.CTRL_C)
    if first in (b"\r", b"\n"):
        return KeyEvent(Key.ENTER)
    if first == b"\t":
        return KeyEvent(Key.TAB)
    if first in (b"\x7f", b"\x08"):
        return KeyEvent(Key.BACKSPACE)

    if IS_WINDOWS and first in (b"\x00", b"\xe0"):
        return KeyEvent(_WINDOWS_ARROWS.get(_read_byte(), Key.UNKNOWN))

    if first == b"\x1b":
        second = _read_byte(timeout=_ESCAPE_SEQUENCE_TIMEOUT)
        if second is None:
            return KeyEvent(Key.ESCAPE)
        if second == b"[":
            # CSI: zero or more parameter/intermediate bytes (0x20-0x3F, e.g.
            # the "5" in PgUp's ESC[5~) then exactly one final byte. Consume
            # the whole thing, so an undecoded sequence cannot leak its tail
            # into the next read_key() as a phantom keystroke.
            final = _read_byte(timeout=_ESCAPE_SEQUENCE_TIMEOUT)
            while final is not None and 0x20 <= final[0] <= 0x3F:
                final = _read_byte(timeout=_ESCAPE_SEQUENCE_TIMEOUT)
            return KeyEvent(_POSIX_ARROWS.get(final, Key.UNKNOWN))
        return KeyEvent(Key.UNKNOWN)

    try:
        return KeyEvent(Key.CHAR, first.decode("utf-8"))
    except UnicodeDecodeError:
        return KeyEvent(Key.UNKNOWN)


# ── raw/cbreak mode ──────────────────────────────────────────────────────────

def _posix_modules():
    """Lazy import so the module stays importable where termios/tty do not
    exist. Split out so a test can substitute a fake pair with no terminal."""
    import termios
    import tty

    return termios, tty


@contextmanager
def raw_mode():
    """Cbreak mode for the duration of the block, always restored afterwards —
    including when the block raises (``KeyboardInterrupt`` included).

    A no-op on Windows (``msvcrt`` reads keys without a mode switch) and
    whenever stdin is not a real TTY (piped input, tests, CI).
    """
    if IS_WINDOWS:
        yield
        return
    try:
        termios, tty = _posix_modules()
    except ImportError:
        yield
        return
    try:
        fd = sys.stdin.fileno()
        is_tty = os.isatty(fd)
    except (OSError, ValueError):
        yield
        return
    if not is_tty:
        yield
        return

    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def is_interactive_tty() -> bool:
    """The single funnel for "can a keyboard-driven menu work here?".

    Both streams must be real TTYs: stdin to read keys, stdout to repaint the
    menu in place. Either one redirected (``| cat``, CI logs, tests) means the
    caller should fall back to the numbered prompt instead.
    """
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - a stream with no isatty() is not a terminal
        return False


def demo() -> None:
    import unittest.mock as mock

    def source(*chunks):
        queue = list(chunks)
        return lambda timeout=None: queue.pop(0) if queue else None

    with mock.patch.object(sys.modules[__name__], "_read_byte", source(b"\x1b", b"[", b"A")):
        assert read_key().key is Key.UP
    with mock.patch.object(sys.modules[__name__], "_read_byte", source(b"\x1b", None)):
        assert read_key().key is Key.ESCAPE
    with mock.patch.object(sys.modules[__name__], "_read_byte", source(b"x")):
        assert read_key() == KeyEvent(Key.CHAR, "x")
    print("keys.demo: ok")


if __name__ == "__main__":
    demo()
