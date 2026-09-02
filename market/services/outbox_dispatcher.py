"""Durable outbox event dispatcher.

The dispatcher deliberately contains no business logic. Producers persist an
event through :class:`OutboxEventRepository`; the application registers a
handler for the event name and this service takes care of claiming, retrying
and recovering abandoned deliveries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional


EventHandler = Callable[[Dict], None]


@dataclass(frozen=True)
class DispatchStats:
    claimed: int = 0
    published: int = 0
    failed: int = 0
    skipped: int = 0
    recovered: int = 0


class OutboxDispatcher:
    """Dispatch one bounded batch of outbox events.

    A handler must be idempotent because a process crash after the handler
    completes and before ``mark_published`` can cause a redelivery.
    """

    def __init__(self, repository, event_bus=None, *, handlers: Optional[Dict[str, EventHandler]] = None,
                 retry_seconds: int = 60, lease_seconds: int = 30):
        self.repository = repository
        self.event_bus = event_bus
        self.handlers: Dict[str, EventHandler] = dict(handlers or {})
        self.retry_seconds = max(1, int(retry_seconds))
        self.lease_seconds = max(1, int(lease_seconds))
        self._running = False

    def start(self) -> None:
        """Compatibility lifecycle hook; process-level manager owns polling."""
        self._running = True

    def stop(self) -> None:
        self._running = False

    def register(self, event_name: str, handler: EventHandler) -> None:
        name = str(event_name or "").strip()
        if not name:
            raise ValueError("事件名称不能为空")
        self.handlers[name] = handler

    def dispatch_once(self, *, limit: int = 100) -> DispatchStats:
        recovered = int(self.repository.recover_stale(self.lease_seconds) or 0)
        events = self.repository.claim_pending(limit)
        stats = DispatchStats(claimed=len(events), recovered=recovered)
        published = failed = skipped = 0
        for event in events:
            handler = self.handlers.get(str(event.get("event_name") or ""))
            if handler is None:
                skipped += 1
                self.repository.mark_failed(
                    event["event_id"],
                    f"未注册事件处理器: {event.get('event_name')}",
                    retry_seconds=self.retry_seconds,
                )
                failed += 1
                continue
            try:
                handler(event)
            except Exception as exc:
                self.repository.mark_failed(
                    event["event_id"], str(exc), retry_seconds=self.retry_seconds
                )
                failed += 1
            else:
                self.repository.mark_published(event["event_id"])
                published += 1
        return DispatchStats(
            claimed=len(events), published=published, failed=failed,
            skipped=skipped, recovered=recovered,
        )
