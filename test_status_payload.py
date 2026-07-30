#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from datetime import datetime

from market.models.statistics import StatisticsData


def load_builder():
    try:
        from status_payload import build_system_status_payload
        return build_system_status_payload
    except Exception:
        return None


class StatusPayloadTests(unittest.TestCase):
    def test_build_system_status_payload_marks_mt5_connected(self):
        build_system_status_payload = load_builder()
        self.assertTrue(
            callable(build_system_status_payload),
            "build_system_status_payload should exist",
        )

        latest = StatisticsData(
            symbol="BTCUSD#",
            timestamp=datetime.now(),
            bid_price=100.0,
            ask_price=101.0,
            spread=1.0,
            spread_points=100.0,
            balance=1000.0,
            equity=1000.0,
            margin_level=100.0,
            tick_count=1,
        )

        payload = build_system_status_payload(
            pending_instructions=3,
            statistics_records=8,
            symbols=["BTCUSD#"],
            latest_statistics=latest,
        )

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["mt5_connected"])
        self.assertEqual(payload["statistics_records"], 8)
        self.assertIn("system", payload)
        self.assertIn("last_statistics_at", payload)


if __name__ == "__main__":
    unittest.main()
