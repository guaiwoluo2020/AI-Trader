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

    def update_payload(self, plan_id, updates):
        for plan in self.plans:
            if plan.get("plan_id") == plan_id:
                plan.update(updates)

    def invalidate_plan(self, plan_id, reason):
        for plan in self.plans:
            if plan.get("plan_id") == plan_id:
                plan.update({"status": "invalidated", "invalidated_reason": reason})

    def suppress_plan(self, plan_id, event_risk):
        for plan in self.plans:
            if plan.get("plan_id") == plan_id:
                if plan.get("status") in {"active", "watching"}:
                    plan["status_before_event"] = plan.get("status")
                plan.update({"status": "event_suppressed", "event_risk": dict(event_risk or {})})

    def resume_plan(self, plan_id):
        for plan in self.plans:
            if plan.get("plan_id") == plan_id:
                restored = plan.get("status_before_event") or "active"
                plan["status"] = restored
                plan.pop("event_risk", None)
                return restored
        return ""


def _range_structure(status="confirmed", direction=""):
    bias = direction if direction in {"up", "down"} else "sideways"
    return {
        "atr": 2.0, "major_state": bias, "internal_state": "sideways",
        "external_state": bias, "internal_events": [],
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
            {"kind": "high", "label": "LH", "price": 110.0, "index": 38, "confirmed_at": 39},
            {"kind": "low", "label": "LL", "price": 100.0, "index": 34},
        ]
        internal_pivots = [
            {"kind": "high", "label": "LH", "price": 109.5, "index": 38, "confirmed_at": 39},
        ]
        levels = {
            "protected_high": {"price": 112.0, "index": 32},
            "protected_low": {"price": 100.0, "index": 34},
            "weak_low": {"price": 100.0, "index": 34},
        }
    else:
        swing_pivots = [
            {"kind": "low", "label": "HL", "price": 110.0, "index": 38, "confirmed_at": 39},
            {"kind": "high", "label": "HH", "price": 120.0, "index": 34},
        ]
        internal_pivots = [
            {"kind": "low", "label": "HL", "price": 110.5, "index": 38, "confirmed_at": 39},
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
    def setUp(self):
        self.store = _KlineStore()

    def test_confirmed_breakout_creates_trend_continuation_plan(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows,
            _range_structure("breakout_confirmed", "up"),
        )
        self.assertEqual(plans[0]["plan_type"], "swing_range_breakout")
        self.assertIn("swing:bos", plans[0].get("event_chain") or [])

    def test_active_range_creates_boundary_retest_plan(self):
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, _range_structure(),
        )
        self.assertTrue(any(item.get("plan_type") == "event_confirmation" for item in plans))
        self.assertTrue(any("retest" in " ".join(item.get("event_chain") or []) for item in plans))

    def test_swing_choch_creates_structure_reversal_plan(self):
        structure = _trend_structure("down")
        structure["trend_phase"] = "failed"
        for layer in structure["structure_hierarchy"].values():
            if layer.get("protected_low"):
                layer["protected_low"]["price"] = 109.7
        structure["major_events"] = [{
            "type": "choch", "direction": "up", "level": 110.0,
            "confirmed_at": 39, "displacement_atr": 0.8,
        }]
        self.store.rows[-1]["close"] = 110.0
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["plan_type"], "structure_reversal")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertIn("swing:choch", plans[0].get("event_chain") or [])

    def test_internal_choch_creates_early_reversal_plan(self):
        structure = _trend_structure("down")
        structure["trend_phase"] = "failed"
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
        self.assertEqual(plans[0]["plan_type"], "early_reversal")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertIn("internal:choch", plans[0].get("event_chain") or [])

    def test_liquidity_sweep_creates_reversal_plan(self):
        structure = {
            "atr": 2.0, "major_state": "up", "current_state": "up",
            "range": {}, "structure_hierarchy": {},
            "internal_events": [{
                "type": "liquidity_sweep", "direction": "down",
                "level": 108.0, "confirmed_at": 39,
            }],
        }
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        self.assertEqual(plans[0]["plan_type"], "internal_liquidity_reversal")
        self.assertEqual(plans[0]["direction"], "buy")
        self.assertIn("internal:liquidity_sweep", plans[0].get("event_chain") or [])

    def test_internal_hl_creates_internal_pullback_plan(self):
        structure = _trend_structure("up")
        structure["structure_hierarchy"]["internal"]["pivots"] = [
            {"kind": "low", "label": "HL", "price": 110.0, "index": 38},
        ]
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        internal = [item for item in plans if "internal:hl_confirmed" in " ".join(item.get("event_chain") or [])]
        self.assertTrue(internal)
        self.assertEqual(internal[0]["plan_type"], "internal_pullback")

    def test_hl_confirmed_creates_swing_pullback_plan(self):
        structure = _trend_structure("up")
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        tradable = [item for item in plans if item.get("plan_type") not in {"", "event"}]
        self.assertTrue(tradable)
        self.assertEqual(tradable[0]["plan_type"], "swing_pullback")
        self.assertTrue(any("hl_confirmed" in " ".join(item.get("event_chain") or []) for item in tradable))

    def test_choch_keeps_same_direction_sweep_and_drops_bos(self):
        structure = _trend_structure("up")
        structure["major_events"] = [
            {"type": "choch", "direction": "up", "level": 110.0, "confirmed_at": 39},
            {"type": "liquidity_sweep", "direction": "down", "level": 108.0, "confirmed_at": 39},
            {"type": "bos", "direction": "up", "level": 112.0, "confirmed_at": 39},
        ]
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        types = {item.get("plan_type") for item in plans if item.get("plan_type") not in {"", "event", "no_trade"}}
        self.assertIn("structure_reversal", types)
        self.assertIn("swing_liquidity_reversal", types)
        self.assertIn("swing_pullback", types)
        self.assertNotIn("trend_continuation", types)

    def test_stale_catalog_sweep_does_not_create_plan(self):
        structure = {
            "atr": 2.0, "major_state": "up", "current_state": "up",
            "range": {}, "structure_hierarchy": {},
            "internal_events": [{
                "type": "liquidity_sweep", "direction": "down",
                "level": 108.0, "confirmed_at": 10,
            }],
        }
        plans = StructurePlanBuilder().build(
            "source-1", "BTCUSD", "M5", self.store.rows, structure,
        )
        types = {item.get("plan_type") for item in plans}
        self.assertNotIn("internal_liquidity_reversal", types)
