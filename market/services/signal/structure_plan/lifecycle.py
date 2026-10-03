"""Pure lifecycle and conflict rules for structure trade plans."""
from __future__ import annotations

from typing import Dict, List, Optional


DEFAULT_MAX_ENTRY_ZONE_WIDTHS = 3.5
DEFAULT_MAX_ENTRY_DISTANCE_PCT = 0.8
OUTSIDE_ZONE_CLOSE_LIMITS = {
    "M1": 5,
    "M5": 3,
    "M15": 2,
    "H1": 2,
    "H4": 2,
    "D1": 2,
}


def invalidate_reason(plan: Dict, price: float) -> str:
    """Return the event that invalidates a plan at Tick time, if any."""
    rules = set(plan.get("tick_invalidation_rules") or [])
    metadata = plan.get("structure_metadata") or {}
    top = float(metadata.get("range_top") or 0)
    bottom = float(metadata.get("range_bottom") or 0)
    setup = str(plan.get("setup_type") or "")
    direction = str(plan.get("direction") or "")
    evidence = plan.get("validation_evidence") or {}
    zone_lower = float(evidence.get("zone_lower") or 0)
    zone_upper = float(evidence.get("zone_upper") or 0)
    zone_buffer = float(evidence.get("zone_invalidation_buffer") or 0)
    if "pressure_zone_return_inside" in rules and zone_upper > zone_lower > 0:
        if zone_lower < price < zone_upper:
            return "pressure_zone_returned_inside"
    if "pressure_protected_level_break" in rules and zone_upper > zone_lower > 0:
        invalid = (zone_lower - zone_buffer) if direction == "buy" else (zone_upper + zone_buffer)
        if (direction == "buy" and price <= invalid) or (direction == "sell" and price >= invalid):
            return "pressure_protected_zone_broken"
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
    distance_reason = distance_invalidate_reason(plan, price)
    if distance_reason:
        return distance_reason
    return ""


def _as_float(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number == number else float(default)


def max_entry_zone_widths(plan: Optional[Dict] = None) -> float:
    plan = plan or {}
    evidence = plan.get("validation_evidence") or {}
    configured = _as_float(evidence.get("max_entry_zone_widths"))
    if configured <= 0:
        configured = _as_float(plan.get("max_entry_zone_widths"))
    if configured <= 0:
        configured = DEFAULT_MAX_ENTRY_ZONE_WIDTHS
    return max(1.0, configured)


def max_entry_distance_pct(plan: Optional[Dict] = None) -> float:
    plan = plan or {}
    evidence = plan.get("validation_evidence") or {}
    configured = _as_float(evidence.get("max_entry_distance_pct"))
    if configured <= 0:
        configured = _as_float(plan.get("max_entry_distance_pct"))
    if configured <= 0:
        configured = DEFAULT_MAX_ENTRY_DISTANCE_PCT
    return max(0.3, configured)


def outside_zone_close_limit(period: str = "") -> int:
    return int(OUTSIDE_ZONE_CLOSE_LIMITS.get(str(period or "").upper(), 3))


def distance_invalidate_reason(plan: Dict, price: float) -> str:
    """Shared far-from-entry retirement rule for Tick and closed-bar paths."""
    entry = _as_float(plan.get("entry_price"))
    price = _as_float(price)
    if entry <= 0 or price <= 0:
        return ""
    zone = plan.get("entry_zone") or {}
    lower = _as_float(zone.get("lower"))
    upper = _as_float(zone.get("upper"))
    zone_width = abs(upper - lower) if upper > lower > 0 else 0.0
    distance = abs(price - entry)
    if zone_width > 0:
        distance_ratio = distance / zone_width
        max_ratio = max_entry_zone_widths(plan)
        if distance_ratio > max_ratio:
            return (
                f"价格距离计划入场 {distance_ratio:.1f} 倍入场区宽度，"
                f"超过最大等待距离 {max_ratio:.1f} 倍"
            )
        return ""
    # A missing zone must not fall back to 0.8% of price: on FX that is
    # several ATR and lets reclaim plans chase a finished move.
    atr = _as_float((plan.get("structure_snapshot") or {}).get("atr"))
    if atr > 0:
        max_atr = 2.5
        if distance > max_atr * atr:
            return (
                f"价格距离计划入场 {distance / atr:.1f} ATR，"
                f"超过最大等待距离 {max_atr:.1f} ATR"
            )
        return ""
    distance_pct = distance / entry * 100.0
    max_distance_pct = max_entry_distance_pct(plan)
    if distance_pct > max_distance_pct:
        return (
            f"价格距离计划入场 {distance_pct:.2f}% ，"
            f"超过最大等待距离 {max_distance_pct:.2f}%"
        )
    return ""


def close_invalidate_reason(
    plan: Dict,
    structure: Optional[Dict],
    close_price: float,
    atr: float = 0.0,
) -> str:
    """Return why a live plan should die on a closed bar, if it should.

    Destruction is close-confirmed. Wicks alone do not kill a waiting plan.
    """
    direction = str(plan.get("direction") or "")
    if direction not in {"buy", "sell"}:
        return ""
    close_price = _as_float(close_price)
    if close_price <= 0:
        return ""
    rules = set(plan.get("close_invalidation_rules") or plan.get("invalidation_rules") or [])
    atr = max(0.0, _as_float(atr))
    buffer = atr * 0.1
    invalid = _as_float(plan.get("invalidation_price") or plan.get("stop_loss"))
    metadata = plan.get("structure_metadata") or {}
    top = _as_float(metadata.get("range_top"))
    bottom = _as_float(metadata.get("range_bottom"))
    setup = str(plan.get("setup_type") or "")
    structure = structure or {}
    current_segment = str(structure.get("structure_segment_id") or "")
    plan_segment = str(
        plan.get("structure_segment_id")
        or metadata.get("segment_id")
        or ""
    )

    if "protected_level_break" in rules and invalid > 0:
        if direction == "buy" and close_price <= invalid - buffer:
            return "保护低点被收盘破坏"
        if direction == "sell" and close_price >= invalid + buffer:
            return "保护高点被收盘破坏"

    # A location pullback is tied to the specific HL/LH selected when the
    # plan was created.  A completed candle through that level kills the old
    # thesis immediately; a later rebound must wait for a new structure plan.
    # This intentionally ignores intrabar wicks.
    if setup == "structure_location_pullback" or "pullback" in setup:
        evidence = plan.get("validation_evidence") or {}
        location_level = _as_float(
            evidence.get("location_entry_level") or plan.get("entry_price")
        )
        if location_level > 0:
            if direction == "buy" and close_price < location_level:
                return "HL 被收盘跌破，原回撤计划失效"
            if direction == "sell" and close_price > location_level:
                return "LH 被收盘突破，原回撤计划失效"

    if "range_structure_break" in rules:
        box = structure.get("range") or {}
        box_status = str(box.get("status") or "")
        broken = False
        if invalid > 0:
            broken = (
                (direction == "buy" and close_price <= invalid - buffer)
                or (direction == "sell" and close_price >= invalid + buffer)
            )
        elif top > bottom > 0:
            broken = (
                (direction == "buy" and close_price < bottom - buffer)
                or (direction == "sell" and close_price > top + buffer)
            )
        if broken or box_status in {"breakout_confirmed", "failed"} and (
            (direction == "buy" and str(box.get("breakout_direction") or "") == "down")
            or (direction == "sell" and str(box.get("breakout_direction") or "") == "up")
        ):
            return "区间结构被收盘破坏"

    if "triangle_pattern_break" in rules and "triangle" in setup and top > bottom > 0:
        if direction == "buy" and close_price < bottom - buffer:
            return "三角形结构被收盘破坏"
        if direction == "sell" and close_price > top + buffer:
            return "三角形结构被收盘破坏"

    if plan_segment and current_segment and plan_segment != current_segment:
        # Segment change means the original trade thesis belongs to a finished
        # structure. Keep only if a newer active opportunity replaces it via
        # supersede; otherwise retire the orphaned waiter.
        if str(plan.get("status") or "") in {"active", "event_suppressed", "watching"}:
            return "结构段已切换，原交易机会失效"

    distance_reason = distance_invalidate_reason(plan, close_price)
    if distance_reason:
        return distance_reason

    zone = plan.get("entry_zone") or {}
    lower = _as_float(zone.get("lower"))
    upper = _as_float(zone.get("upper"))
    if upper > lower > 0 and not (lower <= close_price <= upper):
        streak = int(plan.get("outside_zone_closes") or 0) + 1
        limit = outside_zone_close_limit(str(plan.get("period") or ""))
        if streak >= limit:
            return (
                f"连续 {streak} 根收盘离开入场区，"
                f"超过等待上限 {limit} 根"
            )

    return ""


def opportunity_still_valid(
    plan: Dict,
    structure: Optional[Dict],
    close_price: float,
    atr: float = 0.0,
) -> bool:
    """Whether a waiting plan's entry thesis is still worth keeping."""
    if close_invalidate_reason(plan, structure, close_price, atr):
        return False
    direction = str(plan.get("direction") or "")
    if direction not in {"buy", "sell"}:
        return False
    entry = _as_float(plan.get("entry_price"))
    close_price = _as_float(close_price)
    if entry <= 0 or close_price <= 0:
        return False
    structure = structure or {}
    plan_segment = str(
        plan.get("structure_segment_id")
        or (plan.get("structure_metadata") or {}).get("segment_id")
        or ""
    )
    current_segment = str(structure.get("structure_segment_id") or "")
    if plan_segment and current_segment and plan_segment != current_segment:
        return False
    return True


def next_outside_zone_closes(plan: Dict, close_price: float) -> int:
    """Update helper for consecutive closes outside the entry zone."""
    zone = plan.get("entry_zone") or {}
    lower = _as_float(zone.get("lower"))
    upper = _as_float(zone.get("upper"))
    close_price = _as_float(close_price)
    if not (upper > lower > 0 and close_price > 0):
        return 0
    if lower <= close_price <= upper:
        return 0
    return int(plan.get("outside_zone_closes") or 0) + 1


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
