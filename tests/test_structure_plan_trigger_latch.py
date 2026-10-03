import unittest

from market.services.signal.structure_plan_signal import StructurePlanSignalGenerator


class _Repo:
    def __init__(self):
        self.payloads = []
        self.invalidated = []

    def update_payload(self, plan_id, payload):
        self.payloads.append((plan_id, dict(payload)))

    def invalidate_plan(self, plan_id, reason):
        self.invalidated.append((plan_id, reason))


class StructurePlanTriggerLatchTests(unittest.TestCase):
    def setUp(self):
        self.repo = _Repo()
        self.gen = StructurePlanSignalGenerator.__new__(StructurePlanSignalGenerator)
        self.gen.repository = self.repo
        self.gen._tick_state = {}
        self.gen._param = lambda key, default=None: {
            "max_entry_distance_pct": 0.8,
        }.get(key, default)

    def test_range_touch_plan_keeps_triggering_after_boundary_marked(self):
        plan = {
            "plan_id": "gold-m5",
            "setup_type": "range_lower_reversal",
            "direction": "buy",
            "entry_mode": "touch_or_near",
            "entry_price": 4341.9,
            "entry_zone": {"lower": 4339.2, "upper": 4344.5},
            "boundary_state": "left_boundary",
        }
        self.assertTrue(self.gen._triggered(plan, 4341.9))
        self.assertEqual(plan["boundary_state"], "triggered")
        # A later tick in the same zone must still be eligible for claim.
        self.assertTrue(self.gen._triggered(plan, 4342.1))

    def test_stale_far_plan_is_invalidated_by_distance(self):
        plan = {
            "plan_id": "us100-m1",
            "setup_type": "range_lower_reversal",
            "direction": "buy",
            "entry_price": 29927.7,
            "entry_zone": {"lower": 29924.4, "upper": 29931.0},
            "validation_evidence": {},
        }
        reason = self.gen._event_invalidated(plan, 30143.28)
        self.assertIn("倍入场区宽度", reason)

    def test_stale_far_plan_with_zone_is_invalidated_by_zone_widths(self):
        plan = {
            "plan_id": "btc-m5",
            "setup_type": "range_breakout",
            "direction": "buy",
            "entry_price": 82144.46,
            "entry_zone": {"lower": 82017.56, "upper": 82271.37},
            "validation_evidence": {},
        }
        reason = self.gen._event_invalidated(plan, 85451.35)
        self.assertIn("倍入场区宽度", reason)

    def test_reclaimed_plan_still_triggers_just_outside_zone(self):
        plan = {
            "plan_id": "oil-m1",
            "setup_type": "range_false_breakout",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 94.1417,
            "entry_zone": {"lower": 94.1068, "upper": 94.1767},
            "touch_seen": True,
            "touch_state": "reclaimed",
            "boundary_state": "triggered",
            "structure_snapshot": {"atr": 0.12},
        }
        self.assertTrue(self.gen._triggered(plan, 94.22))
        self.assertEqual(plan["touch_state"], "reclaimed")

    def test_reclaimed_sweep_does_not_chase_finished_bounce(self):
        plan = {
            "plan_id": "usdjpy-m1",
            "setup_type": "liquidity_sweep_reclaim",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 157.508,
            "entry_zone": {"lower": 157.50216286, "upper": 157.51383714},
            "touch_seen": True,
            "touch_state": "reclaimed",
            "boundary_state": "triggered",
            "take_profit": 158.03048571,
            "target_candidates": [{
                "structure_layer": "internal", "price": 157.594,
            }],
            "structure_snapshot": {"atr": 0.01621428571428193},
        }
        self.assertFalse(self.gen._triggered(plan, 157.58))
        allowed, reason = self.gen._tick_stop_gate(plan, 157.58)
        self.assertFalse(allowed)
        self.assertIn("入场", reason)

    def test_reclaimed_plan_resets_when_price_is_too_far(self):
        plan = {
            "plan_id": "oil-m1-far",
            "setup_type": "range_false_breakout",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 94.1417,
            "entry_zone": {"lower": 94.1068, "upper": 94.1767},
            "touch_seen": True,
            "touch_state": "reclaimed",
            "boundary_state": "triggered",
        }
        self.assertFalse(self.gen._triggered(plan, 95.5))
        self.assertEqual(plan["touch_state"], "unvisited")

    def test_false_breakout_requires_reclaiming_close_before_sell(self):
        plan = {
            "plan_id": "gold-false-up",
            "setup_type": "range_false_breakout",
            "direction": "sell",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.0, "upper": 101.0},
            "touch_seen": True,
            "touch_state": "touched",
            "boundary_state": "touched",
            "structure_snapshot": {
                "atr": 2.0,
                "range": {"top": 100.0, "bottom": 90.0},
            },
        }
        config = {
            "false_breakout_require_reclaim_close": True,
            "false_breakout_confirmation_bars": 2,
            "false_breakout_min_reclaim_atr": 0.1,
        }
        self.assertFalse(self.gen._triggered(
            plan, 100.0, config,
            {"timestamp": 1000, "close": 99.9},
        ))
        self.assertFalse(self.gen._triggered(
            plan, 100.0, config,
            {"timestamp": 1060, "close": 99.7},
        ))
        self.assertTrue(self.gen._triggered(
            plan, 100.0, config,
            {"timestamp": 1120, "close": 99.7},
        ))

    def test_false_breakout_requires_reclaiming_close_before_buy(self):
        plan = {
            "plan_id": "silver-false-down",
            "setup_type": "range_false_breakout",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.0, "upper": 101.0},
            "touch_seen": True,
            "touch_state": "touched",
            "boundary_state": "touched",
            "structure_snapshot": {
                "atr": 2.0,
                "range": {"top": 110.0, "bottom": 100.0},
            },
        }
        config = {
            "false_breakout_require_reclaim_close": True,
            "false_breakout_confirmation_bars": 1,
            "false_breakout_min_reclaim_atr": 0.1,
        }
        self.assertFalse(self.gen._triggered(
            plan, 100.0, config,
            {"timestamp": 1000, "close": 100.1},
        ))
        self.assertTrue(self.gen._triggered(
            plan, 100.0, config,
            {"timestamp": 1060, "close": 100.3},
        ))

    def test_location_pullback_rechecks_latest_close_at_entry(self):
        plan = {
            "plan_id": "gold-location-entry",
            "setup_type": "structure_location_pullback",
            "direction": "buy",
            "entry_mode": "touch_and_reclaim",
            "entry_price": 100.0,
            "entry_zone": {"lower": 99.0, "upper": 101.0},
            "touch_seen": True,
            "touch_state": "touched",
            "boundary_state": "touched",
            "structure_snapshot": {"atr": 2.0},
        }
        config = {
            "location_reclaim_min_body_atr": 0.5,
            "location_reclaim_min_close_extension_atr": 0.2,
            "location_reclaim_confirmation_bars": 3,
        }
        self.assertFalse(self.gen._triggered(
            plan, 100.0, config,
            [{"timestamp": 1000, "open": 99.0, "high": 100.6,
              "low": 98.8, "close": 100.5}],
        ))
        first = {"timestamp": 1060, "open": 99.0, "high": 100.7,
                 "low": 98.8, "close": 100.5}
        second = {"timestamp": 1120, "open": 100.3, "high": 100.9,
                  "low": 99.0, "close": 100.6}
        third = {"timestamp": 1180, "open": 100.4, "high": 101.0,
                 "low": 99.2, "close": 100.7}
        self.assertFalse(self.gen._triggered(plan, 100.0, config, [first, second]))
        self.assertTrue(self.gen._triggered(
            plan, 100.0, config, [first, second, third],
        ))
        self.assertEqual(plan["location_entry_reclaim_evidence"]["confirmation_extremes"], [98.8, 99.0, 99.2])
        # The small outside-zone overshoot path must still recheck the latest
        # completed bar instead of reusing a stale confirmed state.
        fourth = {"timestamp": 1240, "open": 100.3, "high": 101.0,
                  "low": 99.3, "close": 99.9}
        self.assertFalse(self.gen._triggered(
            plan, 101.2, config, [second, third, fourth],
        ))


if __name__ == "__main__":
    unittest.main()
