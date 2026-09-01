"""Background delivery worker for persisted outbox events."""
from __future__ import annotations

import threading
import time

from .events import ApplicationEvent


class OutboxDispatcher:
    def __init__(self, repository, event_bus, *, interval: float = 2.0, batch_size: int = 50):
        self.repository = repository
        self.event_bus = event_bus
        self.interval = max(0.2, float(interval))
        self.batch_size = max(1, min(int(batch_size), 200))
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        try:
            self.repository.recover_stale()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, name="outbox-dispatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(max(0.1, float(timeout)))
        self._thread = None

    def dispatch_once(self) -> int:
        self.repository.recover_stale()
        count = 0
        for item in self.repository.claim_pending(self.batch_size):
            try:
                payload = dict(item.get("payload") or {})
                payload["_outbox_replayed"] = True
                self.event_bus.publish(ApplicationEvent(
                    item["event_name"], payload,
                    int(item.get("user_id") or 0), int(item.get("account_id") or 0),
                    str(item.get("symbol") or ""),
                ))
                self.repository.mark_published(item["event_id"])
                count += 1
            except Exception as exc:
                self.repository.mark_failed(item["event_id"], str(exc))
        return count

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.dispatch_once()
            except Exception:
                # A database outage must not kill the worker permanently.
                pass
            self._stop.wait(self.interval)
