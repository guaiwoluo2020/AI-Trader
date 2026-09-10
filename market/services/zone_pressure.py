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
    pivot_zones = _pivot_zones(symbol, period, pivot_levels, atr, cfg)
    _annotate_overlaps(zones, pivot_zones, atr)
    for zone in zones:
        for row in window:
            visit(zone, row, _number(cfg.get("zone_leave_atr")))
        zone["visit_count"] = len(zone.get("visits") or [])
        zone["status"] = "active"
    events = _pressure(zones, window, atr, cfg)
    return {"enabled": True, "atr": round(atr, 8), "zones": zones,
            "pivot_zones": pivot_zones, "events": events,
            "last_bar_time": _time(closed[-1]), "config": cfg}
