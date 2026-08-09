#!/usr/bin/env python3
"""Moving-average crossover signal generator."""

from datetime import datetime
from typing import Dict, List

from ...store import KlineStore
from .signal_rules import (
    build_moving_average_state_signal,
    evaluate_moving_average_state,
)


class MovingAverageSignalGenerator:
    """Track a crossover intent until it becomes a qualified entry trigger."""

    def __init__(self, kline_store: KlineStore = None):
        self.kline_store = kline_store or KlineStore()
        self._last_emitted: Dict[str, datetime] = {}
        self._emitted_events = set()
        self._pending_crosses: Dict[str, Dict] = {}
        print("[MovingAverageSignalGenerator] 均线信号生成器已初始化")

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List:
        signals = []
        for config in strategy.get_signal_sources(
            "moving_average", enabled_only=True
        ):
            period = config["period"]
            params = config.get("params") or {}
            fast_period = int(params.get("fast_period", 5))
            slow_period = int(params.get("slow_period", 20))
            ma_type = str(params.get("ma_type", "sma")).lower()
            min_confidence = max(0, min(100, int(params.get("min_confidence", 70))))
            rows = self.kline_store.get_all_klines(symbol, period)
            closes = [float(row.get("close", 0)) for row in rows]
            state = evaluate_moving_average_state(
                closes,
                fast_period,
                slow_period,
                ma_type,
            )
            latest_time = (
                rows[-1].get("timestamp") or rows[-1].get("time")
                if rows else None
            )
            source_id = config["signal_source_id"]
            intent_key = f"{strategy.strategy_id}:{source_id}:{symbol}"
            event_key = (
                strategy.strategy_id, source_id, symbol, str(latest_time),
                str(state.get("cross") or ""),
            )
            cooldown_key = f"{strategy.strategy_id}:{source_id}:{symbol}"
            cooldown = max(0, int(params.get("cooldown_seconds", 180)))
            last_time = self._last_emitted.get(cooldown_key)
            cross = state.get("cross")
            if cross in {"buy", "sell"} and event_key not in self._emitted_events:
                self._pending_crosses[intent_key] = {
                    "direction": cross,
                    "event_key": event_key,
                    "created_at": datetime.now(),
                }
                self._emitted_events.add(event_key)

            pending = self._pending_crosses.get(intent_key) or {}
            pending_direction = pending.get("direction")
            qualified = (
                pending_direction in {"buy", "sell"}
                and state.get("direction") == (
                    "up" if pending_direction == "buy" else "down"
                )
                and int(state.get("confidence") or 0) >= min_confidence
            )
            trigger = qualified
            if trigger and last_time:
                trigger = (datetime.now() - last_time).total_seconds() >= cooldown
            signal = build_moving_average_state_signal(
                symbol=symbol,
                current_price=current_price,
                period=period,
                state=state,
                fast_period=fast_period,
                slow_period=slow_period,
                ma_type=ma_type,
                is_entry_trigger=trigger,
            )
            if signal:
                if trigger:
                    self._last_emitted[cooldown_key] = datetime.now()
                    self._pending_crosses.pop(intent_key, None)
                    if len(self._emitted_events) > 5000:
                        self._emitted_events.pop()
                signal.signal_source_id = source_id
                signals.append(signal)
        return signals

    def __call__(self, symbol: str, current_price: float) -> List:
        return []
