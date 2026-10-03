"""Convert accepted event decisions into price-independent observation plans."""
from __future__ import annotations

from typing import Dict, Iterable, List


def build_observation_plans(
    events: Iterable[Dict], decisions: Iterable[Dict], *, period: str = "",
) -> List[Dict]:
    """Build the canonical observation layer before price calculations.

    An observation plan is not an order and has no entry/SL/TP.  It records
    the event chain, layer roles and confirmation contract that the execution
    adapter must satisfy.
    """
    event_map = {str(item.get("event_id") or ""): item for item in events or []}
    plans: List[Dict] = []
    for decision in decisions or []:
        if str(decision.get("status") or "") != "accepted":
            continue
        action = str(decision.get("matrix_action") or "")
        if action not in {"create_plan", "update_plan", "confirm_signal"}:
            continue
        event = event_map.get(str(decision.get("event_id") or ""), {})
        event_id = str(decision.get("event_id") or event.get("event_id") or "")
        parent_event_id = str(event.get("parent_event_id") or "")
        chain = []
        if parent_event_id and parent_event_id in event_map:
            parent = event_map[parent_event_id]
            chain.append(f"{parent.get('layer')}:{parent.get('event_type')}")
        chain.append(f"{decision.get('event_layer')}:{decision.get('event_type')}")
        plans.append({
            "observation_plan_id": f"obs-{decision.get('decision_id')}",
            "decision_id": str(decision.get("decision_id") or ""),
            "source_event_id": event_id,
            "parent_event_id": parent_event_id,
            "event_chain": chain,
            "plan_type": str(decision.get("plan_type") or decision.get("setup_type") or ""),
            "matrix_action": action,
            "event_layer": str(decision.get("event_layer") or ""),
            "direction_layer": str(decision.get("direction_layer") or ""),
            "entry_layer": str(decision.get("entry_layer") or ""),
            "direction": str(decision.get("direction") or ""),
            "event_type": str(decision.get("event_type") or ""),
            "required_confirmation": str(decision.get("required_confirmation") or "none"),
            "confirmation_period": str(period or "").upper(),
            "direct_signal": bool(decision.get("direct_signal")),
            "status": "watching" if action == "create_plan" else "confirming",
            "context": dict(decision.get("context") or {}),
        })
    return plans


__all__ = ["build_observation_plans"]
