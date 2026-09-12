"""Structure Plan claim/record/release workflow for order execution."""
from __future__ import annotations



class StructurePlanExecutionCoordinator:
    def __init__(self, repository, execution_service):
        self.repository = repository
        self.execution_service = execution_service

    @staticmethod
    def validate_stage(decision, positions) -> dict:
        """Validate account-side prerequisites for a staged opportunity.

        The market layer may publish a breakout-stage plan before the first
        trial position is visible to the account.  Treat that plan as an
        add-on candidate, never as an independent entry.  A broker position
        with a non-zero protective stop is the portable confirmation shared by
        Paper, MT5 and IBKR; without it the stage remains unexecutable.
        """
        summary = decision.signal_summary or {}
        stage = str(summary.get("selected_trade_opportunity_stage") or "")
        if stage != "breakout":
            return {"allowed": True, "stage": stage, "reason": "非突破加仓阶段"}
        direction = str(decision.action or "")
        matching = []
        for position in positions or []:
            item = position if isinstance(position, dict) else position.to_dict()
            item_direction = str(item.get("direction") or "").lower()
            if not item_direction:
                item_direction = "buy" if str(item.get("type") or "").upper() == "BUY" else "sell"
            if item_direction == direction:
                matching.append(item)
        if not matching:
            return {"allowed": False, "stage": stage, "reason": "突破阶段需要首仓已成交"}
        unprotected = [item for item in matching if float(item.get("sl") or 0) <= 0]
        if unprotected:
            return {"allowed": False, "stage": stage, "reason": "首仓保护止损尚未确认，禁止突破阶段加仓"}
        return {"allowed": True, "stage": stage, "reason": "首仓已成交且保护止损已确认", "position_count": len(matching)}

    def claim_for_decision(
        self, user_id: int, account_id: int, decision, *,
        deployment_id: str = "", execution_mode: str = "live",
        tick_id: str = "", gate_trace=None, account_snapshot=None,
    ):
        summary = decision.signal_summary or {}
        plan_id = str(summary.get("selected_trade_plan_id") or "")
        group_id = str(summary.get("selected_trade_plan_group_id") or "")
        if not plan_id:
            return {"plan_id": "", "group_id": "", "deployment": None, "claimed": False}
        deployment = {"deployment_id": str(deployment_id)} if deployment_id else None
        if deployment is None and self.repository is not None:
            deployment = self.repository.storage.fetchone(
                "SELECT deployment_id FROM strategy_deployments "
                "WHERE user_id=? AND account_id=? AND strategy_id=? "
                "AND execution_mode=? AND status='active' LIMIT 1",
                (int(user_id), int(account_id), str(decision.strategy_id),
                 str(execution_mode or "live")),
            )
        if not deployment:
            return {"plan_id": plan_id, "group_id": group_id, "deployment": None, "claimed": False}
        stage = str(summary.get("selected_trade_opportunity_stage") or "default")
        direction = str(decision.action or summary.get("direction") or "none").lower()
        plan = {
            **summary, "plan_id": plan_id, "plan_group_id": group_id,
            "plan_stage": stage, "direction": direction,
        }
        claimed = self.execution_service.claim(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(deployment["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=plan,
            reason=str(decision.decision_reason or ""),
            tick_id=str(tick_id or ""), execution_mode=str(execution_mode or ""),
            gate_trace=gate_trace, account_snapshot=account_snapshot,
        )
        return {
            "plan_id": plan_id, "group_id": group_id,
            "deployment": deployment, "claimed": bool(claimed), "plan": plan,
            "tick_id": str(tick_id or ""), "execution_mode": str(execution_mode or ""),
            "gate_trace": list(gate_trace or []),
            "account_snapshot": dict(account_snapshot or {}),
        }

    def record_order(self, user_id: int, account_id: int, decision, context, order_id: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        plan = context.get("plan") or {}
        self.execution_service.record_order(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            strategy_id=str(decision.strategy_id), plan=plan,
            order_id=order_id, reason=str(decision.decision_reason or ""),
            tick_id=str(context.get("tick_id") or ""),
            execution_mode=str(context.get("execution_mode") or ""),
            gate_trace=context.get("gate_trace") or [],
            account_snapshot=context.get("account_snapshot") or {},
        )

    def release(self, user_id: int, account_id: int, context, reason: str) -> None:
        if not context.get("claimed") or not context.get("deployment"):
            return
        self.execution_service.release(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(context["deployment"]["deployment_id"]),
            plan=context.get("plan") or {"plan_id": context.get("plan_id", "")},
            reason=reason,
        )
