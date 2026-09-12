"""Application boundary for Paper order creation and lifecycle."""

import json
import uuid
from dataclasses import dataclass
from typing import Dict

from market.services.position_attribution import build_position_attribution
from repositories.instrument_specs import InstrumentSpecRepository, normalize_volume


@dataclass(frozen=True)
class PaperOrderCreationResult:
    created: bool
    reason_code: str
    message: str = ""
    order_id: str = ""

    def __bool__(self) -> bool:
        return self.created


class PaperOrderService:
    def __init__(self, paper_service):
        self.paper_service = paper_service

    def create(
        self, user_id: int, deployment, decision: Dict, now: int,
        execution_context=None,
    ) -> PaperOrderCreationResult:
        account_id = int(deployment["account_id"])
        if self.paper_service.storage.fetchone(
            "SELECT 1 FROM paper_orders WHERE account_id = ? AND decision_id = ?",
            (account_id, decision["decision_id"]),
        ):
            return PaperOrderCreationResult(
                False, "claim_conflict", "同一模拟账户已处理该策略决策",
            )
        strategy = self.paper_service._deployment_strategy(user_id, deployment)
        account_open_count = int(self.paper_service.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_positions WHERE account_id = ? AND status = 'open'",
            (account_id,),
        )["count"])
        account_limits = self.paper_service.storage.fetchone(
            "SELECT max_total_positions, max_single_volume FROM trading_accounts WHERE id = ?",
            (account_id,),
        )
        account_pending_count = int(self.paper_service.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id = ? AND status = 'pending'",
            (account_id,),
        )["count"])
        reason = (
            str(decision.get("decision_reason") or "模拟盘独立风控未通过")
            if decision.get("status") == "rejected" else ""
        )
        if not reason and account_open_count + account_pending_count >= int(
            account_limits["max_total_positions"]
        ):
            reason = "已达到账户最大持仓数"
        deployment_id = str(deployment["deployment_id"])
        strategy_count = int(self.paper_service.storage.fetchone(
            """
            SELECT (
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id = ? AND deployment_id = ? AND status = 'open'
            ) + (
                SELECT COUNT(*) FROM paper_orders
                WHERE account_id = ? AND deployment_id = ? AND status = 'pending'
            ) AS count
            """,
            (account_id, deployment_id, account_id, deployment_id),
        )["count"])
        if not reason and strategy_count >= max(
            1, int(strategy.get("max_positions", 3))
        ):
            reason = "已达到策略最大持仓数"
        same_direction = int(self.paper_service.storage.fetchone(
            """
            SELECT (
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id = ? AND deployment_id = ? AND symbol = ?
                  AND status = 'open' AND direction = ?
            ) + (
                SELECT COUNT(*) FROM paper_orders
                WHERE account_id = ? AND deployment_id = ? AND symbol = ?
                  AND status = 'pending' AND direction = ?
            ) AS count
            """,
            (
                account_id, deployment_id, decision["symbol"], decision["action"],
                account_id, deployment_id, decision["symbol"], decision["action"],
            ),
        )["count"])
        if not reason and same_direction >= max(
            1, int(strategy.get("max_same_direction", 2))
        ):
            reason = "已达到同方向最大持仓数"
        entry = float(decision.get("entry_price", 0))
        sl = float(decision.get("sl", 0))
        tp = float(decision.get("tp", 0))
        summary = decision.get("signal_summary") or {}
        source_id = str(summary.get("selected_signal_source_id", ""))
        source = str(summary.get("selected_signal_source", ""))
        management = summary.get("position_management") or {}
        policy_snapshot = management.get("policy_snapshot") or strategy.get(
            "position_management_policy_snapshot", {}
        )
        instrument_spec = InstrumentSpecRepository(self.paper_service.storage).get(
            account_id, str(decision.get("symbol") or "")
        )
        requested_volume = normalize_volume(
            float(decision.get("volume", 0.01)), instrument_spec, opening=True
        )
        attribution = build_position_attribution(
            summary,
            decision_id=str(decision.get("decision_id") or ""),
            strategy_id=str(decision.get("strategy_id") or ""),
            strategy_name=str(decision.get("strategy_name") or ""),
            direction=str(decision.get("action") or ""),
            entry_reason=str(decision.get("decision_reason") or ""),
            initial_stop_loss=sl,
            initial_take_profit=tp,
            initial_volume=requested_volume,
        )
        reason_code = ""
        if requested_volume <= 0:
            reason = "下单手数按品种最小手数/步进修正后为 0"
            reason_code = "invalid_volume"
        elif requested_volume > float(account_limits["max_single_volume"]):
            reason = "超过账户单笔最大手数"
            reason_code = "risk_limit"
        if not reason and not self.paper_service._valid_exits(decision["action"], entry, sl, tp):
            reason = "止盈止损价格无效"
            reason_code = "position_policy"
        if reason and not reason_code:
            if "持仓数" in reason:
                reason_code = "position_limit"
            elif decision.get("status") == "rejected":
                position_check = decision.get("position_check") or {}
                reason_code = (
                    "position_limit"
                    if position_check and not position_check.get("allowed", True)
                    else "risk_limit"
                )
            else:
                reason_code = "position_policy"
        order_id = uuid.uuid4().hex[:12]
        status = "rejected" if reason else "pending"
        trade_plan_id = str(attribution.get("trade_plan_id") or "")
        trade_plan_group_id = str(
            attribution.get("trade_plan_group_id") or ""
        )
        claimed_structure_plan = False
        plan_context = {
            "plan_id": "", "group_id": "", "deployment": None,
            "claimed": False,
        }
        if trade_plan_id and status == "pending":
            decision_object = type("DecisionContext", (), {
                "strategy_id": str(deployment["strategy_id"]),
                "action": str(decision.get("action") or "none"),
                "decision_reason": str(decision.get("decision_reason") or ""),
                "signal_summary": summary,
            })()
            account_snapshot = {
                "open_positions": account_open_count,
                "pending_orders": account_pending_count,
                "strategy_positions_and_orders": strategy_count,
                "same_direction_positions_and_orders": same_direction,
                "requested_volume": requested_volume,
            }
            plan_context = self.paper_service.structure_plan_execution_coordinator.claim_for_decision(
                int(user_id), account_id, decision_object,
                deployment_id=deployment_id, execution_mode="paper",
                tick_id=str(getattr(execution_context, "tick_id", "")),
                gate_trace=[{
                    "allowed": True, "reason_code": "eligible",
                    "message": "模拟账户订单创建门禁通过", "details": {},
                }],
                account_snapshot=account_snapshot,
            )
            claimed_structure_plan = bool(plan_context.get("claimed"))
            if not claimed_structure_plan:
                # Another Tick/worker (or an earlier decision) has already
                # consumed this public plan for the same deployment.
                return PaperOrderCreationResult(
                    False, "claim_conflict",
                    "同一部署已消费该公共结构计划阶段和方向",
                )
        try:
            self.paper_service.storage.execute(
                """
                INSERT INTO paper_orders(
                    order_id, user_id, account_id, deployment_id, strategy_id,
                    decision_id, symbol, direction, status, requested_volume,
                    requested_price, stop_loss, take_profit, confidence,
                    signal_source_id, exit_mode, trailing_activation_r,
                    trailing_distance_r, position_policy_snapshot_json,
                    position_attribution_json,
                    rejection_reason, requested_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, user_id, account_id, deployment["deployment_id"],
                    deployment["strategy_id"], decision["decision_id"],
                    decision["symbol"], decision["action"], status,
                    requested_volume, entry, sl, tp,
                    float(decision.get("confidence_score", 0)), source_id,
                    "position_manager", 1.0, 1.0,
                    json.dumps(policy_snapshot, ensure_ascii=False),
                    json.dumps(attribution, ensure_ascii=False),
                    reason, now, now, now,
                ),
            )
            if trade_plan_id and status == "pending":
                self.paper_service.structure_plan_execution_coordinator.record_order(
                    int(user_id), account_id, decision_object,
                    plan_context, order_id,
                )
            if status == "rejected":
                # The order can be rejected by the creation-time second guard
                # after the decision audit was persisted as pending.  Reflect
                # that terminal result immediately instead of leaving the
                # execution centre showing "等待模拟撮合" indefinitely.
                self.paper_service._sync_paper_decision_status(
                    int(user_id), account_id, str(decision.get("decision_id") or ""),
                    order_id, status="rejected", auto_executed=False,
                )
            self.paper_service._record_execution_receipt(
                int(user_id), account_id,
                {**decision, "order_id": order_id, "direction": decision.get("action"),
                 "requested_price": entry, "requested_volume": requested_volume,
                 "symbol": decision.get("symbol"),
                 "position_attribution_json": json.dumps(attribution, ensure_ascii=False)},
                status,
                reason,
            )
            if status == "rejected":
                return PaperOrderCreationResult(
                    False, reason_code or "position_policy", reason, order_id,
                )
            return PaperOrderCreationResult(True, "eligible", "模拟订单已创建", order_id)
        except Exception as exc:
            if claimed_structure_plan:
                self.paper_service.structure_plan_execution_coordinator.release(
                    int(user_id), account_id, plan_context,
                    reason=f"模拟订单写入失败：{exc}",
                )
            if "UNIQUE constraint failed" in str(exc):
                return PaperOrderCreationResult(
                    False, "claim_conflict", "同一模拟账户已处理该策略决策",
                )
            raise
