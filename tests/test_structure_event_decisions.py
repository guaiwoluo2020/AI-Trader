import unittest

from market.services.structure_events import decide_event_observations


class StructureEventDecisionTests(unittest.TestCase):
    def test_internal_bos_uses_swing_direction_and_is_accepted(self):
        structure = {"structure_hierarchy": {
            "internal": {"bias": "up"}, "swing": {"bias": "up"}, "external": {"bias": "up"},
        }}
        decision = decide_event_observations(structure, [{
            "event_id": "e1", "layer": "internal", "event_type": "bos", "direction": "up",
        }])[0]
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(decision["setup_type"], "trend_continuation")
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
        self.assertEqual(decision["setup_type"], "choch_reversal")


if __name__ == "__main__":
    unittest.main()
