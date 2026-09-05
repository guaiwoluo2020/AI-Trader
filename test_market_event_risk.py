import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from market.services.market_event_risk_service import _event_at, active_event
from market.services.major_us_calendar_collector import parse_bls_nfp_ics, parse_fomc_calendar
from market.services.signal.structure_plan_signal import STRUCTURE_PLAN_DEFAULT_CONFIG


class MarketEventRiskTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(STRUCTURE_PLAN_DEFAULT_CONFIG)

    def test_new_york_open_uses_native_dst_offset(self):
        rule = {
            "timezone": "America/New_York", "time": "09:30",
            "weekdays": [0, 1, 2, 3, 4],
        }
        winter_now = int(datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc).timestamp())
        summer_now = int(datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(
            _event_at(rule, winter_now),
            int(datetime(2026, 1, 5, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()),
        )
        self.assertEqual(
            _event_at(rule, summer_now),
            int(datetime(2026, 7, 6, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()),
        )

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_nfp_is_l4_even_when_calendar_marks_medium_impact(self, events):
        at = int(datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc).timestamp())
        events.return_value = [{
            "id": "nfp", "name": "US Nonfarm Payrolls", "importance": 2,
            "event_timestamp": at,
        }]
        event = active_event(self.config, "BTCUSD", "M5", "range_upper_reversal", at - 30 * 60)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "nfp")
        self.assertEqual(event["level"], "L4")
        self.assertEqual(event["suppress_from"], at - 45 * 60)
        self.assertIn("美国非农", event["reason"])

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_fomc_is_l4_and_trend_setup_is_not_paused_by_default(self, events):
        at = int(datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc).timestamp())
        events.return_value = [{
            "id": "fomc", "title": "FOMC Interest Rate Decision", "importance": 1,
            "event_timestamp": at,
        }]
        event = active_event(self.config, "GOLD_", "M1", "liquidity_sweep_reclaim", at)
        self.assertEqual(event["event_type"], "fomc")
        self.assertEqual(event["level"], "L4")
        self.assertIsNone(active_event(self.config, "GOLD_", "M1", "trend_continuation", at))

    @patch("market.services.market_event_risk_service._calendar_events")
    def test_normal_high_impact_calendar_window_and_resume_bar(self, events):
        at = int(datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc).timestamp())
        events.return_value = [{"name": "US ISM", "importance": 3, "event_timestamp": at}]
        event = active_event(self.config, "BTCUSD", "M5", "range_lower_reversal", at)
        self.assertEqual(event["level"], "L4")
        self.assertEqual(event["resume_confirmation_bars"], 1)
        self.assertEqual(event["resume_after"], at + 45 * 60 + 5 * 60)

    def test_official_calendar_parsers_normalize_nfp_and_fomc(self):
        nfp = parse_bls_nfp_ics("""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Employment Situation
DTSTART:20260904T123000Z
END:VEVENT
END:VCALENDAR""")
        self.assertEqual(nfp[0]["name"], "美国非农就业报告（NFP）")
        fomc = parse_fomc_calendar(
            "<h2>2026 FOMC Meetings</h2><p>January 27-28 March 17-18</p>"
            "<h2>2027 FOMC Meetings</h2>", 2026,
        )
        self.assertEqual(len(fomc), 2)
        self.assertTrue(all(item["name"] == "美联储议息决议（FOMC）" for item in fomc))


if __name__ == "__main__":
    unittest.main()
