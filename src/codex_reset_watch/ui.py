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
SEL = "\033[48;2;12;46;38m"        # selected row backing, same hue at 12% value
FRAME = "\033[38;5;239m"
MUTED = "\033[38;5;245m"
WARN = "\033[38;5;179m"
ERR = "\033[38;5;203m"

GLYPH_MARK = "◆"
GLYPH_GROUP = "▍"
GLYPH_CURSOR = "▸"
GLYPH_PROMPT = "❱"
GLYPH_CARET = "▏"
PANEL_WIDTH = 72

_PAINT_NAMES = ("RESET", "BOLD", "DIM", "ACCENT", "TITLE", "OK", "SEL",
                "FRAME", "MUTED", "WARN", "ERR")


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


def colour_enabled(stream: Optional[IO[str]] = None) -> bool:
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 - a stream with no isatty() is not a terminal
        return False


class Paint:
    """Colour codes, or empty strings when colour is off. One object, no globals."""

    def __init__(self, enabled: bool):
        for name in _PAINT_NAMES:
            setattr(self, name.lower(), globals()[name] if enabled else "")


def width(text: str) -> int:
    """Display columns, counting CJK/emoji as two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)



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


def _header(paint: Paint, lang: str) -> List[str]:
    title = i18n.t("menu.title", lang)
    version = f"v{package_version()}"
    gap = PANEL_WIDTH - 3 - width(title) - width(version)
    return [
        f" {paint.accent}{GLYPH_MARK}{paint.reset} {paint.bold}{paint.title}{title}{paint.reset}"
        f"{' ' * max(1, gap)}{paint.muted}{version}{paint.reset}",
        f"   {paint.muted}{_clip(_tilde(config.config_path()), PANEL_WIDTH - 3)}{paint.reset}",
        f" {paint.frame}{'─' * PANEL_WIDTH}{paint.reset}",
    ]


def _leader(label: str, shown: str, *, indent: int) -> int:
    return max(2, PANEL_WIDTH - indent - width(label) - width(shown) - 2)


def _value_colour(paint: Paint, setting: config.Setting, value: Any) -> str:
    if setting.kind == "bool":
        return paint.ok if value else paint.muted
    if setting.kind in ("secret", "text_optional", "path") and not value:
        return paint.muted
    return paint.title


def _row(paint: Paint, index: int, setting: config.Setting, value: Any, lang: str,
         *, selected: bool = False, editing: bool = False, edit_buffer: str = "") -> str:
    """One settings line: ``▸  4 Scan interval ······· 2 hours``."""
    label = config.label(setting, lang)
    if editing:
        shown = f"{edit_buffer}{GLYPH_CARET}"
        colour = paint.ok
    else:
        shown = config.render(setting, value, lang)
        colour = _value_colour(paint, setting, value)
    # A long value (a user agent, a query string) is clipped rather than
    # allowed to wrap: a wrapped row would break the in-place repaint.
    # 10 = " ▸ NN " (6) + the space, 2-cell minimum leader and space after it.
    shown = _clip(shown, max(8, PANEL_WIDTH - 10 - width(label)))
    mark = f"{paint.accent}{GLYPH_CURSOR}{paint.reset}" if selected else " "
    number = f"{paint.muted}{index:>2}{paint.reset}"
    body = f"{label} {paint.frame}{paint.dim}{'·' * _leader(label, shown, indent=6)}{paint.reset} "
    line = f" {mark} {number} {body}{colour}{shown}{paint.reset}"
    if selected and paint.sel:
        # Back the whole row, not just the text, so the cursor reads as a band.
        plain_len = 6 + width(label) + 1 + _leader(label, shown, indent=6) + 1 + width(shown)
        line = f"{paint.sel}{line}{' ' * max(0, PANEL_WIDTH - plain_len)}{paint.reset}"
    return line


def _group_heading(paint: Paint, group: str, lang: str) -> str:
    return (f" {paint.accent}{GLYPH_GROUP}{paint.reset} "
            f"{paint.bold}{paint.title}{config.group_label(group, lang)}{paint.reset}")


def _setting_blocks(cfg: Dict[str, Any], paint: Paint, lang: str, cursor: Optional[int],
                    editing: bool, edit_buffer: str) -> List[Tuple[Optional[int], str]]:
    """``(row index or None, line)`` for every group heading and settings row."""
    blocks: List[Tuple[Optional[int], str]] = []
    current_group = None
    for index, setting in enumerate(config.SETTINGS):
        if setting.group != current_group:
            current_group = setting.group
            blocks.append((None, ""))
            blocks.append((None, _group_heading(paint, setting.group, lang)))
        selected = cursor == index
        blocks.append((index, _row(
            paint, index + 1, setting, cfg.get(setting.key, setting.default), lang,
            selected=selected, editing=selected and editing, edit_buffer=edit_buffer)))
    return blocks


def _window(blocks: Sequence[Tuple[Optional[int], str]], cursor: Optional[int],
            budget: int) -> List[str]:
    """The slice of *blocks* that fits *budget* lines and still shows the cursor."""
    if budget <= 0 or len(blocks) <= budget:
        return [line for _, line in blocks]
    position = next((i for i, (row, _) in enumerate(blocks) if row == cursor), 0)
    start = max(0, min(position - budget // 2, len(blocks) - budget))
    return [line for _, line in blocks[start:start + budget]]


def render_settings(cfg: Dict[str, Any], *, paint: Optional[Paint] = None,
                    lang: Optional[str] = None) -> str:
    """The whole settings panel as one string — what ``crw config --list`` prints."""
    paint = paint or Paint(colour_enabled())
    lang = lang or i18n.current_language(cfg)
    lines = _header(paint, lang)
    lines += [line for _, line in _setting_blocks(cfg, paint, lang, None, False, "")]
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


def _hint_bars(paint: Paint, lang: str) -> List[str]:
    """Two fixed lines — navigation, then actions.

    Always two, never one wrapped line: the frame's height must not change with
    the language, or the in-place repaint would leave orphan rows behind.
    """
    def key(glyph: str, msg_id: str) -> str:
        return f"{paint.accent}{glyph}{paint.reset} {paint.muted}{i18n.t(msg_id, lang)}{paint.reset}"

    def bar(parts: List[str]) -> str:
        return " " + f" {paint.frame}·{paint.reset} ".join(parts)

    return [
        bar([key("↑↓", "menu.move"), key("←→", "menu.change"), key("⏎", "menu.edit")]),
        bar([key("a", "menu.apply"), key("d", "menu.defaults"), key("e", "menu.export"),
             key("i", "menu.import"), key("q", "menu.quit")]),
    ]


def render_menu(cfg: Dict[str, Any], cursor: int, *, paint: Optional[Paint] = None,
                lang: Optional[str] = None, editing: bool = False, edit_buffer: str = "",
                error: Optional[str] = None, notice: Optional[str] = None,
                confirm_defaults: bool = False, prompt: Optional[str] = None,
                prompt_buffer: str = "", height: Optional[int] = None) -> List[str]:
    """Every line of one frame of the keyboard menu, already windowed to *height*."""
    paint = paint or Paint(colour_enabled())
    lang = lang or i18n.current_language(cfg)
    height = height or shutil.get_terminal_size((80, 40)).lines

    head = _header(paint, lang)
    setting = config.SETTINGS[cursor]
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
        if editing and setting.kind == "secret":
            help_line = i18n.t("menu.secret_hint", lang)
        foot.append(f"   {paint.muted}{_clip(help_line, PANEL_WIDTH - 3)}{paint.reset}")
        if setting.kind == "secret":
            # Say where the token actually lives — or that it cannot be stored
            # here at all, which is the one case the user must act on.
            note = (i18n.t("setting.telegram_bot_token.note", lang,
                           backend=secrets_store.backend_label())
                    if secrets_store.available() else i18n.t("menu.no_secret_store", lang))
            colour = paint.muted if secrets_store.available() else paint.warn
            foot.append(f"   {colour}{_clip(note, PANEL_WIDTH - 3)}{paint.reset}")
    if error:
        foot.append(f" {paint.err}✗ {_clip(error, PANEL_WIDTH - 3)}{paint.reset}")
    if notice:
        foot.append(f" {paint.ok}✓ {_clip(notice, PANEL_WIDTH - 3)}{paint.reset}")
    foot.extend(_hint_bars(paint, lang))

    blocks = _setting_blocks(cfg, paint, lang, cursor, editing, edit_buffer)
    return head + _window(blocks, cursor, height - len(head) - len(foot)) + foot


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
    schedule_dirty: bool = False
    quitting: bool = False


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
        schedule_dirty=state.schedule_dirty or setting.key in config.SCHEDULE_KEYS,
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


def _step_editing(state: MenuState, event: keys.KeyEvent, setting: config.Setting) -> MenuState:
    if event.key is keys.Key.ESCAPE:
        return replace(state, editing=False, edit_buffer="", error=None)
    if event.key is keys.Key.BACKSPACE:
        return replace(state, edit_buffer=state.edit_buffer[:-1])
    if event.key is keys.Key.CHAR and event.char:
        return replace(state, edit_buffer=state.edit_buffer + event.char)
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
    read as one.
    """
    if not (event.key is keys.Key.CHAR and event.char in ("y", "Y")):
        return replace(state, confirm_defaults=False, error=None)
    return replace(
        state,
        values={**state.values, **config.DEFAULTS},
        confirm_defaults=False,
        pending_save=True,
        schedule_dirty=True,
        error=None,
        notice=i18n.t("menu.defaults_done", i18n.current_language(state.values)),
    )


def _step_browsing(state: MenuState, event: keys.KeyEvent,
                   settings: Sequence[config.Setting]) -> MenuState:
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


def step(state: MenuState, event: keys.KeyEvent,
         settings: Sequence[config.Setting] = config.SETTINGS) -> MenuState:
    """*state* advanced by one keypress. Pure — the only function the tests need.

    An undecoded key returns *state* unchanged rather than raising.
    """
    if event.key is keys.Key.CTRL_C:
        return replace(state, quitting=True, editing=False, confirm_defaults=False, prompt=None)
    if state.confirm_defaults:
        return _step_confirm(state, event)
    if state.prompt:
        return _step_prompt(state, event)
    if state.editing:
        return _step_editing(state, event, settings[state.cursor])
    return _step_browsing(state, event, settings)


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
        state = replace(state, schedule_dirty=False) if ok else state
    elif action == "export":
        ok, message = export_settings(cfg, state.prompt_buffer, lang)
    elif action == "import":
        ok, message = import_settings(cfg, state.prompt_buffer, lang)
        if ok:
            state = replace(state, values=dict(cfg), pending_save=True, schedule_dirty=True)
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
    with keys.raw_mode():
        while True:
            lang = i18n.current_language(state.values)
            painted = _draw(render_menu(
                state.values, state.cursor, paint=paint, lang=lang, editing=state.editing,
                edit_buffer=state.edit_buffer, error=state.error, notice=state.notice,
                confirm_defaults=state.confirm_defaults, prompt=state.prompt,
                prompt_buffer=state.prompt_buffer, height=height), out, painted)
            try:
                event = read()
            except (KeyboardInterrupt, StopIteration):
                # cbreak leaves ISIG on, so a real Ctrl-C arrives as a signal
                # rather than as a byte — same exit as pressing q. A drained
                # fake source (tests) ends the loop the same way.
                break
            state = step(state, event)
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
    schedule_dirty = False
    while True:
        lang = i18n.current_language(cfg)
        print("\n" + render_settings(cfg, paint=paint, lang=lang), file=out)
        print(f"\n {paint.muted}{i18n.t('menu.number_hint', lang)}{paint.reset}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}a{paint.reset} {i18n.t('menu.apply', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}d{paint.reset} {i18n.t('menu.defaults', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}e{paint.reset} {i18n.t('menu.export', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}i{paint.reset} {i18n.t('menu.import', lang)}  "
              f"{paint.muted}·{paint.reset}  {paint.accent}q{paint.reset} {i18n.t('menu.quit', lang)}", file=out)
        print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} ", end="", file=out, flush=True)
        choice = _read_line(stdin)
        if choice is None or choice.strip().lower() in ("q", "quit", "exit"):
            break
        choice = choice.strip()
        if not choice:
            continue
        lowered = choice.lower()
        if lowered == "a":
            config.save(cfg)
            ok, message = _apply_schedule(cfg, lang)
            print(f" {paint.ok if ok else paint.err}{'✓' if ok else '✗'} {message}{paint.reset}", file=out)
            print(" " + summary_line(cfg, paint=paint, lang=lang), file=out)
            schedule_dirty = schedule_dirty and not ok
            continue
        if lowered == "d":
            cfg = dict(config.DEFAULTS)
            config.save(cfg)
            schedule_dirty = True
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
                schedule_dirty = True
            if ok and action == "export":
                print(f" {paint.warn}⚠ {i18n.t('menu.export_secrets', lang, keys=', '.join(sorted(config.SECRET_KEYS)))}{paint.reset}", file=out)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(config.SETTINGS):
            print(f" {paint.err}✗ {i18n.t('menu.invalid_choice', lang, count=len(config.SETTINGS))}"
                  f"{paint.reset}", file=out)
            continue
        setting = config.SETTINGS[int(choice) - 1]
        if _edit(setting, cfg, paint, stdin, out, lang):
            config.save(cfg)
            schedule_dirty = schedule_dirty or setting.key in config.SCHEDULE_KEYS
            print(f" {paint.ok}✓ {i18n.t('menu.saved', i18n.current_language(cfg), label=config.label(setting, i18n.current_language(cfg)), value=config.render(setting, cfg[setting.key], i18n.current_language(cfg)))}{paint.reset}", file=out)
    lang = i18n.current_language(cfg)
    if schedule_dirty:
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
