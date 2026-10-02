"""Persist Paper execution outcomes using the canonical execution model."""
from __future__ import annotations

import json
import time


class PaperExecutionReporter:
    def __init__(self, repository):
        self.repository = repository

    def record(self, user_id: int, account_id: int, order: dict, status: str,
               reason: str = "", executed_price: float = 0.0,
               executed_volume: float = 0.0) -> None:
        try:
            attribution = json.loads(order.get("position_attribution_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            attribution = {}
        simulated_at = int(time.time())
        self.repository.record(int(user_id), int(account_id), {
            "instruction_id": f"paper:{order.get('order_id')}",
            "order_id": str(order.get("order_id") or ""),
            "symbol": str(order.get("symbol") or ""),
            "action": str(order.get("direction") or order.get("action") or ""),
            "status": status,
            "success": status in {"filled", "partially_filled"},
            "requested_price": float(order.get("requested_price") or 0),
            "executed_price": float(executed_price or 0),
            "requested_volume": float(order.get("requested_volume") or order.get("volume") or 0),
            "executed_volume": float(executed_volume or 0),
            "error_message": reason,
            "reported_timestamp": simulated_at,
            "trigger_tick_at": int(order.get("trigger_tick_at") or simulated_at),
            "instruction_created_at": int(order.get("created_at") or order.get("requested_at") or simulated_at),
            "simulated_executed_at": simulated_at if status in {"filled", "partially_filled"} else 0,
            "trigger_price": float(order.get("trigger_price") or order.get("requested_price") or 0),
            "simulated_price": float(executed_price or 0),
            "transport": "paper",
            "strategy_id": str(order.get("strategy_id") or ""),
            "position_attribution": attribution,
        })
