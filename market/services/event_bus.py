"""Small synchronous in-process event bus.

Handlers are isolated: one observer failure is logged and cannot interrupt the
trading path. This is intentionally transport-agnostic and can later be
replaced by an outbox adapter without changing publishers.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from threading import RLock
from typing import Callable, Dict, List

from .events import ApplicationEvent

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[Callable[[ApplicationEvent], None]]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_name: str, handler: Callable[[ApplicationEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._handlers[str(event_name)].append(handler)
        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(str(event_name), [])
                if handler in handlers:
                    handlers.remove(handler)
        return unsubscribe

    def publish(self, event: ApplicationEvent) -> int:
        with self._lock:
            handlers = list(self._handlers.get(event.name, [])) + list(self._handlers.get("*", []))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("event handler failed: %s", event.name)
        return len(handlers)

