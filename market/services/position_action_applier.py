"""Translate PositionManager actions into execution instructions."""
from __future__ import annotations

from typing import Dict, Optional


def apply_action(action, state: Dict, position, ticket: int) -> Dict:
    """Apply one management action to state and return normalized instructions."""
    result = {"close": False, "stop_update": None, "partial": None}
    if action.action == "close":
        result["close"] = True
        return result
    if action.action == "modify_sl" and action.stop_loss:
        state["stop_loss"] = float(action.stop_loss)
        state["pending_stop_loss"] = float(action.stop_loss)
        result["stop_update"] = {
            "ticket": ticket, "sl": round(float(action.stop_loss), 8),
            "tp": round(float(position.tp or 0), 8), "reason": action.reason,
        }
        return result
    if action.action != "partial_close" or action.close_volume <= 0:
        return result
    done = set(state.get("partial_levels_done") or [])
    level_ids = action.level_ids or [action.level_id]
    if all(level_id in done for level_id in level_ids):
        return result
    close_volume = round(float(action.close_volume), 2)
    if close_volume <= 0:
        return result
    done.update(level_ids)
    state["partial_levels_done"] = sorted(done)
    stop_update = None
    if action.stop_loss or action.level_id == "signal_take_profit":
        if action.stop_loss:
            state["stop_loss"] = float(action.stop_loss)
            state["pending_stop_loss"] = float(action.stop_loss)
        if action.level_id == "signal_take_profit":
            state["take_profit"] = 0.0
        stop_update = {
            "ticket": ticket, "sl": round(float(state["stop_loss"]), 8),
            "tp": 0 if action.level_id == "signal_take_profit" else round(float(position.tp or 0), 8),
            "reason": f"{action.reason}:clear_tp" if action.level_id == "signal_take_profit" else f"{action.reason}:move_sl",
        }
    result["stop_update"] = stop_update
    result["partial"] = {
        "ticket": ticket, "volume": close_volume,
        "level_id": action.level_id, "level_ids": level_ids,
        "instruction_id": f"exit-{ticket}-{'-'.join(level_ids)}",
        "reason": action.reason,
    }
    return result
