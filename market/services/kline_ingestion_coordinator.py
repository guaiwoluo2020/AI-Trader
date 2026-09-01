"""Application service for EA K-line ingestion orchestration."""
from __future__ import annotations

from typing import Dict, Iterable

from .events import ApplicationEvent, BAR_CLOSED, PIVOT_UPDATED, STRUCTURE_UPDATED


SUPPORTED_PERIODS = {"H4", "H1", "M15", "M5", "M1"}


class KlineIngestionCoordinator:
    def __init__(self, kline_service, pivot_service, refresh_plans, persist_history,
                 event_bus=None, user_id: int = 0, account_id: int = 0):
        self.kline_service = kline_service
        self.pivot_service = pivot_service
        self.refresh_plans = refresh_plans
        self.persist_history = persist_history
        self.event_bus = event_bus
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)

    def _publish(self, name: str, symbol: str, period: str, payload: Dict) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(ApplicationEvent(
                name, payload, self.user_id, self.account_id, str(symbol or ""),
            ))

    @staticmethod
    def normalize_period(period: str) -> str:
        value = str(period or "").upper()
        if value not in SUPPORTED_PERIODS:
            raise ValueError(f"不支持的周期: {value}")
        return value

    def process_batch(self, symbol: str, data: Dict, is_full: bool = False,
                      broker_utc_offset_seconds: int = 0) -> Dict[str, Dict]:
        results = {}
        for raw_period, klines in (data or {}).items():
            try:
                period = self.normalize_period(raw_period)
            except ValueError:
                continue
            result = self.kline_service.process_kline_data(symbol, period, klines, is_full)
            self.persist_history(symbol, period, klines, broker_utc_offset_seconds)
            results[period] = result
            if result.get("status") == "ok":
                latest = (klines[-1] if klines else {}) or {}
                self._publish(BAR_CLOSED, symbol, period, {
                    "period": period, "count": len(klines), "is_full": bool(is_full),
                    "last_bar_time": latest.get("timestamp") or latest.get("time"),
                })
                objects = self.kline_service.get_all_kline_objects(symbol, period)
                if objects:
                    self.pivot_service.update_pivots(symbol, period, objects)
                    self._publish(PIVOT_UPDATED, symbol, period, {
                        "period": period, "pivot_count": len(self.pivot_service.get_pivots(symbol, period)),
                    })
                result["structure_plan_count"] = self.refresh_plans(symbol, period)
                self._publish(STRUCTURE_UPDATED, symbol, period, {
                    "period": period, "plan_count": int(result.get("structure_plan_count") or 0),
                })
        return results
