"""Terminal presentation for ``crw config``.

**Look.** Frameless on purpose: no box encloses the settings, only a green
accent mark, a hairline rule under the header and a dotted leader between each
label and its value. The signature colour is ChatGPT's green (``#10A37F``),
used for the accent mark, the group bars, the cursor and the selected row's
backing — everything else stays grey so the green means something. Colour is
dropped entirely when stdout is not a TTY or ``NO_COLOR`` is set, so
``crw config --list | cat`` stays readable and the tests can assert plain text.

**Interaction.** Two menus, one schema:

* :func:`run_menu` — the keyboard menu used on a real terminal. ``↑↓`` moves,
  ``←→`` changes a toggle or a choice, ``Enter`` edits a typed value, and the
  selected row's help line explains itself as you move. Its brain is
  :func:`step`, a *pure* function of ``(state, keypress)``, so the whole
  interaction is testable without a terminal; :func:`run_menu` is only the
  shell that draws, reads one key, and performs the side effects ``step`` asks
  for (save, apply schedule, export, import).
* :func:`fallback_menu` — the numbered prompt used whenever a keyboard menu
  cannot work (piped stdin/stdout, CI, tests). Same schema, same validation.

:func:`config_menu` picks between them, so callers never have to.
"""
from __future__ import annotations

import contextlib
import functools
import importlib.metadata
import json
import os
import pathlib
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, IO, List, Optional, Sequence, Tuple

from . import config, i18n, keys, scheduler, secrets_store

# ── palette ──────────────────────────────────────────────────────────────────
# ChatGPT green (#10A37F) is the body colour; the rest is deliberately grey.
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ACCENT = "\033[38;2;16;163;127m"   # #10A37F — the one brand colour
TITLE = "\033[38;2;25;195;125m"    # #19C37D — a step brighter, for headings
OK = "\033[38;2;25;195;125m"
FRAME = "\033[38;5;239m"
MUTED = "\033[38;5;245m"
WARN = "\033[38;5;179m"
ERR = "\033[38;5;203m"
# The selected row: a saturated band, near-white label, bright value. Dark
# enough to keep green text legible, light enough to find at a glance — the
# earlier 12%-value backing was effectively invisible on a dim display.
SEL = "\033[48;2;20;74;62m"
SEL_TEXT = "\033[38;2;236;253;245m"
SEL_VALUE = "\033[38;2;110;231;183m"
SEL_DOT = "\033[38;2;45;120;100m"

GLYPH_MARK = "◆"
GLYPH_GROUP = "▍"
GLYPH_CURSOR = "▸"
# The selected row's cursor breathes. Rotating it was tried and abandoned: a
# terminal cell holds one glyph from the font, the font has no continuous
# family of in-between shapes, so any "rotation" is a handful of visibly
# different characters swapping in place — which reads as a flicker, not as
# motion. Colour is the one axis a terminal *can* animate continuously, at
# 24-bit depth, so the glyph never moves and never changes shape and only its
# brightness ramps up and down.
#
# The dim end sits well clear of the selected row's band (rgb 20,74,62): a
# cursor that fades into its own background is exactly the blink this avoids.
CURSOR_PULSE_DIM = (70, 165, 135)
CURSOR_PULSE_BRIGHT = (190, 255, 225)
CURSOR_PULSE_INTERVAL = 0.1   # seconds per frame while a real terminal is idle


def _pulse_frames(glyph: str, dim: Tuple[int, int, int],
                  bright: Tuple[int, int, int], steps: int = 7) -> Tuple[str, ...]:
    """The breathe cycle: *steps* colours from *bright* down to *dim* and back,
    each an SGR prefix on *glyph*.

    Frame 0 is the brightest, so a one-shot render that never touches the
    animation shows a cursor at full strength. The endpoints are not repeated
    on the way back up, so the cycle has no stutter at the turning points.
    """
    levels = [i / (steps - 1) for i in range(steps)]
    frames = []
    for level in levels[::-1] + levels[1:-1]:
        rgb = tuple(round(d + (b - d) * level) for d, b in zip(dim, bright))
        frames.append("\033[38;2;%d;%d;%dm%s" % (*rgb, glyph))
    return tuple(frames)


CURSOR_PULSE_FRAMES = _pulse_frames(
    GLYPH_CURSOR, CURSOR_PULSE_DIM, CURSOR_PULSE_BRIGHT)
GLYPH_PROMPT = "❱"
GLYPH_CARET = "▏"
GLYPH_CYCLE = "←→"      # this row changes with the arrow keys
GLYPH_ENTER = "⏎"       # this row opens an editor
GLYPH_MORE_ABOVE = "▴"  # the list is scrolled: more rows off-screen that way
GLYPH_MORE_BELOW = "▾"
GLYPH_TAB = "⇥"         # the key that switches mode, named on the tab row
GLYPH_TAB_RULE = "━"    # heavy run: the hairline thickened under the active tab
PANEL_WIDTH = 72
TAB_INDENT = 3          # tabs line up with the config path above them
TAB_GAP = 3             # space between the two tabs
AFFORD_WIDTH = 3        # reserved on EVERY row so values never shift

_PAINT_NAMES = ("RESET", "BOLD", "DIM", "ACCENT", "TITLE", "OK", "SEL",
                "SEL_TEXT", "SEL_VALUE", "SEL_DOT", "FRAME", "MUTED", "WARN", "ERR")

_ANSI = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """*text* with every SGR escape removed — what the terminal actually shows."""
    return _ANSI.sub("", text)


@functools.lru_cache(maxsize=1)
def package_version() -> str:
    """The running version, from installed package metadata.

    Shown in the header so a stale install is visible at a glance rather than
    guessed at. A source checkout that was never installed has no metadata —
    that is a normal way to run this, so it reports ``dev`` instead of raising.
    """
    with contextlib.suppress(importlib.metadata.PackageNotFoundError, Exception):
        return importlib.metadata.version("codex-reset-watch")
    return "dev"


@functools.lru_cache(maxsize=1)
def _enable_windows_vt() -> None:
    """One-time opt-in so the classic ``cmd.exe``/conhost window renders our
    ``\\033[...m`` codes instead of printing them literally.

    Windows Terminal and modern PowerShell already interpret them without
    this; only the legacy console host needs the SetConsoleMode nudge. Best
    effort and cached: no console (piped output, a service) has nothing to
    enable, and there is no reason to retry every print.
    """
    import ctypes

    with contextlib.suppress(Exception):
        handle = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def colour_enabled(stream: Optional[IO[str]] = None) -> bool:
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        enabled = bool(stream.isatty())
    except Exception:  # noqa: BLE001 - a stream with no isatty() is not a terminal
        return False
    if enabled and keys.IS_WINDOWS:
        _enable_windows_vt()
    return enabled


class Paint:
    """Colour codes, or empty strings when colour is off. One object, no globals."""

    def __init__(self, enabled: bool):
        for name in _PAINT_NAMES:
            setattr(self, name.lower(), globals()[name] if enabled else "")


def width(text: str) -> int:
    """Display columns, counting CJK/emoji as two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)



def _pad(text: str, columns: int) -> str:
    return text + " " * max(0, columns - width(text))


def _slice_by_width(text: str, columns: int) -> Tuple[str, str]:
    """*text* split into a prefix that fits *columns* display cells and the rest."""
    used = 0
    for i, ch in enumerate(text):
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > columns:
            return text[:i], text[i:]
        used += step
    return text, ""


def _wrap(text: str, columns: int, max_lines: int = 3) -> List[str]:
    """Word-wrap *text* to *columns* display cells instead of clipping it.

    A footer message (help text, a validation error, an import summary) used
    to lose its back half behind a single ``…`` — the fix is to keep every
    word, just spread across more lines. A run with no spaces wider than one
    line (a CJK sentence, a long path) is hard-broken since there is no word
    boundary to break on. Capped at *max_lines* so one runaway message cannot
    swallow the whole settings list; anything left over falls back to the old
    ellipsis on the last line.
    """
    lines: List[str] = [""]
    for word in text.split(" "):
        candidate = f"{lines[-1]} {word}".strip() if lines[-1] else word
        if width(candidate) <= columns:
            lines[-1] = candidate
            continue
        if lines[-1]:
            lines.append("")
        while width(word) > columns:
            head, word = _slice_by_width(word, columns)
            lines[-1] = head
            lines.append("")
        lines[-1] = word
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _clip(lines[-1], columns)
    return lines


def _clip(text: str, columns: int) -> str:
    """Truncate to *columns* display cells, with an ellipsis when it had to cut."""
    if width(text) <= columns:
        return text
    out, used = "", 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if used + step > columns - 1:
            break
        out, used = out + ch, used + step
    return out + "…"


# ── static rendering ─────────────────────────────────────────────────────────

def _tilde(path: Any) -> str:
    """``/Users/me/x`` → ``~/x``: the path line is for orientation, not for
    publishing whose laptop this is (these frames end up in screenshots)."""
    text = str(path)
    home = str(pathlib.Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def _header(paint: Paint, lang: str, mode: Optional[str] = None,
            rule: bool = True) -> List[str]:
    """The top of the panel — title, config path, and (by default) the hairline.

    *mode* adds a badge between the title and the version, for the surfaces
    that have no tab bar to carry it (``--list``, which prints the full schema
    and offers no key to press). The keyboard menu passes ``mode=None`` and
    ``rule=False`` instead, and follows this with :func:`_tab_bar`, whose own
    rule replaces the hairline — so the menu gains one line, not two.
    """
    title = i18n.t("menu.title", lang)
    version = f"v{package_version()}"
    badge_text = i18n.t(f"menu.badge_{mode}", lang) if mode else ""
    badge_colour = paint.warn if mode == "advanced" else paint.accent
    tail = f"{paint.muted}{version}{paint.reset}"
    if badge_text:
        tail = f"{badge_colour}{badge_text}{paint.reset} {tail}"
    gap = (PANEL_WIDTH - 3 - width(title) - width(version)
           - (width(badge_text) + 1 if badge_text else 0))
    lines = [
        f" {paint.accent}{GLYPH_MARK}{paint.reset} {paint.bold}{paint.title}{title}{paint.reset}"
        f"{' ' * max(1, gap)}{tail}",
        f"   {paint.muted}{_clip(_tilde(config.config_path()), PANEL_WIDTH - 3)}{paint.reset}",
    ]
    if rule:
        lines.append(f" {paint.frame}{'─' * PANEL_WIDTH}{paint.reset}")
    return lines


def _tab_bar(paint: Paint, lang: str, mode: str) -> List[str]:
    """The two mode tabs, and the rule that underlines the active one.

    Both modes are always on screen with the size of each list, so switching
    is a visible choice between two things rather than a hidden toggle, and
    the row names the key out loud (``⇥ Tab to switch``) — a tab nobody knows
    how to reach is just a label.

    The underline *is* the panel's hairline: only the active tab's own columns
    thicken to ``━`` and turn accent. That is what makes two words read as two
    tabs without a single box-drawing corner, and it survives ``NO_COLOR`` —
    with colour off the heavy run still marks the active tab.
    """
    labels = [(m, f"{i18n.t(f'menu.tab_{m}', lang)} {len(config.visible_settings(m))}")
              for m in config.UI_MODES]
    cells: List[str] = []
    column = TAB_INDENT          # where the next tab's text starts
    active = (column, 0)         # (start column, width) of the selected tab
    for name, text in labels:
        if name == mode:
            active = (column, width(text))
            cells.append(f"{paint.bold}{paint.title}{text}{paint.reset}")
        else:
            cells.append(f"{paint.muted}{text}{paint.reset}")
        column += width(text) + TAB_GAP
    row = " " * TAB_INDENT + (" " * TAB_GAP).join(cells)

    hint_text = i18n.t("menu.tab_hint", lang)
    pad = PANEL_WIDTH - width(strip_ansi(row)) - width(GLYPH_TAB) - 1 - width(hint_text)
    row += (" " * max(1, pad) + f"{paint.accent}{GLYPH_TAB}{paint.reset} "
            f"{paint.muted}{hint_text}{paint.reset}")

    # The rule starts at line column 1 (after the leading space), so the
    # column a tab sits at is `start` characters into the rule's own run.
    start, span = active
    before = "─" * max(0, start - 1)
    after = "─" * max(0, PANEL_WIDTH - len(before) - span)
    return [
        row,
        f" {paint.frame}{before}{paint.reset}{paint.accent}{GLYPH_TAB_RULE * span}"
        f"{paint.reset}{paint.frame}{after}{paint.reset}",
    ]


def _value_colour(paint: Paint, setting: config.Setting, value: Any) -> str:
    if setting.kind == "bool":
        return paint.ok if value else paint.muted
    if setting.kind in ("secret", "text_optional", "path") and not value:
        return paint.muted
    return paint.title


def _affordance(setting: config.Setting) -> str:
    """What the selected row responds to: ``←→`` to cycle, ``⏎`` to edit."""
    return GLYPH_CYCLE if _cycle_options(setting) is not None else GLYPH_ENTER


def _format_hint(setting: config.Setting, lang: str) -> str:
    """What this field will accept, shown while its editor is open.

    Stating the shape up front is the other half of the keystroke filter: the
    filter makes a bad value impossible to type, this says what a good one
    looks like, so nobody has to discover the rule by being refused.
    """
    if setting.kind == "time":
        return i18n.t("hint.time", lang)
    if setting.kind == "interval":
        return i18n.t("hint.interval", lang)
    if setting.kind == "int" and setting.minimum is not None and setting.maximum is not None:
        return i18n.t("hint.range", lang, low=setting.minimum, high=setting.maximum)
    if setting.kind == "secret":
        return i18n.t("menu.secret_hint", lang)
    return ""


def _row(paint: Paint, index: int, setting: config.Setting, value: Any, lang: str,
         *, selected: bool = False, editing: bool = False, edit_buffer: str = "",
         cursor_glyph: str = GLYPH_CURSOR) -> str:
    """One settings line, laid out in fixed columns so nothing shifts::

         ▶  4 Scan interval ···················· 45 minutes  ←→
        └┬┘└┬┘ └──── label ───┘└─ leader ─┘└─ value ─┘└ afford ┘
         │  └ number (2)                       right-aligned
         └ cursor — breathes through CURSOR_PULSE_FRAMES while idle

    Every column — including the affordance hint — is reserved on *every* row,
    so moving the cursor changes colour and nothing else. A row whose value
    would overflow is clipped, never wrapped: a wrapped row would desynchronise
    the in-place repaint.
    """
    label = config.label(setting, lang)
    if editing:
        # The menu owns the terminal in cbreak mode and echoes each keystroke
        # itself — a secret must never appear in that echo, so a bot token is
        # shown as bullets, never the characters typed.
        visible = "•" * len(edit_buffer) if setting.kind == "secret" else edit_buffer
        shown, colour = f"{visible}{GLYPH_CARET}", paint.ok
    else:
        shown = config.render(setting, value, lang)
        colour = paint.sel_value if (selected and paint.sel) else _value_colour(paint, setting, value)

    # The cursor field is as wide as the animation's frames: a one-cell glyph
    # keeps the historic layout, while a style that needs room to move (a
    # nudge, a growing tail) widens every row equally, so the columns still
    # line up. A frame may carry its own SGR colour — dropped when colour is
    # off, so piped output and the tests stay plain text.
    glyph = cursor_glyph if paint.accent else strip_ansi(cursor_glyph)
    cursor_width = width(strip_ansi(cursor_glyph))
    indent = 5 + cursor_width   # " ▶ NN "
    tail = AFFORD_WIDTH   # the reserved hint column on the right
    # Budget: PANEL_WIDTH - indent - tail - label - (space + 2-cell minimum
    # leader + space). Clipping to anything wider makes the leader hit its
    # floor and pushes the row past the panel edge, which knocks the value
    # column out of alignment on exactly the longest rows.
    shown = _clip(shown, max(8, PANEL_WIDTH - indent - tail - width(label) - 4))
    leader = max(2, PANEL_WIDTH - indent - tail - width(label) - width(shown) - 2)

    if selected:
        mark = f"{paint.accent}{glyph}{paint.reset}{paint.sel}"
        label_text = f"{paint.bold}{paint.sel_text}{label}{paint.reset}{paint.sel}"
        number = f"{paint.sel_text}{index:>2}{paint.reset}{paint.sel}"
        dots = f"{paint.sel_dot}{'·' * leader}{paint.reset}{paint.sel}"
        hint = f"{paint.sel_text}{_pad(_affordance(setting), tail - 1)}{paint.reset}{paint.sel}"
    else:
        mark = " " * cursor_width
        label_text = label
        number = f"{paint.muted}{index:>2}{paint.reset}"
        dots = f"{paint.frame}{paint.dim}{'·' * leader}{paint.reset}"
        hint = " " * (tail - 1)

    line = (f" {mark} {number} {label_text} {dots} "
            f"{colour}{shown}{paint.reset}{paint.sel if selected else ''} {hint}")
    if selected and paint.sel:
        return f"{paint.sel}{line}{paint.reset}"
    return line


def _group_heading(paint: Paint, group: str, lang: str) -> str:
    return (f" {paint.accent}{GLYPH_GROUP}{paint.reset} "
            f"{paint.bold}{paint.title}{config.group_label(group, lang)}{paint.reset}")


def _setting_blocks(cfg: Dict[str, Any], paint: Paint, lang: str, cursor: Optional[int],
                    editing: bool, edit_buffer: str, cursor_glyph: str = GLYPH_CURSOR,
                    settings: Sequence[config.Setting] = config.SETTINGS,
                    ) -> List[Tuple[Optional[int], str]]:
    """``(row index or None, line)`` for every group heading and settings row.

    *settings* is whichever rows the current mode shows — ``cursor`` and every
    row number are positions in *that* sequence, not in :data:`config.SETTINGS`.
    """
    blocks: List[Tuple[Optional[int], str]] = []
    current_group = None
    for index, setting in enumerate(settings):
        if setting.group != current_group:
            current_group = setting.group
            blocks.append((None, ""))
            blocks.append((None, _group_heading(paint, setting.group, lang)))
        selected = cursor == index
        blocks.append((index, _row(
            paint, index + 1, setting, cfg.get(setting.key, setting.default), lang,
            selected=selected, editing=selected and editing, edit_buffer=edit_buffer,
            cursor_glyph=cursor_glyph)))
    return blocks


def _window(blocks: Sequence[Tuple[Optional[int], str]], cursor: Optional[int],
            budget: int, paint: Optional[Paint] = None) -> List[str]:
    """The slice of *blocks* that fits *budget* lines and still shows the cursor.

    When the list does not fit, the first and last line of the slice become
    scroll markers, so a short terminal never silently hides rows — the reason
    a marker glyph of its own exists rather than reusing the hint bar's ``↑↓``.
    """
    if budget <= 0 or len(blocks) <= budget:
        return [line for _, line in blocks]
    paint = paint or Paint(False)
    position = next((i for i, (row, _) in enumerate(blocks) if row == cursor), 0)
    start = max(0, min(position - budget // 2, len(blocks) - budget))
    end = start + budget
    lines = [line for _, line in blocks[start:end]]
    if start > 0:
        lines[0] = f" {paint.muted}{GLYPH_MORE_ABOVE} {start} more{paint.reset}"
    if end < len(blocks):
        lines[-1] = f" {paint.muted}{GLYPH_MORE_BELOW} {len(blocks) - end} more{paint.reset}"
    return lines


def render_settings(cfg: Dict[str, Any], *, paint: Optional[Paint] = None,
                    lang: Optional[str] = None,
                    settings: Optional[Sequence[config.Setting]] = None,
                    mode: Optional[str] = None) -> str:
    """The whole settings panel as one string.

    With no *settings*/*mode*, this is ``crw config --list``: every setting,
    Advanced badge, regardless of the stored mode — a full dump is what that
    flag is for. :func:`fallback_menu` passes both explicitly to show the
    current mode's own filtered view instead.
    """
    paint = paint or Paint(colour_enabled())
    lang = lang or i18n.current_language(cfg)
    settings = config.SETTINGS if settings is None else settings
    mode = mode or "advanced"
    lines = _header(paint, lang, mode)
    lines += [line for _, line in _setting_blocks(cfg, paint, lang, None, False, "",
                                                   settings=settings)]
    lines.append("")
    lines.append(f" {paint.muted}{i18n.t('menu.tz_note', lang, tz=config.tz_label(cfg))}{paint.reset}")
    return "\n".join(lines)


def summary_line(cfg: Dict[str, Any], *, paint: Optional[Paint] = None,
                 lang: Optional[str] = None) -> str:
    """One-line digest of the timing settings, for ``doctor`` and after an apply."""
    paint = paint or Paint(colour_enabled())
    lang = lang or i18n.FALLBACK
    daily = (i18n.t("summary.daily", lang, time=cfg.get("daily_time"), tz=config.tz_label(cfg))
             if cfg.get("daily_enabled") else i18n.t("summary.daily_off", lang))
    interval = config.format_interval(int(cfg.get("scan_interval_minutes", 120)), lang)
    monitor = (i18n.t("summary.scan", lang, interval=interval)
               if cfg.get("monitor_enabled") else i18n.t("summary.scan_off", lang))
    return f"{paint.accent}{GLYPH_MARK}{paint.reset} {daily}  {paint.muted}·{paint.reset}  {monitor}"


def _hint_bars(paint: Paint, lang: str, position: str = "") -> List[str]:
    """Two fixed lines — navigation (with the position counter), then actions.

    Always two, never one wrapped line: the frame's height must not change with
    the language, or the in-place repaint would leave orphan rows behind.
    """
    def key(glyph: str, msg_id: str) -> str:
        return f"{paint.accent}{glyph}{paint.reset} {paint.muted}{i18n.t(msg_id, lang)}{paint.reset}"

    def bar(parts: List[str]) -> str:
        return " " + f" {paint.frame}·{paint.reset} ".join(parts)

    # No ⇥ here: the tab bar above the list names that key itself, and one
    # key stated twice reads as two different things.
    nav = bar([key("↑↓", "menu.move"), key("←→", "menu.change"), key("⏎", "menu.edit")])
    if position:
        # Right-aligned on the nav line: which row of how many, so the cursor's
        # place in the list is readable even when the view is scrolled.
        pad = PANEL_WIDTH - width(strip_ansi(nav)) - width(position)
        nav += " " * max(1, pad) + f"{paint.muted}{position}{paint.reset}"
    return [
        nav,
        bar([key("a", "menu.apply"), key("d", "menu.defaults"), key("e", "menu.export"),
             key("i", "menu.import"), key("q", "menu.quit")]),
    ]


def render_menu(cfg: Dict[str, Any], cursor: int, *, paint: Optional[Paint] = None,
                lang: Optional[str] = None, editing: bool = False, edit_buffer: str = "",
                error: Optional[str] = None, notice: Optional[str] = None,
                confirm_defaults: bool = False, prompt: Optional[str] = None,
                prompt_buffer: str = "", height: Optional[int] = None,
                pulse_frame: int = 0,
                settings: Optional[Sequence[config.Setting]] = None) -> List[str]:
    """Every line of one frame of the keyboard menu, already windowed to *height*.

    *pulse_frame* selects the selected row's cursor colour from
    :data:`CURSOR_PULSE_FRAMES` — :func:`run_menu` advances it on every idle
    tick so the cursor breathes in place, at a fixed rate, while a real
    terminal waits for a key. The glyph itself never changes, so no frame can
    move a column.

    *settings* defaults to whatever ``cfg["ui_mode"]`` shows — :func:`run_menu`
    passes it explicitly since it also hands the same sequence to :func:`step`,
    and the two must agree on what row ``cursor`` points at.
    """
    paint = paint or Paint(colour_enabled())
    lang = lang or i18n.current_language(cfg)
    mode = config.current_ui_mode(cfg)
    settings = config.visible_settings(mode) if settings is None else settings
    height = height or shutil.get_terminal_size((80, 40)).lines

    head = _header(paint, lang, rule=False) + _tab_bar(paint, lang, mode)
    setting = settings[cursor]
    foot: List[str] = [f" {paint.frame}{'─' * PANEL_WIDTH}{paint.reset}"]
    if confirm_defaults:
        foot.append(f" {paint.warn}{i18n.t('menu.confirm_defaults', lang)}{paint.reset}")
    elif prompt:
        foot.append(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} "
                    f"{i18n.t(f'menu.{prompt}_prompt', lang)} "
                    f"{paint.ok}{prompt_buffer}{GLYPH_CARET}{paint.reset}  "
                    f"{paint.muted}{i18n.t('menu.cancel_hint', lang)}{paint.reset}")
    else:
        help_line = config.help_text(setting, lang)
        if editing:
            # While typing, the format the field accepts is more useful than
            # the prose describing what the setting means.
            help_line = _format_hint(setting, lang) or help_line
        for line in _wrap(help_line, PANEL_WIDTH - 3):
            foot.append(f"   {paint.muted}{line}{paint.reset}")
        if setting.kind == "secret":
            # Say where the token actually lives — or that it cannot be stored
            # here at all, which is the one case the user must act on.
            # A token reaching the app through TG_BOT_TOKEN never shows in
            # this row — the row is the keychain's value — so an empty field
            # on a working setup used to look like a token that had gone
            # missing, and got retyped. Say where the live one comes from.
            from_env = not cfg.get(setting.key) and os.environ.get("TG_BOT_TOKEN", "").strip()
            if not secrets_store.available():
                note, colour = i18n.t("menu.no_secret_store", lang), paint.warn
            elif from_env:
                note, colour = i18n.t("menu.secret_from_env", lang), paint.warn
            else:
                note = i18n.t("setting.telegram_bot_token.note", lang,
                              backend=secrets_store.backend_label())
                colour = paint.muted
            for line in _wrap(note, PANEL_WIDTH - 3):
                foot.append(f"   {colour}{line}{paint.reset}")
    if error:
        wrapped = _wrap(error, PANEL_WIDTH - 3)
        foot.append(f" {paint.err}✗ {wrapped[0]}{paint.reset}")
        foot.extend(f"   {paint.err}{line}{paint.reset}" for line in wrapped[1:])
    if notice:
        wrapped = _wrap(notice, PANEL_WIDTH - 3)
        foot.append(f" {paint.ok}✓ {wrapped[0]}{paint.reset}")
        foot.extend(f"   {paint.ok}{line}{paint.reset}" for line in wrapped[1:])
    foot.extend(_hint_bars(paint, lang, f"{cursor + 1}/{len(settings)}"))

    cursor_glyph = CURSOR_PULSE_FRAMES[pulse_frame % len(CURSOR_PULSE_FRAMES)]
    blocks = _setting_blocks(cfg, paint, lang, cursor, editing, edit_buffer, cursor_glyph, settings)
    return head + _window(blocks, cursor, height - len(head) - len(foot), paint) + foot


# ── state machine (pure) ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class MenuState:
    """One frame's worth of menu state. Immutable — :func:`step` returns a new one.

    ``pending_save`` / ``pending_action`` are *requests*: :func:`step` never
    touches the disk or the OS scheduler, it only says what the shell should do
    next. That is what keeps the whole interaction testable with no terminal
    and no side effects.
    """

    values: Dict[str, Any] = field(default_factory=dict)
    cursor: int = 0
    editing: bool = False
    edit_buffer: str = ""
    error: Optional[str] = None
    notice: Optional[str] = None
    confirm_defaults: bool = False
    prompt: Optional[str] = None       # "export" | "import"
    prompt_buffer: str = ""
    pending_save: bool = False
    pending_action: Optional[str] = None  # "apply" | "export" | "import"
    #: The values the OS schedule was last built from. Defaults to the values
    #: the menu opened with, and moves forward on a successful apply.
    applied: Optional[Dict[str, Any]] = None
    quitting: bool = False

    def __post_init__(self) -> None:
        if self.applied is None:
            object.__setattr__(self, "applied", dict(self.values))

    @property
    def schedule_dirty(self) -> bool:
        """Derived, never accumulated: toggling a row off and back on again is
        not a change, so it must not trigger a re-apply on quit."""
        return config.schedule_changed(self.applied, self.values)


def _cycle_options(setting: config.Setting) -> Optional[Tuple[Any, ...]]:
    """The values this row cycles through, or ``None`` when it must be typed."""
    if setting.choices is not None:
        return tuple(setting.choices)
    if setting.kind == "bool":
        return (False, True)
    return None


def _touched(state: MenuState, setting: config.Setting, value: Any, **extra: Any) -> MenuState:
    return replace(
        state,
        values={**state.values, setting.key: value},
        pending_save=True,
        error=None,
        notice=None,
        **extra,
    )


def _cycled(state: MenuState, setting: config.Setting, delta: int) -> MenuState:
    options = _cycle_options(setting)
    if options is None:
        return state
    current = state.values.get(setting.key, setting.default)
    position = options.index(current) if current in options else 0
    return _touched(state, setting, options[(position + delta) % len(options)])


def _seed_buffer(state: MenuState, setting: config.Setting) -> str:
    """What a freshly-opened editor starts with.

    Empty for a secret: prefilling would show the token in the clear, and
    committing the mask back would write it over the real one.
    """
    if setting.kind == "secret":
        return ""
    return str(state.values.get(setting.key, setting.default))


INTERVAL_UNITS = "mhd"


def _typed_int(setting: config.Setting, buffer: str, ch: str) -> Optional[str]:
    """Calculator-style digit entry: digits only, live-clamped to the maximum.

    A fresh digit replaces a lone leading zero (``0`` then ``6`` is ``6``, never
    a displayed ``06``), the buffer holds at most as many digits as the field's
    own maximum needs, and a value that would exceed that maximum is clamped on
    the spot — so the editor can never show a number the schema would reject.
    """
    if not ch.isdigit():
        return None
    buffer = "" if buffer == "0" else buffer
    cap = len(str(setting.maximum)) if setting.maximum is not None else None
    if cap is not None and len(buffer) >= cap:
        return None
    candidate = buffer + ch
    if setting.maximum is not None and int(candidate) > setting.maximum:
        return str(setting.maximum)
    return candidate


def _typed_interval(buffer: str, ch: str) -> Optional[str]:
    """Digits, then at most one unit letter — ``45m`` / ``2h`` / ``1d``.

    Once a unit is present the value is complete, so further keys are ignored
    rather than building ``2h5`` for the parser to reject later.
    """
    if buffer and buffer[-1] in INTERVAL_UNITS:
        return None
    if ch.isdigit():
        return buffer + ch if len(buffer) < 4 else None
    if ch.lower() in INTERVAL_UNITS and buffer:
        return buffer + ch.lower()
    return None


def _typed_time(buffer: str, ch: str) -> Optional[str]:
    """``HH:MM`` entry that cannot express an impossible time.

    Digits only; the colon is inserted automatically after the hour; a first
    digit above 2 is read as ``0H`` (typing ``9`` means 09:00, which is what
    someone reaching for a single-digit hour meant); an hour above 23 or a
    minute above 59 is refused at the keystroke.
    """
    if not ch.isdigit():
        return None
    digits = buffer.replace(":", "")
    if len(digits) >= 4:
        return None
    digits += ch
    if len(digits) == 1:
        # 0-2 could still grow into a two-digit hour; 3-9 can only be 0H.
        return f"{digits}" if ch in "012" else f"0{ch}:"
    if len(digits) == 2:
        if int(digits) > 23:
            return None
        return f"{digits}:"
    if len(digits) == 3 and int(ch) > 5:  # minute tens
        return None
    return f"{digits[:2]}:{digits[2:]}"


def _typed(setting: config.Setting, buffer: str, ch: str) -> Optional[str]:
    """The buffer after typing *ch*, or ``None`` when the key is not allowed here.

    One funnel for every per-kind input rule, so "which characters may this
    field contain?" is answered in exactly one place — and the commit-time
    schema check stays as the backstop rather than the first line of defence.
    """
    if setting.kind == "int":
        return _typed_int(setting, buffer, ch)
    if setting.kind == "interval":
        return _typed_interval(buffer, ch)
    if setting.kind == "time":
        return _typed_time(buffer, ch)
    return buffer + ch


def _step_editing(state: MenuState, event: keys.KeyEvent, setting: config.Setting) -> MenuState:
    if event.key is keys.Key.ESCAPE:
        return replace(state, editing=False, edit_buffer="", error=None)
    if event.key is keys.Key.BACKSPACE:
        # Drop an auto-inserted separator along with the digit it followed,
        # so backspace undoes exactly what the last keystroke produced.
        trimmed = state.edit_buffer[:-1]
        if trimmed.endswith(":"):
            trimmed = trimmed[:-1]
        return replace(state, edit_buffer=trimmed, error=None)
    if event.key is keys.Key.CHAR and event.char:
        candidate = _typed(setting, state.edit_buffer, event.char)
        # A stale rejection from an earlier Enter must not sit on screen while
        # the user is actively retyping a fix for it.
        return state if candidate is None else replace(state, edit_buffer=candidate, error=None)
    if event.key is keys.Key.ENTER:
        if setting.kind == "secret" and not state.edit_buffer:
            # An empty commit on a secret means CANCEL: writing "" here would
            # silently destroy the stored token, which is the very thing the
            # empty seed above exists to prevent.
            return replace(state, editing=False, edit_buffer="", error=None)
        try:
            value = config.coerce(setting, state.edit_buffer)
        except ValueError as exc:
            # Stay open with the schema's own message; the rejected value never
            # enters `values`, so it can never reach disk.
            return replace(state, error=str(exc))
        if value == state.values.get(setting.key, setting.default):
            return replace(state, editing=False, edit_buffer="", error=None)
        return _touched(state, setting, value, editing=False, edit_buffer="")
    return state  # arrows and anything undecoded: ignored, buffer intact


def _step_prompt(state: MenuState, event: keys.KeyEvent) -> MenuState:
    if event.key is keys.Key.ESCAPE:
        return replace(state, prompt=None, prompt_buffer="", error=None)
    if event.key is keys.Key.BACKSPACE:
        return replace(state, prompt_buffer=state.prompt_buffer[:-1])
    if event.key is keys.Key.CHAR and event.char:
        return replace(state, prompt_buffer=state.prompt_buffer + event.char)
    if event.key is keys.Key.ENTER:
        if not state.prompt_buffer.strip():
            return replace(state, prompt=None, prompt_buffer="", error=None)
        return replace(state, prompt=None, pending_action=state.prompt, error=None, notice=None)
    return state


def _step_confirm(state: MenuState, event: keys.KeyEvent) -> MenuState:
    """``y`` restores every default; ANY other key cancels.

    Cancel-by-default is deliberate — a restore wipes the stored chat id and
    every tuned value, so a keypress the user did not mean as yes must never be
    read as one. The bot token is the one thing it keeps: see
    :func:`config.restore_defaults`.
    """
    if not (event.key is keys.Key.CHAR and event.char in ("y", "Y")):
        return replace(state, confirm_defaults=False, error=None)
    return replace(
        state,
        values=config.restore_defaults(state.values),
        confirm_defaults=False,
        pending_save=True,
        error=None,
        notice=i18n.t("menu.defaults_done", i18n.current_language(state.values)),
    )


def _toggle_mode(state: MenuState) -> MenuState:
    """Flip Basic/Advanced. :func:`_refit_cursor` keeps the selected row
    selected, so this only has to change the value."""
    new_mode = "advanced" if config.current_ui_mode(state.values) == "basic" else "basic"
    return _touched(state, config.BY_KEY["ui_mode"], new_mode, editing=False, edit_buffer="")


def _step_browsing(state: MenuState, event: keys.KeyEvent,
                   settings: Sequence[config.Setting]) -> MenuState:
    if event.key is keys.Key.TAB:
        return _toggle_mode(state)
    if event.key is keys.Key.CHAR:
        if event.char == "q":
            return replace(state, quitting=True)
        if event.char == "a":
            return replace(state, pending_action="apply", error=None, notice=None)
        if event.char == "d":
            return replace(state, confirm_defaults=True, error=None, notice=None)
        if event.char in ("e", "i"):
            return replace(state, prompt="export" if event.char == "e" else "import",
                           prompt_buffer="", error=None, notice=None)
    if event.key is keys.Key.ESCAPE:
        return replace(state, quitting=True)
    setting = settings[state.cursor]
    if event.key is keys.Key.UP:
        return replace(state, cursor=(state.cursor - 1) % len(settings), error=None, notice=None)
    if event.key is keys.Key.DOWN:
        return replace(state, cursor=(state.cursor + 1) % len(settings), error=None, notice=None)
    if event.key is keys.Key.RIGHT:
        return _cycled(state, setting, 1)
    if event.key is keys.Key.LEFT:
        return _cycled(state, setting, -1)
    if event.key is keys.Key.ENTER:
        if _cycle_options(setting) is not None:
            return _cycled(state, setting, 1)
        return replace(state, editing=True, edit_buffer=_seed_buffer(state, setting),
                       error=None, notice=None)
    return state


def _refit_cursor(before: MenuState, after: MenuState,
                  settings: Sequence[config.Setting]) -> MenuState:
    """Keep the cursor on a row that still exists once the mode changed.

    Basic shows a subset of Advanced, so *any* keypress that flips ``ui_mode``
    — Tab, ←/→/⏎ on the Mode row, or restoring defaults — can leave the cursor
    pointing past the end of the shorter list. Re-find the selected row by key,
    else fall back to the last row.
    """
    if config.current_ui_mode(before.values) == config.current_ui_mode(after.values):
        return after
    rows = config.visible_settings(config.current_ui_mode(after.values))
    key = settings[before.cursor].key if before.cursor < len(settings) else None
    return replace(after, cursor=next((i for i, s in enumerate(rows) if s.key == key),
                                      min(after.cursor, len(rows) - 1)))


def step(state: MenuState, event: keys.KeyEvent,
         settings: Sequence[config.Setting] = config.SETTINGS) -> MenuState:
    """*state* advanced by one keypress. Pure — the only function the tests need.

    An undecoded key returns *state* unchanged rather than raising.
    """
    if event.key is keys.Key.CTRL_C:
        return replace(state, quitting=True, editing=False, confirm_defaults=False, prompt=None)
    if state.confirm_defaults:
        after = _step_confirm(state, event)
    elif state.prompt:
        after = _step_prompt(state, event)
    elif state.editing:
        after = _step_editing(state, event, settings[state.cursor])
    else:
        after = _step_browsing(state, event, settings)
    return _refit_cursor(state, after, settings)


# ── side effects the state machine asks for ──────────────────────────────────

def _apply_schedule(cfg: Dict[str, Any], lang: str) -> Tuple[bool, str]:
    try:
        backend, jobs = scheduler.apply(cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - surface any backend failure, never crash the menu
        return False, i18n.t("menu.apply_failed", lang, error=f"{type(exc).__name__}: {exc}")
    listed = ", ".join(jobs) if jobs else i18n.t("menu.jobs_none", lang)
    return True, i18n.t("menu.applied", lang, backend=backend, jobs=listed)


def export_settings(cfg: Dict[str, Any], target: str, lang: str) -> Tuple[bool, str]:
    """Write the portable settings to *target* (``-`` means stdout).

    Written owner-only through the same 0600 rule as the config file: the
    payload holds no secret, but a settings backup is still nobody else's
    business.
    """
    payload = config.export_payload(cfg)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.strip() == "-":
        sys.stdout.write(text)
        where = i18n.t("menu.export_stdout", lang)
    else:
        path = pathlib.Path(target).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except (OSError, ValueError) as exc:
            # ValueError, not just OSError: a null byte in the path raises it
            # from pathlib rather than from the OS.
            return False, str(exc)
        where = str(path)
    return True, i18n.t("menu.export_done", lang, count=len(payload), target=where)


def import_settings(cfg: Dict[str, Any], source: str, lang: str) -> Tuple[bool, str]:
    """Apply the settings in the JSON file at *source* into *cfg*, all or nothing."""
    path = pathlib.Path(source).expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, str(exc)
    if not isinstance(raw, dict):
        return False, i18n.t("menu.import_not_object", lang, path=str(path))
    try:
        updates, skipped = config.import_updates(raw)
    except ValueError as exc:
        return False, str(exc)
    cfg.update(updates)
    message = i18n.t("menu.import_done", lang, count=len(updates), path=str(path))
    if skipped:
        message += " · " + i18n.t("menu.import_skipped", lang, keys=", ".join(skipped))
    return True, message


def _perform(state: MenuState, cfg: Dict[str, Any], lang: str) -> MenuState:
    """Run the action *state* asked for and fold the outcome back into it."""
    action = state.pending_action
    if action == "apply":
        ok, message = _apply_schedule(cfg, lang)
        state = replace(state, applied=dict(cfg)) if ok else state
    elif action == "export":
        ok, message = export_settings(cfg, state.prompt_buffer, lang)
    elif action == "import":
        ok, message = import_settings(cfg, state.prompt_buffer, lang)
        if ok:
            state = replace(state, values=dict(cfg), pending_save=True)
    else:
        return replace(state, pending_action=None)
    return replace(state, pending_action=None, prompt_buffer="",
                   notice=message if ok else None, error=None if ok else message)


# ── terminal shell (thin: draw, read one key, hand it to `step`) ─────────────

def _draw(lines: List[str], out: IO[str], previous: int) -> int:
    """Repaint *lines* in place: move up over the last frame, clear to the end of
    screen so a frame that lost a line leaves no orphan text behind."""
    prefix = f"\033[{previous}A" if previous else ""
    out.write(prefix + "".join(f"{line}\033[K\n" for line in lines) + "\033[J")
    out.flush()
    return len(lines)


def run_menu(cfg: Optional[Dict[str, Any]] = None, *,
             read: Callable[[], keys.KeyEvent] = keys.read_key,
             out: Optional[IO[str]] = None, cursor: int = 0,
             height: Optional[int] = None) -> int:
    """The keyboard menu. Saves as you go; re-applies the schedule on exit if needed.

    *read* is the injection seam: production passes :func:`keys.read_key`, a
    test passes an iterator of :class:`keys.KeyEvent` and never touches a
    terminal.
    """
    out = out or sys.stdout
    cfg = config.load() if cfg is None else cfg
    paint = Paint(colour_enabled(out))
    state = MenuState(values=cfg, cursor=cursor)
    painted = 0
    pulse_frame = 0
    # Pulse animation peeks the real keyboard. An injected `read` (tests) is
    # the whole input source: waiting on msvcrt.kbhit()/select() would ignore
    # it and hang on a Windows console that nobody is typing into.
    pulse = read is keys.read_key
    with keys.raw_mode():
        while True:
            lang = i18n.current_language(state.values)
            settings = config.visible_settings(config.current_ui_mode(state.values))
            painted = _draw(render_menu(
                state.values, state.cursor, paint=paint, lang=lang, editing=state.editing,
                edit_buffer=state.edit_buffer, error=state.error, notice=state.notice,
                confirm_defaults=state.confirm_defaults, prompt=state.prompt,
                prompt_buffer=state.prompt_buffer, height=height,
                pulse_frame=pulse_frame, settings=settings), out, painted)
            try:
                if pulse and not keys.key_ready(CURSOR_PULSE_INTERVAL):
                    # Idle: advance the cursor's pulse frame and redraw,
                    # without ever reaching `step()` — an animation tick must
                    # never be mistaken for a real keypress (e.g.
                    # `_step_confirm` treats any non-"y" key as "cancel").
                    pulse_frame += 1
                    continue
                event = read()
            except (KeyboardInterrupt, StopIteration):
                # cbreak leaves ISIG on, so a real Ctrl-C arrives as a signal
                # rather than as a byte — same exit as pressing q. It can land
                # while idling in key_ready()'s select() just as easily as in
                # read(), so both must be inside this one try. A drained fake
                # source (tests) ends the loop the same way.
                break
            state = step(state, event, settings)
            if state.pending_save:
                config.save(state.values)
                state = replace(state, pending_save=False)
            if state.pending_action:
                state = _perform(state, state.values, i18n.current_language(state.values))
                if state.pending_save:
                    config.save(state.values)
                    state = replace(state, pending_save=False)
            if state.quitting:
                break
    lang = i18n.current_language(state.values)
    if state.schedule_dirty:
        print(f"\n {Paint(colour_enabled(out)).warn}"
              f"{i18n.t('menu.schedule_dirty', lang)}{paint.reset}", file=out)
        ok, message = _apply_schedule(state.values, lang)
        print(f" {paint.ok if ok else paint.err}{'✓' if ok else '✗'} {message}{paint.reset}", file=out)
        if not ok:
            print(f"   {paint.muted}{i18n.t('menu.apply_retry', lang)}{paint.reset}", file=out)
        print(" " + summary_line(state.values, paint=paint, lang=lang), file=out)
    print(f" {paint.muted}{i18n.t('menu.config_file', lang, path=config.config_path())}"
          f"{paint.reset}", file=out)
    return 0


# ── numbered fallback (piped stdin/stdout, CI, tests) ────────────────────────

def _read_line(stdin: IO[str]) -> Optional[str]:
    line = stdin.readline()
    return None if line == "" else line.rstrip("\n")


def _edit(setting: config.Setting, cfg: Dict[str, Any], paint: Paint,
          stdin: IO[str], out: IO[str], lang: Optional[str] = None) -> bool:
    """Prompt for one setting in the numbered menu. True when the value changed."""
    lang = lang or i18n.current_language(cfg)
    before = cfg.get(setting.key, setting.default)
    if setting.kind == "bool":  # a toggle needs no prompt
        cfg[setting.key] = not bool(before)
        return True
    if setting.choices:  # a choice steps to the next allowed value
        options = tuple(setting.choices)
        position = options.index(before) if before in options else -1
        cfg[setting.key] = options[(position + 1) % len(options)]
        return cfg[setting.key] != before
    print(f"\n {paint.accent}{GLYPH_GROUP}{paint.reset} "
          f"{paint.bold}{config.label(setting, lang)}{paint.reset}", file=out)
    print(f"   {paint.muted}{config.help_text(setting, lang)}{paint.reset}", file=out)
    hint = i18n.t("menu.secret_hint" if setting.kind == "secret" else "menu.keep_hint", lang)
    print(f"   {paint.muted}{i18n.t('menu.current', lang)}{paint.reset} "
          f"{config.render(setting, before, lang)}   {paint.muted}{hint}{paint.reset}", file=out)
    print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} ", end="", file=out, flush=True)
    raw = _read_line(stdin)
    if raw is None or not raw.strip():
        return False
    try:
        config.set_value(cfg, setting.key, raw)
    except ValueError as exc:
        print(f" {paint.err}✗ {exc}{paint.reset}", file=out)
        return False
    return cfg[setting.key] != before


def fallback_menu(stdin: IO[str], out: IO[str]) -> int:
    """The numbered menu: same schema and validation, no keyboard required."""
    paint = Paint(colour_enabled(out))
    cfg = config.load()
    applied = dict(cfg)   # what the OS schedule currently reflects
    while True:
        lang = i18n.current_language(cfg)
        mode = config.current_ui_mode(cfg)
        settings = config.visible_settings(mode)
        print("\n" + render_settings(cfg, paint=paint, lang=lang, settings=settings, mode=mode),
              file=out)
        print(f"\n {paint.muted}{i18n.t('menu.number_hint', lang)}{paint.reset}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}a{paint.reset} {i18n.t('menu.apply', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}d{paint.reset} {i18n.t('menu.defaults', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}e{paint.reset} {i18n.t('menu.export', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}i{paint.reset} {i18n.t('menu.import', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}m{paint.reset} {i18n.t('menu.switch_mode', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}q{paint.reset} {i18n.t('menu.quit', lang)}", file=out)
        print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} ", end="", file=out, flush=True)
        choice = _read_line(stdin)
        if choice is None or choice.strip().lower() in ("q", "quit", "exit"):
            break
        choice = choice.strip()
        if not choice:
            continue
        lowered = choice.lower()
        if lowered == "m":
            new_mode = "advanced" if mode == "basic" else "basic"
            config.set_value(cfg, "ui_mode", new_mode)
            config.save(cfg)
            print(f" {paint.ok}✓ {i18n.t(f'menu.badge_{new_mode}', lang)}{paint.reset}", file=out)
            continue
        if lowered == "a":
            config.save(cfg)
            ok, message = _apply_schedule(cfg, lang)
            print(f" {paint.ok if ok else paint.err}{'✓' if ok else '✗'} {message}{paint.reset}", file=out)
            print(" " + summary_line(cfg, paint=paint, lang=lang), file=out)
            if ok:
                applied = dict(cfg)
            continue
        if lowered == "d":
            cfg = config.restore_defaults(cfg)
            config.save(cfg)
            print(f" {paint.ok}✓ {i18n.t('menu.defaults_done', lang)}{paint.reset}", file=out)
            continue
        if lowered in ("e", "i"):
            action = "export" if lowered == "e" else "import"
            print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} "
                  f"{i18n.t(f'menu.{action}_prompt', lang)} ", end="", file=out, flush=True)
            target = _read_line(stdin)
            if target is None or not target.strip():
                continue
            run = export_settings if action == "export" else import_settings
            ok, message = run(cfg, target.strip(), lang)
            print(f" {paint.ok if ok else paint.err}{'✓' if ok else '✗'} {message}{paint.reset}", file=out)
            if ok and action == "import":
                config.save(cfg)
            if ok and action == "export":
                print(f" {paint.warn}⚠ {i18n.t('menu.export_secrets', lang, keys=', '.join(sorted(config.SECRET_KEYS)))}{paint.reset}", file=out)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(settings):
            print(f" {paint.err}✗ {i18n.t('menu.invalid_choice', lang, count=len(settings))}"
                  f"{paint.reset}", file=out)
            continue
        setting = settings[int(choice) - 1]
        if _edit(setting, cfg, paint, stdin, out, lang):
            config.save(cfg)
            print(f" {paint.ok}✓ {i18n.t('menu.saved', i18n.current_language(cfg), label=config.label(setting, i18n.current_language(cfg)), value=config.render(setting, cfg[setting.key], i18n.current_language(cfg)))}{paint.reset}", file=out)
    lang = i18n.current_language(cfg)
    if config.schedule_changed(applied, cfg):
        print(f"\n {paint.warn}{i18n.t('menu.schedule_dirty', lang)}{paint.reset}", file=out)
        ok, message = _apply_schedule(cfg, lang)
        print(f" {paint.ok if ok else paint.err}{'✓' if ok else '✗'} {message}{paint.reset}", file=out)
        if not ok:
            print(f"   {paint.muted}{i18n.t('menu.apply_retry', lang)}{paint.reset}", file=out)
        else:
            print(" " + summary_line(cfg, paint=paint, lang=lang), file=out)
    print(f" {paint.muted}{i18n.t('menu.config_file', lang, path=config.config_path())}{paint.reset}",
          file=out)
    return 0


def config_menu(stdin: Optional[IO[str]] = None, out: Optional[IO[str]] = None) -> int:
    """``crw config`` with no flags: the keyboard menu, or the numbered fallback.

    A real terminal gets :func:`run_menu`; anything else — piped stdin or
    stdout, CI, a test handing in a StringIO — gets :func:`fallback_menu`,
    which needs nothing but readline.
    """
    if stdin is None and out is None and keys.is_interactive_tty():
        return run_menu()
    return fallback_menu(stdin or sys.stdin, out or sys.stdout)


# ── update prompt (item selection/cursor language reused from the settings
# menu; update_check.py is the only caller, via the answers below) ──────────

#: What update_prompt() returns; update_check.py acts on the answer.
UPDATE_NOW = "update-now"
SKIP = "skip"
SKIP_VERSION = "skip-version"


def _update_lines(paint: Paint, lang: str, current: str, latest: str, selected: int) -> List[str]:
    """Same visual language as :func:`_row`'s selected state — the accent
    cursor glyph, the bold near-white label, the saturated row band — without
    the dotted leader or value column a plain choice doesn't need."""
    version = latest.lstrip("vV")
    header = [
        f" {paint.accent}{GLYPH_MARK}{paint.reset} {paint.bold}{paint.title}"
        f"{i18n.t('update.title', lang)}{paint.reset}",
        f"   {paint.muted}{i18n.t('update.available', lang, latest=version, current=current)}"
        f"{paint.reset}",
        f" {paint.frame}{'─' * PANEL_WIDTH}{paint.reset}",
    ]
    choices = (
        ("update.now", "update.now_detail", {}),
        ("update.skip", "update.skip_detail", {}),
        ("update.skip_version", "update.skip_version_detail", {"version": version}),
    )
    body = []
    for i, (label_id, detail_id, extra) in enumerate(choices, start=1):
        label = i18n.t(label_id, lang)
        detail = i18n.t(detail_id, lang, **extra)
        if selected == i:
            row = (f" {paint.accent}{GLYPH_CURSOR}{paint.reset}{paint.sel} "
                   f"{paint.bold}{paint.sel_text}{i}) {label}{paint.reset}{paint.sel}"
                   f"  {paint.sel_dot}{detail}{paint.reset}")
            row = f"{paint.sel}{row}{paint.reset}" if paint.sel else row
        else:
            mark = " " * width(strip_ansi(GLYPH_CURSOR))
            row = f" {mark} {i}) {label}  {paint.muted}{detail}{paint.reset}"
        body.append(row)
    footer = [
        f" {paint.frame}{'─' * PANEL_WIDTH}{paint.reset}",
        " " + f" {paint.frame}·{paint.reset} ".join([
            f"{paint.accent}↑↓{paint.reset} {paint.muted}{i18n.t('menu.move', lang)}{paint.reset}",
            f"{paint.accent}⏎{paint.reset} {paint.muted}{i18n.t('update.confirm', lang)}{paint.reset}",
            f"{paint.accent}q{paint.reset} {paint.muted}{i18n.t('update.skip_key', lang)}{paint.reset}",
        ]),
    ]
    return header + body + footer


def update_prompt(current: str, latest: str, *,
                  read: Callable[[], keys.KeyEvent] = keys.read_key,
                  out: Optional[IO[str]] = None) -> str:
    """Ask what to do about a newer release; returns UPDATE_NOW / SKIP /
    SKIP_VERSION. An exhausted key source (tests), Ctrl-C, ``q`` or Esc are
    all SKIP — backing out of a question changes nothing.

    *read* is the same injection seam as :func:`run_menu`: production reads
    the real keyboard, a test passes an iterator and never touches a
    terminal. No idle redraw here (unlike the settings menu's breathing
    cursor) — three choices need no animation to stay readable.
    """
    out = out or sys.stdout
    paint = Paint(colour_enabled(out))
    lang = i18n.current_language()
    selected = 1
    painted = 0
    with keys.raw_mode():
        while True:
            painted = _draw(_update_lines(paint, lang, current, latest, selected), out, painted)
            try:
                event = read()
            except (KeyboardInterrupt, StopIteration):
                return SKIP
            if event.key in (keys.Key.CTRL_C, keys.Key.ESCAPE):
                return SKIP
            if event.key == keys.Key.UP:
                selected = selected - 1 if selected > 1 else 3
            elif event.key == keys.Key.DOWN:
                selected = selected + 1 if selected < 3 else 1
            elif event.key == keys.Key.ENTER:
                return (UPDATE_NOW, SKIP, SKIP_VERSION)[selected - 1]
            elif event.key == keys.Key.CHAR and (event.char or "").lower() == "q":
                return SKIP


def demo() -> None:
    import io

    plain = Paint(False)
    cfg = dict(config.DEFAULTS)

    text = render_settings(cfg, paint=plain, lang="en")
    assert "Daily time" in text and "10:00" in text and "2 hours" in text
    assert text.count("\033") == 0
    assert "每日時間" in render_settings(cfg, paint=plain, lang="zh-TW")

    frame = render_menu(cfg, 0, paint=plain, lang="en", height=30)
    assert len(frame) <= 30
    assert any(GLYPH_CURSOR in line for line in frame)
    assert not any(ch in "".join(frame) for ch in "╭╮╰╯")

    state = MenuState(values=dict(config.DEFAULTS))
    state = step(state, keys.KeyEvent(keys.Key.RIGHT))
    assert state.values["daily_enabled"] is False and state.schedule_dirty
    state = step(state, keys.KeyEvent(keys.Key.DOWN))
    state = step(state, keys.KeyEvent(keys.Key.ENTER))
    assert state.editing and state.edit_buffer == "10:00"
    state = step(state, keys.KeyEvent(keys.Key.ESCAPE))
    assert not state.editing
    assert step(state, keys.KeyEvent(keys.Key.CHAR, "q")).quitting

    out = io.StringIO()
    assert _edit(config.BY_KEY["scan_interval_minutes"], cfg, plain, io.StringIO("45m\n"), out) is True
    assert cfg["scan_interval_minutes"] == 45
    assert _edit(config.BY_KEY["scan_interval_minutes"], cfg, plain, io.StringIO("nope\n"), out) is False

    assert width("每日") == 4 and width("ab") == 2
    print("ui.demo: ok")


if __name__ == "__main__":
    demo()
