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
