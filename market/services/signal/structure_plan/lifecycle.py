"""Pure lifecycle and conflict rules for structure trade plans."""
from __future__ import annotations

from typing import Dict, List


def invalidate_reason(plan: Dict, price: float) -> str:
    """Return the event that invalidates a plan at Tick time, if any."""
    rules = set(plan.get("tick_invalidation_rules") or [])
    metadata = plan.get("structure_metadata") or {}
    top = float(metadata.get("range_top") or 0)
    bottom = float(metadata.get("range_bottom") or 0)
    setup = str(plan.get("setup_type") or "")
    direction = str(plan.get("direction") or "")
    if "close_return_to_invalid_boundary" in rules and top > bottom > 0:
        if bottom < price < top:
            return "range_returned_inside"
    if "protected_level_break" in rules:
        invalid = float(plan.get("invalidation_price") or 0)
        if invalid and ((direction == "buy" and price <= invalid) or (direction == "sell" and price >= invalid)):
            return "protected_level_broken"
    if "triangle_pattern_break" in rules and setup.startswith("triangle_") and top > bottom > 0:
        if (direction == "buy" and price < bottom) or (direction == "sell" and price > top):
            return "triangle_pattern_broken"
    return ""


def resolve_conflicts(plans: List[Dict]) -> List[Dict]:
    """Keep the strongest direction when active plans conflict."""
    actionable = [p for p in plans if str(p.get("direction") or "") in {"buy", "sell"}]
    buys = [p for p in actionable if p.get("direction") == "buy"]
    sells = [p for p in actionable if p.get("direction") == "sell"]
    if not buys or not sells:
        return plans
    def score(plan: Dict):
        try:
            rr = float(plan.get("risk_reward_ratio") or 0)
        except (TypeError, ValueError):
            rr = 0.0
        try:
            distance = abs(float(plan.get("entry_price") or 0) - float(plan.get("trigger_price") or 0))
        except (TypeError, ValueError):
            distance = 0.0
        return (int(plan.get("confidence") or 0), rr, -distance)
    winner = max(actionable, key=score)
    return [p for p in plans if p not in actionable or p is winner]


def stage_for(status: str, entry_mode: str) -> str:
    if status == "watching":
        return "candidate"
    if entry_mode in {"breakout_retest", "touch_and_reclaim"}:
        return "confirmed"
    return "active"
