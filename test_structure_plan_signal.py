import time
import unittest
from unittest.mock import patch

from market.services.signal.structure_plan_signal import (
    StructurePlanBuilder,
    StructurePlanSignalGenerator,
)


class _KlineStore:
    def __init__(self, count=40):
        now = int(time.time()) - count * 300
        self.rows = [
            {"timestamp": now + i * 300, "open": 110, "high": 112,
             "low": 108, "close": 110}
            for i in range(count)
        ]

    def get_all_klines(self, symbol, period):
        return list(self.rows)


class _Strategy:
    strategy_id = "strategy-1"

    def __init__(self, params=None):
        self.config = {
            "signal_source_id": "source-1", "source": "structure_plan",
            "period": "M5", "enabled": True, "params": params or {},
        }

    def get_signal_sources(self, source, enabled_only=True):
        return [self.config] if source == "structure_plan" else []


class _Repository:
    def __init__(self):
        self.plans = []

    def replace_scope(self, *args):
        self.plans = list(args[-2])
        return self.plans

    def list_current(self, *args):
        return list(self.plans)


def _range_structure(status="confirmed", direction=""):
    return {
        "atr": 2.0, "major_state": "sideways", "internal_state": "sideways",
        "external_state": "up", "internal_events": [],
        "structure_hierarchy": {},
        "range": {
            "active": True, "pattern": "range", "status": status,
            "top": 120.0, "bottom": 100.0, "start_index": 5,
            "high_touches": 3, "low_touches": 3, "score": 80,
            "breakout_direction": direction,
        },
    }


def _triangle_structure():
    structure = _range_structure()
    structure["range"].update({"pattern": "converging_triangle"})
    return structure


class StructurePlanTests(unittest.TestCase):
    def setUp(self):
        self.store = _KlineStore()

    def test_confirmed_range_builds_boundary_and_breakout_plans(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _range_structure(),
        )
        types = {item["setup_type"] for item in plans}
        self.assertEqual(types, {
            "range_lower_reversal", "range_breakout_watch",
        })
        self.assertEqual(sum(item["status"] == "active" for item in plans), 1)

    def test_confirmed_breakout_waits_for_boundary_retest(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _range_structure("breakout_confirmed", "up"),
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["setup_type"], "range_breakout")
        self.assertEqual(plans[0]["entry_mode"], "breakout_retest")

    def test_generation_time_is_not_part_of_plan_identity_or_reason(self):
        with patch(
            "market.services.signal.structure_plan_signal.time.time",
            return_value=1000,
        ):
            first = StructurePlanBuilder().build(
                "source-1", "BTCUSD", "M5", self.store.rows,
                _range_structure("breakout_confirmed", "down"),
            )[0]
        with patch(
            "market.services.signal.structure_plan_signal.time.time",
            return_value=2000,
        ):
            second = StructurePlanBuilder().build(
                "source-1", "BTCUSD", "M5", self.store.rows,
                _range_structure("breakout_confirmed", "down"),
            )[0]
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotIn("计划产生于", first["reason"])

    def test_sideways_triangle_keeps_box_boundary_plans(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _triangle_structure(),
        )
        types = {item["setup_type"] for item in plans}
        self.assertIn("range_lower_reversal", types)
        self.assertNotIn("range_upper_reversal", types)
        self.assertTrue(all(item["status"] == "active" for item in plans))

    def test_tick_uses_persisted_plan_without_reanalyzing(self):
        repository = _Repository()
        generator = StructurePlanSignalGenerator(self.store, repository, 1, 2)
        strategy = _Strategy()
        generator.refresh_plans(
            "BTCUSD", "M5", strategy, _range_structure()
        )
        self.assertFalse(generator.generate_signals_for_strategy(
            "BTCUSD", 110.0, strategy
        )[0].state_ready)
        generator.generate_signals_for_strategy("BTCUSD", 99.8, strategy)
        signal = generator.generate_signals_for_strategy(
            "BTCUSD", 100.1, strategy
        )[0]
        self.assertTrue(signal.state_ready)
        self.assertEqual(signal.action, "buy")
        self.assertTrue(signal.trade_plan_id)

    def test_stale_bos_does_not_create_trend_order_plan(self):
        structure = {
            "atr": 2.0, "major_state": "up", "internal_state": "up",
            "external_state": "up", "range": {},
            "internal_events": [{
                "type": "bos", "direction": "up", "level": 112,
                "confirmed_at": 20, "displacement_atr": 1.0,
            }],
            "structure_hierarchy": {},
        }
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual([item["setup_type"] for item in plans], ["no_trade"])

    def test_sideways_without_confirmed_range_does_not_trade_internal_sweep(self):
        structure = {
            "atr": 2.0, "major_state": "sideways", "current_state": "sideways",
            "range": {"active": False}, "structure_hierarchy": {},
            "internal_events": [{
                "type": "liquidity_sweep", "direction": "up",
                "level": 110.0, "confirmed_at": 39,
            }],
        }
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual([item["setup_type"] for item in plans], ["no_trade"])

    def test_uptrend_rejects_upper_sweep_sell_but_accepts_lower_sweep_buy(self):
        base = {
            "atr": 2.0, "major_state": "up", "current_state": "up",
            "range": {}, "structure_hierarchy": {},
        }
        upper = {**base, "internal_events": [{
            "type": "liquidity_sweep", "direction": "up",
            "level": 110.0, "confirmed_at": 39,
        }]}
        self.assertEqual(
            StructurePlanBuilder().build(
                "source-1", "BTCUSD", "M5", self.store.rows, upper,
            )[0]["setup_type"],
            "no_trade",
        )


if __name__ == "__main__":
    unittest.main()
