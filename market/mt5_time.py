"""MT5/Python boundary time helpers.

All business instants are UTC. Broker wall-clock values are retained only for
audit and MT5 cursor operations; user-facing timestamps are rendered in China
Standard Time.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_epoch(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number >= 10**12:
        number //= 1000
    return number if number > 0 else None


def broker_wall_epoch_to_utc(value: Any, offset_seconds: Any) -> Optional[int]:
    """Convert MT5's broker-wall-clock epoch representation to UTC epoch."""
    epoch = normalize_epoch(value)
    if epoch is None:
        return None
    try:
        offset = int(offset_seconds or 0)
    except (TypeError, ValueError):
        offset = 0
    return epoch - offset


def utc_datetime(value: Any, fallback_now: bool = False) -> Optional[datetime]:
    epoch = normalize_epoch(value)
    if epoch is not None:
        return datetime.fromtimestamp(epoch, tz=UTC)
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_now() if fallback_now else None


def parse_ea_instant(
    data: dict,
    *,
    utc_field: str,
    broker_epoch_field: Optional[str] = None,
    legacy_wall_field: Optional[str] = None,
) -> datetime:
    """Parse a new UTC field, then broker epoch+offset, then a legacy wall time."""
    instant = utc_datetime(data.get(utc_field))
    if instant is not None:
        return instant
    if broker_epoch_field:
        epoch = broker_wall_epoch_to_utc(
            data.get(broker_epoch_field), data.get("broker_utc_offset_seconds", 0)
        )
        if epoch is not None:
            return datetime.fromtimestamp(epoch, tz=UTC)
    if legacy_wall_field:
        text = str(data.get(legacy_wall_field) or "").strip()
        for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                wall = datetime.strptime(text, fmt)
                offset = int(data.get("broker_utc_offset_seconds") or 0)
                return (wall - timedelta(seconds=offset)).replace(tzinfo=UTC)
            except (TypeError, ValueError):
                continue
    return utc_now()


def utc_iso(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def beijing_text(value: datetime) -> str:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
