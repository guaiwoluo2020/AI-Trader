"""Pure event-to-observation decisions for the layer-driven architecture."""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List


def _decision_id(event_id: str, setup_type: str) -> str:
    return hashlib.sha1(f"{event_id}:{setup_type}".encode()).hexdigest()[:24]


def _bias(structure: Dict, layer: str) -> str:
    hierarchy = (structure or {}).get("structure_hierarchy") or {}
    state = hierarchy.get(layer) or {}
    return str(state.get("bias") or "").lower()


def _external_allows(structure: Dict, direction: str) -> bool:
    external = _bias(structure, "external")
    return external not in {"up", "down"} or external == direction


def decide_event_observations(
    structure: Dict, events: Iterable[Dict], *, require_external_alignment: bool = True,
) -> List[Dict]:
    """Turn independent events into explainable observation decisions.

    This function does not calculate prices or emit orders. It only answers
    whether an event is eligible to create an observation plan and records the
    layer context that led to that answer.
    """
    decisions: List[Dict] = []
    for event in events or []:
        event_type = str(event.get("event_type") or event.get("type") or "").lower()
        layer = str(event.get("layer") or "").lower()
        event_direction = str(event.get("direction") or "").lower()
        if event_type == "bos":
            setup = "trend_continuation"
            direction = event_direction
            direction_layer = "swing"
            entry_layer = layer
            if layer not in {"internal", "swing"} or direction not in {"up", "down"}:
                status, reason = "rejected", "BOS 所属层级或方向无效"
            elif layer == "internal" and _bias(structure, "swing") not in {"", direction}:
                status, reason = "rejected", f"INTERNAL BOS 与 SWING {_bias(structure, 'swing')} 方向不一致"
            elif require_external_alignment and not _external_allows(structure, direction):
                status, reason = "rejected", f"BOS 方向与 EXTERNAL {_bias(structure, 'external')} 不一致"
            else:
                status, reason = "accepted", "BOS 方向与层级上下文一致，建立趋势延续观察"
        elif event_type == "choch":
            setup = "choch_reversal"
            direction = event_direction
            direction_layer = entry_layer = layer
            if layer != "swing" or direction not in {"up", "down"}:
                status, reason = "rejected", "CHOCH 反转只接受 SWING 层事件"
            elif require_external_alignment and not _external_allows(structure, direction):
                status, reason = "rejected", f"CHOCH 方向与 EXTERNAL {_bias(structure, 'external')} 不一致"
            else:
                status, reason = "accepted", "SWING CHOCH 与层级上下文一致，建立反转观察"
        elif event_type == "liquidity_sweep":
            setup = "liquidity_sweep_reclaim"
            direction = "down" if event_direction == "up" else "up" if event_direction == "down" else ""
            direction_layer, entry_layer = "swing", layer
            if layer != "internal" or direction not in {"up", "down"}:
                status, reason = "rejected", "扫单观察只接受 INTERNAL 层的明确方向事件"
            elif require_external_alignment and not _external_allows(structure, direction):
                status, reason = "rejected", f"扫单回收方向与 EXTERNAL {_bias(structure, 'external')} 不一致"
            else:
                status, reason = "accepted", "INTERNAL 扫单方向与层级上下文一致，建立回收观察"
        else:
            continue
        decisions.append({
            "decision_id": _decision_id(str(event.get("event_id") or ""), setup),
            "event_id": str(event.get("event_id") or ""),
            "setup_type": setup,
            "status": status,
            "reason": reason,
            "event_layer": layer,
            "direction_layer": direction_layer,
            "entry_layer": entry_layer,
            "direction": direction,
            "event_type": event_type,
            "context": {
                "internal": _bias(structure, "internal"),
                "swing": _bias(structure, "swing"),
                "external": _bias(structure, "external"),
            },
        })
    return decisions
