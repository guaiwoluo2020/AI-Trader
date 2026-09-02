"""Exactly-once replay protection for AI and Structure trade plans."""
from __future__ import annotations

import json
from typing import Dict


class PlanReplayGuard:
    def __init__(self, storage):
        self.storage = storage

    def check_live(self, user_id: int, account_id: int, strategy, signal) -> Dict:
        setup_type = str(getattr(signal, "setup_type", "") or "generic_entry")
        setup_family = str(getattr(signal, "setup_family", "") or "generic")
        plan_id = str(
            getattr(signal, "trade_plan_id", "")
            or getattr(signal, "ai_plan_id", "") or ""
        )
        valid_from = int(
            getattr(signal, "trade_plan_valid_from", 0)
            or getattr(signal, "ai_plan_valid_from", 0) or 0
        )
        group_id = str(getattr(signal, "trade_plan_group_id", "") or "")
        instance_id = f"{plan_id}:{valid_from}" if plan_id and valid_from else plan_id
        if not instance_id:
            return {"allowed": True}
        payloads = self.storage.fetchall(
            "SELECT payload_json FROM runtime_entities WHERE user_id=? AND account_id=? "
            "AND entity_type='trading_instruction' ORDER BY updated_at DESC LIMIT 500",
            (int(user_id), int(account_id)),
        )
        payloads.extend(self.storage.fetchall(
            "SELECT position_attribution_json AS payload_json FROM trade_execution_reports "
            "WHERE user_id=? AND account_id=? ORDER BY reported_at DESC LIMIT 500",
            (int(user_id), int(account_id)),
        ))
        for item in payloads:
            try:
                payload = json.loads(item.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            attribution = payload.get("position_attribution") or payload
            previous_plan = str(
                attribution.get("trade_plan_instance_id")
                or attribution.get("ai_plan_instance_id") or ""
            )
            previous_group = str(attribution.get("trade_plan_group_id") or "")
            if str(attribution.get("strategy_id") or "") == strategy.strategy_id and (
                previous_plan == instance_id or (group_id and previous_group == group_id)
            ):
                return {
                    "allowed": False, "scope": "live_setup",
                    "setup_type": setup_type, "setup_family": setup_family,
                    "plan_instance_id": instance_id,
                    "reason": "本交易计划已在此实盘部署触发过，不重复开仓",
                }
        return {"allowed": True}
