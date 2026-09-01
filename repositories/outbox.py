"""Reliable persistence for domain events awaiting delivery."""
from __future__ import annotations

import json
import time
import uuid
from typing import Dict, List, Optional


class OutboxEventRepository:
    def __init__(self, storage):
        self.storage = storage

    def enqueue(self, event_name: str, payload: Dict, *, user_id: int = 0,
                account_id: int = 0, symbol: str = "", aggregate_type: str = "",
                aggregate_id: str = "") -> str:
        event_id = uuid.uuid4().hex
        now = int(time.time())
        self.storage.execute(
            """INSERT INTO outbox_events(
                event_id,event_name,aggregate_type,aggregate_id,user_id,account_id,
                symbol,payload_json,status,retry_count,next_retry_at,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, str(event_name), str(aggregate_type or ""), str(aggregate_id or ""),
             int(user_id or 0), int(account_id or 0), str(symbol or ""),
             json.dumps(payload or {}, ensure_ascii=False), "pending", 0, now, now),
        )
        return event_id

    def pending(self, limit: int = 100) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT * FROM outbox_events WHERE status='pending' AND next_retry_at<=? "
            "ORDER BY created_at LIMIT ?", (int(time.time()), max(1, min(int(limit), 500))),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except (TypeError, ValueError):
                item["payload"] = {}
            result.append(item)
        return result

    def claim_pending(self, limit: int = 100) -> List[Dict]:
        """Atomically reserve a batch for one dispatcher instance."""
        rows = self.pending(limit)
        claimed = []
        for item in rows:
            self.storage.execute(
                "UPDATE outbox_events SET status='publishing',claimed_at=?,lease_until=?,updated_at=? "
                "WHERE event_id=? AND status='pending'",
                (int(time.time()), int(time.time()) + 30, int(time.time()), item["event_id"]),
            )
            current = self.storage.fetchone(
                "SELECT status FROM outbox_events WHERE event_id=?", (item["event_id"],)
            )
            if current and str(current["status"]) == "publishing":
                claimed.append(item)
        return claimed

    def recover_stale(self, lease_seconds: int = 30) -> int:
        """Return abandoned publishing events to pending after a crash."""
        now = int(time.time())
        self.storage.execute(
            "UPDATE outbox_events SET status='pending',next_retry_at=?,updated_at=? "
            "WHERE status='publishing' AND (lease_until IS NULL OR lease_until<=?)",
            (now, now, now),
        )
        return 0

    def mark_published(self, event_id: str) -> None:
        self.storage.execute(
            "UPDATE outbox_events SET status='published',published_at=?,lease_until=NULL,updated_at=? WHERE event_id=?",
            (int(time.time()), int(time.time()), str(event_id)),
        )

    def mark_failed(self, event_id: str, error: str, retry_seconds: int = 60) -> None:
        self.storage.execute(
            "UPDATE outbox_events SET status='pending',retry_count=retry_count+1,last_error=?,lease_until=NULL,"
            "next_retry_at=?,updated_at=? WHERE event_id=?",
            (str(error or "")[:500], int(time.time()) + max(1, int(retry_seconds)),
             int(time.time()), str(event_id)),
        )
