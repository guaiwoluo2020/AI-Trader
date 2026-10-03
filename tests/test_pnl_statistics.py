import unittest
from datetime import date

from market.services.pnl_statistics import _is_strategy_trade, build_daily_pnl_statistics


class FakeStorage:
    def __init__(self, account_type="mt5", deals=None):
        self.account_type = account_type
        self.deals = deals or []
        self.inserted = []
        self.inserted_sql = []

    def fetchone(self, sql, params=None):
        if "FROM trading_accounts" in sql:
            return {"account_type": self.account_type}
        return {}

    def fetchall(self, sql, params=None):
        return list(self.deals)

    def execute(self, sql, params=None):
        if sql.strip().startswith("INSERT"):
            self.inserted.append(params)
            self.inserted_sql.append(sql)


class PnlStatisticsTests(unittest.TestCase):
    def test_empty_comment_live_deals_are_manual(self):
        self.assertFalse(_is_strategy_trade({"comments": "", "strategy_id": ""}, {}))
        self.assertFalse(_is_strategy_trade({"comments": "[sl 4130.00]", "strategy_id": ""}, {}))
        self.assertTrue(_is_strategy_trade({"comments": "AIT|21|src", "strategy_id": ""}, {}))
        self.assertTrue(_is_strategy_trade({"strategy_id": "s1"}, {}))
        self.assertTrue(_is_strategy_trade({}, {"plan_type": "structure_reversal"}))

    def test_build_skips_manual_live_deals(self):
        storage = FakeStorage(deals=[{
            "symbol": "GOLD#", "strategy_id": "", "profit": -1.39,
            "attribution": "{}", "comments": ",",
        }])
        count = build_daily_pnl_statistics(storage, 1, 21, date(2026, 10, 2))
        self.assertEqual(count, 0)
        self.assertEqual(storage.inserted, [])

    def test_build_writes_plan_type(self):
        storage = FakeStorage(account_type="paper", deals=[{
            "symbol": "GOLD#", "strategy_id": "s1", "profit": 12.5,
            "attribution": '{"plan_type":"structure_reversal","period":"M15"}',
        }])
        count = build_daily_pnl_statistics(storage, 1, 22, date(2026, 10, 3))
        self.assertEqual(count, 1)
        self.assertIn("plan_type", storage.inserted_sql[0])
        self.assertNotIn("setup_type", storage.inserted_sql[0])
        self.assertEqual(storage.inserted[0][4], "GOLD#")
        self.assertEqual(storage.inserted[0][5], "M15")
        self.assertEqual(storage.inserted[0][6], "structure_reversal")
        self.assertEqual(storage.inserted[0][7], "s1")

