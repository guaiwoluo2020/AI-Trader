"""Pure event-to-observation decisions for the layer-driven architecture."""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Iterable, List
from .matrix import matrix_rule, apply_matrix_overrides


def _decision_id(event_id: str, setup_type: str) -> str:
    return hashlib.sha1(f"{event_id}:{setup_type}".encode()).hexdigest()[:24]


def _bias(structure: Dict, layer: str) -> str:
    hierarchy = (structure or {}).get("structure_hierarchy") or {}
    state = hierarchy.get(layer) or {}
    return str(state.get("bias") or "").lower()


def _external_allows(structure: Dict, direction: str) -> bool:
    external = _bias(structure, "external")
    return external not in {"up", "down"} or external == direction


def _layer_pattern(structure: Dict, layer: str) -> str:
    hierarchy = (structure or {}).get("structure_hierarchy") or {}
    state = hierarchy.get(layer) or {}
    detail = state.get("pattern_detail") if isinstance(state.get("pattern_detail"), dict) else {}
    box = (structure or {}).get("range") or {}
    raw = str(
        state.get("pattern")
        or detail.get("pattern")
        or (box.get("pattern") if layer == "swing" else "")
        or ""
    ).strip().lower()
    if "triangle" in raw:
        return "triangle"
    if raw in {"range", "box", "rectangle", "sideways"}:
        return "range"
    if raw in {"trend", "up", "down", "trend_up", "trend_down"}:
        return "trend"
    major = str((structure or {}).get("major_state") or (structure or {}).get("primary_structure") or "").lower()
    if layer == "swing":
        if "triangle" in major:
            return "triangle"
        if major in {"range", "sideways"}:
            return "range"
        if major in {"up", "down", "trend", "trend_up", "trend_down"}:
            return "trend"
    return "transition"


def _plan_for_event(layer: str, event_type: str, pattern: str, rule: Dict) -> str:
    default = str(rule.get("plan_type") or "")
    if event_type == "bos":
        if layer == "internal":
            return "internal_momentum"
        if pattern == "range":
            return "range_breakout"
        if pattern == "triangle":
            return "triangle_breakout"
        return "trend_continuation"
    if event_type == "choch":
        return "early_reversal" if layer == "internal" else "structure_reversal"
    if event_type == "liquidity_sweep":
        return "liquidity_reversal"
    if event_type in {"hl_confirmed"}:
        return "internal_pullback" if layer == "internal" else "swing_pullback"
    if event_type in {"lh_confirmed"}:
        return "internal_pullback" if layer == "internal" else "swing_pullback"
    if event_type in {"retest", "hl_support_touched", "lh_press_touched"}:
        return "event_confirmation"
    if event_type == "reclaim":
        return "range_reclaim" if pattern == "range" else "event_confirmation"
    return default


def decide_event_observations(
    structure: Dict, events: Iterable[Dict], *, require_external_alignment: bool = True,
    matrix_overrides: Dict | None = None,
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
        rule = matrix_rule(layer, event_type)
        override = (matrix_overrides or {}).get(rule.get("event_key"))
        if isinstance(override, dict):
            rule = apply_matrix_overrides([rule], {rule["event_key"]: override})[0]
        if not rule.get("enabled", True) or rule.get("action") == "ignore":
            decisions.append({
                "decision_id": _decision_id(str(event.get("event_id") or ""), "ignored"),
                "event_id": str(event.get("event_id") or ""),
                "setup_type": "", "plan_type": rule.get("plan_type") or "",
                "status": "ignored", "reason": "事件矩阵已忽略或关闭",
                "matrix_action": "ignore", "event_layer": layer,
                "event_type": event_type, "direction": event_direction,
                "direction_layer": layer, "entry_layer": layer,
                "direct_signal": False,
                "required_confirmation": rule.get("required_confirmation", "none"),
                "allowed_regimes": list(rule.get("allowed_regimes") or []),
                "context": {"internal": _bias(structure, "internal"), "swing": _bias(structure, "swing"), "external": _bias(structure, "external")},
            })
            continue
        pattern = _layer_pattern(structure, layer)
        setup = _plan_for_event(layer, event_type, pattern, rule)
        direction = event_direction
        direction_layer = "swing" if event_type in {"bos", "liquidity_sweep", "retest", "reclaim"} else layer
        entry_layer = layer
        if event_type == "liquidity_sweep":
            direction = "down" if event_direction == "up" else "up" if event_direction == "down" else ""
        if event_type == "bos":
            if layer not in {"internal", "swing"} or direction not in {"up", "down"}:
                status, reason = "rejected", "BOS 所属层级或方向无效"
            elif layer == "internal" and _bias(structure, "swing") not in {"", direction} and pattern != "range":
                status, reason = "rejected", f"INTERNAL BOS 与 SWING {_bias(structure, 'swing')} 方向不一致"
            elif require_external_alignment and layer == "swing" and pattern == "trend" and not _external_allows(structure, direction):
                status, reason = "rejected", f"BOS 方向与 EXTERNAL {_bias(structure, 'external')} 不一致"
            else:
                status, reason = "accepted", f"{layer.upper()} BOS 发生在 {pattern} 形态中，建立观察计划"
        elif event_type == "choch":
            if layer not in {"internal", "swing"} or direction not in {"up", "down"}:
                status, reason = "rejected", "CHOCH 所属层级或方向无效"
            else:
                status, reason = "accepted", f"{layer.upper()} CHOCH 发生在 {pattern} 形态中，建立反转观察"
        elif event_type == "liquidity_sweep":
            if layer not in {"internal", "swing"} or direction not in {"up", "down"}:
                status, reason = "rejected", "扫单观察只接受 INTERNAL/SWING 层的明确方向事件"
            else:
                status, reason = "accepted", f"{layer.upper()} 扫单发生在 {pattern} 形态中，建立回收观察"
        elif event_type in {"hl_confirmed", "lh_confirmed"}:
            expected = "up" if event_type == "hl_confirmed" else "down"
            if layer not in {"internal", "swing"} or direction != expected:
                status, reason = "rejected", f"{event_type.upper()} 层级或方向无效"
            elif pattern == "range":
                status, reason = "rejected", f"{event_type.upper()} 发生在箱体中，不作为趋势回撤"
            elif _bias(structure, "swing") not in {"", direction}:
                status, reason = "rejected", f"{event_type.upper()} 与 SWING {_bias(structure, 'swing')} 方向不一致"
            else:
                status, reason = "accepted", f"{event_type.upper()} 已确认，等待结构位触碰和回测"
        elif event_type in {"hl_support_touched", "lh_press_touched"}:
            expected = "up" if event_type == "hl_support_touched" else "down"
            if layer not in {"internal", "swing"} or direction != expected:
                status, reason = "rejected", f"{event_type.upper()} 层级或方向无效"
            elif _bias(structure, "swing") not in {"", direction}:
                status, reason = "rejected", f"{event_type.upper()} 与 SWING {_bias(structure, 'swing')} 方向不一致"
            else:
                status, reason = "accepted", f"{event_type.upper()} 已触碰，等待当前周期确认"
        elif event_type in {"retest", "reclaim"}:
            if layer not in {"internal", "swing"} or direction not in {"up", "down"}:
                status, reason = "rejected", f"{event_type.upper()} 所属层级或方向无效"
            else:
                status, reason = "accepted", f"{event_type.upper()} 发生在 {pattern} 形态中，建立观察计划"
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
            "pattern": pattern,
            "matrix_action": rule.get("action", "ignore"),
            "plan_type": setup,
            "direct_signal": bool(rule.get("direct_signal")),
            "required_confirmation": rule.get("required_confirmation", "none"),
            "allowed_regimes": list(rule.get("allowed_regimes") or []),
            "context": {
                "internal": _bias(structure, "internal"),
                "swing": _bias(structure, "swing"),
                "external": _bias(structure, "external"),
                "pattern": pattern,
            },
        })
    return decisions
