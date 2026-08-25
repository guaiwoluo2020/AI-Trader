#!/usr/bin/env python3
"""Stable Setup and position-policy attribution carried across executions."""

from __future__ import annotations

import copy
from typing import Dict, Optional


def build_position_attribution(
    signal_summary: Optional[Dict],
    *,
    decision_id: str = "",
    strategy_id: str = "",
    strategy_name: str = "",
    direction: str = "",
    entry_reason: str = "",
    initial_stop_loss: float = 0,
    initial_take_profit: float = 0,
) -> Dict:
    summary = signal_summary or {}
    management = summary.get("position_management") or {}
    setup = management.get("setup_context") or {}
    snapshot = management.get("policy_snapshot") or {}
    profile = management.get("applied_setup_profile") or {}
    initial_risk = float(management.get("initial_risk") or 0)
    ai_plan_id = str(summary.get("selected_ai_plan_id") or "")
    ai_plan_valid_from = int(summary.get("selected_ai_plan_valid_from") or 0)
    return {
        "decision_id": str(decision_id or ""),
        "strategy_id": str(strategy_id or ""),
        "strategy_name": str(strategy_name or ""),
        "direction": str(direction or ""),
        "signal_source": str(
            setup.get("signal_source")
            or summary.get("selected_signal_source") or ""
        ),
        "signal_source_id": str(summary.get("selected_signal_source_id") or ""),
        "setup_type": str(
            setup.get("setup_type")
            or summary.get("selected_setup_type") or "generic_entry"
        ),
        "setup_family": str(
            setup.get("setup_family")
            or summary.get("selected_setup_family") or "generic"
        ),
        "entry_mode": str(
            setup.get("entry_mode")
            or summary.get("selected_entry_mode") or "touch_or_near"
        ),
        "ai_plan_id": ai_plan_id,
        "ai_plan_valid_from": ai_plan_valid_from,
        "ai_plan_expires_at": int(
            summary.get("selected_ai_plan_expires_at") or 0
        ),
        "ai_plan_instance_id": (
            f"{ai_plan_id}:{ai_plan_valid_from}"
            if ai_plan_id and ai_plan_valid_from else ai_plan_id
        ),
        "position_policy_id": str(snapshot.get("policy_id") or management.get("policy_id") or ""),
        "position_policy_name": str(snapshot.get("name") or ""),
        "position_policy_version": int(snapshot.get("version") or 1),
        "setup_profile_id": str(profile.get("profile_id") or ""),
        "setup_profile_name": str(profile.get("name") or "默认方案"),
        "initial_stop_loss": float(initial_stop_loss or 0),
        "initial_take_profit": float(initial_take_profit or 0),
        "initial_risk": initial_risk,
        "entry_reason": str(entry_reason or ""),
        "exit_reason": "",
        "realized_r": 0.0,
        "position_policy_snapshot": copy.deepcopy(snapshot),
    }


def close_position_attribution(
    attribution: Optional[Dict], exit_reason: str, realized_r: float,
) -> Dict:
    result = copy.deepcopy(attribution or {})
    result["exit_reason"] = str(exit_reason or "")
    result["realized_r"] = round(float(realized_r or 0), 6)
    return result
