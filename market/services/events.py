"""Canonical application event names and immutable event envelope."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


MARKET_TICK_RECEIVED = "market_tick_received"
BAR_CLOSED = "bar_closed"
PIVOT_UPDATED = "pivot_updated"
STRUCTURE_UPDATED = "structure_updated"
STRUCTURE_PLAN_CREATED = "structure_plan_created"
STRUCTURE_PLAN_INVALIDATED = "structure_plan_invalidated"
STRATEGY_DECISION_CREATED = "strategy_decision_created"
ORDER_COMMAND_CREATED = "order_command_created"
ORDER_EXECUTION_REPORTED = "order_execution_reported"
POSITION_UPDATED = "position_updated"
POSITION_EVENT_RECORDED = "position_event_recorded"


@dataclass(frozen=True)
class ApplicationEvent:
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    user_id: int = 0
    account_id: int = 0
    symbol: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
