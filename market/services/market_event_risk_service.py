"""Deterministic market-event risk windows for structure trade plans.

The service deliberately has no dependency on a third-party economic calendar.
Regular market opens are calculated in their native time zones (and therefore
respect DST); dated macro events are stored in the public market configuration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from market_event_repository import MarketEventRepository


REVERSAL_SETUPS = {
    "range_lower_reversal", "range_upper_reversal", "range_false_breakout",
    "structure_location_pullback", "liquidity_sweep_reclaim",
    "choch_reversal", "structure_reversal",
}


DEFAULT_EVENT_RISK_RULES = [
    {"id": "tokyo_open", "label": "东京开盘", "event_type": "market_open", "level": "L1",
     "timezone": "Asia/Tokyo", "time": "09:00", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 10, "after_minutes": 20},
    {"id": "shanghai_open", "label": "上海开盘", "event_type": "market_open", "level": "L1",
     "timezone": "Asia/Shanghai", "time": "09:30", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 10, "after_minutes": 20},
    {"id": "london_open", "label": "伦敦开盘", "event_type": "market_open", "level": "L2",
     "timezone": "Europe/London", "time": "08:00", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 20, "after_minutes": 30},
    {"id": "new_york_open", "label": "纽约开盘", "event_type": "market_open", "level": "L2",
     "timezone": "America/New_York", "time": "09:30", "weekdays": [0, 1, 2, 3, 4],
     "before_minutes": 30, "after_minutes": 45},
]

_calendar_cache: Dict[str, object] = {"loaded_at": 0, "events": []}

# Calendar providers do not always assign a consistent importance score.  These
# two US releases therefore receive deterministic treatment even when a source
# labels them as medium impact or omits a country field.
NFP_KEYWORDS = (
    "nonfarm payroll", "non-farm payroll", "nonfarm employment",
    "nonfarm jobs", "非农", "美国就业报告",
)
FOMC_KEYWORDS = (
    "fomc", "federal reserve", "fed interest rate", "fed rate decision",
    "interest rate decision", "美联储", "联邦公开市场委员会", "利率决议",
)


def is_reversal_setup(setup_type: str) -> bool:
    return str(setup_type or "").strip().lower() in REVERSAL_SETUPS


def _matches_scope(values: Iterable[str] | None, wanted: str) -> bool:
    choices = {str(item).strip().upper() for item in (values or []) if str(item).strip()}
    return not choices or "*" in choices or str(wanted or "").upper() in choices


def _event_at(rule: Dict, now: int) -> Optional[int]:
    """Return the nearest scheduled occurrence of a recurring or dated rule."""
    if rule.get("event_time"):
        try:
            return int(rule["event_time"])
        except (TypeError, ValueError):
            return None
    time_text = str(rule.get("time") or "")
    if not time_text or ":" not in time_text:
        return None
    try:
        hour, minute = (int(part) for part in time_text.split(":", 1))
        tz = ZoneInfo(str(rule.get("timezone") or "Asia/Shanghai"))
    except (TypeError, ValueError, KeyError):
        return None
    local_now = datetime.fromtimestamp(now, timezone.utc).astimezone(tz)
    weekdays = {int(day) for day in (rule.get("weekdays") or []) if str(day).isdigit()}
    # Search around today: an event that began before midnight can still be in
    # its after-window, and a pre-window can begin on the previous local day.
    candidates = []
    for offset in (-1, 0, 1):
        day = (local_now + timedelta(days=offset)).date()
        occurrence = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        if weekdays and occurrence.weekday() not in weekdays:
            continue
        candidates.append(int(occurrence.timestamp()))
    return min(candidates, key=lambda item: abs(item - now)) if candidates else None


def _calendar_events(now: int) -> list[Dict]:
    """Load a narrow, shared calendar slice at most once a minute per worker."""
    if now - int(_calendar_cache.get("loaded_at") or 0) < 60:
        return list(_calendar_cache.get("events") or [])
    beijing = datetime.fromtimestamp(now, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
    dates = [(beijing + timedelta(days=offset)).date().isoformat() for offset in (-1, 0, 1)]
    events: list[Dict] = []
    try:
        repository = MarketEventRepository()
        for event_date in dates:
            events.extend(repository.list_calendar(event_date))
            events.extend(repository.list_key_events(event_date))
    except Exception as exc:
        # Risk protection must never take down Tick execution if the calendar
        # source is temporarily unavailable; deterministic opening rules remain.
        print(f"[EventRisk] 财经日历读取失败，继续使用开盘窗口: {exc}")
    _calendar_cache.update({"loaded_at": now, "events": events})
    return list(events)


def _calendar_timestamp(event: Dict) -> int:
    for field in ("event_timestamp", "timestamp"):
        try:
            value = int(event.get(field) or 0)
            if value:
                return value // 1000 if value > 10**12 else value
        except (TypeError, ValueError):
            pass
    date_text, time_text = str(event.get("event_date") or ""), str(event.get("event_time") or "")
    if not date_text or not time_text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(f"{date_text} {time_text}", fmt).replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            ).timestamp())
        except ValueError:
            continue
    return 0


def _major_us_event(event: Dict) -> Optional[str]:
    """Classify NFP/FOMC by normalized calendar fields, not source severity."""
    text = " ".join(
        str(event.get(field) or "")
        for field in ("name", "title", "event", "description", "country", "currency")
    ).lower()
    if any(keyword in text for keyword in NFP_KEYWORDS):
        return "nfp"
    if any(keyword in text for keyword in FOMC_KEYWORDS):
        return "fomc"
    return None


def _calendar_event(config: Dict, symbol: str, setup_type: str, now: int) -> Optional[Dict]:
    if not is_reversal_setup(setup_type):
        return None
    min_importance = max(1, min(3, int(config.get("event_risk_min_importance") or 3)))
    for event in _calendar_events(now):
        try:
            importance = int(event.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        major_type = _major_us_event(event)
        # NFP and FOMC must always be protected, even when a calendar source
        # has not yet normalized its impact level.
        if not major_type and importance < min_importance:
            continue
        symbols = event.get("symbols") or []
        if symbols and not _matches_scope(symbols, symbol):
            continue
        event_time = _calendar_timestamp(event)
        major = bool(major_type)
        before = max(0, int(config.get(
            "event_risk_major_before_minutes" if major else "event_risk_calendar_before_minutes",
            45 if major else 30,
        ) or 0)) * 60
        after = max(0, int(config.get(
            "event_risk_major_after_minutes" if major else "event_risk_calendar_after_minutes",
            90 if major else 45,
        ) or 0)) * 60
        if not event_time or not event_time - before <= now < event_time + after:
            continue
        label = str(event.get("name") or event.get("title") or "财经日历高影响事件")
        major_label = {"nfp": "美国非农（NFP）", "fomc": "美联储议息/FOMC"}.get(major_type)
        return {
            "id": str(event.get("id") or f"calendar:{event_time}:{label}"),
            "label": label,
            "event_type": major_type or "economic_calendar",
            "level": "L4" if major or importance >= 3 else "L3",
            "event_time": event_time,
            "suppress_from": event_time - before,
            "resume_after": event_time + after,
            "reason": f"重大宏观事件：{major_label}" if major else f"财经日历高影响事件：{label}",
            "importance": importance,
            "major_event": major,
        }
    return None


def active_event(config: Dict, symbol: str, period: str, setup_type: str, now: Optional[int] = None) -> Optional[Dict]:
    """Return the applicable event window, including the post-event recheck.

    Only reversal setups are suppressed by default. Rules can explicitly set
    ``affect_setups`` to override this behaviour for a particular event.
    """
    now = int(now or datetime.now(timezone.utc).timestamp())
    if not bool(config.get("event_risk_enabled", True)):
        return None
    setup = str(setup_type or "").strip().lower()
    calendar = _calendar_event(config, symbol, setup, now)
    if calendar:
        confirmation_bars = max(0, int(config.get("event_risk_resume_confirmation_bars") or 1))
        calendar["resume_confirmation_bars"] = confirmation_bars
        calendar["resume_after"] += confirmation_bars * {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(str(period).upper(), 300)
        return calendar
    rules = config.get("event_risk_rules")
    if not isinstance(rules, list) or not rules:
        rules = DEFAULT_EVENT_RISK_RULES
    for raw in rules:
        if not isinstance(raw, dict) or raw.get("enabled", True) is False:
            continue
        if not _matches_scope(raw.get("symbol_scope"), symbol) or not _matches_scope(raw.get("period_scope"), period):
            continue
        affected = {str(value).strip().lower() for value in (raw.get("affect_setups") or []) if str(value).strip()}
        if affected:
            if setup not in affected:
                continue
        elif not is_reversal_setup(setup):
            continue
        event_time = _event_at(raw, now)
        if not event_time:
            continue
        before = max(0, int(raw.get("before_minutes") or 0)) * 60
        after = max(0, int(raw.get("after_minutes") or 0)) * 60
        suppress_from, suppress_until = event_time - before, event_time + after
        if not suppress_from <= now < suppress_until:
            continue
        result = {
            "id": str(raw.get("id") or raw.get("event_type") or "market_event"),
            "label": str(raw.get("label") or "市场事件"),
            "event_type": str(raw.get("event_type") or "market_event"),
            "level": str(raw.get("level") or "L2").upper(),
            "event_time": event_time,
            "suppress_from": suppress_from,
            "resume_after": suppress_until,
            "reason": f"{raw.get('label') or '市场事件'}风险窗口",
        }
        confirmation_bars = max(0, int(config.get("event_risk_resume_confirmation_bars") or 1))
        result["resume_confirmation_bars"] = confirmation_bars
        result["resume_after"] += confirmation_bars * {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(str(period).upper(), 300)
        return result
    return None


def snapshot(config: Dict, symbol: str, period: str, setup_type: str, now: Optional[int] = None) -> Dict:
    event = active_event(config, symbol, period, setup_type, now)
    return {"event_risk": event} if event else {}
