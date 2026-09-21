"""User-editable settings: schema, load/save, coercion, and derived values.

Everything the user may tune lives in :data:`SETTINGS` — one :class:`Setting`
per row. The CLI menu (``crw config``), ``config.example.json``, the
non-interactive ``--set`` path, export/import and the validation rules are all
derived from that tuple, so adding a knob means appending one entry and nothing
else.

``label``/``help`` hold the **English** source text; every other language comes
from :mod:`codex_reset_watch.i18n` under the ids ``setting.<key>.label`` /
``setting.<key>.help``, and ``group`` is an id translated the same way. A
message that was never translated therefore still renders its English source
rather than a raw id.

Config file location: ``$CRW_CONFIG``, else ``<platform config dir>/config.json``.
State and log directories additionally honour the ``state_dir``/``log_dir``
settings; the environment overrides (``CRW_STATE_DIR``/``CRW_LOG_DIR``) still
win over both so a test or a one-off run can redirect them.

Telegram credentials live here too, and what ``crw config`` stored wins:
``TG_BOT_TOKEN``/``TG_CHAT_ID`` are the fallback for a machine that cannot
store a secret at all, not an override (see :func:`telegram_credentials`).
The token is a ``secret`` kind, which means it renders masked and is never
written to an export file.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import i18n, paths, secrets_store

DEFAULT_API_BASE = "https://codex-resets.com"
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440  # one day: the largest unit the scanner offers
FALLBACK_TZ = dt.timezone(dt.timedelta(hours=8), name="UTC+8")


@dataclass(frozen=True)
class Setting:
    """One tunable value. ``kind`` picks the parser, renderer and menu editor.

    ``label``/``help`` are the English source text (see the module docstring);
    ``group`` is a group id, not a heading. ``choices``, when present, makes a
    row cyclable in the menu — left/right steps through the allowed values —
    and is what ``choice`` kinds validate against.
    """

    key: str
    kind: str  # bool | int | text | time | interval | path | tz | choice | secret
    default: Any
    group: str
    label: str
    help: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    choices: Optional[Tuple[str, ...]] = None


SETTINGS: Tuple[Setting, ...] = (
    # ── scheduling ───────────────────────────────────────────────────────
    Setting("daily_enabled", "bool", True, "scheduling", "Daily notification",
            "Send one overview at a fixed time each day."),
    Setting("daily_time", "time", "10:00", "scheduling", "Daily time",
            "HH:MM, read in the timezone below (e.g. 10:00)."),
    Setting("monitor_enabled", "bool", True, "scheduling", "Background scan",
            "Scan the API periodically, notifying only on new information."),
    Setting("scan_interval_minutes", "interval", 120, "scheduling", "Scan interval",
            "Minimum 1 minute, maximum 1 day. Accepts 30m / 2h / 1d or a number of minutes.",
            MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES),
    Setting("timezone", "tz", "UTC+8", "scheduling", "Timezone",
            "UTC+8, UTC-05:30, UTC, local, or an IANA name (Asia/Taipei)."),
    # ── notifications ────────────────────────────────────────────────────
    Setting("notify_new_reset_events", "bool", True, "notifications", "New reset events",
            "Notify when a newly published reset announcement is detected."),
    Setting("notify_upcoming_reset", "bool", True, "notifications", "Upcoming reset signals",
            "Notify when a not-yet-happened forecast or prediction signal is detected."),
    Setting("monitor_notify_when_unchanged", "bool", True, "notifications",
            "Notify on unchanged scan",
            "Push on every background scan, even when nothing changed."),
    Setting("daily_notify_when_unchanged", "bool", False, "notifications",
            "Notify on unchanged day",
            "Push on every daily run, even when nothing changed."),
    # ── telegram ─────────────────────────────────────────────────────────
    Setting("telegram_bot_token", "secret", "", "telegram", "Bot token",
            "Bot API token. Kept in the OS keychain, never in a file; used ahead of TG_BOT_TOKEN."),
    Setting("telegram_chat_id", "text_optional", "", "telegram", "Chat ID",
            "Telegram chat that receives the notifications; used ahead of TG_CHAT_ID."),
    # ── API ──────────────────────────────────────────────────────────────
    Setting("api_base", "text", DEFAULT_API_BASE, "api", "API base",
            "Base URL of the tracked source."),
    Setting("status_path", "text", "/api/v1/status", "api", "status path",
            "Status endpoint (required)."),
    Setting("resets_path", "text", "/api/v1/resets?limit=20&order=desc", "api", "resets path",
            "History endpoint (optional; a failure here does not stop the main flow)."),
    Setting("request_timeout_seconds", "int", 15, "api", "Timeout (seconds)",
            "Per-request HTTP timeout.", 1, 300),
    Setting("request_retries", "int", 3, "api", "Retries",
            "Attempt limit for retryable errors (429/5xx/connection).", 1, 10),
    Setting("user_agent", "text", "codex-reset-watch/1.0 (+https://codex-resets.com/api/docs)",
            "api", "User-Agent", "User-Agent header sent with each request."),
    # ── storage ──────────────────────────────────────────────────────────
    Setting("state_dir", "path", "", "storage", "State folder", "Blank = platform default."),
    Setting("log_dir", "path", "", "storage", "Log folder",
            "Blank = platform default. Changing it needs the schedule re-applied."),
    Setting("max_log_bytes", "int", 2 * 1024 * 1024, "storage", "Log size cap",
            "Rotate once a log passes this size (bytes).", 64 * 1024, 512 * 1024 * 1024),
    Setting("log_backups", "int", 3, "storage", "Log backups",
            "How many rotated files to keep.", 0, 20),
    # ── interface ────────────────────────────────────────────────────────
    Setting("language", "choice", "auto", "interface", "Language",
            "Language for the menu and messages. auto follows the system locale.",
            choices=(i18n.AUTO,) + i18n.LANGUAGE_CODES),
)

BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}
DEFAULTS: Dict[str, Any] = {s.key: s.default for s in SETTINGS}
GROUPS: Tuple[str, ...] = tuple(dict.fromkeys(s.group for s in SETTINGS))

#: Changing any of these means the OS scheduler entries are now stale.
SCHEDULE_KEYS = frozenset({
    "daily_enabled", "daily_time", "monitor_enabled", "scan_interval_minutes",
    "timezone", "log_dir",
})

#: Read off the kind, never hand-listed: a secret added to :data:`SETTINGS` must
#: not start leaking into export files just because this line was not updated.
SECRET_KEYS = frozenset(s.key for s in SETTINGS if s.kind == "secret")

#: Environment variables that stand in for unset Telegram credentials.
TELEGRAM_ENV = {"telegram_bot_token": "TG_BOT_TOKEN", "telegram_chat_id": "TG_CHAT_ID"}


# ── translated text ──────────────────────────────────────────────────────────

def label(setting: Setting, lang: Optional[str] = None) -> str:
    """The row label in *lang*, falling back to the English source text."""
    return i18n.t(f"setting.{setting.key}.label", lang, default=setting.label)


def help_text(setting: Setting, lang: Optional[str] = None) -> str:
    """The row's one-line explanation in *lang*, falling back to English."""
    return i18n.t(f"setting.{setting.key}.help", lang, default=setting.help)


def group_label(group: str, lang: Optional[str] = None) -> str:
    """The heading for a group id in *lang*, falling back to the id itself."""
    return i18n.t(f"group.{group}", lang, default=group)


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
        raise ValueError(i18n.t("error.interval_type"))
    if isinstance(raw, (int, float)):
        minutes = int(raw)
    else:
        m = _INTERVAL_RE.match(str(raw))
        if not m:
            raise ValueError(i18n.t("error.interval_format"))
        minutes = int(m.group(1)) * _UNIT_MINUTES[(m.group(2) or "m").lower()]
    if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(i18n.t("error.interval_range",
                                low=MIN_INTERVAL_MINUTES, high=MAX_INTERVAL_MINUTES))
    return minutes


def format_interval(minutes: int, lang: Optional[str] = None) -> str:
    """``120`` → ``"2 hours"``. Defaults to English so machine-facing callers
    (``--list`` piped to a file, tests) do not depend on the machine's locale;
    the menu passes the language it is rendering in."""
    lang = lang or i18n.FALLBACK
    if minutes % 1440 == 0:
        n = minutes // 1440
        return i18n.t("interval.day" if n == 1 else "interval.days", lang, n=n)
    if minutes % 60 == 0:
        n = minutes // 60
        return i18n.t("interval.hour" if n == 1 else "interval.hours", lang, n=n)
    return i18n.t("interval.minute" if minutes == 1 else "interval.minutes", lang, n=minutes)


def parse_hhmm(raw: Any) -> str:
    """``"9"`` / ``"9:5"``… → canonical ``"HH:MM"``. Raises on an impossible time."""
    m = _TIME_RE.match(str(raw))
    if not m:
        raise ValueError(i18n.t("error.time_format"))
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(i18n.t("error.time_range"))
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


def mask_secret(secret: str) -> str:
    """Stars, plus at most the last 4 characters — never enough to reuse."""
    if not secret:
        return ""
    return "*" * 8 + secret[-4:] if len(secret) > 12 else "*" * 8


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
        raise ValueError(i18n.t("error.bool"))
    if setting.kind == "interval":
        return parse_interval(raw)
    if setting.kind == "time":
        return parse_hhmm(raw)
    if setting.kind == "int":
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(i18n.t("error.int")) from None
        if setting.minimum is not None and value < setting.minimum:
            raise ValueError(i18n.t("error.min", minimum=setting.minimum))
        if setting.maximum is not None and value > setting.maximum:
            raise ValueError(i18n.t("error.max", maximum=setting.maximum))
        return value
    if setting.kind == "path":
        text = str(raw).strip().strip('"').strip("'")
        return str(pathlib.Path(text).expanduser()) if text else ""
    if setting.kind == "tz":
        text = str(raw).strip()
        if not text:
            raise ValueError(i18n.t("error.tz_blank"))
        return text
    if setting.kind == "choice":
        text = str(raw).strip()
        if setting.choices and text not in setting.choices:
            raise ValueError(i18n.t("error.choice", choices=", ".join(setting.choices)))
        return text
    if setting.kind in ("secret", "text_optional"):
        # Blank is a legitimate value here: it is how a stored token or chat id
        # is cleared. Stripping matters — these are pasted, and a trailing
        # newline in a bot token is a 404 nobody enjoys debugging.
        return str(raw).strip()
    text = str(raw).strip()
    if not text:
        raise ValueError(i18n.t("error.blank"))
    return text


def render(setting: Setting, value: Any, lang: Optional[str] = None) -> str:
    """Human-readable form of a stored value, for the menu and ``--list``.

    Defaults to English for the same reason as :func:`format_interval`: a
    piped ``--list`` must not change shape with the machine's locale.
    """
    lang = lang or i18n.FALLBACK
    if setting.kind == "bool":
        return i18n.t("value.on" if value else "value.off", lang)
    if setting.kind == "interval":
        return format_interval(int(value), lang)
    if setting.kind == "path":
        return str(value) if value else i18n.t("value.platform_default", lang)
    if setting.kind == "secret":
        return mask_secret(str(value)) if value else i18n.t("value.unset", lang)
    if setting.kind == "text_optional":
        return str(value) if value else i18n.t("value.unset", lang)
    if setting.kind == "choice":
        return i18n.language_labels().get(str(value), str(value)) \
            if setting.key == "language" else str(value)
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
    """Defaults, overlaid with the config file, plus secrets from the keychain.

    Unreadable or invalid values are dropped rather than fatal — a scheduled
    run must never die on a typo. A plaintext secret found in the file (only
    possible if someone hand-wrote one) is migrated into the keychain and
    scrubbed from disk on the spot; see :func:`_rescue_plaintext_secrets`.
    """
    cfg = dict(DEFAULTS)
    path = config_path()
    raw: Dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            raw = _migrate(parsed)
    leaked = {k: v for k, v in raw.items() if k in SECRET_KEYS and v}
    for key, value in raw.items():
        setting = BY_KEY.get(key)
        if setting is None:
            cfg[key] = value  # keep unknown keys so a newer config survives an older build
            continue
        try:
            cfg[key] = coerce(setting, value)
        except ValueError:
            pass
    for key in SECRET_KEYS:
        stored = secrets_store.get(key)
        if stored:
            cfg[key] = stored
    if leaked:
        _rescue_plaintext_secrets(cfg, raw, path)
    return cfg


def _rescue_plaintext_secrets(cfg: Dict[str, Any], raw: Dict[str, Any],
                              path: pathlib.Path) -> None:
    """Move a plaintext secret out of *path* and into the keychain.

    Only reachable when someone hand-wrote a token into ``config.json``: this
    program never puts one there. The value is kept in *cfg* either way, so a
    machine with no credential store keeps working for this run — it just gets
    rewritten without the secret, which is the point.
    """
    for key in SECRET_KEYS:
        if raw.get(key):
            secrets_store.set(key, str(raw[key]))
    with contextlib.suppress(OSError):
        save(cfg, path)


def save_secrets(cfg: Dict[str, Any]) -> Tuple[str, ...]:
    """Push every secret in *cfg* to the keychain. Returns the keys that failed.

    A non-empty result means "this machine has no credential store" (or it
    refused), and the caller should say so rather than pretending the token was
    saved — it is never silently written to the config file instead.
    """
    failed = [key for key in sorted(SECRET_KEYS)
              if not secrets_store.set(key, str(cfg.get(key, "") or "")) and cfg.get(key)]
    return tuple(failed)


def save(cfg: Dict[str, Any], path: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Atomically write the config, 0600, and route secrets to the keychain.

    Secret-kind settings are filtered out of the JSON payload entirely — the
    file never holds one, so neither does a backup, a sync folder or a
    screenshot of it.
    """
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {s.key: cfg.get(s.key, s.default) for s in SETTINGS if s.kind != "secret"}
    payload.update({k: v for k, v in cfg.items()
                    if k not in BY_KEY and k not in SECRET_KEYS})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(path)
    save_secrets(cfg)
    i18n.forget_stored_language()  # the file this cache mirrors just changed
    return path


def set_value(cfg: Dict[str, Any], key: str, raw: Any) -> Any:
    """Validate and store one setting in ``cfg`` (caller saves). Returns the stored value."""
    setting = BY_KEY.get(key)
    if setting is None:
        raise KeyError(key)
    cfg[key] = coerce(setting, raw)
    if key == "language":
        i18n.forget_stored_language()
    return cfg[key]


# ── Telegram credentials ─────────────────────────────────────────────────────

def telegram_credentials(cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """``(token, chat_id)``: the config file first, then the environment.

    What ``crw config`` stored wins deliberately. That is where the user sets
    these, and a stale ``TG_BOT_TOKEN`` — left in a shell profile, or baked
    into a launchd plist by an older install — must not keep notifying through
    a bot they already replaced. The environment is the fallback for a machine
    with no credential store, where the token cannot be stored at all. Each
    credential falls back on its own, so a half-set config still works, and an
    empty value on either side counts as unset.
    """
    cfg = cfg or {}
    return tuple(  # type: ignore[return-value]
        str(cfg.get(key, "") or "").strip() or os.environ.get(env, "").strip()
        for key, env in TELEGRAM_ENV.items()
    )


# ── export / import ──────────────────────────────────────────────────────────

def export_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The portable settings in *cfg* — i.e. every declared non-secret one.

    Secrets are filtered by ``kind``, never by a hand-kept key list, so a
    secret added to :data:`SETTINGS` cannot start leaking into export files
    because this function was not updated.
    """
    return {s.key: cfg.get(s.key, s.default) for s in SETTINGS if s.kind != "secret"}


def import_updates(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], Tuple[str, ...]]:
    """``(updates, skipped)`` for the settings in *raw*. All or nothing.

    Three things are never written, and each is reported rather than dropped
    silently:

    * **Secrets.** An export carries none, so a value here is either
      hand-written or a mask someone pasted — writing either would destroy the
      real token already stored on this machine.
    * **Unknown keys.** A key from a newer build is left alone instead of being
      stored back as an unvalidated blob.
    * **Anything invalid** raises before the first write, so a half-applied
      config can never be the outcome.
    """
    updates: Dict[str, Any] = {}
    skipped: List[str] = []
    for key, value in raw.items():
        setting = BY_KEY.get(key)
        if setting is None or key in SECRET_KEYS:
            skipped.append(key)
            continue
        updates[key] = coerce(setting, value)  # raises ValueError → caller aborts
    return updates, tuple(skipped)


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
