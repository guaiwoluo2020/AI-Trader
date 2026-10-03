import unittest

from market.services.structure_observation import build_observation_plans


class StructureObservationPlannerTests(unittest.TestCase):
    def test_builds_parent_child_event_chain(self):
        events = [
            {"event_id": "e1", "layer": "swing", "event_type": "hl_confirmed"},
            {"event_id": "e2", "layer": "swing", "event_type": "hl_support_touched", "parent_event_id": "e1"},
        ]
        decisions = [{
            "decision_id": "d2", "event_id": "e2", "status": "accepted",
            "matrix_action": "update_plan", "plan_type": "event_confirmation",
            "event_layer": "swing", "direction_layer": "swing", "entry_layer": "swing",
            "direction": "up", "event_type": "hl_support_touched",
            "required_confirmation": "retest_or_reclaim",
        }]
        plans = build_observation_plans(events, decisions, period="M5")
        self.assertEqual(plans[0]["event_chain"], ["swing:hl_confirmed", "swing:hl_support_touched"])
        self.assertEqual(plans[0]["confirmation_period"], "M5")
        self.assertEqual(plans[0]["status"], "confirming")

    def test_ignored_decisions_do_not_create_observations(self):
        plans = build_observation_plans([], [{"status": "ignored", "matrix_action": "ignore"}], period="M1")
        self.assertEqual(plans, [])


if __name__ == "__main__":
    unittest.main()
