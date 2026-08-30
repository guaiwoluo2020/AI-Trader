"""Shared Beijing-time boundaries for daily trading risk controls."""

from datetime import datetime, time as datetime_time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
RISK_DAY_START_HOUR = 7


def risk_day_key(timestamp: Optional[float] = None) -> str:
    """Return the business date whose risk window starts at Beijing 07:00."""
    now = (
        datetime.now(BEIJING)
        if timestamp is None
        else datetime.fromtimestamp(float(timestamp), BEIJING)
    )
    if now.hour < RISK_DAY_START_HOUR:
        now -= timedelta(days=1)
    return now.date().isoformat()


def risk_day_start_timestamp(timestamp: Optional[float] = None) -> int:
    """Return the UTC epoch for the active Beijing 07:00 risk-day boundary."""
    business_date = datetime.strptime(
        risk_day_key(timestamp), "%Y-%m-%d"
    ).date()
    boundary = datetime.combine(
        business_date,
        datetime_time(hour=RISK_DAY_START_HOUR),
        tzinfo=BEIJING,
    )
    return int(boundary.timestamp())
