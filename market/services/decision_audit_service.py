"""Persistence and audit side effects for strategy decisions."""
from __future__ import annotations

from market.services.strategy.transient_decision_store import transient_decision_store


class DecisionAuditService:
    def record(self, decision, *, user_id, account_id, history, runtime_repository, system_log) -> None:
        if decision.action == "none" and decision.decision_type == "no_action":
            transient_decision_store.record(user_id, account_id, decision)
            return
        transient_decision_store.clear_for_strategy(
            user_id, account_id, decision.strategy_id, decision.symbol,
        )
        history.append(decision)
        rejected = decision.status == "rejected"
        system_log.add_log(
            "risk_blocked" if rejected else "strategy_decision_created",
            {
                "strategy_id": decision.strategy_id,
                "strategy_name": decision.strategy_name,
                "action": decision.action,
                "confidence": decision.confidence_score,
                "entry_price": decision.entry_price,
                "volume": decision.volume,
                "stop_loss": decision.sl,
                "take_profit": decision.tp,
                "order_id": decision.order_id,
                "position_check": decision.position_check,
                "risk_check": decision.risk_check,
            },
            symbol=decision.symbol,
            message=decision.decision_reason,
            level="warning" if rejected else "info",
            category="risk" if rejected else "trading",
            status=decision.status,
            entity_type="strategy_decision",
            entity_id=decision.decision_id,
            correlation_id=decision.order_id or decision.decision_id,
        )
        if runtime_repository:
            runtime_repository.upsert_entity(
                "strategy_decision", decision.decision_id, decision.to_dict(),
                symbol=decision.symbol, status=decision.status,
            )
            runtime_repository.trim_entities("strategy_decision", 1000)
