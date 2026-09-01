"""Coordinate application of evaluated position-management actions."""
from __future__ import annotations

from .position_action_applier import apply_action


class PositionManagementCoordinator:
    def apply(self, *, action, state, position, ticket: int, symbol: str,
              close_instructions, stop_instructions, partial_instructions,
              close_callback) -> dict:
        result = apply_action(action, state, position, ticket)
        if result["close"]:
            close_callback(symbol, position.ticket)
            return result
        if result["stop_update"]:
            stop_instructions[symbol][ticket] = result["stop_update"]
        partial = result.get("partial")
        if partial:
            partial_instructions[symbol][
                f"{ticket}:{'|'.join(partial['level_ids'])}"
            ] = partial
        return result
