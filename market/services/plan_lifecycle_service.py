"""Validated lifecycle transitions for public structure trade plans."""
from __future__ import annotations

import json
import time
from typing import Dict, Set


class PlanLifecycleService:
    TRANSITIONS: Dict[str, Set[str]] = {
        "candidate": {"confirmed", "active", "expired", "invalidated", "superseded", "canceled"},
        "confirmed": {"active", "expired", "invalidated", "superseded", "canceled"},
        "active": {"expired", "invalidated", "superseded", "canceled"},
        "expired": set(), "invalidated": set(), "superseded": set(),
        "canceled": set(),
    }

    def __init__(self, repository, event_bus=None):
        self.repository = repository
        self.event_bus = event_bus

    @classmethod
    def can_transition(cls, current: str, target: str) -> bool:
        current = str(current or "").lower()
        target = str(target or "").lower()
        return current == target or target in cls.TRANSITIONS.get(current, set())

    def transition(self, plan_id: str, target: str, reason: str = "", *, expected: str = "") -> bool:
        target = str(target or "").lower()
        row = self.repository.storage.fetchone(
            "SELECT status,payload_json,user_id,account_id,symbol FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row:
            return False
        current = str(row["status"] or "").lower()
        if expected and current != str(expected).lower():
            return False
        if not self.can_transition(current, target):
            return False
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        payload["status"] = target
        payload["lifecycle_reason"] = str(reason or "")
        payload["lifecycle_updated_at"] = int(time.time())
        self.repository.storage.execute(
            "UPDATE structure_trade_plans SET status=?,payload_json=?,updated_at=? WHERE plan_id=?",
            (target, json.dumps(payload, ensure_ascii=False), int(time.time()), str(plan_id)),
        )
        if self.event_bus:
            from market.services.events import ApplicationEvent, STRUCTURE_PLAN_INVALIDATED
            if target in {"invalidated", "expired", "superseded", "canceled"}:
                self.event_bus.publish(ApplicationEvent(
                    STRUCTURE_PLAN_INVALIDATED,
                    {"plan_id": str(plan_id), "status": target, "reason": str(reason or "")},
                    int(row["user_id"] or 0), int(row["account_id"] or 0), str(row["symbol"] or ""),
                ))
        return True
