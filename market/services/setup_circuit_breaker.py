"""SETUP and deployment loss-streak entry protection."""
from __future__ import annotations

import json
import time
from typing import Dict


class SetupCircuitBreaker:
    """Evaluate completed live trades without owning TradingServer state."""

    def __init__(self, storage, position_policy_repository):
        self.storage = storage
        self.position_policy_repository = position_policy_repository

    def check_live(self, user_id: int, account_id: int, strategy, signal) -> Dict:
        setup_type = str(getattr(signal, "setup_type", "") or "generic_entry")
        signal_source = str(getattr(signal, "source", "") or "").lower()
        plan_id = str(
            getattr(signal, "trade_plan_id", "")
            or getattr(signal, "ai_plan_id", "") or ""
        )
        policy = self.position_policy_repository.get_for_strategy(user_id, strategy)
        config = policy.config if policy is not None else {}
        if not bool(config.get("loss_streak_circuit_breaker_enabled", True)):
            return {"allowed": True, "loss_streak": 0, "scope": "deployment"}
        limit = max(1, int(config.get("loss_streak_limit", 3) or 3))
        pause_seconds = max(
            60, int(config.get("loss_streak_pause_minutes", 10) or 10) * 60,
        )
        candidates = self.storage.fetchall(
            "SELECT profit,swap,commission,deal_timestamp,mt5_position_id,"
            "position_attribution_json FROM live_trade_deals "
            "WHERE user_id=? AND account_id=? AND entry_type IN (1,2,3) "
            "ORDER BY deal_timestamp DESC,id DESC LIMIT 1000",
            (int(user_id), int(account_id)),
        )
        grouped = {}
        is_structure_setup = bool(plan_id or "structure" in signal_source)
        for row in candidates:
            try:
                attribution = json.loads(row.get("position_attribution_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
            if str(attribution.get("strategy_id") or "") != strategy.strategy_id:
                continue
            if is_structure_setup and str(
                attribution.get("setup_type") or "generic_entry"
            ) != setup_type:
                continue
            position_id = str(row.get("mt5_position_id") or "")
            if not position_id:
                continue
            item = grouped.setdefault(position_id, {"net_profit": 0.0, "closed_at": 0})
            item["net_profit"] += sum(
                float(row.get(key) or 0) for key in ("profit", "swap", "commission")
            )
            item["closed_at"] = max(
                item["closed_at"], int(row.get("deal_timestamp") or 0),
            )
        rows = sorted(grouped.values(), key=lambda item: item["closed_at"], reverse=True)
        streak = 0
        for row in rows:
            if float(row["net_profit"]) < 0:
                streak += 1
            else:
                break
        if streak < limit:
            return {"allowed": True, "loss_streak": streak, "scope": "deployment"}
        if is_structure_setup:
            pause_seconds = 3 * 3600
        release_at = int(rows[0]["closed_at"] or 0) + pause_seconds
        if int(time.time()) < release_at:
            return {
                "allowed": False,
                "loss_streak": streak,
                "scope": "setup_type" if is_structure_setup else "deployment",
                "setup_type": setup_type if is_structure_setup else "",
                "release_at": release_at,
                "reason": (
                    f"SETUP {setup_type} 连续亏损 {streak} 次，已暂停该 SETUP 三小时，"
                    f"恢复时间 {time.strftime('%Y-%m-%d %H:%M', time.localtime(release_at))}"
                    if is_structure_setup else
                    f"连续亏损 {streak} 次，策略部署已风险暂停至 "
                    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(release_at))}"
                ),
            }
        return {
            "allowed": True, "loss_streak": streak, "scope": "deployment",
            "cooldown_completed": True,
        }
