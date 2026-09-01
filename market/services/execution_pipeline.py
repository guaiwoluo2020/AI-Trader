"""Shared order-command pipeline used by Paper and live strategy execution.

This first extraction centralizes the decision-to-pending-order transition.
The execution adapter remains responsible for Paper/MT5 transport, while this
module guarantees identical order metadata and attribution for both modes.
"""

from __future__ import annotations

from typing import Optional

from ..models import TradingDecision
from .position_attribution import build_position_attribution


class ExecutionPipeline:
    """Convert an accepted strategy decision into an executable order command."""

    def execute(self, decision: TradingDecision, pending_order_service) -> Optional[str]:
        if decision.action == "none" or decision.status == "rejected":
            return None
        if pending_order_service is None:
            return None

        order_action = "b" if decision.action == "buy" else "s"
        source_id = str(decision.signal_summary.get("selected_signal_source_id", ""))
        description = f"AIT|{decision.strategy_id}|{source_id}"
        attribution = build_position_attribution(
            decision.signal_summary,
            decision_id=decision.decision_id,
            strategy_id=decision.strategy_id,
            strategy_name=decision.strategy_name,
            direction=decision.action,
            entry_reason=decision.decision_reason,
            initial_stop_loss=decision.sl,
            initial_take_profit=decision.tp,
            initial_volume=decision.volume,
        )
        order_id = pending_order_service.create_order(
            symbol=decision.symbol,
            action=order_action,
            price=decision.entry_price,
            mount=decision.volume,
            sl=decision.sl,
            tp=decision.tp,
            reason=decision.decision_reason,
            description=description,
            source="strategy_decision",
            strategy_id=decision.strategy_id,
            strategy_name=decision.strategy_name,
            signal_source_id=source_id,
            exit_mode="position_manager",
            trailing_activation_r=1.0,
            trailing_distance_r=1.0,
            decision_id=decision.decision_id,
            position_attribution=attribution,
        )
        decision.order_id = order_id
        decision.status = "pending"
        confirmed_order = pending_order_service.confirm_order(order_id)
        if confirmed_order:
            decision.auto_executed = True
            decision.status = "confirmed"
        return order_id
