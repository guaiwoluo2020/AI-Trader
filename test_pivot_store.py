#!/usr/bin/env python3
"""转折点基础数据存储测试。"""

import unittest

from market.models import PivotPoint
from market.store.pivot_store import PivotStore


class PivotStoreTestCase(unittest.TestCase):
    def test_each_period_keeps_only_latest_ten_pivots(self):
        store = PivotStore()
        pivots = [
            PivotPoint(
                symbol="GOLD_",
                period="M1",
                timestamp=f"2026-08-02 10:{index:02d}:00",
                price=4000 + index,
                direction="high" if index % 2 else "low",
            )
            for index in range(15)
        ]

        store.save_pivots("GOLD_", "M1", pivots, list(reversed(pivots)))

        saved = store.get_pivot_objects("GOLD_", "M1")
        timeline = store.get_timeline("GOLD_", "M1")
        self.assertEqual(len(saved), 10)
        self.assertEqual(len(timeline), 10)
        self.assertEqual(saved[0].price, 4005)
        self.assertEqual(saved[-1].price, 4014)
        self.assertEqual(len(store.get_pivots("GOLD_", "M1", count=100)), 10)


if __name__ == "__main__":
    unittest.main()
