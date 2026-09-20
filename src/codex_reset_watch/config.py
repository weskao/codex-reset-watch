"""User-editable settings: schema, load/save, coercion, and derived values.

Everything the user may tune lives in :data:`SETTINGS` — one :class:`Setting`
per row. The CLI menu (``crw config``), ``config.example.json``, the
non-interactive ``--set`` path and the validation rules are all derived from
that tuple, so adding a knob means appending one entry and nothing else.

Config file location: ``$CRW_CONFIG``, else ``<platform config dir>/config.json``.
State and log directories additionally honour the ``state_dir``/``log_dir``
settings; the environment overrides (``CRW_STATE_DIR``/``CRW_LOG_DIR``) still
win over both so a test or a one-off run can redirect them.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from . import paths

DEFAULT_API_BASE = "https://codex-resets.com"
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440  # one day: the largest unit the scanner offers
FALLBACK_TZ = dt.timezone(dt.timedelta(hours=8), name="UTC+8")


@dataclass(frozen=True)
class Setting:
    """One tunable value. ``kind`` picks the parser, renderer and menu editor."""

    key: str
    kind: str  # bool | int | text | time | interval | path | tz
    default: Any
    group: str
    label: str
    help: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None


SETTINGS: Tuple[Setting, ...] = (
    # ── 排程 ─────────────────────────────────────────────────────────────
    Setting("daily_enabled", "bool", True, "排程 Scheduling", "每日通知",
            "每天固定時間送一次總覽。關閉後安裝器不會建立 daily 排程。"),
    Setting("daily_time", "time", "10:00", "排程 Scheduling", "每日時間",
            "HH:MM，依下方時區解讀（例：10:00）。"),
    Setting("monitor_enabled", "bool", True, "排程 Scheduling", "背景掃描",
            "定期掃描 API，只在有新資訊時通知。關閉後不建立 monitor 排程。"),
    Setting("scan_interval_minutes", "interval", 120, "排程 Scheduling", "掃描間隔",
            "最小 1 分鐘、最大 1 天。可輸入 30m / 2h / 1d 或純數字（分鐘）。",
            MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES),
    Setting("timezone", "tz", "UTC+8", "排程 Scheduling", "時區",
            "UTC+8、UTC-05:30、UTC、local，或 IANA 名稱（Asia/Taipei）。"),
    # ── 通知 ─────────────────────────────────────────────────────────────
    Setting("notify_new_reset_events", "bool", True, "通知 Notifications", "新 Reset 事件",
            "偵測到新的已發生 Reset 公告時通知。"),
    Setting("notify_upcoming_reset", "bool", True, "通知 Notifications", "未來 Reset 訊號",
            "偵測到尚未發生的預告／預測訊號時通知。"),
    Setting("monitor_notify_when_unchanged", "bool", False, "通知 Notifications", "掃描無變化也通知",
            "開啟會在每次背景掃描都推播，即使內容沒變。"),
    Setting("daily_notify_when_unchanged", "bool", False, "通知 Notifications", "每日無變化也通知",
            "開啟會在每日排程都推播，即使內容沒變。"),
    # ── API ──────────────────────────────────────────────────────────────
    Setting("api_base", "text", DEFAULT_API_BASE, "API", "API 位址", "追蹤來源的 base URL。"),
    Setting("status_path", "text", "/api/v1/status", "API", "status 路徑", "狀態端點（必要）。"),
    Setting("resets_path", "text", "/api/v1/resets?limit=20&order=desc", "API", "resets 路徑",
            "歷史事件端點（非必要，失敗不影響主流程）。"),
    Setting("request_timeout_seconds", "int", 15, "API", "逾時秒數", "單次 HTTP 請求逾時。", 1, 300),
    Setting("request_retries", "int", 3, "API", "重試次數", "可重試錯誤（429/5xx/連線）的嘗試上限。", 1, 10),
    Setting("user_agent", "text", "codex-reset-watch/1.0 (+https://codex-resets.com/api/docs)",
            "API", "User-Agent", "送出的 User-Agent 標頭。"),
    # ── 位置與 log ───────────────────────────────────────────────────────
    Setting("state_dir", "path", "", "位置 Storage", "狀態資料夾", "留空＝系統預設位置。"),
    Setting("log_dir", "path", "", "位置 Storage", "Log 資料夾",
            "留空＝系統預設位置。變更後需重新套用排程（排程會寫入這個資料夾）。"),
    Setting("max_log_bytes", "int", 2 * 1024 * 1024, "位置 Storage", "單一 log 上限",
            "超過就輪替（bytes）。", 64 * 1024, 512 * 1024 * 1024),
    Setting("log_backups", "int", 3, "位置 Storage", "Log 保留份數", "輪替時保留幾個歷史檔。", 0, 20),
)

BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}
DEFAULTS: Dict[str, Any] = {s.key: s.default for s in SETTINGS}
GROUPS: Tuple[str, ...] = tuple(dict.fromkeys(s.group for s in SETTINGS))

#: Changing any of these means the OS scheduler entries are now stale.
SCHEDULE_KEYS = frozenset({
    "daily_enabled", "daily_time", "monitor_enabled", "scan_interval_minutes",
    "timezone", "log_dir",
})


# ── parsing helpers ──────────────────────────────────────────────────────────

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*(min|minutes?|m|hours?|hrs?|h|days?|d)?\s*$", re.I)
_UNIT_MINUTES = {"m": 1, "min": 1, "minute": 1, "minutes": 1,
                 "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
                 "d": 1440, "day": 1440, "days": 1440}
_TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:h．.]?\s*(\d{1,2})?\s*$")
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)


def parse_interval(raw: Any) -> int:
    """``"2h"`` / ``"90"`` / ``"1d"`` → minutes, clamped to the offered range."""
    if isinstance(raw, bool):
        raise ValueError("間隔必須是時間長度，例如 30m / 2h / 1d")
    if isinstance(raw, (int, float)):
        minutes = int(raw)
    else:
        m = _INTERVAL_RE.match(str(raw))
        if not m:
            raise ValueError("看不懂的間隔；請用 30m / 2h / 1d 或純數字（分鐘）")
        minutes = int(m.group(1)) * _UNIT_MINUTES[(m.group(2) or "m").lower()]
    if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(f"間隔需在 {MIN_INTERVAL_MINUTES} 分鐘 ~ 1 天（{MAX_INTERVAL_MINUTES} 分鐘）之間")
    return minutes


def format_interval(minutes: int) -> str:
    if minutes % 1440 == 0:
        n = minutes // 1440
        return f"{n} day" + ("s" if n != 1 else "")
    if minutes % 60 == 0:
        n = minutes // 60
        return f"{n} hour" + ("s" if n != 1 else "")
    return f"{minutes} minute" + ("s" if minutes != 1 else "")


def parse_hhmm(raw: Any) -> str:
    """``"9"`` / ``"9:5"``… → canonical ``"HH:MM"``. Raises on an impossible time."""
    m = _TIME_RE.match(str(raw))
    if not m:
        raise ValueError("時間格式需為 HH:MM（例：10:00）")
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("時間需在 00:00 ~ 23:59 之間")
    return f"{hour:02d}:{minute:02d}"


def tzinfo_for(cfg: Optional[Dict[str, Any]] = None) -> dt.tzinfo:
    """Resolve the ``timezone`` setting. Unknown names fall back to UTC+8.

    Fixed offsets are parsed here rather than via zoneinfo because Windows has
    no system tz database — an offset must keep working with no `tzdata` wheel.
    """
    name = str((cfg or {}).get("timezone") or DEFAULTS["timezone"]).strip()
    upper = name.upper()
    if upper in ("UTC", "GMT", "Z"):
        return dt.timezone.utc
    if upper in ("LOCAL", "SYSTEM"):
        return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
    m = _OFFSET_RE.match(name)
    if m:
        sign = -1 if m.group(1) == "-" else 1
        delta = dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        if delta <= dt.timedelta(hours=24):
            return dt.timezone(sign * delta, name=name)
    with contextlib.suppress(Exception):
        from zoneinfo import ZoneInfo  # stdlib; needs tzdata only on Windows

        return ZoneInfo(name)
    return FALLBACK_TZ


def tz_label(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Short suffix appended to rendered timestamps, e.g. ``UTC+8``."""
    name = str((cfg or {}).get("timezone") or DEFAULTS["timezone"]).strip()
    if name.upper() in ("LOCAL", "SYSTEM"):
        offset = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0)
        total = int(offset.total_seconds()) // 60
        sign = "+" if total >= 0 else "-"
        h, m = divmod(abs(total), 60)
        return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")
    return name


def daily_hm(cfg: Dict[str, Any]) -> Tuple[int, int]:
    hh, mm = parse_hhmm(cfg.get("daily_time", DEFAULTS["daily_time"])).split(":")
    return int(hh), int(mm)


def daily_os_local_hm(cfg: Dict[str, Any], *, now: Optional[dt.datetime] = None) -> Tuple[int, int]:
    """The configured daily time expressed in the machine's own local time.

    The OS schedulers all fire on system local time, while ``daily_time`` is
    stated in the configured timezone. Converting here keeps the two agreeing.
    DST transitions can shift the fired time by an hour until the next re-apply;
    the in-process daily gate (`last_daily_date`) makes that harmless.
    """
    tz = tzinfo_for(cfg)
    hour, minute = daily_hm(cfg)
    reference = (now or dt.datetime.now(tz)).astimezone(tz)
    target = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
    local = target.astimezone()
    return local.hour, local.minute


# ── coercion ─────────────────────────────────────────────────────────────────

_TRUE = {"1", "true", "yes", "y", "on", "開", "是"}
_FALSE = {"0", "false", "no", "n", "off", "關", "否"}


def coerce(setting: Setting, raw: Any) -> Any:
    """Turn user/file input into the stored type. Raises ValueError with a message."""
    if setting.kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError("請輸入 on/off（或 true/false、1/0）")
    if setting.kind == "interval":
        return parse_interval(raw)
    if setting.kind == "time":
        return parse_hhmm(raw)
    if setting.kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError("請輸入整數") from None
        if setting.minimum is not None and value < setting.minimum:
            raise ValueError(f"不得小於 {setting.minimum}")
        if setting.maximum is not None and value > setting.maximum:
            raise ValueError(f"不得大於 {setting.maximum}")
        return value
    if setting.kind == "path":
        text = str(raw).strip().strip('"').strip("'")
        return str(pathlib.Path(text).expanduser()) if text else ""
    if setting.kind == "tz":
        text = str(raw).strip()
        if not text:
            raise ValueError("時區不可留空")
        return text
    text = str(raw).strip()
    if not text:
        raise ValueError("不可留空")
    return text


def render(setting: Setting, value: Any) -> str:
    """Human-readable form of a stored value, for the menu and ``--list``."""
    if setting.kind == "bool":
        return "On" if value else "Off"
    if setting.kind == "interval":
        return format_interval(int(value))
    if setting.kind == "path":
        return str(value) if value else "（系統預設）"
    if setting.key == "max_log_bytes":
        return f"{int(value) / 1024 / 1024:g} MiB"
    return str(value)


# ── load / save ──────────────────────────────────────────────────────────────

def config_path() -> pathlib.Path:
    override = os.environ.get("CRW_CONFIG")
    return pathlib.Path(override) if override else pathlib.Path(paths.app_config_dir()) / "config.json"


def _migrate(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Accept configs written before these keys were renamed/merged."""
    out = dict(raw)
    if "daily_time" not in out and ("daily_hour" in out or "daily_minute" in out):
        with contextlib.suppress(ValueError, TypeError):
            out["daily_time"] = f"{int(out.get('daily_hour', 10)):02d}:{int(out.get('daily_minute', 0)):02d}"
    if "timezone" not in out and out.get("timezone_label"):
        out["timezone"] = out["timezone_label"]
    return out


def load() -> Dict[str, Any]:
    """Defaults, overlaid with the config file. Unreadable/invalid values are
    dropped rather than fatal — a scheduled run must never die on a typo."""
    cfg = dict(DEFAULTS)
    path = config_path()
    raw: Dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            raw = _migrate(parsed)
    for key, value in raw.items():
        setting = BY_KEY.get(key)
        if setting is None:
            cfg[key] = value  # keep unknown keys so a newer config survives an older build
            continue
        try:
            cfg[key] = coerce(setting, value)
        except ValueError:
            pass
    return cfg


def save(cfg: Dict[str, Any], path: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Atomically write the whole config, 0600. Returns the path written."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {s.key: cfg.get(s.key, s.default) for s in SETTINGS}
    payload.update({k: v for k, v in cfg.items() if k not in BY_KEY})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def set_value(cfg: Dict[str, Any], key: str, raw: Any) -> Any:
    """Validate and store one setting in ``cfg`` (caller saves). Returns the stored value."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise KeyError(key)
    cfg[key] = coerce(setting, raw)
    return cfg[key]


# ── directories ──────────────────────────────────────────────────────────────

def _dir(env_var: str, configured: Any, fallback: Any) -> pathlib.Path:
    override = os.environ.get(env_var)
    if override:
        return pathlib.Path(override)
    if configured:
        return pathlib.Path(str(configured)).expanduser()
    return pathlib.Path(str(fallback))


def state_dir(cfg: Optional[Dict[str, Any]] = None) -> pathlib.Path:
    return _dir("CRW_STATE_DIR", (cfg or {}).get("state_dir"), paths.app_state_dir())


def log_dir(cfg: Optional[Dict[str, Any]] = None) -> pathlib.Path:
    return _dir("CRW_LOG_DIR", (cfg or {}).get("log_dir"), paths.app_log_dir())


def demo() -> None:
    assert parse_interval("2h") == 120
    assert parse_interval(" 45 min ") == 45
    assert parse_interval("1d") == 1440
    assert parse_interval(30) == 30
    for bad in ("0", "2w", "1441", "", "-5"):
        try:
            parse_interval(bad)
        except ValueError:
            pass
        else:  # pragma: no cover - guards the guard
            raise AssertionError(f"accepted {bad!r}")
    assert format_interval(120) == "2 hours"
    assert format_interval(1) == "1 minute"
    assert format_interval(1440) == "1 day"
    assert parse_hhmm("9") == "09:00"
    assert parse_hhmm("7:05") == "07:05"
    assert coerce(BY_KEY["daily_enabled"], "off") is False
    assert coerce(BY_KEY["scan_interval_minutes"], "3h") == 180
    assert tzinfo_for({"timezone": "UTC-05:30"}).utcoffset(None) == dt.timedelta(hours=-5, minutes=-30)
    assert tzinfo_for({"timezone": "nonsense/zone"}) is FALLBACK_TZ
    assert _migrate({"daily_hour": 7, "daily_minute": 30})["daily_time"] == "07:30"
    print("config.demo: ok")


if __name__ == "__main__":
    demo()
