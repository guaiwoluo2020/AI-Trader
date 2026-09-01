"""Application service for EA K-line ingestion orchestration."""
from __future__ import annotations

from typing import Dict, Iterable


SUPPORTED_PERIODS = {"H4", "H1", "M15", "M5", "M1"}


class KlineIngestionCoordinator:
    def __init__(self, kline_service, pivot_service, refresh_plans, persist_history):
        self.kline_service = kline_service
        self.pivot_service = pivot_service
        self.refresh_plans = refresh_plans
        self.persist_history = persist_history

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
                objects = self.kline_service.get_all_kline_objects(symbol, period)
                if objects:
                    self.pivot_service.update_pivots(symbol, period, objects)
                result["structure_plan_count"] = self.refresh_plans(symbol, period)
        return results
