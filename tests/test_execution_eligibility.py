import unittest
from types import SimpleNamespace

from market.services.execution_eligibility import (
    ExecutionEligibilityEvaluator,
    ExecutionGateResult,
    classify_execution_outcome,
    execution_audit_targets,
    record_preflight_audits,
)


class _AuditRepository:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


class ExecutionEligibilityTests(unittest.TestCase):
    def test_execution_audit_targets_keep_each_structure_plan_stage(self):
        targets = execution_audit_targets([
            SimpleNamespace(
                action="buy", is_entry_trigger=True,
                trade_plan_id="plan-1", trade_opportunity_stage="initial",
            ),
            SimpleNamespace(
                action="buy", is_entry_trigger=True,
                trade_plan_id="plan-1", trade_opportunity_stage="breakout",
            ),
            SimpleNamespace(
                action="none", is_entry_trigger=False,
                trade_plan_id="", trade_opportunity_stage="",
            ),
        ])

        self.assertEqual(targets, [
            {"plan_id": "plan-1", "plan_stage": "initial", "direction": "buy"},
            {"plan_id": "plan-1", "plan_stage": "breakout", "direction": "buy"},
        ])

    def test_record_preflight_audits_associates_block_with_visible_plans(self):
        repository = _AuditRepository()
        result = ExecutionEligibilityEvaluator.preflight(
            automation_enabled=True,
            account_status="active",
            account_enabled=True,
            trading_enabled=False,
            auto_trading_enabled=True,
        )

        record_preflight_audits(
            repository,
            user_id=7,
            account_id=22,
            deployment_id="dep-1",
            strategy_id="strategy-1",
            tick_id="tick-1",
            execution_mode="paper",
            symbol="BTCUSD#",
            signals=[SimpleNamespace(
                action="sell", is_entry_trigger=True,
                trade_plan_id="plan-1", trade_opportunity_stage="breakout",
            )],
            result=result,
        )

        self.assertEqual(len(repository.calls), 1)
        audit = repository.calls[0]
        self.assertEqual(audit["plan_id"], "plan-1")
        self.assertEqual(audit["plan_stage"], "breakout")
        self.assertEqual(audit["direction"], "sell")
        self.assertEqual(audit["reason_code"], "trading_disabled")

    def test_preflight_reports_account_inactive_before_trading_switches(self):
        result = ExecutionEligibilityEvaluator.preflight(
            automation_enabled=True,
            account_status="closed",
            account_enabled=False,
            trading_enabled=False,
            auto_trading_enabled=False,
            authorization_allowed=False,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "account_inactive")
        self.assertEqual(
            [item["reason_code"] for item in result.gate_trace],
            ["automation_enabled", "account_inactive"],
        )

    def test_preflight_reports_trading_disabled(self):
        result = ExecutionEligibilityEvaluator.preflight(
            automation_enabled=True,
            account_status="active",
            account_enabled=True,
            trading_enabled=True,
            auto_trading_enabled=False,
            authorization_allowed=True,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "trading_disabled")

    def test_preflight_reports_live_authorization_block(self):
        result = ExecutionEligibilityEvaluator.preflight(
            automation_enabled=True,
            account_status="active",
            account_enabled=True,
            trading_enabled=True,
            auto_trading_enabled=True,
            authorization_allowed=False,
            authorization_message="当前会员不允许实盘交易",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "authorization_blocked")
        self.assertEqual(result.message, "当前会员不允许实盘交易")

    def test_preflight_reports_global_automation_disabled(self):
        result = ExecutionEligibilityEvaluator.preflight(
            automation_enabled=False,
            account_status="active",
            account_enabled=True,
            trading_enabled=True,
            auto_trading_enabled=True,
            authorization_allowed=True,
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "automation_disabled")

    def test_evaluator_stops_at_first_failed_gate_and_keeps_ordered_trace(self):
        evaluator = ExecutionEligibilityEvaluator()
        result = evaluator.evaluate([
            ExecutionGateResult.allow("snapshot", "共享快照可用"),
            ExecutionGateResult.deny("position_limit", "已达到同方向最大持仓数"),
            ExecutionGateResult.allow("risk_limit", "账户风险允许"),
        ])

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "position_limit")
        self.assertEqual(
            [item["reason_code"] for item in result.gate_trace],
            ["snapshot", "position_limit"],
        )

    def test_snapshot_missing_has_stable_reason_code(self):
        result = ExecutionEligibilityEvaluator.snapshot_missing(
            "strategy-1", "tick-1"
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "snapshot_missing")
        self.assertEqual(result.details["strategy_id"], "strategy-1")

    def test_classifies_no_action_cooldown_from_structured_summary(self):
        outcome = classify_execution_outcome({
            "action": "none",
            "status": "skipped",
            "decision_reason": "当前账户、部署和计划阶段仍在决策冷却中",
            "signal_summary": {
                "decision_cooldown": {"cooldown_key": "paper:1:2"},
            },
        })

        self.assertEqual(outcome.status, "no_action")
        self.assertEqual(outcome.reason_code, "decision_cooldown")

    def test_classifies_position_rejection_before_generic_risk_rejection(self):
        outcome = classify_execution_outcome({
            "action": "buy",
            "status": "rejected",
            "decision_reason": "风控拦截: 已达到同方向最大持仓数",
            "position_check": {
                "allowed": False,
                "warnings": ["已达到同方向最大持仓数"],
            },
            "risk_check": {"allowed": True},
        })

        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.reason_code, "position_limit")

    def test_order_creation_reason_overrides_decision_level_reason(self):
        outcome = classify_execution_outcome(
            {"action": "sell", "status": "pending", "decision_reason": "可执行"},
            creation_result={
                "created": False,
                "reason_code": "claim_conflict",
                "message": "同一部署已消费该计划阶段",
            },
        )

        self.assertEqual(outcome.status, "blocked")
        self.assertEqual(outcome.reason_code, "claim_conflict")


if __name__ == "__main__":
    unittest.main()
