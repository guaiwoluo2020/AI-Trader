import unittest

from routes_structure_plans import assemble_structure_plan_execution


class StructurePlanRouteAssemblyTests(unittest.TestCase):
    def test_active_deployment_remains_visible_when_account_trading_is_disabled(self):
        plans = [{"plan_id": "plan-1"}]
        strategies = [{
            "strategy_id": "strategy-1",
            "strategy_name": "BTC M5",
            "period": "M5",
            "deployments": [],
        }]
        deployments = [{
            "deployment_id": "dep-1",
            "strategy_id": "strategy-1",
            "account_id": 22,
            "account_name": "ULTRAPAPER",
            "account_type": "paper",
            "execution_mode": "paper",
            "status": "active",
            "symbol": "BTCUSD#",
            "enabled": 1,
            "trading_enabled": 0,
            "auto_trading_enabled": 1,
        }]
        audits = [{
            "deployment_id": "dep-1",
            "plan_id": "plan-1",
            "status": "blocked",
            "reason_code": "trading_disabled",
            "tick_id": "tick-1",
            "updated_at": 100,
        }]

        result = assemble_structure_plan_execution(
            plans, strategies, deployments, [], audits, "BTCUSD#",
        )

        matrix = result[0]["execution_matrix"]
        self.assertEqual(len(matrix), 1)
        self.assertTrue(matrix[0]["active"])
        self.assertFalse(matrix[0]["account_execution_enabled"])
        self.assertEqual(matrix[0]["gate_reason_code"], "trading_disabled")


if __name__ == "__main__":
    unittest.main()
