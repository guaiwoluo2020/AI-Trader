"""Best-effort collector for the two US macro events that affect reversals most.

The collector deliberately writes into the existing shared market-calendar
storage, so the event-risk service and the News page consume exactly the same
records as EA-provided calendar data.  It relies only on official publishers:
BLS for Employment Situation (NFP) and the Federal Reserve for FOMC dates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from typing import Dict, Iterable, List
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from market_event_repository import MarketEventRepository


BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
NEW_YORK = ZoneInfo("America/New_York")
BEIJING = ZoneInfo("Asia/Shanghai")
MONTHS = {name: number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
), start=1)}


def _fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "AI-Trader/1.0 market-calendar"})
    with urlopen(request, timeout=15) as response:  # nosec B310 - fixed official URLs
        return response.read().decode("utf-8", errors="replace")


def _beijing_payload(event_id: str, name: str, timestamp: int, source_url: str) -> Dict:
    local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(BEIJING)
    return {
        "id": event_id,
        "name": name,
        "event_date": local.date().isoformat(),
        "event_time": local.strftime("%H:%M"),
        "event_timestamp": timestamp,
        "importance": 3,
        "country": "US",
        "currency": "USD",
        "symbols": [],
        "source_url": source_url,
        "major_event": True,
    }


def _unfold_ics(text: str) -> Iterable[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def parse_bls_nfp_ics(text: str) -> List[Dict]:
    """Extract official Employment Situation releases from BLS ICS."""
    events, current = [], {}
    for line in _unfold_ics(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            summary = str(current.get("SUMMARY") or "").lower()
            stamp = str(current.get("DTSTART") or "")
            if "employment situation" in summary and stamp:
                timestamp = _ics_timestamp(stamp)
                if timestamp:
                    events.append(_beijing_payload(
                        f"bls-nfp-{timestamp}", "美国非农就业报告（NFP）", timestamp, BLS_ICS_URL,
                    ))
            current = {}
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current[key.split(";", 1)[0]] = value.strip()
    return events


def _ics_timestamp(value: str) -> int:
    value = value.strip()
    try:
        if value.endswith("Z"):
            return int(datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())
        return int(datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=NEW_YORK).timestamp())
    except ValueError:
        return 0


def parse_fomc_calendar(html: str, year: int) -> List[Dict]:
    """Extract the final meeting day; policy decision is normally 14:00 ET."""
    text = unescape(re.sub(r"<[^>]+>", " ", html))
    start = text.find(f"{year} FOMC Meetings")
    if start < 0:
        return []
    tail = text[start:start + 8000]
    following = tail.find(f"{year + 1} FOMC Meetings")
    if following >= 0:
        tail = tail[:following]
    events, seen = [], set()
    pattern = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?", re.I)
    for match in pattern.finditer(tail):
        month = MONTHS[match.group(1).capitalize()]
        day = int(match.group(3) or match.group(2))
        try:
            stamp = int(datetime(year, month, day, 14, 0, tzinfo=NEW_YORK).timestamp())
        except ValueError:
            continue
        if stamp in seen:
            continue
        seen.add(stamp)
        events.append(_beijing_payload(
            f"fed-fomc-{stamp}", "美联储议息决议（FOMC）", stamp, FOMC_URL,
        ))
    return events


class MajorUSCalendarCollector:
    """Refresh NFP and FOMC events and merge them without removing other news."""

    def __init__(self, repository: MarketEventRepository | None = None):
        self.repository = repository or MarketEventRepository()

    def sync(self, year: int | None = None) -> Dict:
        year = int(year or datetime.now(BEIJING).year)
        nfp = parse_bls_nfp_ics(_fetch(BLS_ICS_URL))
        fomc = parse_fomc_calendar(_fetch(FOMC_URL), year)
        collected = [*nfp, *fomc]
        by_date: Dict[str, List[Dict]] = {}
        for event in collected:
            by_date.setdefault(event["event_date"], []).append(event)
        written = 0
        for event_date, new_events in by_date.items():
            existing = self.repository.list_calendar(event_date)
            merged = {str(item.get("id")): item for item in existing if item.get("id")}
            merged.update({str(item["id"]): item for item in new_events})
            self.repository.replace_calendar_day(event_date, list(merged.values()), "official_major_us_calendar")
            written += len(new_events)
        return {"year": year, "nfp": len(nfp), "fomc": len(fomc), "written": written}
