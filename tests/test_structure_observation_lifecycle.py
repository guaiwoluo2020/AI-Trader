import unittest

from market.services.structure_observation import advance_observation_state, observation_state


class StructureObservationLifecycleTests(unittest.TestCase):
    def test_plan_moves_from_touch_to_confirmation_to_trigger(self):
        plan = {}
        self.assertEqual(observation_state(plan), "watching")
        self.assertEqual(advance_observation_state(plan, touched=True), "confirming")
        self.assertEqual(advance_observation_state(plan, confirmed=True), "confirmed")
        self.assertEqual(advance_observation_state(plan, triggered=True), "triggered")

    def test_terminal_invalidation_wins_over_other_flags(self):
        plan = {}
        self.assertEqual(advance_observation_state(plan, touched=True, invalidated=True), "invalidated")


if __name__ == "__main__":
    unittest.main()
