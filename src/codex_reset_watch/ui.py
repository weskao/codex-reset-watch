"""Terminal presentation for ``crw config``.

Own look, deliberately not the neon box style used elsewhere on this machine:
a muted twilight palette (steel / periwinkle / orchid / sage / sand), rounded
corners, a left accent gutter per group and a dotted leader between label and
value. Colour is dropped entirely when stdout is not a TTY or ``NO_COLOR`` is
set, so ``crw config --list | cat`` stays readable and the tests can assert on
plain text.
"""
from __future__ import annotations

import os
import sys
import unicodedata
from typing import Any, Dict, IO, List, Optional, Tuple

from . import config, scheduler

# ── palette ──────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
FRAME = "\033[38;5;103m"    # dusty steel
TITLE = "\033[38;5;153m"    # pale periwinkle
ACCENT = "\033[38;5;177m"   # orchid
OK = "\033[38;5;114m"       # sage
WARN = "\033[38;5;179m"     # sand
ERR = "\033[38;5;174m"      # dusty rose
MUTED = "\033[38;5;102m"

GLYPH_MARK = "◈"
GLYPH_GROUP = "▎"
GLYPH_PROMPT = "❱"
PANEL_WIDTH = 66


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
        for name in ("RESET", "BOLD", "DIM", "FRAME", "TITLE", "ACCENT", "OK", "WARN", "ERR", "MUTED"):
            setattr(self, name.lower(), globals()[name] if enabled else "")


def width(text: str) -> int:
    """Display columns, counting CJK/emoji as two. Labels here are Chinese."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, columns: int) -> str:
    return text + " " * max(0, columns - width(text))


def header(paint: Paint, title: str, subtitle: str) -> List[str]:
    inner = PANEL_WIDTH - 2
    return [
        f"{paint.frame}╭{'─' * inner}╮{paint.reset}",
        f"{paint.frame}│{paint.reset} {paint.accent}{GLYPH_MARK}{paint.reset} "
        f"{paint.bold}{paint.title}{title}{paint.reset}"
        f"{_pad('', inner - 4 - width(title))}{paint.frame}│{paint.reset}",
        f"{paint.frame}│{paint.reset} {paint.muted}{_pad(subtitle, inner - 2)}{paint.reset}"
        f" {paint.frame}│{paint.reset}",
        f"{paint.frame}╰{'─' * inner}╯{paint.reset}",
    ]


def _row_line(paint: Paint, index: int, setting: config.Setting, value: Any) -> str:
    label = setting.label
    shown = config.render(setting, value)
    accent = paint.ok if (setting.kind == "bool" and value) else (
        paint.muted if setting.kind == "bool" else paint.title)
    leader_cols = PANEL_WIDTH - 8 - width(label) - width(shown)
    leader = f"{paint.frame}{paint.dim}{'·' * max(2, leader_cols)}{paint.reset}"
    return (f" {paint.accent}{index:>2}{paint.reset}  {label} {leader} "
            f"{accent}{shown}{paint.reset}")


def render_settings(cfg: Dict[str, Any], *, paint: Optional[Paint] = None) -> str:
    """The whole settings panel as one string."""
    paint = paint or Paint(colour_enabled())
    tz = config.tz_label(cfg)
    lines = header(paint, "Codex Reset Watch · 設定", f"{config.config_path()}")
    index = 0
    current_group = None
    for setting in config.SETTINGS:
        index += 1
        if setting.group != current_group:
            current_group = setting.group
            lines.append("")
            lines.append(f" {paint.accent}{GLYPH_GROUP}{paint.reset} "
                         f"{paint.bold}{paint.title}{setting.group}{paint.reset}")
        lines.append(_row_line(paint, index, setting, cfg.get(setting.key, setting.default)))
    lines.append("")
    lines.append(f" {paint.muted}時間以 {tz} 顯示；排程由作業系統以本機時間觸發。{paint.reset}")
    return "\n".join(lines)


def summary_line(cfg: Dict[str, Any], *, paint: Optional[Paint] = None) -> str:
    """One-line digest of the timing settings, for ``doctor`` and after an apply."""
    paint = paint or Paint(colour_enabled())
    daily = (f"每日 {cfg.get('daily_time')} ({config.tz_label(cfg)})"
             if cfg.get("daily_enabled") else "每日 關閉")
    monitor = (f"掃描 每 {config.format_interval(int(cfg.get('scan_interval_minutes', 120)))}"
               if cfg.get("monitor_enabled") else "掃描 關閉")
    return f"{paint.accent}{GLYPH_MARK}{paint.reset} {daily}  {paint.muted}·{paint.reset}  {monitor}"


# ── interaction ──────────────────────────────────────────────────────────────

def _read(stdin: IO[str]) -> Optional[str]:
    line = stdin.readline()
    return None if line == "" else line.rstrip("\n")


def _edit(setting: config.Setting, cfg: Dict[str, Any], paint: Paint,
          stdin: IO[str], out: IO[str]) -> bool:
    """Prompt for one setting. Returns True when the stored value changed."""
    before = cfg.get(setting.key, setting.default)
    if setting.kind == "bool":  # a toggle needs no prompt
        cfg[setting.key] = not bool(before)
        return True
    print(f"\n {paint.accent}{GLYPH_GROUP}{paint.reset} {paint.bold}{setting.label}{paint.reset}", file=out)
    print(f"   {paint.muted}{setting.help}{paint.reset}", file=out)
    print(f"   {paint.muted}目前：{paint.reset}{config.render(setting, before)}"
          f"   {paint.muted}(Enter 保持不變){paint.reset}", file=out)
    print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} ", end="", file=out, flush=True)
    raw = _read(stdin)
    if raw is None or not raw.strip():
        return False
    try:
        config.set_value(cfg, setting.key, raw)
    except ValueError as exc:
        print(f" {paint.err}✗ {exc}{paint.reset}", file=out)
        return False
    return cfg[setting.key] != before


def _apply_schedule(cfg: Dict[str, Any], paint: Paint, out: IO[str]) -> None:
    try:
        backend, jobs = scheduler.apply(cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - surface any backend failure, never crash the menu
        print(f" {paint.err}✗ 排程套用失敗（{type(exc).__name__}: {exc}）{paint.reset}", file=out)
        print(f"   {paint.muted}可改用安裝器重試：uv run python scripts/install.py{paint.reset}", file=out)
        return
    listed = "、".join(jobs) if jobs else "（全部關閉）"
    print(f" {paint.ok}✓ 已重新套用 {backend} 排程：{listed}{paint.reset}", file=out)
    print(" " + summary_line(cfg, paint=paint), file=out)


def config_menu(stdin: Optional[IO[str]] = None, out: Optional[IO[str]] = None) -> int:
    """Interactive settings menu. Saves immediately; re-applies the OS schedule on exit."""
    stdin = stdin or sys.stdin
    out = out or sys.stdout
    paint = Paint(colour_enabled(out))
    cfg = config.load()
    schedule_dirty = False
    while True:
        print("\n" + render_settings(cfg, paint=paint), file=out)
        print(f"\n {paint.muted}輸入編號修改{paint.reset}  {paint.muted}·{paint.reset}  "
              f"{paint.accent}a{paint.reset} 立即套用排程  {paint.muted}·{paint.reset}  "
              f"{paint.accent}d{paint.reset} 還原預設  {paint.muted}·{paint.reset}  "
              f"{paint.accent}q{paint.reset} 離開", file=out)
        print(f" {paint.accent}{GLYPH_PROMPT}{paint.reset} ", end="", file=out, flush=True)
        choice = _read(stdin)
        if choice is None or choice.strip().lower() in ("q", "quit", "exit"):
            break
        choice = choice.strip()
        if not choice:
            continue
        if choice.lower() == "a":
            config.save(cfg)
            _apply_schedule(cfg, paint, out)
            schedule_dirty = False
            continue
        if choice.lower() == "d":
            cfg = dict(config.DEFAULTS)
            config.save(cfg)
            schedule_dirty = True
            print(f" {paint.ok}✓ 已還原預設值{paint.reset}", file=out)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(config.SETTINGS):
            print(f" {paint.err}✗ 請輸入 1-{len(config.SETTINGS)}、a、d 或 q{paint.reset}", file=out)
            continue
        setting = config.SETTINGS[int(choice) - 1]
        if _edit(setting, cfg, paint, stdin, out):
            config.save(cfg)
            schedule_dirty = schedule_dirty or setting.key in config.SCHEDULE_KEYS
            print(f" {paint.ok}✓ {setting.label} → "
                  f"{config.render(setting, cfg[setting.key])}{paint.reset}", file=out)
    if schedule_dirty:
        print(f"\n {paint.warn}排程設定已變更，正在重新套用…{paint.reset}", file=out)
        _apply_schedule(cfg, paint, out)
    print(f" {paint.muted}設定檔：{config.config_path()}{paint.reset}", file=out)
    return 0


def demo() -> None:
    import io

    plain = Paint(False)
    cfg = dict(config.DEFAULTS)
    text = render_settings(cfg, paint=plain)
    assert "每日時間" in text and "10:00" in text
    assert "2 hours" in text
    assert text.count("\033") == 0

    # A toggle needs no input; a value prompt reads one line and validates it.
    out = io.StringIO()
    assert _edit(config.BY_KEY["daily_enabled"], cfg, plain, io.StringIO(""), out) is True
    assert cfg["daily_enabled"] is False
    assert _edit(config.BY_KEY["scan_interval_minutes"], cfg, plain, io.StringIO("45m\n"), out) is True
    assert cfg["scan_interval_minutes"] == 45
    assert _edit(config.BY_KEY["scan_interval_minutes"], cfg, plain, io.StringIO("nope\n"), out) is False
    assert cfg["scan_interval_minutes"] == 45
    assert _edit(config.BY_KEY["scan_interval_minutes"], cfg, plain, io.StringIO("\n"), out) is False

    assert width("每日") == 4 and width("ab") == 2
    print("ui.demo: ok")


if __name__ == "__main__":
    demo()
