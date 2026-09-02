"""Pure structural price calculations used by StructurePlanBuilder.

These functions deliberately have no storage or application dependencies so
they can be unit-tested and reused by paper/live execution and replay.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _layer_price(hierarchy: Dict, layer: str, name: str) -> float:
    return _number(((hierarchy.get(layer) or {}).get(name) or {}).get("price"))


def calculate_next_target(hierarchy: Dict, direction: str, price: float) -> float:
    names = ("weak_high", "protected_high") if direction == "buy" else ("weak_low", "protected_low")
    values = []
    for layer in ("swing", "external"):
        for name in names:
            value = _layer_price(hierarchy, layer, name)
            if (direction == "buy" and value > price) or (direction == "sell" and 0 < value < price):
                values.append(value)
    return min(values) if direction == "buy" and values else (max(values) if values else 0.0)


def protected_reference(hierarchy: Dict, direction: str, entry: float) -> float:
    name = "protected_low" if direction == "buy" else "protected_high"
    candidates = []
    for layer in ("internal", "swing", "external"):
        value = _layer_price(hierarchy, layer, name)
        if (direction == "buy" and 0 < value < entry) or (direction == "sell" and value > entry):
            candidates.append(value)
    return max(candidates) if direction == "buy" and candidates else (min(candidates) if candidates else 0.0)


def location_reclaim_confirmation(
    rows: List[Dict], entry: float, direction: str, atr: float,
    min_body_atr: float = 0.3, min_close_extension_atr: float = 0.1,
) -> Tuple[bool, Dict, str]:
    """Validate that the latest closed bar decisively reclaimed an HL/LH.

    A wick touching a level is not enough.  The reclaim candle must point in
    the trade direction, have a meaningful body and close clearly back beyond
    the structural level.
    """
    if not rows or entry <= 0:
        return False, {}, "缺少可验证的收盘K线或结构入场位"
    row = rows[-1]
    open_price = _number(row.get("open") or row.get("open_price"))
    high = _number(row.get("high") or row.get("high_price"))
    low = _number(row.get("low") or row.get("low_price"))
    close = _number(row.get("close") or row.get("close_price"))
    normalized_atr = max(1e-9, float(atr or 0))
    tolerance = normalized_atr * 0.10
    body_atr = abs(close - open_price) / normalized_atr
    directional = close > open_price if direction == "buy" else close < open_price
    if direction == "buy":
        touched = low <= entry + tolerance
        extension_atr = (close - entry) / normalized_atr
    else:
        touched = high >= entry - tolerance
        extension_atr = (entry - close) / normalized_atr
    evidence = {
        "open_price": round(open_price, 8),
        "close_price": round(close, 8),
        "entry_level": round(entry, 8),
        "body_atr": round(body_atr, 3),
        "minimum_body_atr": round(max(0.0, float(min_body_atr or 0)), 3),
        "close_extension_atr": round(extension_atr, 3),
        "minimum_close_extension_atr": round(
            max(0.0, float(min_close_extension_atr or 0)), 3
        ),
        "directional_body": directional,
        "touched": touched,
    }
    if not touched:
        return False, evidence, "最近收盘K线尚未触碰 HL/LH 结构位"
    if not directional:
        return False, evidence, "回收K线实体方向与计划交易方向不一致"
    if body_atr < max(0.0, float(min_body_atr or 0)):
        return False, evidence, (
            f"回收K线实体仅 {body_atr:.2f} ATR，低于最低要求 "
            f"{float(min_body_atr):.2f} ATR"
        )
    if extension_atr < max(0.0, float(min_close_extension_atr or 0)):
        return False, evidence, (
            f"回收K线收盘仅越过 HL/LH {extension_atr:.2f} ATR，低于最低要求 "
            f"{float(min_close_extension_atr):.2f} ATR"
        )
    return True, evidence, ""


def location_reclaim_confirmed(rows: List[Dict], entry: float, direction: str, atr: float) -> bool:
    """Backward-compatible boolean wrapper using the stricter defaults."""
    accepted, _, _ = location_reclaim_confirmation(rows, entry, direction, atr)
    return accepted


def exit_candidates(
    structure_snapshot: Dict, direction: str, entry: float,
    param: Callable[[str, object], object],
) -> Tuple[List[Dict], List[Dict]]:
    levels = structure_snapshot.get("structure_levels") or {}
    atr = max(0.0, _number(structure_snapshot.get("atr")))
    stop_buffer = atr * max(0.0, _number(param("stop_buffer_atr", 0.25)))
    target_buffer = atr * max(0.0, _number(param("target_buffer_atr", 0.1)))
    stop_name = "protected_low" if direction == "buy" else "protected_high"
    target_names = (("weak_high", "protected_high") if direction == "buy" else ("weak_low", "protected_low"))
    stops, targets = [], []
    for rank, layer in enumerate(("internal", "swing", "external"), start=1):
        item = levels.get(layer) or {}
        reference = _number(item.get(stop_name))
        stop = reference - stop_buffer if direction == "buy" else reference + stop_buffer
        if reference > 0 and ((direction == "buy" and stop < entry) or (direction == "sell" and stop > entry)):
            stops.append({"level_id": f"structure_sl_{layer}", "structure_layer": layer,
                          "price": round(stop, 8), "reference_price": round(reference, 8),
                          "rank": rank, "reason": f"{layer} {stop_name}"})
        for target_name in target_names:
            reference = _number(item.get(target_name))
            target = reference - target_buffer if direction == "buy" else reference + target_buffer
            if reference > 0 and ((direction == "buy" and target > entry) or (direction == "sell" and target < entry)):
                targets.append({"level_id": f"structure_tp_{layer}", "structure_layer": layer,
                                "price": round(target, 8), "reference_price": round(reference, 8),
                                "rank": rank, "reason": f"{layer} {target_name}"})
                break

    def unique(items: List[Dict], reverse: bool) -> List[Dict]:
        result = {}
        for item in items:
            result.setdefault(round(_number(item["price"]), 8), item)
        return sorted(result.values(), key=lambda item: _number(item["price"]), reverse=reverse)

    return unique(stops, direction == "buy"), unique(targets, direction == "sell")
