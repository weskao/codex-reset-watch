#!/usr/bin/env python3
"""Codex Reset Watch — resilient macOS/launchd monitor for codex-resets.com.

Runtime dependencies are stdlib-only; packaging/runtime are managed by uv tool.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import config as cfgmod
from . import filelock, i18n, scheduler, secrets_store, telegram_notify, ui

APP_NAME = "codex-reset-watch"
DEFAULT_API_BASE = cfgmod.DEFAULT_API_BASE
UTC = dt.timezone.utc


def home() -> pathlib.Path:
    return pathlib.Path.home()


def display_path(value: Any) -> str:
    """Render paths under the current home directory as ~/... for user-facing output.

    Filesystem operations must still use expanded absolute paths (launchd in particular
    does not expand '~'). This helper is display-only.
    """
    raw = os.fspath(value) if isinstance(value, os.PathLike) else str(value)
    home_s = str(home())
    if raw == home_s:
        return "~"
    prefix = home_s + os.sep
    if raw.startswith(prefix):
        return "~" + raw[len(home_s):]
    return raw


# Thin aliases kept for import-site stability: config.py owns the schema/paths now.
default_config_path = cfgmod.config_path
default_state_dir = cfgmod.state_dir
default_log_dir = cfgmod.log_dir
DEFAULT_CONFIG: Dict[str, Any] = cfgmod.DEFAULTS


@dataclass
class Event:
    event_id: str = ""
    timestamp: Optional[dt.datetime] = None
    event_type: str = ""
    message: str = ""
    source_url: str = ""

    @property
    def key(self) -> str:
        raw = "|".join([
            self.event_id,
            iso_utc(self.timestamp) if self.timestamp else "",
            self.event_type,
            self.message,
            self.source_url,
        ])
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


@dataclass
class Upcoming:
    timestamp: Optional[dt.datetime] = None
    timing_kind: str = "forecast_window"
    event_type: str = ""
    status: str = ""
    title: str = ""
    time_text: str = ""
    chance_percent: Optional[float] = None
    confidence: str = ""
    window_label: str = ""
    message: str = ""
    source_url: str = ""
    raw_field: str = ""

    @property
    def key(self) -> str:
        raw = "|".join([
            iso_utc(self.timestamp) if self.timestamp else "",
            self.timing_kind,
            self.event_type,
            self.status,
            self.title,
            self.time_text,
            str(self.chance_percent),
            self.confidence,
            self.window_label,
            self.message,
            self.source_url,
        ])
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:24]


@dataclass
class Snapshot:
    checked_at: dt.datetime
    latest: Optional[Event]
    upcoming: Optional[Upcoming]
    status_ok: bool
    resets_ok: bool
    status_error: str = ""
    resets_error: str = ""


def now_utc() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(value: Optional[dt.datetime]) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[dt.datetime]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:
            v /= 1000.0
        if v > 1e9:
            with contextlib.suppress(ValueError, OSError, OverflowError):
                return dt.datetime.fromtimestamp(v, tz=UTC)
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    if s.isdigit():
        return parse_time(int(s))
    normalized = s.replace("Z", "+00:00")
    with contextlib.suppress(ValueError):
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    # Common RFC3339-ish variants.
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        with contextlib.suppress(ValueError):
            parsed = dt.datetime.strptime(s, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    return None


def normalize_key(value: Any) -> str:
    """Normalize JSON field names so snake_case/kebab-case/camelCase match."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def first_value(d: Dict[str, Any], keys: Sequence[str]) -> Any:
    normalized = {normalize_key(k): v for k, v in d.items()}
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
        v = normalized.get(normalize_key(key))
        if v not in (None, ""):
            return v
    return None


def first_str(d: Dict[str, Any], keys: Sequence[str]) -> str:
    v = first_value(d, keys)
    if isinstance(v, str):
        return v.strip()
    if v is None:
        return ""
    return str(v)


def numeric(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            with contextlib.suppress(ValueError):
                return float(m.group(0))
    return None


def deep_first(d: Dict[str, Any], keys: Sequence[str], max_depth: int = 3) -> Tuple[Any, str]:
    wanted = {normalize_key(k) for k in keys}
    queue: List[Tuple[Dict[str, Any], str, int]] = [(d, "", 0)]
    while queue:
        cur, prefix, depth = queue.pop(0)
        for k, v in cur.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if normalize_key(k) in wanted and v not in (None, ""):
                return v, path
        if depth < max_depth:
            for k, v in cur.items():
                if isinstance(v, dict):
                    path = f"{prefix}.{k}" if prefix else str(k)
                    queue.append((v, path, depth + 1))
    return None, ""


def list_from_payload(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("resets", "events", "items", "results", "records"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return list_from_payload(data)
    return []


EVENT_TIME_KEYS = (
    "timestamp", "occurred_at", "occurredAt", "published_at", "publishedAt",
    "created_at", "createdAt", "reset_at", "resetAt", "posted_at", "postedAt",
    "announced_at", "announcedAt", "date", "time", "datetime",
)
SOURCE_URL_KEYS = (
    "source_url", "sourceUrl", "url", "x_url", "xUrl", "tweet_url", "tweetUrl",
    "post_url", "postUrl", "href", "link",
)


def x_snowflake_time(value: Any) -> Optional[dt.datetime]:
    """Decode an X/Twitter snowflake ID into its UTC creation timestamp.

    This is a fallback only. API-provided timestamps always win. X snowflakes encode
    milliseconds since 2010-11-04T01:42:54.657Z in the upper bits.
    """
    if value is None:
        return None
    m = re.search(r"(?:/status/)?(\d{15,22})", str(value))
    if not m:
        return None
    try:
        snowflake = int(m.group(1))
        millis = (snowflake >> 22) + 1288834974657
        parsed = dt.datetime.fromtimestamp(millis / 1000.0, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None
    # Reject obviously invalid IDs/times so an unrelated numeric identifier cannot
    # silently become an event timestamp.
    if parsed < dt.datetime(2010, 11, 4, tzinfo=UTC) or parsed > now_utc() + dt.timedelta(days=2):
        return None
    return parsed


def nested_source_url(d: Dict[str, Any]) -> str:
    direct = first_value(d, SOURCE_URL_KEYS)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    source = d.get("source")
    if isinstance(source, dict):
        value, _ = deep_first(source, SOURCE_URL_KEYS, max_depth=3)
        if isinstance(value, str):
            return value.strip()
    return ""


def event_from_dict(d: Dict[str, Any]) -> Event:
    # The upstream API has changed shape over time (snake_case/camelCase and nested
    # source objects). Search a few levels deep instead of assuming one fixed schema.
    ts_value, _ = deep_first(d, EVENT_TIME_KEYS, max_depth=3)
    ts = parse_time(ts_value)
    event_id = first_str(d, ["id", "event_id", "eventId", "reset_id", "resetId", "tweet_id", "tweetId", "post_id", "postId"])
    source_url = nested_source_url(d)
    if ts is None:
        # Public X post IDs carry their creation timestamp. This keeps historical
        # event time available even if codex-resets.com omits/renames its time field.
        ts = x_snowflake_time(source_url) or x_snowflake_time(event_id)
    return Event(
        event_id=event_id,
        timestamp=ts,
        event_type=first_str(d, ["type", "event_type", "eventType", "reset_type", "resetType", "kind", "category", "status"]),
        message=first_str(d, ["message", "text", "content", "body", "announcement", "description", "summary"]),
        source_url=source_url,
    )


def latest_event(status: Any, resets: Any) -> Optional[Event]:
    candidates: List[Event] = []
    if isinstance(status, dict):
        for key in ("latest_reset", "latest_event", "last_reset", "latest"):
            v = status.get(key)
            if isinstance(v, dict):
                candidates.append(event_from_dict(v))
        data = status.get("data")
        if isinstance(data, dict):
            for key in ("latest_reset", "latest_event", "last_reset", "latest"):
                v = data.get(key)
                if isinstance(v, dict):
                    candidates.append(event_from_dict(v))
    for item in list_from_payload(resets):
        if isinstance(item, dict):
            candidates.append(event_from_dict(item))
    candidates = [c for c in candidates if c.timestamp or c.message or c.event_id]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.timestamp or dt.datetime.min.replace(tzinfo=UTC), reverse=True)
    return candidates[0]


UPCOMING_CONTAINER_KEYS = (
    "forecast", "prediction", "upcoming", "upcoming_reset", "next_reset", "next",
    "outlook", "reset_forecast", "next_reset_forecast", "forecast_window",
    "scheduled_reset", "reset_schedule", "next_reset_schedule", "banked_reset",
)
EXACT_TIME_KEYS = (
    "next_reset_at", "scheduled_reset_at", "target_at", "estimated_at", "eta",
    "eta_at", "reset_at", "scheduled_at", "expected_at", "scheduled_for",
)
WINDOW_TIME_KEYS = (
    "window_end_at", "window_end", "until", "by", "end_at", "forecast_until", "deadline"
)
UPCOMING_TYPE_KEYS = ("type", "reset_type", "event_type", "kind", "category", "mode")
UPCOMING_STATUS_KEYS = ("status", "state", "schedule_status", "reset_status")
UPCOMING_TITLE_KEYS = ("title", "headline", "label", "name")
UPCOMING_TIME_TEXT_KEYS = (
    "time_text", "time_label", "schedule_text", "timing_text", "when", "eta_text",
    "scheduled_for_text",
)
UPCOMING_FLAG_KEYS = (
    "scheduled", "is_scheduled", "is_upcoming", "has_upcoming", "has_upcoming_reset",
    "pending", "announced",
)


def iter_candidate_containers(status: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    wanted = {normalize_key(k) for k in UPCOMING_CONTAINER_KEYS}

    def yield_from(parent: Dict[str, Any], prefix: str = "") -> Iterable[Tuple[str, Dict[str, Any]]]:
        for key, value in parent.items():
            if isinstance(value, dict) and normalize_key(key) in wanted:
                name = f"{prefix}.{key}" if prefix else str(key)
                yield name, value

    yield from yield_from(status)
    data = status.get("data")
    if isinstance(data, dict):
        yield from yield_from(data, "data")

    # Some API versions flatten forecast/schedule fields into the root object.
    root_signal_keys = (
        *EXACT_TIME_KEYS, *WINDOW_TIME_KEYS, *UPCOMING_FLAG_KEYS,
        "chance_percent", "probability", "confidence", "schedule_status",
        "upcoming_reset_type",
    )
    flat_keys = {normalize_key(k) for k in status.keys()}
    if flat_keys.intersection({normalize_key(k) for k in root_signal_keys}):
        yield "root", status


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "scheduled", "pending", "upcoming"}
    return False


def looks_like_future_signal(*values: Any) -> bool:
    text = " ".join(str(v) for v in values if v not in (None, "")).lower()
    phrases = (
        "scheduled", "upcoming", "pending", "time to be announced", "to be announced",
        "tba", "coming", "next reset", "will reset", "will give", "will credit",
        "will do", "lands", "landing", "scheduled reset",
    )
    return any(p in text for p in phrases)


def upcoming_from_status(status: Any, *, now: Optional[dt.datetime] = None) -> Optional[Upcoming]:
    if not isinstance(status, dict):
        return None
    now = now or now_utc()
    found: List[Upcoming] = []
    for container_name, d in iter_candidate_containers(status):
        ts: Optional[dt.datetime] = None
        raw_field = ""
        timing_kind = "forecast_window"

        val, nested_path = deep_first(d, EXACT_TIME_KEYS)
        maybe = parse_time(val)
        if maybe:
            ts, raw_field, timing_kind = maybe, f"{container_name}.{nested_path}", "announced_or_estimated_time"
        if ts is None:
            val, nested_path = deep_first(d, WINDOW_TIME_KEYS)
            maybe = parse_time(val)
            if maybe:
                ts, raw_field, timing_kind = maybe, f"{container_name}.{nested_path}", "forecast_window"
        if ts is not None and ts <= now:
            continue

        event_type_raw, _ = deep_first(d, UPCOMING_TYPE_KEYS)
        status_raw, _ = deep_first(d, UPCOMING_STATUS_KEYS)
        title_raw, _ = deep_first(d, UPCOMING_TITLE_KEYS)
        time_text_raw, _ = deep_first(d, UPCOMING_TIME_TEXT_KEYS)
        flag_raw, _ = deep_first(d, UPCOMING_FLAG_KEYS)
        chance_raw, _ = deep_first(d, ["chance_percent", "probability_percent", "chance", "probability", "percent", "likelihood_percent"])
        chance = numeric(chance_raw)
        if chance is not None and 0 <= chance <= 1:
            chance *= 100
        confidence_raw, _ = deep_first(d, ["confidence", "confidence_label", "level", "signal"])
        window_raw, _ = deep_first(d, ["window_label", "window", "horizon", "within", "time_window"])
        message_raw, _ = deep_first(d, ["message", "text", "reason", "summary", "hint", "source_text", "description", "announcement"])
        source_url = nested_source_url(d)

        event_type = str(event_type_raw).strip() if event_type_raw is not None else ""
        status_text = str(status_raw).strip() if status_raw is not None else ""
        title = str(title_raw).strip() if title_raw is not None else ""
        time_text = str(time_text_raw).strip() if time_text_raw is not None else ""
        message = str(message_raw).strip() if message_raw is not None else ""

        # A scheduled reset is useful information even when the upstream tracker has
        # not published an exact timestamp yet (e.g. "Time to be announced").
        if ts is None:
            meaningful = any((event_type, status_text, title, time_text, message, source_url, chance is not None))
            future_signal = boolish(flag_raw) or looks_like_future_signal(
                container_name, event_type, status_text, title, time_text, message
            )
            if not meaningful or not future_signal:
                continue
            timing_kind = "scheduled_tba"
            if not time_text:
                time_text = "Time to be announced"

        found.append(Upcoming(
            timestamp=ts,
            timing_kind=timing_kind,
            event_type=event_type,
            status=status_text,
            title=title,
            time_text=time_text,
            chance_percent=chance,
            confidence=str(confidence_raw).strip() if confidence_raw is not None else "",
            window_label=str(window_raw).strip() if window_raw is not None and not isinstance(window_raw, dict) else "",
            message=message,
            source_url=source_url,
            raw_field=raw_field,
        ))
    if not found:
        return None

    # Exact/forecast times sort before TBA signals. If all are TBA, preserve the
    # API/container order because that is the tracker's own priority.
    found.sort(key=lambda u: (u.timestamp is None, u.timestamp or dt.datetime.max.replace(tzinfo=UTC)))
    return found[0]


class RotatingJsonl:
    def __init__(self, path: pathlib.Path, max_bytes: int, backups: int):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
        except OSError:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        with contextlib.suppress(OSError):
            oldest.unlink()
        for i in range(self.backups - 1, 0, -1):
            src = self.path.with_suffix(self.path.suffix + f".{i}")
            dst = self.path.with_suffix(self.path.suffix + f".{i+1}")
            if src.exists():
                with contextlib.suppress(OSError):
                    src.replace(dst)
        with contextlib.suppress(OSError):
            self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))

    def write(self, obj: Dict[str, Any]) -> None:
        self._rotate()
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class Logger:
    def __init__(self, cfg: Dict[str, Any]):
        self.log_dir = default_log_dir(cfg)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = int(cfg.get("max_log_bytes", DEFAULT_CONFIG["max_log_bytes"]))
        backups = int(cfg.get("log_backups", DEFAULT_CONFIG["log_backups"]))
        self.events = RotatingJsonl(self.log_dir / "events.jsonl", max_bytes, backups)
        self.api = RotatingJsonl(self.log_dir / "api.jsonl", max_bytes, backups)

    def event(self, level: str, name: str, **fields: Any) -> None:
        self.events.write({"ts": iso_utc(now_utc()), "level": level, "event": name, **fields})

    def api_response(self, endpoint: str, ok: bool, payload: Any = None, error: str = "", http_status: Optional[int] = None) -> None:
        self.api.write({
            "ts": iso_utc(now_utc()), "endpoint": endpoint, "ok": ok, "http_status": http_status,
            "error": error, "payload": payload,
        })


class StateStore:
    def __init__(self, state_dir: Optional[pathlib.Path] = None, cfg: Optional[Dict[str, Any]] = None):
        self.dir = state_dir or default_state_dir(cfg)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"
        self.lock_path = self.dir / "run.lock"

    def load(self) -> Dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def lock(self, blocking: bool = False):
        return filelock.lock(self.lock_path, blocking=blocking)


class APIClient:
    def __init__(self, cfg: Dict[str, Any], logger: Optional[Logger] = None):
        self.cfg = cfg
        self.logger = logger
        self.base = str(os.environ.get("CRW_API_BASE", cfg.get("api_base", DEFAULT_API_BASE))).rstrip("/")
        self.timeout = float(cfg.get("request_timeout_seconds", 15))
        self.retries = max(1, int(cfg.get("request_retries", 3)))
        self.ua = str(cfg.get("user_agent", DEFAULT_CONFIG["user_agent"]))

    def get_json(self, path: str, *, optional: bool = False) -> Tuple[Optional[Any], str]:
        url = path if path.startswith("http") else self.base + path
        error = ""
        for attempt in range(1, self.retries + 1):
            status_code: Optional[int] = None
            body = ""
            try:
                req = urllib.request.Request(url, headers={
                    "Accept": "application/json",
                    "User-Agent": self.ua,
                    "Cache-Control": "no-cache",
                })
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status_code = int(getattr(resp, "status", 200))
                    raw = resp.read(1024 * 1024)
                    payload = json.loads(raw.decode("utf-8"))
                    if self.logger:
                        self.logger.api_response(url, True, payload=payload, http_status=status_code)
                    return payload, ""
            except urllib.error.HTTPError as e:
                status_code = e.code
                with contextlib.suppress(Exception):
                    body = e.read(2048).decode("utf-8", "replace")
                error = f"HTTP {e.code}: {body[:500]}".strip()
                retryable = e.code == 429 or 500 <= e.code < 600
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                error = f"{type(e).__name__}: {e}"
                retryable = True
            if attempt < self.retries and retryable:
                time.sleep(min(4, 2 ** (attempt - 1)))
                continue
            break
        if self.logger:
            self.logger.api_response(url, False, error=error, http_status=status_code)
        if optional:
            return None, error
        return None, error

    def snapshot(self) -> Snapshot:
        checked = now_utc()
        status_path = str(self.cfg.get("status_path", DEFAULT_CONFIG["status_path"]))
        resets_path = str(self.cfg.get("resets_path", DEFAULT_CONFIG["resets_path"]))
        status, status_error = self.get_json(status_path)
        resets, resets_error = self.get_json(resets_path, optional=True)
        latest = latest_event(status, resets)
        upcoming = upcoming_from_status(status, now=checked)
        return Snapshot(
            checked_at=checked,
            latest=latest,
            upcoming=upcoming,
            status_ok=status is not None,
            resets_ok=resets is not None,
            status_error=status_error,
            resets_error=resets_error,
        )


load_config = cfgmod.load


def fmt_local(value: Optional[dt.datetime], cfg: Optional[Dict[str, Any]] = None) -> str:
    if value is None:
        return "—"
    local = value.astimezone(cfgmod.tzinfo_for(cfg))
    return local.strftime(f"%Y-%m-%d %H:%M {cfgmod.tz_label(cfg)}")


def fmt_remaining(target: Optional[dt.datetime], now: Optional[dt.datetime] = None) -> str:
    if target is None:
        return "—"
    now = now or now_utc()
    seconds = max(0, int((target - now).total_seconds()))
    total_minutes = seconds // 60
    days, rem_min = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_min, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days} Day" + ("s" if days != 1 else ""))
    if hours or days:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    return " ".join(parts)


def safe_text(s: str, max_len: int = 900) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= max_len else s[:max_len - 1] + "…"


def upcoming_status_label(upcoming: Upcoming) -> str:
    if upcoming.title:
        return upcoming.title
    parts: List[str] = []
    if upcoming.event_type:
        event_type = upcoming.event_type.replace("_", " ").strip()
        parts.append(event_type[:1].upper() + event_type[1:])
    if upcoming.status:
        status = upcoming.status.replace("_", " ").strip()
        if not parts or normalize_key(status) not in normalize_key(" ".join(parts)):
            parts.append(status)
    if parts:
        label = " ".join(parts)
        if "reset" not in label.lower() and upcoming.event_type:
            label = label.split()[0] + " reset" + (" " + " ".join(label.split()[1:]) if len(label.split()) > 1 else "")
        return label
    return "Reset scheduled" if upcoming.timestamp is None else "Upcoming reset"


def append_upcoming_links(lines: List[str], upcoming: Upcoming) -> None:
    if upcoming.source_url:
        lines.append(f"🔗 公告：{upcoming.source_url}")
    tracker = DEFAULT_API_BASE + "/"
    if upcoming.source_url.rstrip("/") != tracker.rstrip("/"):
        lines.append(f"🌐 Codex Resets：{tracker}")


def format_manual(snapshot: Snapshot, cfg: Optional[Dict[str, Any]] = None, *, paint: Optional[ui.Paint] = None) -> str:
    p = paint or ui.Paint(False)
    divider = f"{p.frame}──────────────{p.reset}"

    def heading(colour: str, text: str) -> str:
        return f"{colour}{p.bold}{text}{p.reset}"

    lines = ["🔎 Codex Reset 即時查詢", "━━━━━━━━━━━━━━"]
    lines.append(f"🛰️ 檢查時間：{fmt_local(snapshot.checked_at, cfg)}")
    if snapshot.latest:
        lines += [
            "",
            divider,
            heading(p.ok, "✅ 最近一次 Reset"),
            f"🕒 時間：{fmt_local(snapshot.latest.timestamp, cfg)}",
            f"🏷️ 類型：{snapshot.latest.event_type or '未標示'}",
        ]
        if snapshot.latest.message:
            lines.append(f"📝 公告：{safe_text(snapshot.latest.message)}")
        if snapshot.latest.source_url:
            lines.append(f"🔗 來源：{snapshot.latest.source_url}")
    else:
        lines += ["", "ℹ️ 最近一次 Reset：API 未提供可解析資料"]
    if snapshot.upcoming:
        u = snapshot.upcoming
        lines += ["", divider, heading(p.warn, "🔮 尚未發生的 Reset 訊號")]
        lines.append(f"🚦 狀態：{upcoming_status_label(u)}")
        if u.event_type:
            lines.append(f"🏷️ 類型：{u.event_type}")
        if u.timestamp is None:
            lines.append(f"🕒 時間：尚未公布（{u.time_text or 'Time to be announced'}）")
        else:
            label = "預告/估計時間" if u.timing_kind == "announced_or_estimated_time" else "預測窗口截止"
            lines.append(f"🕒 {label}：{fmt_local(u.timestamp, cfg)}")
            lines.append(f"⏳ 距離現在：{fmt_remaining(u.timestamp, snapshot.checked_at)}")
        if u.chance_percent is not None:
            lines.append(f"🎯 機率：{u.chance_percent:g}%")
        if u.confidence:
            lines.append(f"📊 信心：{u.confidence}")
        if u.window_label:
            lines.append(f"🪟 Window：{u.window_label}")
        if u.message:
            lines.append(f"💬 訊號：{safe_text(u.message)}")
        append_upcoming_links(lines, u)
        lines.append("⚠️ 此為第三方公開追蹤/預測訊號，不等同 OpenAI 對個人帳戶的保證時間。")
    else:
        lines += ["", "🌙 尚未偵測到未來 Reset 訊號。"]
    if snapshot.status_error:
        lines += ["", f"⚠️ status API：{safe_text(snapshot.status_error, 300)}"]
    if snapshot.resets_error:
        lines.append(f"⚠️ resets API（非必要）：{safe_text(snapshot.resets_error, 300)}")
    return "\n".join(lines)


def format_upcoming_notice(upcoming: Upcoming, checked_at: dt.datetime, cfg: Optional[Dict[str, Any]] = None) -> str:
    lines = [
        "🚨 Codex Reset Watch",
        "━━━━━━━━━━━━━━",
        "🔮 發現尚未發生的 Reset 訊號",
        f"📌 狀態：{upcoming_status_label(upcoming)}",
    ]
    if upcoming.event_type:
        lines.append(f"🏷️ 類型：{upcoming.event_type}")
    if upcoming.timestamp is None:
        lines.append(f"🕒 時間：尚未公布（{upcoming.time_text or 'Time to be announced'}）")
    else:
        label = "預告/估計時間" if upcoming.timing_kind == "announced_or_estimated_time" else "預測窗口截止"
        lines.append(f"🕒 {label}：{fmt_local(upcoming.timestamp, cfg)}")
        lines.append(f"⏳ 距離現在：{fmt_remaining(upcoming.timestamp, checked_at)}")
    if upcoming.chance_percent is not None:
        lines.append(f"🎯 機率：{upcoming.chance_percent:g}%")
    if upcoming.confidence:
        lines.append(f"📊 信心：{upcoming.confidence}")
    if upcoming.window_label:
        lines.append(f"🪟 Window：{upcoming.window_label}")
    if upcoming.message:
        lines.append(f"💬 訊號：{safe_text(upcoming.message)}")
    append_upcoming_links(lines, upcoming)
    lines += [
        f"🛰️ 檢查時間：{fmt_local(checked_at, cfg)}",
        "⚠️ 第三方公開追蹤/預測，不代表你的個人 Codex 額度一定會在該時間重置。",
    ]
    return "\n".join(lines)


def format_new_event_notice(event: Event, checked_at: dt.datetime, cfg: Optional[Dict[str, Any]] = None) -> str:
    lines = [
        "✅ Codex Reset 更新",
        "━━━━━━━━━━━━━━",
        "🎉 偵測到新的公開 Reset 事件/公告",
        f"🕒 時間：{fmt_local(event.timestamp, cfg)}",
        f"🏷️ 類型：{event.event_type or '未標示'}",
    ]
    if event.message:
        lines.append(f"📝 公告：{safe_text(event.message)}")
    if event.source_url:
        lines.append(f"🔗 來源：{event.source_url}")
    lines.append(f"🛰️ 檢查時間：{fmt_local(checked_at, cfg)}")
    return "\n".join(lines)


def send_telegram(cfg: Dict[str, Any], message: str, logger: Logger) -> bool:
    # The keychain-backed config first, then the environment — see
    # config.telegram_credentials. Neither value is ever logged.
    token, chat_id = cfgmod.telegram_credentials(cfg)
    if not token or not chat_id:
        logger.event("ERROR", "telegram_credentials_missing")
        return False
    ok = telegram_notify.send_telegram(token, chat_id, message)
    logger.event("INFO" if ok else "ERROR", "telegram_send", ok=ok)
    return ok


def snapshot_to_log(snapshot: Snapshot) -> Dict[str, Any]:
    def event_dict(e: Optional[Event]) -> Optional[Dict[str, Any]]:
        if not e:
            return None
        d = asdict(e)
        d["timestamp"] = iso_utc(e.timestamp)
        d["key"] = e.key
        return d
    def upcoming_dict(u: Optional[Upcoming]) -> Optional[Dict[str, Any]]:
        if not u:
            return None
        d = asdict(u)
        d["timestamp"] = iso_utc(u.timestamp)
        d["key"] = u.key
        return d
    return {
        "checked_at": iso_utc(snapshot.checked_at),
        "status_ok": snapshot.status_ok,
        "resets_ok": snapshot.resets_ok,
        "status_error": snapshot.status_error,
        "resets_error": snapshot.resets_error,
        "latest": event_dict(snapshot.latest),
        "upcoming": upcoming_dict(snapshot.upcoming),
    }


def is_recent_event(event: Event, checked_at: dt.datetime, hours: int = 12) -> bool:
    return bool(event.timestamp and dt.timedelta(0) <= checked_at - event.timestamp <= dt.timedelta(hours=hours))


def run_check(mode: str, *, notify: bool, force_daily: bool = False) -> int:
    cfg = load_config()
    logger = Logger(cfg)
    store = StateStore(cfg=cfg)
    with store.lock(blocking=(mode in ("manual", "daily"))) as acquired:
        if not acquired:
            logger.event("INFO", "skipped_locked", mode=mode)
            if mode == "manual":
                print("ℹ️ 另一個 Codex Reset Watch 檢查正在執行，請稍後再試。")
            return 0

        state = store.load()
        local_now = now_utc().astimezone(cfgmod.tzinfo_for(cfg))
        today = local_now.date().isoformat()
        if mode == "daily" and not force_daily:
            if not bool(cfg.get("daily_enabled", True)):
                logger.event("INFO", "daily_disabled")
                return 0
            daily_hour, daily_minute = cfgmod.daily_hm(cfg)
            due = local_now.hour > daily_hour or (
                local_now.hour == daily_hour and local_now.minute >= daily_minute
            )
            if not due:
                logger.event("INFO", "daily_not_due", local_time=local_now.isoformat())
                return 0
            if state.get("last_daily_date") == today:
                logger.event("INFO", "daily_already_done", date=today)
                return 0

        client = APIClient(cfg, logger)
        snapshot = client.snapshot()
        logger.event("INFO", "snapshot", mode=mode, **snapshot_to_log(snapshot))

        if not snapshot.status_ok:
            if mode == "manual":
                print(format_manual(snapshot, cfg, paint=ui.Paint(ui.colour_enabled())))
                return 1
            logger.event("WARNING", "scheduled_status_api_failed", mode=mode, error=snapshot.status_error)
            if mode == "daily":
                # Do not mark daily complete on network/API failure; the 2-hour monitor or a reload can retry.
                pass
            return 0

        prev_latest = state.get("latest_event_key", "")
        prev_upcoming = state.get("upcoming_key", "")
        is_first = not state.get("initialized_at")
        messages: List[str] = []

        if mode == "manual":
            text = format_manual(snapshot, cfg)
            print(format_manual(snapshot, cfg, paint=ui.Paint(ui.colour_enabled())))
            if notify:
                send_telegram(cfg, text, logger)
        else:
            if snapshot.latest and bool(cfg.get("notify_new_reset_events", True)):
                changed = snapshot.latest.key != prev_latest
                # First run establishes a baseline; only notify an already-existing event if very recent.
                if changed and (not is_first or is_recent_event(snapshot.latest, snapshot.checked_at)):
                    messages.append(format_new_event_notice(snapshot.latest, snapshot.checked_at, cfg))
            if snapshot.upcoming and bool(cfg.get("notify_upcoming_reset", True)):
                changed = snapshot.upcoming.key != prev_upcoming
                notify_unchanged = bool(cfg.get("monitor_notify_when_unchanged" if mode == "monitor" else "daily_notify_when_unchanged", False))
                if changed or notify_unchanged:
                    messages.append(format_upcoming_notice(snapshot.upcoming, snapshot.checked_at, cfg))
            if notify:
                for message in messages:
                    send_telegram(cfg, message, logger)

        state["initialized_at"] = state.get("initialized_at") or iso_utc(snapshot.checked_at)
        state["last_check_at"] = iso_utc(snapshot.checked_at)
        state["latest_event_key"] = snapshot.latest.key if snapshot.latest else ""
        state["latest_event_at"] = iso_utc(snapshot.latest.timestamp) if snapshot.latest else ""
        state["upcoming_key"] = snapshot.upcoming.key if snapshot.upcoming else ""
        state["upcoming_at"] = iso_utc(snapshot.upcoming.timestamp) if snapshot.upcoming else ""
        if mode == "daily":
            state["last_daily_date"] = today
            state["last_daily_at"] = iso_utc(snapshot.checked_at)
        store.save(state)
        return 0


def _credential_source(cfg: Dict[str, Any], key: str) -> str:
    """Where a credential came from, for ``doctor``. Never prints the value."""
    if not str(cfg.get(key, "") or "").strip():
        return "environment"
    return secrets_store.backend_label() if key in cfgmod.SECRET_KEYS else "config"


def doctor() -> int:
    cfg = load_config()
    logger = Logger(cfg)
    checks: List[Tuple[str, bool, str]] = []
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", py_ok, f"{sys.version.split()[0]} ({display_path(sys.executable)})"))
    cfg_path = default_config_path()
    checks.append(("Config", cfg_path.exists(), display_path(cfg_path)))
    token, chat_id = cfgmod.telegram_credentials(cfg)
    checks.append(("Secret store", secrets_store.available(), secrets_store.backend_label()))
    checks.append(("Telegram bot token", bool(token),
                   f"{cfgmod.mask_secret(token)} ({_credential_source(cfg, 'telegram_bot_token')})"
                   if token else "unset"))
    checks.append(("Telegram chat id", bool(chat_id),
                   f"{chat_id} ({_credential_source(cfg, 'telegram_chat_id')})" if chat_id else "unset"))
    client = APIClient(cfg, logger)
    status, err = client.get_json(str(cfg.get("status_path", "/api/v1/status")))
    checks.append(("Codex Resets status API", status is not None, "OK" if status is not None else err))
    try:
        backend = scheduler.current_backend()
        checks.append((f"Scheduler ({backend})", True, ui.summary_line(cfg, paint=ui.Paint(False))))
    except ValueError as exc:
        checks.append(("Scheduler", False, str(exc)))
    # The scheduler job stores an absolute path. A bare `uv tool install` rewrites
    # uv's receipt and deletes the entrypoints it recorded, so this is the one way
    # the jobs go silently dead — they keep "existing" while invoking nothing.
    cli = scheduler.cli_path()
    checks.append(("Scheduled CLI", cli.exists(),
                   display_path(cli) if cli.exists()
                   else f"missing: {display_path(cli)} — re-run the installer to repair"))
    print("🩺 Codex Reset Watch doctor\n")
    failed = False
    for name, ok, detail in checks:
        print(f"{'✅' if ok else '❌'} {name}: {detail}")
        failed = failed or not ok
    print(f"\n📁 Logs: {display_path(default_log_dir(cfg))}")
    print(f"💾 State: {display_path(default_state_dir(cfg) / 'state.json')}")
    return 1 if failed else 0


def tail_logs(n: int) -> int:
    cfg = load_config()
    path = default_log_dir(cfg) / "events.jsonl"
    if not path.exists():
        print(f"尚無 log：{display_path(path)}")
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    for line in lines:
        print(line)
    return 0


def apply_schedule_cmd() -> int:
    cfg = load_config()
    try:
        backend, jobs = scheduler.apply(cfg=cfg)
    except Exception as exc:  # noqa: BLE001 - report, don't crash a CLI invocation
        print(f"❌ 排程套用失敗（{type(exc).__name__}: {exc}）")
        return 1
    listed = "、".join(jobs) if jobs else "（全部關閉）"
    print(f"✅ 已重新套用 {backend} 排程：{listed}")
    print(ui.summary_line(cfg, paint=ui.Paint(False)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codex-reset-watch", description="Monitor codex-resets.com and notify via Telegram.")
    p.add_argument("--version", "-V", action="version", version=f"%(prog)s {ui.package_version()}")
    sub = p.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", aliases=["update"], help="立即查詢、更新狀態、輸出到 Terminal，預設同時 Telegram 通知")
    check.add_argument("--no-notify", action="store_true", help="只顯示，不傳 Telegram")
    mon = sub.add_parser("monitor", help="排程器背景掃描（間隔由 `crw config` 設定，只通知新資訊）")
    mon.add_argument("--no-notify", action="store_true")
    daily = sub.add_parser("daily", help="每日 catch-up 檢查（時間由 `crw config` 設定）")
    daily.add_argument("--force", action="store_true", help="忽略當日時間 gate，用於測試")
    daily.add_argument("--no-notify", action="store_true")
    sub.add_parser("doctor", help="檢查 Python/API/Telegram/排程設定")
    logs = sub.add_parser("logs", help="顯示最近事件 logs")
    logs.add_argument("-n", "--lines", type=int, default=30)
    cfgp = sub.add_parser("config", help="互動式設定選單（排程時間、掃描間隔、通知、路徑…）")
    cfgp.add_argument("--list", action="store_true", help="列出目前設定後結束，不進入選單")
    cfgp.add_argument("--set", action="append", metavar="KEY=VALUE",
                      help="非互動式修改一項設定，可重複；影響排程的鍵會自動重新套用")
    cfgp.add_argument("--apply-schedule", action="store_true", help="搭配 --set 時，強制重新套用 OS 排程")
    cfgp.add_argument("--export", metavar="FILE", help="把可攜設定寫成 JSON（`-` 代表標準輸出）；機密不會匯出")
    cfgp.add_argument("--import", dest="import_file", metavar="FILE", help="從 JSON 匯入設定（全有全無）")
    sub.add_parser("apply-schedule", help="依目前設定重新套用 OS 排程（launchd/systemd/schtasks）")
    return p


# ── `--`-optional syntax ─────────────────────────────────────────────────────
# Every subcommand may be written `--config` as well as `config`, and every
# flag `list` as well as `--list`. One pure rewrite of argv before argparse
# sees it, rather than a second parser or a pile of aliases.

SUBCOMMANDS: Tuple[str, ...] = (
    "check", "update", "monitor", "daily", "doctor", "logs", "config", "apply-schedule",
)

#: Bare words that mean a flag, per subcommand. A name is only rewritten after
#: the subcommand that actually declares it, so `check force` stays a
#: positional argparse can complain about instead of a flag we invented.
SUBCOMMAND_FLAGS: Dict[str, Tuple[str, ...]] = {
    "check": ("no-notify",),
    "update": ("no-notify",),
    "monitor": ("no-notify",),
    "daily": ("force", "no-notify"),
    "logs": ("lines",),
    "config": ("list", "set", "apply-schedule", "export", "import"),
    "doctor": (),
    "apply-schedule": (),
}

#: Flags whose next token is their value, so it is never itself rewritten —
#: `config set export` sets a key literally called "export".
VALUE_FLAGS = frozenset({"set", "lines", "export", "import"})


def _normalize_argv(argv: Sequence[str]) -> List[str]:
    """Rewrite *argv* so `--` is optional on subcommands and on their flags.

    Pure and total: a token it does not recognise passes through untouched, so
    argparse still produces its own error for a genuine typo. Everything after
    a bare ``--`` is left exactly as typed.
    """
    out: List[str] = []
    command: Optional[str] = None
    take_value = False
    for index, token in enumerate(argv):
        if token == "--":  # POSIX end-of-options: the rest is verbatim
            out.extend(argv[index:])
            return out
        if take_value:
            out.append(token)
            take_value = False
            continue
        bare = token.lstrip("-")
        if token.startswith("-"):
            if command is None and bare in SUBCOMMANDS:
                command = bare
                out.append(bare)  # `--config` → `config`
                continue
            out.append(token)
            take_value = bare in VALUE_FLAGS or bare == "n"
            continue
        if command is None and token in SUBCOMMANDS:
            command = token
            out.append(token)
            continue
        if command is not None and token in SUBCOMMAND_FLAGS.get(command, ()):
            out.append(f"--{token}")
            take_value = token in VALUE_FLAGS
            continue
        out.append(token)
    return out


def config_cmd(args: argparse.Namespace) -> int:
    cfg = load_config()
    lang = i18n.current_language(cfg)
    if args.export:
        ok, message = ui.export_settings(cfg, args.export, lang)
        print(f"{'✅' if ok else '❌'} {message}")
        if ok:
            print("⚠️  " + i18n.t("menu.export_secrets", lang,
                                  keys=", ".join(sorted(cfgmod.SECRET_KEYS))))
        return 0 if ok else 1
    if args.import_file:
        ok, message = ui.import_settings(cfg, args.import_file, lang)
        print(f"{'✅' if ok else '❌'} {message}")
        if not ok:
            return 1
        cfgmod.save(cfg)
        return apply_schedule_cmd()
    if args.list:
        print(ui.render_settings(cfg))
        return 0
    if args.set:
        schedule_dirty = False
        for item in args.set:
            if "=" not in item:
                print("❌ " + i18n.t("menu.set_format", lang, item=item))
                return 2
            key, _, value = item.partition("=")
            key = key.strip()
            try:
                cfgmod.set_value(cfg, key, value)
            except KeyError:
                print("❌ " + i18n.t("menu.set_unknown_key", lang, key=key))
                return 2
            except ValueError as exc:
                print(f"❌ {key}：{exc}")
                return 2
            schedule_dirty = schedule_dirty or key in cfgmod.SCHEDULE_KEYS
        cfgmod.save(cfg)
        unstored = cfgmod.save_secrets(cfg)
        print("✅ " + i18n.t("menu.set_done", lang, count=len(args.set),
                            path=cfgmod.config_path()))
        if unstored:
            print(f"⚠️  {', '.join(unstored)}: no OS credential store on this machine — "
                  f"set TG_BOT_TOKEN in the environment instead")
        if schedule_dirty or args.apply_schedule:
            return apply_schedule_cmd()
        return 0
    return ui.config_menu()


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(_normalize_argv(raw))
    try:
        if args.command in ("check", "update"):
            return run_check("manual", notify=not args.no_notify)
        if args.command == "monitor":
            return run_check("monitor", notify=not args.no_notify)
        if args.command == "daily":
            return run_check("daily", notify=not args.no_notify, force_daily=args.force)
        if args.command == "doctor":
            return doctor()
        if args.command == "logs":
            return tail_logs(max(1, args.lines))
        if args.command == "config":
            return config_cmd(args)
        if args.command == "apply-schedule":
            return apply_schedule_cmd()
        return 2
    except KeyboardInterrupt:
        # Every subcommand — not just the `config` menu, which handles its own
        # Ctrl-C in-place — must exit on Ctrl-C rather than dump a traceback;
        # 130 is the conventional shell exit code for SIGINT.
        paint = ui.Paint(ui.colour_enabled(sys.stderr))
        print(f"\n{paint.warn}{i18n.t('cli.cancelled', i18n.current_language())}{paint.reset}",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
