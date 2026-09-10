import unittest
from types import SimpleNamespace

from market.services.structure_plan_execution_coordinator import StructurePlanExecutionCoordinator


class StagedExecutionTests(unittest.TestCase):
    def decision(self, stage="breakout", action="buy"):
        return SimpleNamespace(action=action, signal_summary={
            "selected_trade_opportunity_stage": stage,
        })

    def test_breakout_requires_same_direction_protected_position(self):
        decision = self.decision()
        self.assertFalse(
            StructurePlanExecutionCoordinator.validate_stage(decision, [])["allowed"]
        )
        self.assertFalse(
            StructurePlanExecutionCoordinator.validate_stage(
                decision, [{"direction": "buy", "sl": 0}]
            )["allowed"]
        )
        result = StructurePlanExecutionCoordinator.validate_stage(
            decision, [{"direction": "buy", "sl": 99.0}]
        )
        self.assertTrue(result["allowed"])

    def test_initial_stage_does_not_require_existing_position(self):
        result = StructurePlanExecutionCoordinator.validate_stage(
            self.decision("initial"), []
        )
        self.assertTrue(result["allowed"])

if __name__ == "__main__":
    unittest.main()
