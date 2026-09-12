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

    def test_claim_uses_explicit_deployment_mode_stage_and_direction(self):
        calls = []

        class _ExecutionService:
            def claim(self, **kwargs):
                calls.append(kwargs)
                return True

        coordinator = StructurePlanExecutionCoordinator(None, _ExecutionService())
        decision = SimpleNamespace(
            strategy_id="strategy-1", action="buy", decision_reason="triggered",
            signal_summary={
                "selected_trade_plan_id": "plan-1",
                "selected_trade_plan_group_id": "group-1",
                "selected_trade_opportunity_stage": "breakout",
            },
        )
        context = coordinator.claim_for_decision(
            7, 22, decision, deployment_id="deployment-1",
            execution_mode="paper", tick_id="tick-1",
        )

        self.assertTrue(context["claimed"])
        self.assertEqual(calls[0]["deployment_id"], "deployment-1")
        self.assertEqual(calls[0]["execution_mode"], "paper")
        self.assertEqual(calls[0]["tick_id"], "tick-1")
        self.assertEqual(calls[0]["plan"]["plan_stage"], "breakout")
        self.assertEqual(calls[0]["plan"]["direction"], "buy")

if __name__ == "__main__":
    unittest.main()
