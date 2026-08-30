import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from market.services.adaptive_signal_tuner import AdaptiveSignalTuner
from server import TradingServer
from sqlite_storage import LiveTradeDealRepository


ATTRIBUTION = json.dumps({
    "signal_source_id": "source-1",
    "setup_type": "range_reversal",
})


class _AdaptiveStorage:
    def __init__(self):
        self.queries = []

    def fetchall(self, sql, params=()):
        self.queries.append(sql)
        if "FROM paper_trades" in sql:
            return []
        return [
            {
                "account_id": 4, "ticket": 1, "mt5_position_id": 11,
                "entry_type": 0, "volume": 1, "profit": 0, "swap": 0,
                "commission": 0, "deal_timestamp": 100,
                "position_attribution_json": ATTRIBUTION,
            },
            {
                "account_id": 4, "ticket": 2, "mt5_position_id": 11,
                "entry_type": 1, "volume": 0.4, "profit": 4, "swap": 0,
                "commission": -0.2, "deal_timestamp": 200,
                "position_attribution_json": ATTRIBUTION,
            },
            {
                "account_id": 4, "ticket": 3, "mt5_position_id": 11,
                "entry_type": 1, "volume": 0.6, "profit": -1, "swap": 0,
                "commission": -0.3, "deal_timestamp": 210,
                "position_attribution_json": ATTRIBUTION,
            },
            {
                "account_id": 4, "ticket": 4, "mt5_position_id": 12,
                "entry_type": 0, "volume": 1, "profit": 0, "swap": 0,
                "commission": 0, "deal_timestamp": 300,
                "position_attribution_json": ATTRIBUTION,
            },
            {
                "account_id": 4, "ticket": 5, "mt5_position_id": 12,
                "entry_type": 1, "volume": 0.5, "profit": 2, "swap": 0,
                "commission": 0, "deal_timestamp": 310,
                "position_attribution_json": ATTRIBUTION,
            },
        ]


class _DealStorage:
    def __init__(self):
        self.existing = None
        self.execute_count = 0
        self.last_sql = ""

    def fetchone(self, sql, params=()):
        if "FROM trade_execution_reports" in sql:
            return None
        if "FROM live_trade_deals" in sql:
            return self.existing
        return None

    def execute(self, sql, params=()):
        self.execute_count += 1
        self.last_sql = sql
        self.existing = {
            "mt5_order": params[3], "mt5_position_id": params[4],
            "symbol": params[5], "deal_type": params[6], "entry_type": params[7],
            "volume": params[8], "price": params[9], "profit": params[10],
            "swap": params[11], "commission": params[12], "deal_time": params[13],
            "deal_timestamp": params[14], "broker_utc_offset_seconds": params[15],
            "comment": params[16], "payload_json": params[18],
            "position_attribution_json": params[19],
        }


class _LossGuardStorage:
    def __init__(self):
        self.query = ""

    def fetchall(self, sql, params=()):
        self.query = sql
        return [
            {
                "profit": -1, "swap": 0, "commission": 0,
                "deal_timestamp": timestamp, "mt5_position_id": position_id,
                "position_attribution_json": json.dumps({"strategy_id": "strategy-1"}),
            }
            for position_id, timestamp in ((1, 100), (2, 90), (3, 80))
        ]


class TradeHistoryFixesTest(unittest.TestCase):
    def test_adaptive_tuner_merges_partial_live_exits_and_ignores_open_position(self):
        storage = _AdaptiveStorage()
        tuner = AdaptiveSignalTuner(storage=storage, source_repository=object())

        rows = tuner._closed_trades(1, "source-1")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_id"], "live:4:11")
        self.assertAlmostEqual(rows[0]["net_profit"], 2.5)
        self.assertEqual(rows[0]["closed_at"], 210)
        self.assertTrue(any("deal_timestamp" in sql for sql in storage.queries))
        self.assertFalse(any("received_at AS closed_at" in sql for sql in storage.queries))

    def test_duplicate_trade_upload_is_unchanged_and_preserves_received_at(self):
        storage = _DealStorage()
        repository = LiveTradeDealRepository(storage)
        deal = {
            "ticket": 1001, "order": 2001, "position_id": 3001,
            "symbol": "BTCUSDm", "type": 1, "entry": 1,
            "volume": 0.1, "price": 78000, "profit": -1.2,
            "swap": 0, "commission": -0.1,
            "deal_timestamp": 1788040381, "comment": "[sl 77900]",
        }

        first = repository.record_many(1, 4, [deal])
        second = repository.record_many(1, 4, [deal])

        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(storage.execute_count, 1)
        self.assertNotIn("received_at = excluded.received_at", storage.last_sql)

    def test_live_loss_pause_uses_actual_deal_time(self):
        storage = _LossGuardStorage()
        trading_server = object.__new__(TradingServer)
        trading_server.user_id = 1
        trading_server.account_id = 4
        trading_server._runtime_repository = SimpleNamespace(storage=storage)
        policy = SimpleNamespace(config={
            "loss_streak_circuit_breaker_enabled": True,
            "loss_streak_limit": 3,
            "loss_streak_pause_minutes": 10,
        })

        with patch("server.PositionManagementPolicyRepository") as repository:
            with patch("server.time.time", return_value=1000):
                repository.return_value.get_for_strategy.return_value = policy
                result = trading_server._live_loss_streak_guard(
                    "BTCUSDm", SimpleNamespace(strategy_id="strategy-1"),
                    "buy", SimpleNamespace(),
                )

        self.assertTrue(result["allowed"])
        self.assertTrue(result["cooldown_completed"])
        self.assertIn("ORDER BY deal_timestamp DESC", storage.query)
        self.assertNotIn("received_at", storage.query)


if __name__ == "__main__":
    unittest.main()
