"""The canonical event decision matrix.

The matrix is deliberately data shaped: UI, APIs and the signal planner can
show the same rule that was used to accept or reject an event.  A matrix row
describes what an event is allowed to do; candle confirmation remains a later
observation step on the plan's trading period.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, Iterable, List


_RULES = [
    ("external", "bos", "context_update", "environment", False, "none"),
    ("external", "choch", "context_update", "environment", False, "none"),
    ("external", "liquidity_sweep", "context_update", "environment", False, "none"),
    ("external", "hl_confirmed", "context_update", "environment", False, "none"),
    ("external", "lh_confirmed", "context_update", "environment", False, "none"),
    ("external", "hl_support_touched", "context_update", "environment", False, "none"),
    ("external", "lh_press_touched", "context_update", "environment", False, "none"),
    ("external", "retest", "context_update", "environment", False, "none"),
    ("external", "reclaim", "context_update", "environment", False, "none"),
    ("swing", "bos", "create_plan", "trend_continuation", False, "retest_or_reclaim"),
    ("swing", "choch", "create_plan", "structure_reversal", False, "retest_or_reclaim"),
    ("swing", "liquidity_sweep", "create_plan", "swing_liquidity_reversal", False, "retest_or_sequence"),
    ("swing", "hl_confirmed", "create_plan", "swing_pullback", False, "hl_retest"),
    ("swing", "lh_confirmed", "create_plan", "swing_pullback", False, "lh_retest"),
    ("swing", "hl_support_touched", "update_plan", "event_confirmation", False, "retest_or_reclaim"),
    ("swing", "lh_press_touched", "update_plan", "event_confirmation", False, "retest_or_reclaim"),
    ("swing", "retest", "update_plan", "event_confirmation", True, "confirmation_sequence"),
    ("swing", "reclaim", "update_plan", "event_confirmation", True, "confirmation_sequence"),
    ("internal", "bos", "create_plan", "internal_momentum", True, "confirmation_sequence"),
    ("internal", "choch", "create_plan", "early_reversal", False, "retest_or_reclaim"),
    ("internal", "liquidity_sweep", "create_plan", "internal_liquidity_reversal", True, "confirmation_sequence"),
    ("internal", "hl_confirmed", "create_plan", "internal_pullback", False, "hl_retest"),
    ("internal", "lh_confirmed", "create_plan", "internal_pullback", False, "lh_retest"),
    ("internal", "hl_support_touched", "update_plan", "event_confirmation", False, "retest_or_reclaim"),
    ("internal", "lh_press_touched", "update_plan", "event_confirmation", False, "retest_or_reclaim"),
    ("internal", "retest", "update_plan", "event_confirmation", True, "confirmation_sequence"),
    ("internal", "reclaim", "update_plan", "event_confirmation", True, "confirmation_sequence"),
]


def event_matrix() -> List[Dict]:
    """Return a serializable copy for APIs and admin pages."""
    return [
        {
            "event_key": f"{layer}:{event_type}",
            "layer": layer,
            "event_type": event_type,
            "action": action,
            "plan_type": plan_type,
            "direct_signal": direct_signal,
            "required_confirmation": confirmation,
            "allowed_regimes": ["trend_up", "trend_down", "range", "triangle", "transition"],
            "enabled": True,
        }
        for layer, event_type, action, plan_type, direct_signal, confirmation in _RULES
    ]


def matrix_rule(layer: str, event_type: str) -> Dict:
    key = f"{str(layer or '').lower()}:{str(event_type or '').lower()}"
    return next((deepcopy(item) for item in event_matrix() if item["event_key"] == key), {
        "event_key": key, "layer": str(layer or "").lower(),
        "event_type": str(event_type or "").lower(), "action": "ignore",
        "plan_type": "", "direct_signal": False,
        "required_confirmation": "none", "allowed_regimes": [], "enabled": False,
    })


def apply_matrix_overrides(rules: Iterable[Dict], overrides: Dict | None) -> List[Dict]:
    """Apply optional persisted per-event overrides without mutating defaults."""
    override_map = overrides or {}
    result = []
    for rule in rules:
        item = deepcopy(rule)
        override = override_map.get(item["event_key"]) or {}
        if isinstance(override, dict):
            item.update({key: value for key, value in override.items() if key in {
                "action", "plan_type", "direct_signal", "required_confirmation",
                "allowed_regimes", "enabled",
            }})
        result.append(item)
    return result


__all__ = ["event_matrix", "matrix_rule", "apply_matrix_overrides"]
