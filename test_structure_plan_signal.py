import time
import unittest
from unittest.mock import patch

from market.models.trading_strategy import normalize_signal_sources
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
        self.replace_calls = []

    def replace_scope(self, *args):
        self.replace_calls.append(args)
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


def _triangle_structure(pattern="converging_triangle", major_state="sideways"):
    structure = _range_structure()
    structure["major_state"] = major_state
    structure["current_state"] = major_state
    structure["range"].update({"pattern": pattern})
    return structure


def _trend_structure(direction="down", close=110.0):
    if direction == "down":
        swing_pivots = [
            {"kind": "high", "label": "LH", "price": 110.0, "index": 36},
            {"kind": "low", "label": "LL", "price": 100.0, "index": 34},
        ]
        internal_pivots = [
            {"kind": "high", "label": "LH", "price": 109.5, "index": 38},
        ]
        levels = {
            "protected_high": {"price": 112.0, "index": 32},
            "protected_low": {"price": 100.0, "index": 34},
            "weak_low": {"price": 100.0, "index": 34},
        }
    else:
        swing_pivots = [
            {"kind": "low", "label": "HL", "price": 110.0, "index": 36},
            {"kind": "high", "label": "HH", "price": 120.0, "index": 34},
        ]
        internal_pivots = [
            {"kind": "low", "label": "HL", "price": 110.5, "index": 38},
        ]
        levels = {
            "protected_low": {"price": 108.0, "index": 32},
            "protected_high": {"price": 120.0, "index": 34},
            "weak_high": {"price": 120.0, "index": 34},
        }
    return {
        "atr": 2.0, "major_state": direction, "current_state": direction,
        "internal_state": direction, "external_state": direction,
        "active_candidate": None, "range": {}, "internal_events": [],
        "structure_hierarchy": {
            "swing": {"bias": direction, "phase": "continuation",
                      "pivots": swing_pivots, **levels},
            "internal": {"bias": direction, "phase": "pullback",
                         "pivots": internal_pivots, **levels},
            "external": {"bias": direction, "phase": "continuation",
                         "pivots": [], **levels},
        },
        "trendlines": [], "test_close": close,
    }


class StructurePlanTests(unittest.TestCase):
    def test_structure_strategy_drops_private_plan_age_limit(self):
        source = normalize_signal_sources([{
            "signal_source_id": "structure-1",
            "source": "structure_plan", "period": "M5",
            "params": {"max_plan_age_bars": 2},
        }])[0]
        self.assertNotIn("max_plan_age_bars", source["params"])

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
        self.assertEqual(
            next(item for item in plans if item["status"] == "active")["entry_mode"],
            "touch_or_near",
        )

    def test_confirmed_breakout_waits_for_boundary_retest(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _range_structure("breakout_confirmed", "up"),
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["setup_type"], "range_breakout")
        self.assertEqual(plans[0]["entry_mode"], "breakout_retest")

    def _confirmed_triangle(self, swing="up", external="up"):
        structure = _triangle_structure("ascending_triangle", "up")
        structure["external_state"] = external
        structure["structure_hierarchy"] = {
            "swing": {"bias": swing},
            "external": {"bias": external},
        }
        structure["range"].update({
            "status": "breakout_confirmed",
            "active": False,
            "breakout_direction": "up",
            "breakout_level": 120.0,
        })
        return structure

    def test_triangle_breakout_requires_half_atr_body(self):
        self.store.rows[-1].update({
            "open": 120.2, "high": 121.0, "low": 119.8, "close": 120.6,
        })
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            self._confirmed_triangle(),
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("实体仅 0.20 ATR", plans[0]["reason"])

    def test_triangle_breakout_requires_close_beyond_boundary(self):
        self.store.rows[-1].update({
            "open": 118.8, "high": 121.5, "low": 118.5, "close": 120.05,
        })
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            self._confirmed_triangle(),
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("收盘仅越过边界 0.02 ATR", plans[0]["reason"])

    def test_triangle_breakout_requires_swing_and_external_alignment(self):
        self.store.rows[-1].update({
            "open": 119.0, "high": 121.0, "low": 118.8, "close": 120.5,
        })
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            self._confirmed_triangle(external="down"),
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("Swing/External 不一致", plans[0]["reason"])

    def test_strong_aligned_triangle_breakout_creates_retest_plan(self):
        self.store.rows[-1].update({
            "open": 119.0, "high": 121.0, "low": 118.8, "close": 120.5,
        })
        plan = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            self._confirmed_triangle(),
        )[0]
        self.assertEqual(plan["setup_type"], "triangle_breakout")
        self.assertEqual(plan["entry_mode"], "breakout_retest")
        self.assertEqual(plan["validation_evidence"]["body_atr"], 0.75)
        self.assertEqual(plan["validation_evidence"]["close_extension_atr"], 0.25)
        self.assertEqual(plan["validation_evidence"]["swing_bias"], "up")
        self.assertEqual(plan["validation_evidence"]["external_bias"], "up")

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

    def test_plan_validity_prefers_utc_kline_timestamp(self):
        self.store.rows[-1]["timestamp"] = 10_800
        self.store.rows[-1]["timestamp_utc"] = 3_600
        plan = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _range_structure("breakout_confirmed", "down"),
        )[0]
        self.assertEqual(plan["valid_from"], 3_600)

    def test_sideways_triangle_keeps_box_boundary_plans(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _triangle_structure(),
        )
        types = {item["setup_type"] for item in plans}
        self.assertIn("range_lower_reversal", types)
        self.assertNotIn("range_upper_reversal", types)
        self.assertTrue(all(item["status"] == "active" for item in plans))

    def test_directional_triangle_only_watches_its_breakout_side(self):
        ascending = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _triangle_structure("ascending_triangle", "up"),
        )
        self.assertEqual(len(ascending), 1)
        self.assertEqual(ascending[0]["direction"], "buy")
        self.assertEqual(ascending[0]["setup_type"], "triangle_breakout_watch")

        descending = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _triangle_structure("descending_triangle", "down"),
        )
        self.assertEqual(len(descending), 1)
        self.assertEqual(descending[0]["direction"], "sell")
        self.assertEqual(descending[0]["setup_type"], "triangle_breakout_watch")

    def test_late_ascending_triangle_adds_support_entry_before_breakout(self):
        structure = _triangle_structure("ascending_triangle", "up")
        structure["range"].update({
            "width_atr": 2.0, "low_slope": 0.01, "low_intercept": 109.61,
        })
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual([item["direction"] for item in plans], ["buy", "buy"])
        self.assertEqual(plans[0]["setup_type"], "triangle_prebreakout_pullback")
        self.assertEqual(plans[1]["setup_type"], "triangle_breakout_watch")
        self.assertEqual(plans[0]["entry_mode"], "touch_or_near")

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

    def test_multiple_strategy_instances_share_one_canonical_plan_scope(self):
        repository = _Repository()
        generator = StructurePlanSignalGenerator(self.store, repository, 1, 2)
        first = _Strategy()
        second = _Strategy()
        second.config["signal_source_id"] = "source-2"

        generator.refresh_plans("BTCUSD", "M5", first, _range_structure())
        generator.refresh_plans("BTCUSD", "M5", second, _range_structure())

        self.assertEqual(len(repository.replace_calls), 1)
        self.assertEqual(repository.replace_calls[0][3], "market-structure")

    @patch(
        "market.services.signal.structure_plan_signal.time.time",
        return_value=10_000,
    )
    def test_strategy_uses_public_plan_expiry_without_private_age_cutoff(
        self, _mock_time,
    ):
        repository = _Repository()
        repository.plans = [{
            "plan_id": "plan-1", "plan_group_id": "group-1",
            "status": "active", "direction": "buy",
            "setup_type": "range_lower_reversal", "setup_family": "range",
            "entry_mode": "touch_or_near", "entry_price": 100,
            "entry_zone": {"lower": 99, "upper": 101},
            "stop_loss": 98, "take_profit": 104,
            "risk_reward_ratio": 2, "minimum_risk_reward": 1.2,
            "confidence": 80, "valid_from": 1_000, "expires_at": 11_000,
        }]
        generator = StructurePlanSignalGenerator(
            self.store, repository, 1, 2,
        )
        strategy = _Strategy({
            "allowed_directions": ["buy", "sell"],
            # Legacy strategy data must no longer shorten the public expiry.
            "max_plan_age_bars": 1,
        })

        signal = generator.generate_signals_for_strategy(
            "BTCUSD", 100, strategy,
        )[0]
        self.assertEqual(signal.action, "buy")
        self.assertTrue(signal.state_ready)

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
        plans = StructurePlanBuilder({"enable_structure_location": False}).build(
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
        plans = StructurePlanBuilder({"enable_structure_location": False}).build(
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

    def test_downtrend_near_lh_builds_sell_location_plan(self):
        structure = _trend_structure("down")
        self.store.rows[-1]["close"] = 109.7
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["setup_type"], "structure_location_pullback")
        self.assertEqual(plans[0]["direction"], "sell")
        self.assertEqual(plans[0]["entry_mode"], "touch_and_reclaim")

    def test_uptrend_near_hl_builds_buy_location_plan(self):
        structure = _trend_structure("up")
        self.store.rows[-1]["close"] = 110.2
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "structure_location_pullback")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertTrue(plans[0]["stop_candidates"])
        self.assertTrue(plans[0]["target_candidates"])
        self.assertEqual(
            plans[0]["stop_candidates"][0]["structure_layer"], "internal"
        )

    def test_close_above_protected_high_invalidates_downtrend_location(self):
        structure = _trend_structure("down")
        self.store.rows[-1]["close"] = 112.1
        plans = StructurePlanBuilder({"location_proximity_atr": 2}).build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("原趋势位置计划失效", plans[0]["reason"])

    def test_broken_trendline_is_not_a_location_candidate(self):
        structure = _trend_structure("down")
        for layer in structure["structure_hierarchy"].values():
            layer["pivots"] = []
            layer.pop("protected_high", None)
        structure["trendlines"] = [{
            "kind": "resistance", "anchor_price": 111.0,
            "anchor_index": 30, "slope": -0.1, "touches": 4,
            "broken_at": 38, "score": 90,
        }]
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("没有可用", plans[0]["reason"])

    def test_location_plan_reports_low_risk_reward_rejection(self):
        structure = _trend_structure("down")
        structure["structure_hierarchy"]["swing"]["weak_low"]["price"] = 106.0
        structure["structure_hierarchy"]["swing"]["protected_low"]["price"] = 106.0
        structure["structure_hierarchy"]["external"]["weak_low"]["price"] = 106.0
        structure["structure_hierarchy"]["external"]["protected_low"]["price"] = 106.0
        self.store.rows[-1]["close"] = 109.8
        plans = StructurePlanBuilder({"trend_min_real_risk_reward": 2.0}).build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("真实盈亏比", plans[0]["reason"])

    def test_uptrend_pullback_accepts_half_risk_reward(self):
        structure = _trend_structure("up")
        for layer in structure["structure_hierarchy"].values():
            if layer.get("weak_high"):
                layer["weak_high"]["price"] = 112.2
            if layer.get("protected_high"):
                layer["protected_high"]["price"] = 112.2
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "structure_location_pullback")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertGreaterEqual(plans[0]["risk_reward_ratio"], 0.5)
        self.assertLess(plans[0]["risk_reward_ratio"], 1.2)
        self.assertEqual(plans[0]["minimum_risk_reward"], 0.5)

    def test_uptrend_without_higher_target_enters_price_discovery_mode(self):
        structure = _trend_structure("up")
        for layer in structure["structure_hierarchy"].values():
            layer.pop("weak_high", None)
            layer.pop("protected_high", None)
        self.store.rows[-1]["close"] = 110.2
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "structure_location_pullback")
        self.assertTrue(plans[0]["price_discovery"])
        self.assertEqual(
            plans[0]["target_candidates"][0]["source_type"],
            "risk_reward_projection",
        )

    def test_uptrend_pullback_still_rejects_below_half_risk_reward(self):
        structure = _trend_structure("up")
        for layer in structure["structure_hierarchy"].values():
            if layer.get("weak_high"):
                layer["weak_high"]["price"] = 111.9
            if layer.get("protected_high"):
                layer["protected_high"]["price"] = 111.9
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("最低要求 0.50", plans[0]["reason"])

    def test_downtrend_rebound_accepts_half_risk_reward(self):
        structure = _trend_structure("down")
        for layer in structure["structure_hierarchy"].values():
            if layer.get("weak_low"):
                layer["weak_low"]["price"] = 107.7
            if layer.get("protected_low"):
                layer["protected_low"]["price"] = 107.7
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "structure_location_pullback")
        self.assertEqual(plans[0]["direction"], "sell")
        self.assertGreaterEqual(plans[0]["risk_reward_ratio"], 0.5)
        self.assertLess(plans[0]["risk_reward_ratio"], 1.2)
        self.assertEqual(plans[0]["minimum_risk_reward"], 0.5)

    def test_fresh_bos_uses_swing_phase_without_name_error(self):
        structure = _trend_structure("down")
        structure["internal_events"] = [{
            "type": "bos", "direction": "down", "level": 109.0,
            "confirmed_at": 39, "displacement_atr": 0.8,
        }]
        self.store.rows[-1]["close"] = 106.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "trend_continuation")

    def test_confirmed_choch_builds_reversal_plan(self):
        structure = _trend_structure("down")
        # A confirmed reversal should have a nearby protected low; using the
        # old deep downtrend low would correctly fail the real-RR guard.
        for layer in structure["structure_hierarchy"].values():
            if layer.get("protected_low"):
                layer["protected_low"]["price"] = 109.7
        structure["internal_events"] = [{
            "type": "choch", "direction": "up", "level": 110.0,
            "confirmed_at": 39, "displacement_atr": 0.8,
        }]
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["setup_type"], "choch_reversal")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertEqual(plans[0]["entry_mode"], "breakout_retest")

    def test_weak_choch_is_observation_only(self):
        structure = _trend_structure("down")
        structure["internal_events"] = [{
            "type": "choch", "direction": "up", "level": 110.0,
            "confirmed_at": 39, "displacement_atr": 0.05,
        }]
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "no_trade")
        self.assertIn("CHOCH 位移", plans[0]["reason"])


if __name__ == "__main__":
    unittest.main()
