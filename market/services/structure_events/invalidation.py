"""Newer layer events that retire an existing observation or trade plan."""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

from .catalog import latest_event_for


def _unix(event: Dict | None) -> int:
    if not event:
        return -1
    try:
        return int(event.get("confirmed_at") or event.get("index") or 0)
    except (TypeError, ValueError):
        return -1


def _trade_dir(value) -> str:
    text = str(value or "").lower()
    if text in {"up", "buy", "long", "bullish"}:
        return "buy"
    if text in {"down", "sell", "short", "bearish"}:
        return "sell"
    return ""


def _plan_time(plan: Dict) -> int:
    source = plan.get("source_event") if isinstance(plan.get("source_event"), dict) else {}
    evidence = plan.get("validation_evidence") if isinstance(plan.get("validation_evidence"), dict) else {}
    for value in (
        source.get("confirmed_at"),
        evidence.get("confirmed_at"),
        plan.get("structure_anchor_time"),
    ):
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            number = 0
        if number:
            return number
    return 0


def _layer(plan: Dict) -> str:
    layer = str(plan.get("event_layer") or "").lower()
    if layer:
        return layer
    chain = plan.get("event_chain") or []
    first = str(chain[0] if chain else "")
    if ":" in first:
        return first.split(":", 1)[0].lower()
    return ""


def plan_family(setup_type: str) -> str:
    setup = str(setup_type or "").lower()
    if "range_breakout" in setup:
        return "breakout"
    if setup.startswith("range_"):
        return "range"
    if "triangle" in setup:
        return "triangle"
    if "liquidity" in setup or "sweep" in setup:
        return "liquidity"
    if "reversal" in setup:
        return "reversal"
    if "pullback" in setup or setup == "event_confirmation":
        return "pullback"
    if setup in {"internal_momentum", "trend_continuation"}:
        return "continuation"
    return setup


def layer_character(events: Iterable[Dict], layer: str) -> Tuple[str, str, int]:
    """Latest same-layer structure break. A tie prefers CHOCH over BOS."""
    choch = latest_event_for(events, layer=layer, event_type="choch")
    bos = latest_event_for(events, layer=layer, event_type="bos")
    choch_at, bos_at = _unix(choch), _unix(bos)
    if choch_at < 0 and bos_at < 0:
        return "", "", -1
    if choch_at >= bos_at:
        return "choch", _trade_dir((choch or {}).get("direction")), choch_at
    return "bos", _trade_dir((bos or {}).get("direction")), bos_at


def event_conflict_reason(plan: Dict, events: Iterable[Dict] | None) -> str:
    """Return why a newer event makes this plan obsolete, if it does.

    Same-layer CHOCH and BOS are alternative readings of a break. The latest
    one is the live thesis: CHOCH retires continuation, BOS retires reversal.
    Opposite-direction waiters on that layer also die. EXTERNAL CHOCH can
    retire a SWING continuation or pullback that now fights the larger move.
    """
    layer = _layer(plan)
    if layer not in {"internal", "swing"}:
        return ""
    family = plan_family(str(plan.get("setup_type") or plan.get("plan_type") or ""))
    direction = str(plan.get("direction") or "").lower()
    kind, character_dir, character_at = layer_character(events or [], layer)
    if kind == "choch" and character_dir:
        if family in {"continuation", "breakout", "triangle"}:
            return f"{layer.upper()} 已出现 CHOCH，原延续/突破计划失效"
        if direction and direction != character_dir:
            side = "买入" if character_dir == "buy" else "卖出"
            return f"{layer.upper()} CHOCH 已转为{side}，反向计划失效"
    if kind == "bos" and character_dir:
        if family == "reversal":
            return f"{layer.upper()} 已出现 BOS，原 CHOCH 反转计划失效"
        if direction and direction != character_dir:
            side = "买入" if character_dir == "buy" else "卖出"
            return f"{layer.upper()} BOS 延续{side}，反向计划失效"
    reclaim = latest_event_for(events or [], layer=layer, event_type="reclaim")
    if reclaim and family == "breakout" and _unix(reclaim) >= _plan_time(plan):
        return f"{layer.upper()} 突破后收回，原突破计划失效"
    if layer == "swing":
        ext_kind, ext_dir, _ext_at = layer_character(events or [], "external")
        if ext_kind == "choch" and ext_dir and family in {"continuation", "breakout", "pullback"}:
            if direction and direction != ext_dir:
                return "EXTERNAL CHOCH 与 SWING 计划方向相反，计划失效"
    return ""
