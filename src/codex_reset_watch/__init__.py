#!/usr/bin/env python3
"""Codex Reset Watch — resilient macOS/launchd monitor for codex-resets.com.

Runtime dependencies are stdlib-only; packaging/runtime are managed by uv tool.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

APP_NAME = "codex-reset-watch"
DEFAULT_API_BASE = "https://codex-resets.com"
TZ8 = dt.timezone(dt.timedelta(hours=8), name="UTC+8")
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


def default_config_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CRW_CONFIG", home() / "Library/Application Support/codex-reset-watch/config.json"))


def default_state_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CRW_STATE_DIR", home() / "Library/Application Support/codex-reset-watch"))


def default_log_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CRW_LOG_DIR", home() / "Library/Logs/codex-reset-watch"))


DEFAULT_CONFIG: Dict[str, Any] = {
    "api_base": DEFAULT_API_BASE,
    "status_path": "/api/v1/status",
    "resets_path": "/api/v1/resets?limit=20&order=desc",
    "timezone_label": "UTC+8",
    "telegram_sender": "~/.claude/scripts/tg-send.sh",
    "request_timeout_seconds": 15,
    "request_retries": 3,
    "daily_hour": 10,
    "daily_minute": 0,
    "daily_notify_when_unchanged": False,
    "monitor_notify_when_unchanged": False,
    "notify_new_reset_events": True,
    "notify_upcoming_reset": True,
    "max_log_bytes": 2 * 1024 * 1024,
    "log_backups": 3,
    "user_agent": "codex-reset-watch/1.0 (+https://codex-resets.com/api/docs)",
}


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


def first_value(d: Dict[str, Any], keys: Sequence[str]) -> Any:
    lowered = {str(k).lower(): v for k, v in d.items()}
    for key in keys:
        if key in d and d[key] not in (None, ""):
            return d[key]
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
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
    wanted = {k.lower() for k in keys}
    queue: List[Tuple[Dict[str, Any], str, int]] = [(d, "", 0)]
    while queue:
        cur, prefix, depth = queue.pop(0)
        for k, v in cur.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if str(k).lower() in wanted and v not in (None, ""):
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
    "forecast", "prediction", "upcoming", "upcoming_reset", "next_reset", "next", "outlook", "reset_forecast", "next_reset_forecast", "forecast_window"
)
EXACT_TIME_KEYS = (
    "next_reset_at", "scheduled_reset_at", "target_at", "estimated_at", "eta", "eta_at", "reset_at", "scheduled_at", "expected_at"
)
WINDOW_TIME_KEYS = (
    "window_end_at", "window_end", "until", "by", "end_at", "forecast_until", "deadline"
)


def iter_candidate_containers(status: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for key in UPCOMING_CONTAINER_KEYS:
        v = status.get(key)
        if isinstance(v, dict):
            yield key, v
    data = status.get("data")
    if isinstance(data, dict):
        for key in UPCOMING_CONTAINER_KEYS:
            v = data.get(key)
            if isinstance(v, dict):
                yield f"data.{key}", v
    # Some APIs flatten the next-reset forecast into top-level fields.
    flat_keys = set(k.lower() for k in status.keys())
    if flat_keys.intersection(set(EXACT_TIME_KEYS + WINDOW_TIME_KEYS + ("chance_percent", "probability", "confidence"))):
        yield "root", status


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
        if ts is None or ts <= now:
            continue
        chance_raw, _ = deep_first(d, ["chance_percent", "probability_percent", "chance", "probability", "percent", "likelihood_percent"])
        chance = numeric(chance_raw)
        if chance is not None and 0 <= chance <= 1:
            chance *= 100
        confidence_raw, _ = deep_first(d, ["confidence", "confidence_label", "level", "signal"])
        window_raw, _ = deep_first(d, ["window_label", "window", "horizon", "within", "time_window"])
        message_raw, _ = deep_first(d, ["message", "text", "reason", "summary", "hint", "source_text", "description"])
        source_raw, _ = deep_first(d, ["source_url", "url", "x_url", "tweet_url", "post_url"])
        found.append(Upcoming(
            timestamp=ts,
            timing_kind=timing_kind,
            chance_percent=chance,
            confidence=str(confidence_raw).strip() if confidence_raw is not None else "",
            window_label=str(window_raw).strip() if window_raw is not None and not isinstance(window_raw, dict) else "",
            message=str(message_raw).strip() if message_raw is not None else "",
            source_url=str(source_raw).strip() if source_raw is not None else "",
            raw_field=raw_field,
        ))
    if not found:
        return None
    found.sort(key=lambda u: u.timestamp or dt.datetime.max.replace(tzinfo=UTC))
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
        self.log_dir = default_log_dir()
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
    def __init__(self, state_dir: Optional[pathlib.Path] = None):
        self.dir = state_dir or default_state_dir()
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

    @contextlib.contextmanager
    def lock(self, blocking: bool = False):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


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


def load_config() -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = default_config_path()
    if path.exists():
        with contextlib.suppress(Exception):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg.update(raw)
    # Environment is intentionally limited to operational overrides useful for tests/recovery.
    if os.environ.get("CRW_TG_SEND"):
        cfg["telegram_sender"] = os.environ["CRW_TG_SEND"]
    return cfg


def fmt_local(value: Optional[dt.datetime]) -> str:
    if value is None:
        return "—"
    local = value.astimezone(TZ8)
    return local.strftime("%Y-%m-%d %H:%M UTC+8")


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


def format_manual(snapshot: Snapshot) -> str:
    lines = ["🔎 Codex Reset 即時查詢", "━━━━━━━━━━━━━━"]
    lines.append(f"🛰️ 檢查時間：{fmt_local(snapshot.checked_at)}")
    if snapshot.latest:
        lines += [
            "",
            "✅ 最近一次 Reset",
            f"🕒 時間：{fmt_local(snapshot.latest.timestamp)}",
            f"🏷️ 類型：{snapshot.latest.event_type or '未標示'}",
        ]
        if snapshot.latest.message:
            lines.append(f"📝 公告：{safe_text(snapshot.latest.message)}")
        if snapshot.latest.source_url:
            lines.append(f"🔗 來源：{snapshot.latest.source_url}")
    else:
        lines += ["", "ℹ️ 最近一次 Reset：API 未提供可解析資料"]
    if snapshot.upcoming:
        label = "預告/估計時間" if snapshot.upcoming.timing_kind == "announced_or_estimated_time" else "預測窗口截止"
        lines += [
            "",
            "🔮 尚未發生的 Reset 訊號",
            f"🕒 {label}：{fmt_local(snapshot.upcoming.timestamp)}",
            f"⏳ 距離現在：{fmt_remaining(snapshot.upcoming.timestamp, snapshot.checked_at)}",
        ]
        if snapshot.upcoming.chance_percent is not None:
            lines.append(f"🎯 機率：{snapshot.upcoming.chance_percent:g}%")
        if snapshot.upcoming.confidence:
            lines.append(f"📊 信心：{snapshot.upcoming.confidence}")
        if snapshot.upcoming.window_label:
            lines.append(f"🪟 Window：{snapshot.upcoming.window_label}")
        if snapshot.upcoming.message:
            lines.append(f"💬 訊號：{safe_text(snapshot.upcoming.message)}")
        if snapshot.upcoming.source_url:
            lines.append(f"🔗 來源：{snapshot.upcoming.source_url}")
        lines.append("⚠️ 此為第三方公開追蹤/預測訊號，不等同 OpenAI 對個人帳戶的保證時間。")
    else:
        lines += ["", "🌙 尚未偵測到未來 Reset 的可解析時間訊號。"]
    if snapshot.status_error:
        lines += ["", f"⚠️ status API：{safe_text(snapshot.status_error, 300)}"]
    if snapshot.resets_error:
        lines.append(f"⚠️ resets API（非必要）：{safe_text(snapshot.resets_error, 300)}")
    return "\n".join(lines)


def format_upcoming_notice(upcoming: Upcoming, checked_at: dt.datetime) -> str:
    label = "預告/估計時間" if upcoming.timing_kind == "announced_or_estimated_time" else "預測窗口截止"
    lines = [
        "🚨 Codex Reset Watch",
        "━━━━━━━━━━━━━━",
        "🔮 發現尚未發生的 Reset 訊號",
        f"🕒 {label}：{fmt_local(upcoming.timestamp)}",
        f"⏳ 距離現在：{fmt_remaining(upcoming.timestamp, checked_at)}",
    ]
    if upcoming.chance_percent is not None:
        lines.append(f"🎯 機率：{upcoming.chance_percent:g}%")
    if upcoming.confidence:
        lines.append(f"📊 信心：{upcoming.confidence}")
    if upcoming.window_label:
        lines.append(f"🪟 Window：{upcoming.window_label}")
    if upcoming.message:
        lines.append(f"💬 訊號：{safe_text(upcoming.message)}")
    if upcoming.source_url:
        lines.append(f"🔗 來源：{upcoming.source_url}")
    lines += [
        f"🛰️ 檢查時間：{fmt_local(checked_at)}",
        "⚠️ 第三方公開追蹤/預測，不代表你的個人 Codex 額度一定會在該時間重置。",
    ]
    return "\n".join(lines)


def format_new_event_notice(event: Event, checked_at: dt.datetime) -> str:
    lines = [
        "✅ Codex Reset 更新",
        "━━━━━━━━━━━━━━",
        "🎉 偵測到新的公開 Reset 事件/公告",
        f"🕒 時間：{fmt_local(event.timestamp)}",
        f"🏷️ 類型：{event.event_type or '未標示'}",
    ]
    if event.message:
        lines.append(f"📝 公告：{safe_text(event.message)}")
    if event.source_url:
        lines.append(f"🔗 來源：{event.source_url}")
    lines.append(f"🛰️ 檢查時間：{fmt_local(checked_at)}")
    return "\n".join(lines)


def send_telegram(cfg: Dict[str, Any], message: str, logger: Logger) -> bool:
    sender = os.path.expanduser(str(cfg.get("telegram_sender", "~/.claude/scripts/tg-send.sh")))
    if not os.path.isfile(sender) or not os.access(sender, os.X_OK):
        logger.event("ERROR", "telegram_sender_unavailable", path=sender)
        return False
    try:
        proc = subprocess.run([sender, "send", message], text=True, capture_output=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as e:
        logger.event("ERROR", "telegram_exception", error=str(e), path=sender)
        return False
    logger.event(
        "INFO" if proc.returncode == 0 else "ERROR",
        "telegram_send",
        returncode=proc.returncode,
        stderr=safe_text(proc.stderr, 800),
        stdout=safe_text(proc.stdout, 800),
    )
    return proc.returncode == 0


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
    store = StateStore()
    with store.lock(blocking=(mode in ("manual", "daily"))) as acquired:
        if not acquired:
            logger.event("INFO", "skipped_locked", mode=mode)
            if mode == "manual":
                print("ℹ️ 另一個 Codex Reset Watch 檢查正在執行，請稍後再試。")
            return 0

        state = store.load()
        local_now = now_utc().astimezone(TZ8)
        today = local_now.date().isoformat()
        if mode == "daily" and not force_daily:
            due = local_now.hour > int(cfg["daily_hour"]) or (
                local_now.hour == int(cfg["daily_hour"]) and local_now.minute >= int(cfg["daily_minute"])
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
                print(format_manual(snapshot))
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
            text = format_manual(snapshot)
            print(text)
            if notify:
                send_telegram(cfg, text, logger)
        else:
            if snapshot.latest and bool(cfg.get("notify_new_reset_events", True)):
                changed = snapshot.latest.key != prev_latest
                # First run establishes a baseline; only notify an already-existing event if very recent.
                if changed and (not is_first or is_recent_event(snapshot.latest, snapshot.checked_at)):
                    messages.append(format_new_event_notice(snapshot.latest, snapshot.checked_at))
            if snapshot.upcoming and bool(cfg.get("notify_upcoming_reset", True)):
                changed = snapshot.upcoming.key != prev_upcoming
                notify_unchanged = bool(cfg.get("monitor_notify_when_unchanged" if mode == "monitor" else "daily_notify_when_unchanged", False))
                if changed or notify_unchanged:
                    messages.append(format_upcoming_notice(snapshot.upcoming, snapshot.checked_at))
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


def doctor() -> int:
    cfg = load_config()
    logger = Logger(cfg)
    checks: List[Tuple[str, bool, str]] = []
    py_ok = sys.version_info >= (3, 11)
    checks.append(("Python >= 3.11", py_ok, f"{sys.version.split()[0]} ({display_path(sys.executable)})"))
    cfg_path = default_config_path()
    checks.append(("Config", cfg_path.exists(), display_path(cfg_path)))
    sender = pathlib.Path(os.path.expanduser(str(cfg.get("telegram_sender", ""))))
    checks.append(("Telegram sender executable", sender.is_file() and os.access(sender, os.X_OK), display_path(sender)))
    if sender.is_file() and os.access(sender, os.X_OK):
        try:
            proc = subprocess.run([str(sender), "preflight"], capture_output=True, text=True, timeout=30)
            checks.append(("Telegram preflight", proc.returncode == 0, safe_text(proc.stderr or proc.stdout, 180)))
        except Exception as e:
            checks.append(("Telegram preflight", False, str(e)))
    client = APIClient(cfg, logger)
    status, err = client.get_json(str(cfg.get("status_path", "/api/v1/status")))
    checks.append(("Codex Resets status API", status is not None, "OK" if status is not None else err))
    print("🩺 Codex Reset Watch doctor\n")
    failed = False
    for name, ok, detail in checks:
        print(f"{'✅' if ok else '❌'} {name}: {detail}")
        failed = failed or not ok
    print(f"\n📁 Logs: {display_path(default_log_dir())}")
    print(f"💾 State: {display_path(default_state_dir() / 'state.json')}")
    return 1 if failed else 0


def tail_logs(n: int) -> int:
    path = default_log_dir() / "events.jsonl"
    if not path.exists():
        print(f"尚無 log：{display_path(path)}")
        return 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    for line in lines:
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="codex-reset-watch", description="Monitor codex-resets.com and notify via Telegram.")
    sub = p.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", aliases=["update"], help="立即查詢、更新狀態、輸出到 Terminal，預設同時 Telegram 通知")
    check.add_argument("--no-notify", action="store_true", help="只顯示，不傳 Telegram")
    mon = sub.add_parser("monitor", help="launchd 每 2 小時背景掃描（只通知新資訊）")
    mon.add_argument("--no-notify", action="store_true")
    daily = sub.add_parser("daily", help="10:00 daily/catch-up 檢查")
    daily.add_argument("--force", action="store_true", help="忽略當日 10:00 gate，用於測試")
    daily.add_argument("--no-notify", action="store_true")
    sub.add_parser("doctor", help="檢查 Python/API/Telegram 設定")
    logs = sub.add_parser("logs", help="顯示最近事件 logs")
    logs.add_argument("-n", "--lines", type=int, default=30)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
