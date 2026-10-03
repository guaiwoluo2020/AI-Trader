"""Normalize the structure engine's per-layer events into one event catalog."""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List, Optional


LAYERS = ("internal", "swing", "external")
EVENT_KEYS = {
    "internal": "internal_events",
    "swing": "major_events",
    "external": "external_events",
}


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _event_id(symbol: str, period: str, layer: str, event: Dict) -> str:
    raw = json.dumps({
        "symbol": str(symbol).upper(), "period": str(period).upper(),
        "layer": layer, "type": event.get("type") or event.get("event_type"),
        "direction": event.get("direction"),
        "level": _number(event.get("level")),
        "confirmed_at": event.get("confirmed_at", event.get("index", 0)),
        "swing_index": event.get("swing_index", event.get("pivot_at", -1)),
    }, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def collect_structure_events(
    structure: Dict, symbol: str, period: str, *, limit_per_layer: int = 20,
) -> List[Dict]:
    """Return normalized, independently identifiable events for all layers."""
    payload = structure or {}
    result: List[Dict] = []
    hierarchy = payload.get("structure_hierarchy") or {}
    for layer in LAYERS:
        events = list(payload.get(EVENT_KEYS[layer]) or [])
        layer_state = hierarchy.get(layer) if isinstance(hierarchy, dict) else {}
        for raw in events[-max(1, int(limit_per_layer)):]:
            if not isinstance(raw, dict):
                continue
            event_type = str(raw.get("type") or raw.get("event_type") or "").strip().lower()
            if not event_type:
                continue
            event = dict(raw)
            event.update({
                "event_id": _event_id(symbol, period, layer, raw),
                "symbol": str(symbol).upper(),
                "period": str(period).upper(),
                "layer": layer,
                "event_type": event_type,
                "direction": str(raw.get("direction") or "").lower(),
                "level": _number(raw.get("level")),
                "confirmed_at": int(raw.get("confirmed_at", raw.get("index", 0)) or 0),
            })
            direction = event["direction"]
            protected_name = "protected_low" if direction == "up" else "protected_high"
            protected = (layer_state or {}).get(protected_name) or 0
            if isinstance(protected, dict):
                protected = protected.get("price") or 0
            event["protected_level"] = _number(raw.get("protected_level") or protected)
            result.append(event)
    # Geometry events are deliberately synthesized here rather than treated
    # as a separate "location plan" or "range plan" family.  A confirmed
    # HL/LH is a structural event; the later price touch creates RETEST.
    hierarchy = payload.get("structure_hierarchy") or {}
    swing = hierarchy.get("swing") if isinstance(hierarchy, dict) else {}
    internal = hierarchy.get("internal") if isinstance(hierarchy, dict) else {}
    for layer, state in (("swing", swing), ("internal", internal)):
        for pivot in (state or {}).get("pivots") or []:
            label = str(pivot.get("label") or "").upper()
            kind = str(pivot.get("kind") or "").lower()
            if label not in {"HL", "LH"} or kind not in {"low", "high"}:
                continue
            direction = "up" if label == "HL" else "down"
            raw = {
                "type": "hl_confirmed" if label == "HL" else "lh_confirmed", "direction": direction,
                "level": pivot.get("price"), "confirmed_at": pivot.get("index", 0),
                "pivot_index": pivot.get("index", 0), "source": label,
            }
            event = dict(raw)
            event.update({
                "event_id": _event_id(symbol, period, layer, raw),
                "symbol": str(symbol).upper(), "period": str(period).upper(),
                "layer": layer, "event_type": "hl_confirmed" if label == "HL" else "lh_confirmed",
                "direction": direction, "level": _number(raw.get("level")),
                "confirmed_at": int(raw.get("confirmed_at") or 0),
                "protected_level": _number((state or {}).get(
                    "protected_low" if direction == "up" else "protected_high"
                )),
                "source": label,
            })
            result.append(event)
            latest_close = _number(payload.get("latest_close") or payload.get("close"))
            atr = max(1e-9, _number(payload.get("atr")))
            proximity = atr * max(0.05, _number(payload.get("retest_proximity_atr", 0.4)))
            if latest_close > 0 and abs(latest_close - _number(raw.get("level"))) <= proximity:
                touched_type = "hl_support_touched" if label == "HL" else "lh_press_touched"
                touched_raw = {
                    "type": touched_type, "direction": direction,
                    "level": pivot.get("price"), "confirmed_at": pivot.get("index", 0),
                    "pivot_index": pivot.get("index", 0), "parent_event_id": event["event_id"],
                }
                touched = dict(touched_raw)
                touched.update({
                    "event_id": _event_id(symbol, period, layer, touched_raw),
                    "symbol": str(symbol).upper(), "period": str(period).upper(),
                    "layer": layer, "event_type": touched_type, "direction": direction,
                    "level": _number(touched_raw.get("level")),
                    "confirmed_at": int(touched_raw.get("confirmed_at") or 0),
                    "protected_level": event["protected_level"],
                    "parent_event_id": event["event_id"], "source": label,
                })
                result.append(touched)
    def _append_box_event(layer: str, event_type: str, direction: str, level, confirmed_at, source: str) -> None:
        if direction not in {"up", "down"} or _number(level) <= 0:
            return
        raw = {
            "type": event_type, "direction": direction, "level": level,
            "confirmed_at": confirmed_at, "source": source,
        }
        event = dict(raw)
        event.update({
            "event_id": _event_id(symbol, period, layer, raw),
            "symbol": str(symbol).upper(), "period": str(period).upper(),
            "layer": layer, "event_type": event_type, "direction": direction,
            "level": _number(level), "confirmed_at": int(confirmed_at or 0),
            "protected_level": _number(level), "source": source,
        })
        result.append(event)

    for layer in LAYERS:
        state = hierarchy.get(layer) if isinstance(hierarchy, dict) else {}
        detail = state.get("pattern_detail") if isinstance((state or {}).get("pattern_detail"), dict) else {}
        box = dict(detail)
        if not box and layer == "swing":
            box = dict(payload.get("range") or {})
        box_status = str(box.get("status") or "").lower()
        if box_status == "breakout_confirmed":
            direction = str(box.get("breakout_direction") or "").lower()
            _append_box_event(
                layer, "bos", direction,
                box.get("top") if direction == "up" else box.get("bottom"),
                box.get("breakout_at", box.get("end_index", 0)),
                "range_breakout",
            )
        if box_status in {"", "confirmed", "active"} and box.get("active"):
            _append_box_event(layer, "retest", "up", box.get("bottom"), box.get("start_index", 0), "range_boundary")
            _append_box_event(layer, "retest", "down", box.get("top"), box.get("start_index", 0), "range_boundary")
        if box_status == "failed_breakout":
            failed = str(box.get("breakout_direction") or "").lower()
            direction = "down" if failed == "up" else "up" if failed == "down" else ""
            _append_box_event(
                layer, "reclaim", direction,
                box.get("top") if failed == "up" else box.get("bottom"),
                box.get("failed_at", box.get("end_index", 0)),
                "range_boundary",
            )
    return sorted(result, key=lambda item: (int(item.get("confirmed_at") or 0), item["layer"]))


def latest_event_for(
    events: Iterable[Dict], *, layer: str = "", event_type: str = "",
) -> Optional[Dict]:
    """Find the newest event matching the optional layer/type filters."""
    wanted_layer = str(layer or "").strip().lower()
    wanted_type = str(event_type or "").strip().lower()
    matches = [
        event for event in events or []
        if (not wanted_layer or str(event.get("layer") or "").lower() == wanted_layer)
        and (not wanted_type or str(event.get("event_type") or event.get("type") or "").lower() == wanted_type)
    ]
    return max(matches, key=lambda item: int(item.get("confirmed_at") or 0)) if matches else None
