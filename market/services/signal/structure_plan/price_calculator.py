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


def location_reclaim_confirmed(rows: List[Dict], entry: float, direction: str, atr: float) -> bool:
    if not rows or entry <= 0:
        return False
    row = rows[-1]
    high = _number(row.get("high") or row.get("high_price"))
    low = _number(row.get("low") or row.get("low_price"))
    close = _number(row.get("close") or row.get("close_price"))
    tolerance = max(0.0, float(atr or 0)) * 0.10
    if direction == "buy":
        return low <= entry + tolerance and close >= entry
    return high >= entry - tolerance and close <= entry


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
