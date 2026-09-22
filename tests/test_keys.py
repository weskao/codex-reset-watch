"""Single-keystroke decoding, without a terminal.

Every test replaces the one OS-specific byte funnel (``keys._read_byte``) with
a queue of bytes, so the escape-sequence logic is exercised identically on
macOS, Linux and Windows CI.
"""
import io
import unittest
import unittest.mock as mock

from codex_reset_watch import keys


def feed(*chunks, windows=False):
    """Patch the byte funnel with *chunks*; a ``None`` chunk means 'nothing arrived'."""
    queue = list(chunks)

    def fake(timeout=None):
        return queue.pop(0) if queue else None

    return mock.patch.multiple(keys, _read_byte=fake, IS_WINDOWS=windows)


class SimpleKeyTests(unittest.TestCase):
    def test_carriage_return_is_enter(self):
        with feed(b"\r"):
            self.assertEqual(keys.read_key().key, keys.Key.ENTER)

    def test_newline_is_enter(self):
        with feed(b"\n"):
            self.assertEqual(keys.read_key().key, keys.Key.ENTER)

    def test_delete_is_backspace(self):
        with feed(b"\x7f"):
            self.assertEqual(keys.read_key().key, keys.Key.BACKSPACE)

    def test_ctrl_c_is_decoded(self):
        with feed(b"\x03"):
            self.assertEqual(keys.read_key().key, keys.Key.CTRL_C)

    def test_printable_char_carries_its_value(self):
        with feed(b"q"):
            event = keys.read_key()
        self.assertEqual(event.key, keys.Key.CHAR)
        self.assertEqual(event.char, "q")

    def test_exhausted_source_is_unknown_not_an_exception(self):
        with feed():
            self.assertEqual(keys.read_key().key, keys.Key.UNKNOWN)


class ArrowTests(unittest.TestCase):
    def test_posix_up_arrow(self):
        with feed(b"\x1b", b"[", b"A"):
            self.assertEqual(keys.read_key().key, keys.Key.UP)

    def test_posix_down_arrow(self):
        with feed(b"\x1b", b"[", b"B"):
            self.assertEqual(keys.read_key().key, keys.Key.DOWN)

    def test_posix_right_arrow(self):
        with feed(b"\x1b", b"[", b"C"):
            self.assertEqual(keys.read_key().key, keys.Key.RIGHT)

    def test_posix_left_arrow(self):
        with feed(b"\x1b", b"[", b"D"):
            self.assertEqual(keys.read_key().key, keys.Key.LEFT)

    def test_windows_arrows_use_their_own_prefix(self):
        with feed(b"\xe0", b"H", windows=True):
            self.assertEqual(keys.read_key().key, keys.Key.UP)
        with feed(b"\x00", b"P", windows=True):
            self.assertEqual(keys.read_key().key, keys.Key.DOWN)

    def test_lone_escape_is_escape(self):
        with feed(b"\x1b", None):
            self.assertEqual(keys.read_key().key, keys.Key.ESCAPE)

    def test_unrecognised_csi_sequence_consumes_its_whole_tail(self):
        # ESC [ 5 ~  (PgUp) is not decoded, but its tail must not leak into the
        # next read as a phantom "~" keystroke.
        with feed(b"\x1b", b"[", b"5", b"~", b"q") as _:
            first = keys.read_key()
            second = keys.read_key()
        self.assertEqual(first.key, keys.Key.UNKNOWN)
        self.assertEqual(second.char, "q")


class InteractivityTests(unittest.TestCase):
    def test_non_tty_streams_are_not_interactive(self):
        with mock.patch("sys.stdin", io.StringIO()), mock.patch("sys.stdout", io.StringIO()):
            self.assertFalse(keys.is_interactive_tty())

    def test_both_streams_must_be_ttys(self):
        tty_in, tty_out = mock.Mock(), mock.Mock()
        tty_in.isatty.return_value = True
        tty_out.isatty.return_value = False
        with mock.patch("sys.stdin", tty_in), mock.patch("sys.stdout", tty_out):
            self.assertFalse(keys.is_interactive_tty())
        tty_out.isatty.return_value = True
        with mock.patch("sys.stdin", tty_in), mock.patch("sys.stdout", tty_out):
            self.assertTrue(keys.is_interactive_tty())

    def test_a_stream_that_raises_is_not_interactive(self):
        broken = mock.Mock()
        broken.isatty.side_effect = ValueError("closed")
        with mock.patch("sys.stdin", broken), mock.patch("sys.stdout", broken):
            self.assertFalse(keys.is_interactive_tty())


class KeyReadyTests(unittest.TestCase):
    """`key_ready` must not poll a real keyboard outside an interactive TTY.

    Windows CI hangs in `run_menu` when this contract is broken: GitHub's
    runner has a console, `msvcrt.kbhit()` stays false, and the pulse loop
    never reaches the injected test reader.
    """

    def test_non_tty_stdin_is_ready_immediately(self):
        with mock.patch("sys.stdin", io.StringIO()):
            self.assertTrue(keys.key_ready(5.0))

    def test_windows_non_tty_stdin_does_not_poll_msvcrt(self):
        import sys

        fake_msvcrt = mock.Mock()
        fake_msvcrt.kbhit.return_value = False
        stdin = mock.Mock()
        stdin.isatty.return_value = False
        with mock.patch.object(keys, "IS_WINDOWS", True), \
                mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}), \
                mock.patch("sys.stdin", stdin):
            self.assertTrue(keys.key_ready(5.0))
        fake_msvcrt.kbhit.assert_not_called()

    def test_a_stdin_without_isatty_is_ready_immediately(self):
        broken = mock.Mock(spec=[])  # no isatty
        with mock.patch("sys.stdin", broken):
            self.assertTrue(keys.key_ready(5.0))


class RawModeTests(unittest.TestCase):
    def test_raw_mode_is_a_noop_without_a_real_tty(self):
        with mock.patch("sys.stdin", io.StringIO()):
            with keys.raw_mode():
                pass  # must not raise

    def test_terminal_settings_are_restored_even_on_an_exception(self):
        termios, tty = mock.Mock(), mock.Mock()
        original = object()
        termios.tcgetattr.return_value = original
        stdin = mock.Mock()
        stdin.fileno.return_value = 0
        with mock.patch.object(keys, "_posix_modules", return_value=(termios, tty)), \
                mock.patch.object(keys, "IS_WINDOWS", False), \
                mock.patch("sys.stdin", stdin), \
                mock.patch("os.isatty", return_value=True):
            with self.assertRaises(RuntimeError):
                with keys.raw_mode():
                    raise RuntimeError("boom")
        termios.tcsetattr.assert_called_once()
        self.assertIs(termios.tcsetattr.call_args[0][2], original)


if __name__ == "__main__":
    unittest.main()
