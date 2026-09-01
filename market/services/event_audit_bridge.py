"""Bridge selected domain events into the persistent audit log."""
from __future__ import annotations

from .events import ApplicationEvent, STRUCTURE_PLAN_CREATED, STRUCTURE_PLAN_INVALIDATED


class EventAuditBridge:
    """Persist low-volume lifecycle events without duplicating hot-path logs."""

    def __init__(self, system_log):
        self.system_log = system_log
        self.outbox = None
        self._subscriptions = []

    def attach(self, event_bus, outbox=None) -> None:
        self.outbox = outbox
        self._subscriptions.extend([
            event_bus.subscribe(STRUCTURE_PLAN_CREATED, self._on_plan_created),
            event_bus.subscribe(STRUCTURE_PLAN_INVALIDATED, self._on_plan_invalidated),
        ])

    def _on_plan_created(self, event: ApplicationEvent) -> None:
        if event.payload.get("_outbox_replayed"):
            return
        self._enqueue(event)
        self.system_log.add_log(
            "structure_plan_created", event.payload,
            symbol=event.symbol,
            message=f"结构交易计划刷新: {event.payload.get('count', 0)} 个",
            entity_type="structure_plan",
            entity_id=str(event.payload.get("strategy_id") or ""),
        )

    def _on_plan_invalidated(self, event: ApplicationEvent) -> None:
        if event.payload.get("_outbox_replayed"):
            return
        self._enqueue(event)
        self.system_log.add_log(
            "structure_plan_invalidated", event.payload,
            symbol=event.symbol,
            message=f"结构交易计划失效: {event.payload.get('reason') or ''}",
            entity_type="structure_plan",
            entity_id=str(event.payload.get("plan_id") or ""),
        )

    def _enqueue(self, event: ApplicationEvent) -> None:
        if self.outbox is None:
            return
        try:
            self.outbox.enqueue(
                event.name, event.payload, user_id=event.user_id,
                account_id=event.account_id, symbol=event.symbol,
                aggregate_type="structure_plan",
                aggregate_id=str(event.payload.get("plan_id") or event.payload.get("strategy_id") or ""),
            )
        except Exception as exc:
            # Outbox must never block the trading path.
            self.system_log.add_log("outbox_write_failed", {"error": str(exc)}, symbol=event.symbol)
