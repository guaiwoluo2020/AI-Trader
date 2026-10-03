import unittest

from market.services.structure_events import decide_event_observations, collect_structure_events, event_matrix


class StructureEventDecisionTests(unittest.TestCase):
    def test_internal_bos_uses_swing_direction_and_is_accepted(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {"bias": "up"}, "external": {"bias": "up"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e1", "layer": "internal", "event_type": "bos", "direction": "up",
        }])[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["setup_type"], "internal_momentum")
        self.assertEqual(decision["direction_layer"], "swing")

    def test_internal_bos_against_swing_is_rejected(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {"bias": "down"}, "external": {"bias": "down"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e2", "layer": "internal", "event_type": "bos", "direction": "up",
        }])[0]
        self.assertEqual(decision["status"], "rejected")

    def test_swing_choch_becomes_reversal_observation(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "down"}, "swing": {"bias": "down"}, "external": {"bias": "up"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e3", "layer": "swing", "event_type": "choch", "direction": "up",
        }])[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["setup_type"], "structure_reversal")

    def test_internal_hl_creates_internal_pullback(self):
        structure = {"structure_hierarchy": {
            "internal": {
                "bias": "up",
                "pattern": "trend",
                "pivots": [{"label": "HL", "kind": "low", "price": 100, "index": 12}],
            },
            "swing": {"bias": "up", "pattern": "trend"},
            "external": {"bias": "up"},
        }}
        events = collect_structure_events(structure, "XAUUSD", "M1")
        hl = [event for event in events if event["event_type"] == "hl_confirmed" and event["layer"] == "internal"]
        self.assertEqual(len(hl), 1)
        decision = decide_event_observations(structure, hl)[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["plan_type"], "internal_pullback")
        self.assertEqual(decision["event_layer"], "internal")

    def test_hl_lh_is_catalogued_as_structural_event(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {
                "bias": "up", "pivots": [{"label": "HL", "kind": "low", "price": 100, "index": 12}],
            }, "external": {"bias": "up"},
        }}
        events = collect_structure_events(structure, "XAUUSD", "M1")
        hl = [event for event in events if event["event_type"] == "hl_confirmed"]
        self.assertEqual(len(hl), 1)
        decision = decide_event_observations(structure, hl)[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["setup_type"], "swing_pullback")
        self.assertEqual(decision["matrix_action"], "create_plan")

    def test_hl_touch_is_separate_from_hl_confirmation(self):
        structure = {"atr": 10, "latest_close": 101, "retest_proximity_atr": 0.4,
                     "structure_hierarchy": {
                         "internal": {"bias": "up"}, "swing": {
                             "bias": "up", "pivots": [{"label": "HL", "kind": "low", "price": 100, "index": 12}],
                         }, "external": {"bias": "up"},
                     }}
        events = collect_structure_events(structure, "XAUUSD", "M1")
        types = {event["event_type"] for event in events}
        self.assertIn("hl_confirmed", types)
        self.assertIn("hl_support_touched", types)

    def test_internal_range_breakout_is_internal_bos(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up", "pattern_detail": {
                "status": "breakout_confirmed", "breakout_direction": "up",
                "top": 110, "bottom": 90, "breakout_at": 12,
            }},
            "swing": {"bias": "up"}, "external": {"bias": "up"},
        }}
        events = collect_structure_events(structure, "XAUUSD", "M1")
        bos = [event for event in events if event["event_type"] == "bos" and event["layer"] == "internal"]
        self.assertEqual(len(bos), 1)
        decision = decide_event_observations(structure, bos)[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["plan_type"], "internal_range_breakout")
        self.assertEqual(decision["event_layer"], "internal")

    def test_failed_range_breakout_is_catalogued_as_reclaim_event(self):
        structure = {"range": {
            "status": "failed_breakout", "breakout_direction": "up", "top": 110, "bottom": 90,
        }, "structure_hierarchy": {
            "internal": {"bias": "down"}, "swing": {"bias": "down"}, "external": {"bias": "down"},
        }}
        events = collect_structure_events(structure, "XAUUSD", "M1")
        reclaim = [event for event in events if event["event_type"] == "reclaim"]
        self.assertEqual(len(reclaim), 1)
        self.assertEqual(reclaim[0]["direction"], "down")

    def test_matrix_allows_internal_bos_as_lower_confidence_plan(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {"bias": "up"}, "external": {"bias": "up"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e4", "layer": "internal", "event_type": "bos", "direction": "up",
        }])[0]
        self.assertEqual(decision["matrix_action"], "create_plan")
        self.assertEqual(decision["plan_type"], "internal_momentum")

    def test_matrix_exposes_layer_event_rules(self):
        rules = event_matrix()
        self.assertTrue(any(item["event_key"] == "swing:hl_confirmed" for item in rules))
        self.assertTrue(any(item["event_key"] == "internal:liquidity_sweep" for item in rules))

    def test_matrix_override_can_ignore_an_event(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {"bias": "up"}, "external": {"bias": "up"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e5", "layer": "internal", "event_type": "bos", "direction": "up",
        }], matrix_overrides={"internal:bos": {"enabled": False}})[0]
        self.assertEqual(decision["status"], "ignored")


if __name__ == "__main__":
    unittest.main()
