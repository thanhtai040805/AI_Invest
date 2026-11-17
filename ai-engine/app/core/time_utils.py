"""Canonical timezone helpers for UTC persistence and Asia/Ho_Chi_Minh market logic."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC_TZ = timezone.utc


def now_vn() -> datetime:
    """Return current timezone-aware datetime in Asia/Ho_Chi_Minh."""
    return datetime.now(VN_TZ)


def today_vn() -> date:
    """Return current calendar date in Asia/Ho_Chi_Minh."""
    return datetime.now(VN_TZ).date()


def now_utc() -> datetime:
    """Return current timezone-aware datetime in UTC."""
    return datetime.now(UTC_TZ)


def now_utc_naive() -> datetime:
    """Return current UTC datetime stripped of tzinfo for naive TIMESTAMP columns."""
    return datetime.now(UTC_TZ).replace(tzinfo=None)


def to_utc_naive(dt: datetime, *, assume_tz: ZoneInfo | timezone = VN_TZ) -> datetime:
    """Convert a datetime to naive UTC for PostgreSQL timestamp-without-time-zone columns."""
    aware = dt.replace(tzinfo=assume_tz) if dt.tzinfo is None else dt
    return aware.astimezone(UTC_TZ).replace(tzinfo=None)


def to_vn_datetime(dt: datetime, *, assume_tz: ZoneInfo | timezone = UTC_TZ) -> datetime:
    """Convert a datetime to timezone-aware Asia/Ho_Chi_Minh."""
    aware = dt.replace(tzinfo=assume_tz) if dt.tzinfo is None else dt
    return aware.astimezone(VN_TZ)


def coerce_vn_date(value: Any, default: Optional[date] = None) -> date:
    """Coerce a date, datetime, or ISO string to a Vietnam trading calendar date."""
    if isinstance(value, datetime):
        return (value.astimezone(VN_TZ) if value.tzinfo is not None else value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text:
            if len(text) == 10:
                return date.fromisoformat(text)
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return (dt.astimezone(VN_TZ) if dt.tzinfo is not None else dt).date()
    return default if default is not None else today_vn()


def parse_vn_bound(raw: str, *, is_end: bool = False) -> datetime:
    """Parse an ISO date or timestamp into timezone-aware Asia/Ho_Chi_Minh bounds."""
    text = raw.strip()
    if len(text) == 10:
        d = date.fromisoformat(text)
        return datetime.combine(d, time(23, 59, 59) if is_end else time(0, 0, 0), tzinfo=VN_TZ)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)
