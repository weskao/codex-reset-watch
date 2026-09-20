"""Message catalogue and language resolution — the one home for translations.

:data:`LANGUAGES` says which languages exist and how a system locale maps onto
them; :data:`MESSAGES` is the catalogue. Adding a language means appending one
:class:`Language` and one entry per message; adding a message means appending
one entry to :data:`MESSAGES`.

The catalogue is keyed **message id first, language second**, so every
translation of one string sits on adjacent lines — a missing translation shows
up by reading down the block rather than by diffing two far-apart tables. A gap
is never fatal: :func:`t` falls back to :data:`FALLBACK`.

English is the source language: ``Setting.label``/``Setting.help`` in
:mod:`codex_reset_watch.config` hold the English text and this module supplies
the rest, so a message id that was never translated still renders something
sensible instead of a raw id.

Import direction (deliberate, do not invert): this module is a **leaf** at
import time — :mod:`codex_reset_watch.config` imports it freely, and the one
config read :func:`current_language` needs is a function-local, file-level read
(:func:`_stored_language`) rather than ``config.load()``. Going through
``config.load()`` would recurse, because loading a config coerces values and a
coercion error asks this module for its message.
"""
from __future__ import annotations

import contextlib
import json
import locale
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

FALLBACK = "en"

#: The config value meaning "ask the OS" — resolved by :func:`system_language`.
AUTO = "auto"

#: Environment override, for a one-off run in another language.
ENV_VAR = "CRW_LANG"


@dataclass(frozen=True)
class Language:
    """One supported language: its config value, its label, its locale prefixes.

    *aliases* are lowercased, underscore-normalised locale prefixes matched
    against the system locale (``zh_TW.UTF-8`` → ``zh_tw``). List every variant
    that should resolve here; a locale matching nothing falls back to
    :data:`FALLBACK` rather than guessing a neighbouring language.
    """

    code: str
    label: str
    aliases: Tuple[str, ...]


# Deliberately no bare "zh" alias: zh_CN/zh_SG are Simplified and must not
# resolve to Traditional — they fall back to English until a zh-CN entry lands.
LANGUAGES: Tuple[Language, ...] = (
    Language("en", "English", ("en",)),
    Language("zh-TW", "繁體中文", ("zh_tw", "zh_hant", "zh_hk", "zh_mo")),
)

LANGUAGE_CODES: Tuple[str, ...] = tuple(lang.code for lang in LANGUAGES)


# ── catalogue ────────────────────────────────────────────────────────────────
# Setting labels/help use the id form `setting.<key>.label` / `setting.<key>.help`
# and group headings `group.<id>`, so config.SETTINGS needs no translated text.

MESSAGES: Dict[str, Dict[str, str]] = {
    # ── groups ──────────────────────────────────────────────────────────────
    "group.scheduling": {"en": "Scheduling", "zh-TW": "排程"},
    "group.notifications": {"en": "Notifications", "zh-TW": "通知"},
    "group.telegram": {"en": "Telegram", "zh-TW": "Telegram"},
    "group.api": {"en": "API", "zh-TW": "API"},
    "group.storage": {"en": "Storage", "zh-TW": "位置"},
    "group.interface": {"en": "Interface", "zh-TW": "介面"},

    # ── settings: scheduling ────────────────────────────────────────────────
    "setting.daily_enabled.label": {"en": "Daily notification", "zh-TW": "每日通知"},
    "setting.daily_enabled.help": {
        "en": "Send one overview at a fixed time each day. Off means no daily job is created.",
        "zh-TW": "每天固定時間送一次總覽。關閉後安裝器不會建立 daily 排程。",
    },
    "setting.daily_time.label": {"en": "Daily time", "zh-TW": "每日時間"},
    "setting.daily_time.help": {
        "en": "HH:MM, read in the timezone below (e.g. 10:00).",
        "zh-TW": "HH:MM，依下方時區解讀（例：10:00）。",
    },
    "setting.monitor_enabled.label": {"en": "Background scan", "zh-TW": "背景掃描"},
    "setting.monitor_enabled.help": {
        "en": "Scan the API periodically, notifying only on new information. Off means no monitor job.",
        "zh-TW": "定期掃描 API，只在有新資訊時通知。關閉後不建立 monitor 排程。",
    },
    "setting.scan_interval_minutes.label": {"en": "Scan interval", "zh-TW": "掃描間隔"},
    "setting.scan_interval_minutes.help": {
        "en": "Minimum 1 minute, maximum 1 day. Accepts 30m / 2h / 1d or a bare number of minutes.",
        "zh-TW": "最小 1 分鐘、最大 1 天。可輸入 30m / 2h / 1d 或純數字（分鐘）。",
    },
    "setting.timezone.label": {"en": "Timezone", "zh-TW": "時區"},
    "setting.timezone.help": {
        "en": "UTC+8, UTC-05:30, UTC, local, or an IANA name (Asia/Taipei).",
        "zh-TW": "UTC+8、UTC-05:30、UTC、local，或 IANA 名稱（Asia/Taipei）。",
    },

    # ── settings: notifications ─────────────────────────────────────────────
    "setting.notify_new_reset_events.label": {"en": "New reset events", "zh-TW": "新 Reset 事件"},
    "setting.notify_new_reset_events.help": {
        "en": "Notify when a newly published reset announcement is detected.",
        "zh-TW": "偵測到新的已發生 Reset 公告時通知。",
    },
    "setting.notify_upcoming_reset.label": {"en": "Upcoming reset signals", "zh-TW": "未來 Reset 訊號"},
    "setting.notify_upcoming_reset.help": {
        "en": "Notify when a not-yet-happened forecast or prediction signal is detected.",
        "zh-TW": "偵測到尚未發生的預告／預測訊號時通知。",
    },
    "setting.monitor_notify_when_unchanged.label": {
        "en": "Notify on unchanged scan", "zh-TW": "掃描無變化也通知"},
    "setting.monitor_notify_when_unchanged.help": {
        "en": "Push on every background scan, even when nothing changed.",
        "zh-TW": "開啟會在每次背景掃描都推播，即使內容沒變。",
    },
    "setting.daily_notify_when_unchanged.label": {
        "en": "Notify on unchanged day", "zh-TW": "每日無變化也通知"},
    "setting.daily_notify_when_unchanged.help": {
        "en": "Push on every daily run, even when nothing changed.",
        "zh-TW": "開啟會在每日排程都推播，即使內容沒變。",
    },

    # ── settings: telegram ──────────────────────────────────────────────────
    "setting.telegram_bot_token.label": {"en": "Bot token", "zh-TW": "Bot Token"},
    "setting.telegram_bot_token.help": {
        "en": "Bot API token. Kept in the OS keychain, never in a file; shown masked, never exported.",
        "zh-TW": "Bot API token。存放在系統金鑰圈，不會寫進任何檔案；畫面遮蔽，匯出時不帶走。",
    },
    "setting.telegram_bot_token.note": {
        "en": "Environment TG_BOT_TOKEN wins. Store: {backend}",
        "zh-TW": "環境變數 TG_BOT_TOKEN 優先。儲存位置：{backend}",
    },
    "menu.no_secret_store": {
        "en": "No OS credential store here — set TG_BOT_TOKEN in the environment instead",
        "zh-TW": "這台機器沒有系統金鑰圈，請改用環境變數 TG_BOT_TOKEN",
    },
    "setting.telegram_chat_id.label": {"en": "Chat ID", "zh-TW": "Chat ID"},
    "setting.telegram_chat_id.help": {
        "en": "Telegram chat that receives the notifications. TG_CHAT_ID in the environment wins.",
        "zh-TW": "接收通知的 Telegram 對話。環境變數 TG_CHAT_ID 優先於這個值。",
    },

    # ── settings: api ───────────────────────────────────────────────────────
    "setting.api_base.label": {"en": "API base", "zh-TW": "API 位址"},
    "setting.api_base.help": {
        "en": "Base URL of the tracked source.", "zh-TW": "追蹤來源的 base URL。"},
    "setting.status_path.label": {"en": "status path", "zh-TW": "status 路徑"},
    "setting.status_path.help": {
        "en": "Status endpoint (required).", "zh-TW": "狀態端點（必要）。"},
    "setting.resets_path.label": {"en": "resets path", "zh-TW": "resets 路徑"},
    "setting.resets_path.help": {
        "en": "History endpoint (optional; a failure here does not stop the main flow).",
        "zh-TW": "歷史事件端點（非必要，失敗不影響主流程）。",
    },
    "setting.request_timeout_seconds.label": {"en": "Timeout (seconds)", "zh-TW": "逾時秒數"},
    "setting.request_timeout_seconds.help": {
        "en": "Per-request HTTP timeout.", "zh-TW": "單次 HTTP 請求逾時。"},
    "setting.request_retries.label": {"en": "Retries", "zh-TW": "重試次數"},
    "setting.request_retries.help": {
        "en": "Attempt limit for retryable errors (429/5xx/connection).",
        "zh-TW": "可重試錯誤（429/5xx/連線）的嘗試上限。",
    },
    "setting.user_agent.label": {"en": "User-Agent", "zh-TW": "User-Agent"},
    "setting.user_agent.help": {
        "en": "User-Agent header sent with each request.", "zh-TW": "送出的 User-Agent 標頭。"},

    # ── settings: storage ───────────────────────────────────────────────────
    "setting.state_dir.label": {"en": "State folder", "zh-TW": "狀態資料夾"},
    "setting.state_dir.help": {
        "en": "Blank = platform default.", "zh-TW": "留空＝系統預設位置。"},
    "setting.log_dir.label": {"en": "Log folder", "zh-TW": "Log 資料夾"},
    "setting.log_dir.help": {
        "en": "Blank = platform default. Changing it needs the schedule re-applied (jobs write here).",
        "zh-TW": "留空＝系統預設位置。變更後需重新套用排程（排程會寫入這個資料夾）。",
    },
    "setting.max_log_bytes.label": {"en": "Log size cap", "zh-TW": "單一 log 上限"},
    "setting.max_log_bytes.help": {
        "en": "Rotate once a log passes this size (bytes).", "zh-TW": "超過就輪替（bytes）。"},
    "setting.log_backups.label": {"en": "Log backups", "zh-TW": "Log 保留份數"},
    "setting.log_backups.help": {
        "en": "How many rotated files to keep.", "zh-TW": "輪替時保留幾個歷史檔。"},

    # ── settings: interface ─────────────────────────────────────────────────
    "setting.language.label": {"en": "Language", "zh-TW": "語言"},
    "setting.language.help": {
        "en": "Language for the menu and messages. auto follows the system locale.",
        "zh-TW": "選單與訊息的語言。auto 會跟隨系統語系。",
    },

    # ── values ──────────────────────────────────────────────────────────────
    "value.on": {"en": "On", "zh-TW": "開啟"},
    "value.off": {"en": "Off", "zh-TW": "關閉"},
    "value.platform_default": {"en": "(platform default)", "zh-TW": "（系統預設）"},
    "value.unset": {"en": "(not set)", "zh-TW": "（未設定）"},
    "value.from_env": {"en": "(from environment)", "zh-TW": "（來自環境變數）"},

    # ── menu chrome ─────────────────────────────────────────────────────────
    "menu.title": {"en": "Codex Reset Watch · Settings", "zh-TW": "Codex Reset Watch · 設定"},
    "menu.tz_note": {
        "en": "Times shown in {tz}; the OS fires each job in its own local time.",
        "zh-TW": "時間以 {tz} 顯示；排程由作業系統以本機時間觸發。",
    },
    "menu.move": {"en": "move", "zh-TW": "移動"},
    "menu.change": {"en": "change", "zh-TW": "切換"},
    "menu.edit": {"en": "edit", "zh-TW": "編輯"},
    "menu.apply": {"en": "apply schedule", "zh-TW": "套用排程"},
    "menu.defaults": {"en": "defaults", "zh-TW": "還原預設"},
    "menu.export": {"en": "export", "zh-TW": "匯出"},
    "menu.import": {"en": "import", "zh-TW": "匯入"},
    "menu.quit": {"en": "quit", "zh-TW": "離開"},
    "menu.cancel_hint": {"en": "Esc cancels", "zh-TW": "Esc 取消"},
    "menu.number_hint": {"en": "Type a number to edit", "zh-TW": "輸入編號修改"},
    "menu.current": {"en": "Current:", "zh-TW": "目前："},
    "menu.keep_hint": {"en": "(Enter keeps it)", "zh-TW": "(Enter 保持不變)"},
    "menu.secret_hint": {
        "en": "(Enter with an empty box keeps the stored value)",
        "zh-TW": "(留空按 Enter 保持原值)",
    },
    "menu.config_file": {"en": "Config file: {path}", "zh-TW": "設定檔：{path}"},
    "menu.saved": {"en": "{label} → {value}", "zh-TW": "{label} → {value}"},
    "menu.applied": {
        "en": "Re-applied the {backend} schedule: {jobs}",
        "zh-TW": "已重新套用 {backend} 排程：{jobs}",
    },
    "menu.apply_failed": {
        "en": "Could not apply the schedule ({error})",
        "zh-TW": "排程套用失敗（{error}）",
    },
    "menu.apply_retry": {
        "en": "Retry with the installer: uv run python scripts/install.py",
        "zh-TW": "可改用安裝器重試：uv run python scripts/install.py",
    },
    "menu.jobs_none": {"en": "(all off)", "zh-TW": "（全部關閉）"},
    "menu.schedule_dirty": {
        "en": "Schedule settings changed, re-applying…",
        "zh-TW": "排程設定已變更，正在重新套用…",
    },
    "menu.defaults_done": {"en": "Restored every default", "zh-TW": "已還原預設值"},
    "menu.confirm_defaults": {
        "en": "Restore every setting to its default? y / any other key cancels",
        "zh-TW": "確定要把所有設定還原成預設值？y 確認，其他鍵取消",
    },
    "menu.invalid_choice": {
        "en": "Enter 1-{count}, or a / d / e / i / q",
        "zh-TW": "請輸入 1-{count}，或 a / d / e / i / q",
    },
    "menu.export_prompt": {"en": "Export to file:", "zh-TW": "匯出到檔案："},
    "menu.import_prompt": {"en": "Import from file:", "zh-TW": "從檔案匯入："},
    "menu.export_done": {
        "en": "Exported {count} settings to {target}",
        "zh-TW": "已匯出 {count} 項設定到 {target}",
    },
    "menu.export_secrets": {
        "en": "Secrets are never exported — set these again on the other machine: {keys}",
        "zh-TW": "機密不會被匯出，請在另一台機器重新設定：{keys}",
    },
    "menu.export_stdout": {"en": "standard output", "zh-TW": "標準輸出"},
    "menu.import_done": {
        "en": "Imported {count} settings from {path}",
        "zh-TW": "已從 {path} 匯入 {count} 項設定",
    },
    "menu.import_skipped": {
        "en": "Skipped (secrets are never imported, unknown keys are left alone): {keys}",
        "zh-TW": "已略過（機密不會匯入，未知的鍵保持原樣）：{keys}",
    },
    "menu.import_not_object": {
        "en": "{path} is not a JSON object of settings",
        "zh-TW": "{path} 不是設定用的 JSON 物件",
    },
    "menu.set_done": {
        "en": "Updated {count} settings and saved: {path}",
        "zh-TW": "已更新 {count} 項設定並儲存：{path}",
    },
    "menu.set_format": {
        "en": "Expected KEY=VALUE: {item}", "zh-TW": "格式需為 KEY=VALUE：{item}"},
    "menu.set_unknown_key": {
        "en": "Unknown setting: {key} (see `crw config --list` for the keys)",
        "zh-TW": "未知設定：{key}（可用鍵見 `crw config --list`）",
    },

    # ── summary line ────────────────────────────────────────────────────────
    "summary.daily": {"en": "Daily {time} ({tz})", "zh-TW": "每日 {time}（{tz}）"},
    "summary.daily_off": {"en": "Daily off", "zh-TW": "每日 關閉"},
    "summary.scan": {"en": "Scan every {interval}", "zh-TW": "掃描 每 {interval}"},
    "summary.scan_off": {"en": "Scan off", "zh-TW": "掃描 關閉"},

    # ── intervals ───────────────────────────────────────────────────────────
    "interval.minute": {"en": "{n} minute", "zh-TW": "{n} 分鐘"},
    "interval.minutes": {"en": "{n} minutes", "zh-TW": "{n} 分鐘"},
    "interval.hour": {"en": "{n} hour", "zh-TW": "{n} 小時"},
    "interval.hours": {"en": "{n} hours", "zh-TW": "{n} 小時"},
    "interval.day": {"en": "{n} day", "zh-TW": "{n} 天"},
    "interval.days": {"en": "{n} days", "zh-TW": "{n} 天"},

    # ── validation errors ───────────────────────────────────────────────────
    "error.bool": {
        "en": "Enter on/off (or true/false, 1/0)", "zh-TW": "請輸入 on/off（或 true/false、1/0）"},
    "error.interval_type": {
        "en": "An interval must be a duration, e.g. 30m / 2h / 1d",
        "zh-TW": "間隔必須是時間長度，例如 30m / 2h / 1d",
    },
    "error.interval_format": {
        "en": "Unrecognised interval; use 30m / 2h / 1d or a bare number of minutes",
        "zh-TW": "看不懂的間隔；請用 30m / 2h / 1d 或純數字（分鐘）",
    },
    "error.interval_range": {
        "en": "The interval must be between {low} minutes and 1 day ({high} minutes)",
        "zh-TW": "間隔需在 {low} 分鐘 ~ 1 天（{high} 分鐘）之間",
    },
    "error.time_format": {
        "en": "The time must look like HH:MM (e.g. 10:00)", "zh-TW": "時間格式需為 HH:MM（例：10:00）"},
    "error.time_range": {
        "en": "The time must be between 00:00 and 23:59", "zh-TW": "時間需在 00:00 ~ 23:59 之間"},
    "error.int": {"en": "Enter a whole number", "zh-TW": "請輸入整數"},
    "error.min": {"en": "Must not be less than {minimum}", "zh-TW": "不得小於 {minimum}"},
    "error.max": {"en": "Must not be greater than {maximum}", "zh-TW": "不得大於 {maximum}"},
    "error.blank": {"en": "Must not be blank", "zh-TW": "不可留空"},
    "error.tz_blank": {"en": "The timezone must not be blank", "zh-TW": "時區不可留空"},
    "error.choice": {
        "en": "Must be one of: {choices}", "zh-TW": "必須是下列其中之一：{choices}"},
}


# ── resolution ───────────────────────────────────────────────────────────────

def _system_locale() -> str:
    """The OS's locale string, or ``""`` when it cannot be determined.

    POSIX environment variables first (macOS and Linux, in the precedence POSIX
    defines), then :func:`locale.getdefaultlocale` — which is what reaches
    ``GetUserDefaultLocaleName`` on Windows, where none of those variables are
    normally set.
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return value
    with contextlib.suppress(Exception):
        # Deprecated since 3.11 but not removed, and still the only stdlib call
        # that reads the Windows user default without setlocale's global side
        # effects. Any failure just means "unknown locale".
        return locale.getdefaultlocale()[0] or ""
    return ""


def system_language() -> str:
    """The language the OS asks for, or :data:`FALLBACK` when it asks for none.

    ``C`` / ``POSIX`` / an unsupported locale all land on :data:`FALLBACK` —
    matching nothing is the answer, not the nearest language.
    """
    raw = _system_locale().strip().lower().replace("-", "_")
    for lang in LANGUAGES:
        if any(raw == alias or raw.startswith(alias + "_") for alias in lang.aliases):
            return lang.code
    stem = raw.split(".", 1)[0].split("@", 1)[0]
    for lang in LANGUAGES:
        if stem in lang.aliases:
            return lang.code
    return FALLBACK


def resolve_language(configured: Any) -> str:
    """The language *configured* selects — ``auto``/junk/``None`` means the OS's.

    Never raises: an unknown code from a hand-edited config resolves to the
    system language, so a typo degrades to a sane default instead of an error
    inside a notification path.
    """
    code = str(configured or AUTO).strip()
    return code if code in LANGUAGE_CODES else system_language()


@lru_cache(maxsize=1)
def _stored_language() -> str:
    """The raw ``language`` value in the config file, read without ``config.load``.

    A direct file read on purpose — see the module docstring: ``config.load()``
    coerces values, and a coercion error asks this module for its message, so
    routing through it would recurse.
    """
    with contextlib.suppress(Exception):
        from .config import config_path

        parsed = json.loads(config_path().read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            return str(parsed.get("language") or AUTO)
    return AUTO


def forget_stored_language() -> None:
    """Drop the cached config read — called after the language setting changes."""
    _stored_language.cache_clear()


def current_language(cfg: Optional[Dict[str, Any]] = None) -> str:
    """The language to render in: ``$CRW_LANG``, else *cfg*, else the config file.

    A junk ``$CRW_LANG`` is ignored rather than fatal, so an exported shell
    variable can never break a scheduled run.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override in LANGUAGE_CODES:
        return override
    if override == AUTO:
        return system_language()
    configured = cfg.get("language") if cfg is not None else _stored_language()
    return resolve_language(configured)


def language_labels() -> Dict[str, str]:
    """Config value → the name to SHOW for it, e.g. ``{"en": "English"}``.

    Every language names itself in its own script (``English``, ``繁體中文``),
    which is what language pickers do and means these names need no translating.
    ``auto`` renders as whichever language it currently resolves to.
    """
    names = {lang.code: lang.label for lang in LANGUAGES}
    resolved = names.get(system_language(), system_language())
    return {AUTO: f"auto ({resolved})", **names}


def t(msg_id: str, lang: Optional[str] = None, default: Optional[str] = None, **kwargs: Any) -> str:
    """The *msg_id* message in *lang*, formatted with *kwargs*.

    Falls back, in order: the requested language → English → *default* → the id
    itself. A missing placeholder returns the unformatted template rather than
    raising — a translation typo must never crash a notification path.
    """
    translations = MESSAGES.get(msg_id)
    if translations is None:
        text = default if default is not None else msg_id
    else:
        code = lang if lang is not None else current_language()
        text = translations.get(code) or translations.get(FALLBACK) or default or msg_id
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def demo() -> None:
    assert t("menu.quit", lang="en") == "quit"
    assert t("menu.quit", lang="zh-TW") == "離開"
    assert t("no.such.id", lang="en", default="fb") == "fb"
    assert t("error.min", lang="en", minimum=3) == "Must not be less than 3"
    assert resolve_language("zh-TW") == "zh-TW"
    assert language_labels()["zh-TW"] == "繁體中文"
    missing = [f"{k}:{c}" for k, v in MESSAGES.items() for c in LANGUAGE_CODES if c not in v]
    assert not missing, missing
    print("i18n.demo: ok")


if __name__ == "__main__":
    demo()
