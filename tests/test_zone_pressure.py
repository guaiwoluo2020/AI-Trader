import copy
import unittest

from market.services.zone_pressure import advance, visit, momentum, _update_zone_states
from market.services.signal.structure_plan_signal import StructurePlanBuilder
from market.services.signal.structure_plan.lifecycle import invalidate_reason


def bar(t, c, h=None, l=None):
    return dict(timestamp=t, open=c, close=c, high=h or c+.2, low=l or c-.2)


class ZonePressureTests(unittest.TestCase):
    def test_structure_engine_snapshot_exposes_zone_pressure(self):
        from market.services.market_structure_engine_v2 import analyze

        rows = [bar(60 * index, 100 + (index % 5) * 0.2) for index in range(80)]
        result = analyze("X", "M1", rows)
        self.assertIn("zone_pressure", result)
        self.assertIn("zones", result["zone_pressure"])

    def test_pressure_reversal_becomes_one_trade_plan(self):
        rows = [bar(60 * index, 100.5) for index in range(20)]
        structure = {
            "atr": 1.0,
            "major_state": "down",
            "internal_state": "down",
            "external_state": "down",
            "structure_segment_id": "segment-a",
            "structure_hierarchy": {
                "internal": {"protected_high": {"price": 101.2}, "protected_low": {"price": 98.5}},
                "swing": {"protected_high": {"price": 101.2}, "protected_low": {"price": 98.5}},
                "external": {"protected_high": {"price": 102.0}, "protected_low": {"price": 97.0}},
            },
            "zone_pressure": {
                "zones": [{"zone_id": "z1", "lower": 99.0, "upper": 101.0, "center": 100.0}],
                "events": [{
                    "event_id": "e1", "zone_id": "z1", "type": "pressure_reversal_confirmed",
                    "direction": "sell", "level": 100.5, "boundary": 101.0,
                    "test_count": 3, "confirmed_at": 60 * 19,
                    "displacement_atr": 1.0, "efficiency": 0.8,
                }],
            },
        }
        plans = StructurePlanBuilder({"min_real_risk_reward": 1.0}).build(
            "market-structure", "X", "M1", rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "pressure_reversal")
        self.assertEqual(plans[0]["direction"], "sell")
        self.assertEqual(plans[0]["validation_evidence"]["event_id"], "e1")
        self.assertEqual(plans[0]["lifecycle_stage"], "active")
        self.assertEqual(plans[0]["event_status"], "confirmed")
        self.assertEqual(plans[0]["opportunity_status"], "initial_pending")
        first_opportunity = plans[0]["opportunity_id"]
        structure["structure_segment_id"] = "segment-b"
        second = StructurePlanBuilder({"min_real_risk_reward": 1.0}).build(
            "market-structure", "X", "M1", rows, structure,
        )
        self.assertNotEqual(first_opportunity, second[0]["opportunity_id"])

    def test_visit_requires_independent_departure(self):
        zone = dict(lower=99, upper=101, atr=2, visits=[], current_visit=None)
        for row in [bar(1, 100), bar(2, 100), bar(3, 100)]:
            visit(zone, row, .5)
        self.assertEqual(len(zone['visits']), 0)
        visit(zone, bar(4, 103), .5)
        self.assertEqual(len(zone['visits']), 1)
        visit(zone, bar(5, 100), .5)
        visit(zone, bar(6, 97), .5)
        self.assertEqual(len(zone['visits']), 2)

    def test_stream_restart_equals_prefix_replay_and_no_mutation(self):
        rows = [bar(60*i+60, 100 + (i%6)*.2) for i in range(80)]
        full = advance('X', 'M1', rows)
        prefix = advance('X', 'M1', rows[:65])
        saved = copy.deepcopy(prefix)
        resumed = advance('X', 'M1', rows, previous=prefix)
        self.assertEqual(full, resumed)
        self.assertEqual(prefix, saved)
        self.assertEqual(full, advance('X', 'M1', rows, previous=full))
        self.assertTrue(full['zones'])

    def test_unclosed_bars_excluded(self):
        rows = [bar(60*i+60, 100) for i in range(65)]
        future = dict(bar(99999, 50), is_closed=False)
        self.assertEqual(advance('X', 'M1', rows), advance('X', 'M1', rows+[future]))

    def test_momentum_is_direction_symmetric(self):
        rows = [bar(i, c) for i,c in enumerate([100,99,98,96])]
        down = momentum(rows, 2)
        up = momentum([dict(r, close=200-r['close'], high=200-r['low'], low=200-r['high']) for r in rows], 2)
        self.assertEqual(down['direction'], 'sell')
        self.assertEqual(up['direction'], 'buy')
        self.assertEqual(down['efficiency'], up['efficiency'])

    def test_density_zone_has_revision(self):
        rows = [bar(60*i, 100 + (i % 4) * .1) for i in range(40)]
        result = advance("X", "M1", rows)
        self.assertTrue(result["zones"])
        self.assertTrue(result["zones"][0].get("zone_revision"))

    def test_density_zone_keeps_identity_when_atr_bin_moves(self):
        rows = [bar(60*i, 100.1) for i in range(40)]
        first = advance("X", "M1", rows, config={"zone_bin_atr": 0.5})
        second = advance(
            "X", "M1", rows, config={"zone_bin_atr": 0.55}, previous=first,
        )
        self.assertTrue(first["zones"])
        self.assertTrue(second["zones"])
        self.assertEqual(first["zones"][0]["zone_id"], second["zones"][0]["zone_id"])
        self.assertEqual(second["zones"][0]["identity_source"], "previous_snapshot")

    def test_density_zone_revision_changes_when_inherited_band_moves(self):
        rows = [bar(60*i, 100.1) for i in range(40)]
        first = advance("X", "M1", rows, config={"zone_bin_atr": 0.5})
        moved_rows = rows[:-1] + [bar(60*39, 100.25)]
        second = advance(
            "X", "M1", moved_rows, config={"zone_bin_atr": 0.55}, previous=first,
        )
        self.assertTrue(second["zones"])
        self.assertEqual(first["zones"][0]["zone_id"], second["zones"][0]["zone_id"])
        self.assertNotEqual(first["zones"][0]["zone_revision"], second["zones"][0]["zone_revision"])

    def test_zone_lifecycle_states_are_deterministic(self):
        rows = [bar(1, 100.0), bar(2, 100.0), bar(3, 100.0)]
        zone = {"zone_id": "z1", "lower": 99.0, "upper": 101.0,
                "visits": [], "current_visit": None}
        _update_zone_states([zone], [], rows, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "candidate")

        zone["visits"] = [{"left_direction": "up", "left_price": 102.0}]
        _update_zone_states([zone], [], rows, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "tested")

        reversal = [{"event_id": "e1", "zone_id": "z1",
                     "type": "pressure_reversal_confirmed", "confirmed_at": 3}]
        _update_zone_states([zone], reversal, rows, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "rejected")

        breakout = [{"event_id": "e2", "zone_id": "z1",
                     "type": "zone_breakout_confirmed", "direction": "buy",
                     "confirmed_at": 3}]
        outside = [bar(1, 100.0), bar(2, 100.0), bar(3, 102.0)]
        _update_zone_states([zone], breakout, outside, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "breakout_watch")
        self.assertEqual(breakout[0]["event_status"], "confirmed")
        inside = [bar(1, 100.0), bar(2, 100.0), bar(3, 100.5)]
        _update_zone_states([zone], breakout, inside, 1.0, {"zone_leave_atr": .5})
        self.assertEqual(zone["status"], "invalidated")
        self.assertTrue(zone["invalidated"])
        self.assertEqual(breakout[0]["event_status"], "invalidated")

    def test_confirmed_pivots_form_support_resistance_zones(self):
        result = advance(
            "X", "M1", [bar(60*i, 100 + (i % 3) * .1) for i in range(40)],
            pivot_levels={
                "small": [
                    {"kind": "high", "price": 101.0, "index": 20},
                    {"kind": "high", "price": 101.2, "index": 25},
                    {"kind": "low", "price": 99.0, "index": 21},
                ],
                "medium": [{"kind": "high", "price": 101.1, "index": 28}],
            },
        )
        self.assertTrue(result["pivot_zones"])
        resistance = next(item for item in result["pivot_zones"] if item["boundary_type"] == "resistance")
        self.assertIn("small", resistance["layers"])

    def test_pressure_plan_has_stable_opportunity_and_zone_invalidation(self):
        structure = {
            "atr": 1.0, "major_state": "up", "internal_state": "up",
            "external_state": "up", "structure_segment_id": "seg-1",
            "structure_revision": "rev-1",
            "zone_pressure": {"zones": [{"zone_id": "z1", "lower": 99.0, "upper": 101.0}],
                "events": [{"event_id": "e1", "zone_id": "z1", "type": "zone_breakout_confirmed",
                    "direction": "buy", "level": 101.0, "boundary": 101.0,
                    "confirmed_at": 120}]},
        }
        rows = [bar(i * 60, 101.2) for i in range(3)]
        plans = StructurePlanBuilder({"min_real_risk_reward": 1.0}).build(
            "market-structure", "X", "M1", rows, structure,
        )
        self.assertEqual(plans[0]["setup_type"], "pressure_zone_breakout")
        self.assertEqual(plans[0]["opportunity_stage"], "breakout")
        self.assertEqual(plans[0]["lifecycle_stage"], "confirmed")
        self.assertEqual(plans[0]["event_stage"], "breakout")
        self.assertEqual(plans[0]["structure_segment_id"], "seg-1")
        self.assertEqual(
            invalidate_reason(plans[0], 100.0), "pressure_zone_returned_inside"
        )

    def test_invalidated_zone_does_not_recreate_breakout_plan(self):
        structure = {
            "atr": 1.0, "major_state": "up", "internal_state": "up",
            "external_state": "up", "structure_segment_id": "seg-1",
            "zone_pressure": {
                "zones": [{"zone_id": "z1", "lower": 99.0, "upper": 101.0,
                            "status": "invalidated"}],
                "events": [{"event_id": "e1", "zone_id": "z1",
                    "type": "zone_breakout_confirmed", "direction": "buy",
                    "level": 101.0, "boundary": 101.0, "confirmed_at": 120}],
            },
        }
        rows = [bar(i * 60, 100.5) for i in range(3)]
        plans = StructurePlanBuilder({"min_real_risk_reward": 1.0}).build(
            "market-structure", "X", "M1", rows, structure,
        )
        self.assertTrue(plans)
        self.assertTrue(all(item.get("setup_type") == "no_trade" for item in plans))


if __name__ == '__main__':
    unittest.main()
