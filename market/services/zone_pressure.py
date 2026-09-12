"""Close-confirmed price zones and directional pressure events.

The service intentionally consumes only completed bars.  Its output is a
deterministic snapshot, so structure snapshots can be safely restored after a
restart and replay obtains the same zones/events from the same bar sequence.
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable, List, Optional


DEFAULT_CONFIG = {
    "zone_pressure_enabled": True,
    "zone_lookback_bars": 80,
    "zone_bin_atr": 0.5,
    "zone_min_close_ratio": 0.20,
    "zone_min_visits": 3,
    "zone_leave_atr": 0.5,
    "zone_max_width_atr": 2.0,
    # A density bucket is a presentation detail, not the identity of a
    # market area.  When ATR or the bin origin moves slightly, match the new
    # band to the previous snapshot and keep its zone_id.
    "zone_identity_match_atr": 0.75,
    "zone_identity_max_gap_bars": 2,
    "pressure_touch_atr": 0.35,
    "pressure_min_rejections": 3,
    "pressure_reclaim_ratio": 0.50,
    "pressure_min_displacement_atr": 0.8,
    "pressure_min_efficiency": 0.55,
    "pivot_zone_enabled": True,
    "pivot_zone_merge_atr": 0.45,
    "pivot_zone_min_points": 1,
}


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _time(row: Dict) -> int:
    value = row.get("timestamp_utc") or row.get("timestamp") or row.get("time") or 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _value(row: Dict, key: str) -> float:
    return _number(row.get(key) or row.get(f"{key}_price"))


def _closed(rows: Iterable[Dict]) -> List[Dict]:
    return [dict(row) for row in (rows or []) if row and row.get("is_closed", True) is not False]


def _atr(rows: List[Dict], length: int = 14) -> float:
    previous, values = 0.0, []
    for row in rows:
        high, low, close = _value(row, "high"), _value(row, "low"), _value(row, "close")
        if high <= 0 or low <= 0:
            high = low = close
        values.append(max(high - low, abs(high - previous), abs(low - previous)) if previous else high - low)
        previous = close
    return sum(values[-length:]) / max(1, min(length, len(values)))


def _id(*items) -> str:
    return hashlib.sha1("|".join(str(item) for item in items).encode()).hexdigest()[:16]


def visit(zone: Dict, row: Dict, leave_atr: float) -> Dict:
    """Advance one zone visit.  A visit completes only after a real departure."""
    close, stamp = _value(row, "close"), _time(row)
    lower, upper = _number(zone.get("lower")), _number(zone.get("upper"))
    atr = max(1e-9, _number(zone.get("atr")))
    margin = max(0.0, float(leave_atr)) * atr
    inside = lower <= close <= upper
    current = zone.get("current_visit")
    if inside and not current:
        zone["current_visit"] = {"entered_at": stamp, "last_inside_at": stamp, "min": close, "max": close}
    elif inside and current:
        current["last_inside_at"] = stamp
        current["min"] = min(_number(current.get("min")), close)
        current["max"] = max(_number(current.get("max")), close)
    elif current and (close < lower - margin or close > upper + margin):
        direction = "down" if close < lower else "up"
        zone.setdefault("visits", []).append({
            **current, "left_at": stamp, "left_direction": direction,
            "left_price": close,
        })
        zone["current_visit"] = None
    return zone


def momentum(rows: List[Dict], atr: float) -> Dict:
    """Closed-bar displacement and path efficiency, symmetric by direction."""
    rows = _closed(rows)
    if len(rows) < 2 or atr <= 0:
        return {"direction": "none", "displacement_atr": 0.0, "efficiency": 0.0}
    closes = [_value(row, "close") for row in rows]
    displacement = closes[-1] - closes[0]
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    direction = "buy" if displacement > 0 else "sell" if displacement < 0 else "none"
    return {
        "direction": direction,
        "displacement_atr": round(abs(displacement) / max(atr, 1e-9), 4),
        "efficiency": round(abs(displacement) / path, 4) if path else 0.0,
        "start_price": round(closes[0], 8), "end_price": round(closes[-1], 8),
        "start_time": _time(rows[0]), "confirmed_at": _time(rows[-1]),
    }


def _dense_zones(symbol: str, period: str, rows: List[Dict], atr: float, cfg: Dict) -> List[Dict]:
    if not rows or atr <= 0:
        return []
    width = max(atr * max(.05, _number(cfg.get("zone_bin_atr"))), 1e-9)
    min_count = max(int(cfg.get("zone_min_visits") or 3), math.ceil(len(rows) * max(.01, _number(cfg.get("zone_min_close_ratio")))))
    buckets: Dict[int, List[Dict]] = {}
    for row in rows:
        close = _value(row, "close")
        if close:
            buckets.setdefault(math.floor(close / width), []).append(row)
    zones = []
    for bucket, members in buckets.items():
        if len(members) < min_count:
            continue
        lower, upper = bucket * width, (bucket + 1) * width
        if upper - lower > atr * max(.1, _number(cfg.get("zone_max_width_atr"))):
            continue
        zones.append({
            "zone_id": _id(symbol.upper(), period.upper(), "density", round(lower / width)),
            "kind": "density", "lower": round(lower, 8), "upper": round(upper, 8),
            "center": round((lower + upper) / 2, 8), "atr": round(atr, 8),
            "formed_at": _time(members[0]), "last_density_at": _time(members[-1]),
            "close_count": len(members), "close_ratio": round(len(members) / len(rows), 4),
            "visits": [], "current_visit": None,
        })
        zones[-1]["zone_revision"] = _id(
            zones[-1]["zone_id"], zones[-1]["lower"], zones[-1]["upper"]
        )
    return sorted(zones, key=lambda item: (-item["close_count"], item["center"]))[:3]


def _zone_overlap(left: Dict, right: Dict) -> float:
    """Return intersection over the smaller band width."""
    left_lower, left_upper = _number(left.get("lower")), _number(left.get("upper"))
    right_lower, right_upper = _number(right.get("lower")), _number(right.get("upper"))
    intersection = max(0.0, min(left_upper, right_upper) - max(left_lower, right_lower))
    width = min(max(0.0, left_upper - left_lower), max(0.0, right_upper - right_lower))
    return intersection / width if width > 0 else 0.0


def _inherit_zone_identity(
    zones: List[Dict], previous: Optional[Dict], atr: float, cfg: Dict,
    period: str = "", current_last_time: int = 0,
) -> None:
    """Carry a density zone identity across a small ATR/bin recalculation.

    ``zone_id`` identifies the market area, while ``zone_revision`` identifies
    its current boundaries.  Matching is intentionally one-to-one and only
    uses the previous bounded snapshot, so it cannot merge two live zones or
    introduce look-ahead data.
    """
    if not zones or not previous:
        return
    previous_pressure = previous.get("zone_pressure") or previous
    previous_last_time = _number(previous_pressure.get("last_bar_time"))
    if current_last_time and previous_last_time:
        period_seconds = {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(str(period).upper(), 300)
        max_gap = period_seconds * max(
            1, int(cfg.get("zone_identity_max_gap_bars", 2) or 2)
        )
        # A stale prefix snapshot must not rewrite the identity of a fresh
        # full replay; only a contiguous/near-contiguous stream may inherit.
        if abs(float(current_last_time) - previous_last_time) > max_gap:
            return
    old_zones = [
        item for item in (previous_pressure.get("zones") or [])
        if item.get("kind") == "density" and item.get("zone_id")
    ]
    if not old_zones:
        return
    tolerance = max(1e-9, atr * max(.05, _number(cfg.get("zone_identity_match_atr", .75))))
    candidates = []
    for new_index, current in enumerate(zones):
        for old_index, old in enumerate(old_zones):
            center_distance = abs(_number(current.get("center")) - _number(old.get("center")))
            overlap = _zone_overlap(current, old)
            if overlap < 0.25 and center_distance > tolerance:
                continue
            score = (overlap, -center_distance, _number(old.get("last_density_at")))
            candidates.append((score, new_index, old_index))
    used_new, used_old = set(), set()
    for _, new_index, old_index in sorted(candidates, reverse=True):
        if new_index in used_new or old_index in used_old:
            continue
        current, old = zones[new_index], old_zones[old_index]
        if (
            str(current.get("zone_id")) == str(old.get("zone_id"))
            and _number(current.get("lower")) == _number(old.get("lower"))
            and _number(current.get("upper")) == _number(old.get("upper"))
        ):
            used_new.add(new_index)
            used_old.add(old_index)
            continue
        current["zone_id"] = str(old["zone_id"])
        current["identity_source"] = "previous_snapshot"
        current["previous_zone_revision"] = str(old.get("zone_revision") or "")
        current["zone_revision"] = _id(
            current["zone_id"], current["lower"], current["upper"],
            current.get("last_density_at"), current.get("close_count"),
        )
        used_new.add(new_index)
        used_old.add(old_index)


def _pivot_zones(symbol: str, period: str, pivot_levels: Optional[Dict], atr: float, cfg: Dict) -> List[Dict]:
    """Cluster confirmed Internal/Swing/External pivots into S/R bands."""
    if not cfg.get("pivot_zone_enabled", True) or not pivot_levels or atr <= 0:
        return []
    points = []
    for layer, pivots in (pivot_levels or {}).items():
        for pivot in pivots or []:
            if pivot.get("kind") not in {"high", "low"} or _number(pivot.get("price")) <= 0:
                continue
            points.append({
                "layer": str(layer), "kind": str(pivot["kind"]),
                "price": _number(pivot["price"]),
                "index": int(pivot.get("index") or 0),
            })
    if not points:
        return []
    tolerance = max(1e-9, atr * max(.05, _number(cfg.get("pivot_zone_merge_atr"))))
    result = []
    for kind in ("high", "low"):
        clusters = []
        for point in sorted((item for item in points if item["kind"] == kind), key=lambda item: item["price"]):
            if not clusters or point["price"] - clusters[-1][-1]["price"] > tolerance:
                clusters.append([point])
            else:
                clusters[-1].append(point)
        for cluster in clusters:
            if len(cluster) < max(1, int(cfg.get("pivot_zone_min_points") or 1)):
                continue
            prices = [item["price"] for item in cluster]
            lower, upper = min(prices), max(prices)
            layers = sorted({item["layer"] for item in cluster})
            zone = {
                "zone_id": _id(symbol.upper(), period.upper(), "pivot", kind, round(sum(prices) / len(prices), 8)),
                "zone_revision": _id(kind, round(lower, 8), round(upper, 8), *layers),
                "kind": "pivot_resistance" if kind == "high" else "pivot_support",
                "boundary_type": "resistance" if kind == "high" else "support",
                "lower": round(lower, 8), "upper": round(upper, 8),
                "center": round(sum(prices) / len(prices), 8),
                "point_count": len(cluster), "layers": layers,
                "latest_index": max(item["index"] for item in cluster),
                "atr": round(atr, 8), "status": "active",
            }
            result.append(zone)
    return sorted(result, key=lambda item: (-item["point_count"], item["center"]))


def _annotate_overlaps(density_zones: List[Dict], pivot_zones: List[Dict], atr: float) -> None:
    tolerance = max(1e-9, atr * .1)
    for zone in density_zones:
        overlaps = []
        for pivot in pivot_zones:
            if pivot["upper"] + tolerance >= zone["lower"] and pivot["lower"] - tolerance <= zone["upper"]:
                overlaps.append({"zone_id": pivot["zone_id"], "kind": pivot["kind"], "layers": pivot["layers"]})
        zone["pivot_overlaps"] = overlaps
        zone["structure_strength"] = round(min(1.0, len(overlaps) / 3.0), 3)


def _pressure(zones: List[Dict], rows: List[Dict], atr: float, cfg: Dict) -> List[Dict]:
    events = []
    if len(rows) < 5:
        return events
    touch_distance = atr * max(.01, _number(cfg.get("pressure_touch_atr")))
    for zone in zones:
        completed = list(zone.get("visits") or [])
        above = [item for item in completed if item.get("left_direction") == "down" and _number(item.get("max")) >= _number(zone["upper"]) - touch_distance]
        below = [item for item in completed if item.get("left_direction") == "up" and _number(item.get("min")) <= _number(zone["lower"]) + touch_distance]
        for direction, touches, boundary in (("sell", above, _number(zone["upper"])), ("buy", below, _number(zone["lower"]))):
            if len(touches) < max(2, int(cfg.get("pressure_min_rejections") or 3)):
                continue
            recent = touches[-max(2, int(cfg.get("pressure_min_rejections") or 3)):]
            displacement = momentum(rows[-4:], atr)
            if displacement["direction"] != direction:
                continue
            if displacement["displacement_atr"] < _number(cfg.get("pressure_min_displacement_atr")):
                continue
            if displacement["efficiency"] < _number(cfg.get("pressure_min_efficiency")):
                continue
            trigger = min(_number(item["left_price"]) for item in recent) if direction == "sell" else max(_number(item["left_price"]) for item in recent)
            events.append({
                "event_id": _id(zone["zone_id"], direction, "reversal", displacement["confirmed_at"], trigger),
                "opportunity_id": _id(zone["zone_id"], direction, "pressure"),
                "zone_id": zone["zone_id"], "type": "pressure_reversal_confirmed",
                "direction": direction, "level": round(trigger, 8), "boundary": round(boundary, 8),
                "test_count": len(recent), "confirmed_at": displacement["confirmed_at"],
                "displacement_atr": displacement["displacement_atr"], "efficiency": displacement["efficiency"],
                "reason": f"{len(recent)} 次独立测试后{('向下' if direction == 'sell' else '向上')}动量确认",
            })
        # A close outside a dense zone is a separate event from rejection.
        # Require a crossing close and directional displacement so a wick does
        # not create a breakout plan.
        for index in range(1, len(rows)):
            previous_close = _value(rows[index - 1], "close")
            close = _value(rows[index], "close")
            boundary = _number(zone.get("upper")) if close > _number(zone.get("upper")) else _number(zone.get("lower"))
            direction = "buy" if boundary == _number(zone.get("upper")) else "sell"
            crossed = (
                direction == "buy"
                and previous_close <= boundary
                and close > boundary + touch_distance
            ) or (
                direction == "sell"
                and previous_close >= boundary
                and close < boundary - touch_distance
            )
            if not crossed:
                continue
            displacement = momentum(rows[max(0, index - 3): index + 1], atr)
            if displacement["direction"] != direction:
                continue
            if displacement["displacement_atr"] < _number(cfg.get("pressure_min_displacement_atr")):
                continue
            events.append({
                "event_id": _id(zone["zone_id"], direction, "breakout", _time(rows[index])),
                "opportunity_id": _id(zone["zone_id"], direction, "pressure"),
                "zone_id": zone["zone_id"], "type": "zone_breakout_confirmed",
                "direction": direction, "level": round(boundary, 8),
                "boundary": round(boundary, 8), "confirmed_at": _time(rows[index]),
                "displacement_atr": displacement["displacement_atr"],
                "efficiency": displacement["efficiency"],
                "reason": f"密集区{('上沿' if direction == 'buy' else '下沿')}收盘突破并确认动量",
            })
            break
    return events


def _update_zone_states(
    zones: List[Dict], events: List[Dict], rows: List[Dict], atr: float, cfg: Dict,
) -> None:
    """Assign a deterministic lifecycle state to each density zone.

    The state is derived from the bounded closed-bar window, not process
    memory.  This keeps restart/replay identical while making invalidation
    explicit for plan consumers.
    """
    latest_close = _value(rows[-1], "close") if rows else 0.0
    leave_margin = max(0.0, _number(cfg.get("zone_leave_atr", .5))) * max(atr, 1e-9)
    by_zone: Dict[str, List[Dict]] = {}
    for event in events:
        by_zone.setdefault(str(event.get("zone_id") or ""), []).append(event)
    for zone in zones:
        zone_id = str(zone.get("zone_id") or "")
        lower, upper = _number(zone.get("lower")), _number(zone.get("upper"))
        visits = list(zone.get("visits") or [])
        current_visit = zone.get("current_visit")
        zone_events = sorted(
            by_zone.get(zone_id, []),
            key=lambda item: int(item.get("confirmed_at") or 0),
        )
        latest_event = zone_events[-1] if zone_events else None
        for event in zone_events:
            event["event_status"] = "confirmed"
            event["event_stage"] = (
                "initial" if event.get("type") == "pressure_reversal_confirmed"
                else "breakout" if event.get("type") == "zone_breakout_confirmed"
                else "single"
            )
        state, reason = "candidate", "尚无完整离开记录"
        if visits or current_visit:
            state, reason = "tested", "已发生至少一次区域访问"
        if latest_event and latest_event.get("type") == "pressure_reversal_confirmed":
            state, reason = "rejected", "区域多次测试后出现方向性拒绝"
        elif latest_event and latest_event.get("type") == "zone_breakout_confirmed":
            direction = str(latest_event.get("direction") or "")
            outside = (
                direction == "buy" and latest_close > upper + leave_margin
            ) or (
                direction == "sell" and latest_close < lower - leave_margin
            )
            if outside:
                state, reason = "breakout_watch", "收盘突破区域边界，等待突破后续确认"
            else:
                state, reason = "invalidated", "突破确认后价格重新回到区域内部"
                latest_event["event_status"] = "invalidated"
                latest_event["invalidated_at"] = _time(rows[-1]) if rows else 0
                latest_event["invalidation_reason"] = "zone_return_inside"
        elif latest_close and (
            latest_close < lower - leave_margin or latest_close > upper + leave_margin
        ):
            state, reason = "broken", "价格离开区域但尚未形成有效方向事件"
        zone["status"] = state
        zone["status_reason"] = reason
        zone["last_event_id"] = str((latest_event or {}).get("event_id") or "")
        zone["last_event_type"] = str((latest_event or {}).get("type") or "")
        zone["last_event_at"] = int((latest_event or {}).get("confirmed_at") or 0)
        zone["invalidated"] = state == "invalidated"


def advance(symbol: str, period: str, rows: List[Dict], config: Optional[Dict] = None,
            previous: Optional[Dict] = None, pivot_levels: Optional[Dict] = None) -> Dict:
    """Produce the canonical closed-bar zone-pressure snapshot.

    ``previous`` is deliberately read-only.  Rebuilding from the same bounded
    closed bars makes restart/replay results identical and prevents an in-place
    cache mutation from changing historic decisions.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    closed = _closed(rows)
    if not cfg["zone_pressure_enabled"] or not closed:
        return {"enabled": bool(cfg["zone_pressure_enabled"]), "zones": [],
                "pivot_zones": [], "events": [],
                "last_bar_time": _time(closed[-1]) if closed else 0}
    window = closed[-max(20, int(cfg.get("zone_lookback_bars") or 80)):]
    atr = _atr(window)
    zones = _dense_zones(symbol, period, window, atr, cfg)
    _inherit_zone_identity(zones, previous, atr, cfg, period, _time(closed[-1]))
    pivot_zones = _pivot_zones(symbol, period, pivot_levels, atr, cfg)
    _annotate_overlaps(zones, pivot_zones, atr)
    for zone in zones:
        for row in window:
            visit(zone, row, _number(cfg.get("zone_leave_atr")))
        zone["visit_count"] = len(zone.get("visits") or [])
    events = _pressure(zones, window, atr, cfg)
    _update_zone_states(zones, events, window, atr, cfg)
    return {"enabled": True, "atr": round(atr, 8), "zones": zones,
            "pivot_zones": pivot_zones, "events": events,
            "last_bar_time": _time(closed[-1]), "config": cfg}
