"""Unified subscription and exactly-once consumption for market plans."""
from __future__ import annotations

from typing import Dict, Optional


class PlanExecutionService:
    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def matches_subscription(plan: Dict, config: Dict) -> bool:
        direction = str(plan.get("direction") or "none")
        allowed = {str(x) for x in (config.get("allowed_directions") or ["buy", "sell"])}
        if direction in {"buy", "sell"} and direction not in allowed:
            return False
        setup_filter = {str(x).strip().lower() for x in (config.get("allowed_setups") or []) if str(x).strip()}
        if setup_filter and str(plan.get("setup_type") or "").lower() not in setup_filter:
            return False
        return True

    def claim(self, *, user_id: int, account_id: int, deployment_id: str,
              strategy_id: str, plan: Dict, reason: str = "",
              tick_id: str = "", execution_mode: str = "",
              reason_code: str = "claimed", gate_trace=None,
              account_snapshot=None) -> bool:
        return self.repository.claim_execution(
            user_id, account_id, deployment_id, strategy_id,
            str(plan.get("plan_id") or ""), str(plan.get("plan_group_id") or ""),
            plan_stage=str(plan.get("plan_stage") or "default"),
            direction=str(plan.get("direction") or "none"),
            tick_id=tick_id, execution_mode=execution_mode,
            reason_code=reason_code, reason=reason, payload=plan,
            gate_trace=gate_trace, account_snapshot=account_snapshot,
        )

    def record_order(self, *, user_id: int, account_id: int, deployment_id: str,
                     strategy_id: str, plan: Dict, order_id: str, reason: str = "",
                     tick_id: str = "", execution_mode: str = "",
                     gate_trace=None, account_snapshot=None) -> None:
        self.repository.record_execution(
            user_id, account_id, deployment_id, strategy_id,
            str(plan.get("plan_id") or ""), str(plan.get("plan_group_id") or ""),
            "ordered", order_id=order_id, reason=reason, payload=plan,
            plan_stage=str(plan.get("plan_stage") or "default"),
            direction=str(plan.get("direction") or "none"),
            tick_id=tick_id, execution_mode=execution_mode,
            reason_code="ordered", gate_trace=gate_trace,
            account_snapshot=account_snapshot,
        )

    def release(self, *, user_id: int, account_id: int, deployment_id: str,
                plan: Dict, reason: str) -> None:
        self.repository.release_claim(
            user_id, account_id, deployment_id, str(plan.get("plan_id") or ""),
            plan_stage=str(plan.get("plan_stage") or "default"),
            direction=str(plan.get("direction") or "none"), reason=reason,
        )

    def update_status(self, *, user_id: int, account_id: int, deployment_id: str,
                      plan_id: str, status: str, order_id: str = "",
                      reason: str = "", payload: Optional[Dict] = None,
                      plan_stage: str = "", direction: str = "",
                      reason_code: str = "") -> bool:
        return self.repository.update_execution_status(
            user_id, account_id, deployment_id, plan_id, status,
            order_id=order_id, reason=reason, payload=payload,
            plan_stage=plan_stage, direction=direction,
            reason_code=reason_code,
        )
