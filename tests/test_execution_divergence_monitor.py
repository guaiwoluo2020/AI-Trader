import unittest

from market.services.execution_divergence_monitor import ExecutionDivergenceMonitor


class ExecutionDivergenceMonitorTests(unittest.TestCase):
    def test_missing_deployment_audit_is_technical_divergence(self):
        deployments = [
            {"deployment_id": "live-1", "execution_mode": "live"},
            {"deployment_id": "paper-1", "execution_mode": "paper"},
        ]
        audits = [{
            "deployment_id": "paper-1", "execution_mode": "paper",
            "status": "eligible", "reason_code": "eligible", "tick_id": "tick-1",
        }]

        result = ExecutionDivergenceMonitor().build_matrix(deployments, audits)

        self.assertTrue(result["diverged"])
        self.assertEqual(result["findings"][0]["type"], "missing_audit")

    def test_account_risk_difference_is_expected_not_technical_divergence(self):
        deployments = [
            {"deployment_id": "live-1", "execution_mode": "live"},
            {"deployment_id": "paper-1", "execution_mode": "paper"},
        ]
        audits = [
            {"deployment_id": "live-1", "execution_mode": "live", "status": "blocked",
             "reason_code": "risk_limit", "tick_id": "tick-1"},
            {"deployment_id": "paper-1", "execution_mode": "paper", "status": "eligible",
             "reason_code": "eligible", "tick_id": "tick-1"},
        ]

        result = ExecutionDivergenceMonitor().build_matrix(deployments, audits)

        self.assertFalse(result["diverged"])
        self.assertEqual(result["findings"][0]["type"], "account_gate_difference")

    def test_snapshot_missing_is_technical_divergence(self):
        result = ExecutionDivergenceMonitor().build_matrix(
            [{"deployment_id": "live-1", "execution_mode": "live"}],
            [{"deployment_id": "live-1", "execution_mode": "live", "status": "blocked",
              "reason_code": "snapshot_missing", "tick_id": "tick-1"}],
        )

        self.assertTrue(result["diverged"])
        self.assertEqual(result["findings"][0]["type"], "snapshot_missing")

    def test_execution_lifecycle_is_merged_without_hiding_gate_result(self):
        result = ExecutionDivergenceMonitor().build_matrix(
            [{"deployment_id": "paper-1", "execution_mode": "paper"}],
            [{
                "deployment_id": "paper-1", "execution_mode": "paper",
                "status": "ordered", "reason_code": "eligible", "tick_id": "tick-1",
                "updated_at": 100,
            }],
            [{
                "deployment_id": "paper-1", "status": "filled",
                "order_id": "order-1", "reason_code": "filled",
                "reason": "模拟订单已成交", "updated_at": 120,
            }],
        )

        row = result["rows"][0]
        self.assertEqual(row["gate_status"], "ordered")
        self.assertEqual(row["gate_reason_code"], "eligible")
        self.assertEqual(row["execution_status"], "filled")
        self.assertEqual(row["order_id"], "order-1")

    def test_shared_signal_outcome_difference_is_technical_divergence(self):
        result = ExecutionDivergenceMonitor().build_matrix(
            [
                {"deployment_id": "live-1", "execution_mode": "live"},
                {"deployment_id": "paper-1", "execution_mode": "paper"},
            ],
            [
                {"deployment_id": "live-1", "status": "no_action",
                 "reason_code": "no_direction", "updated_at": 100},
                {"deployment_id": "paper-1", "status": "ordered",
                 "reason_code": "eligible", "updated_at": 100},
            ],
        )

        self.assertTrue(result["diverged"])
        self.assertEqual(result["findings"][0]["type"], "shared_decision_difference")


if __name__ == "__main__":
    unittest.main()
