"""Structure Plan claim/record/release workflow for order execution."""
from __future__ import annotations


class StructurePlanExecutionCoordinator:
    def __init__(self, repository, execution_service):
        self.repository = repository
        self.execution_service = execution_service

    def claim_for_decision(self, user_id: int, account_id: int, decision):
        summary = decision.signal_summary or {}
        plan_id = str(summary.get("selected_trade_plan_id") or "")
        group_id = str(summary.get("selected_trade_plan_group_id") or "")
        if not plan_id:
            return {"plan_id": "", "group_id": "", "deployment": None, "claimed": False}
        deployment = self.repository.storage.fetchone(
            "SELECT deployment_id FROM strategy_deployments "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND execution_mode='live' AND status='active' LIMIT 1",
            (int(user_id), int(account_id), str(decision.strategy_id)),
        )
        if not deployment:
            return {"plan_id": plan_id, "group_id": group_id, "deployment": None, "claimed": False}
        plan = {**summary, "plan_id": plan_id, "plan_group_id": group_id}
        claimed = self.execution_service.claim(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(deployment["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=plan,
            reason=str(decision.decision_reason or ""),
        )
        return {
            "plan_id": plan_id, "group_id": group_id,
            "deployment": deployment, "claimed": bool(claimed), "plan": plan,
        }

    def record_order(self, user_id: int, account_id: int, decision, context, order_id: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        self.execution_service.record_order(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=context["plan"],
            order_id=order_id, reason=str(decision.decision_reason or ""),
        )

    def release(self, user_id: int, account_id: int, context, reason: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        self.execution_service.release(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            plan={"plan_id": context.get("plan_id", "")}, reason=reason,
        )
