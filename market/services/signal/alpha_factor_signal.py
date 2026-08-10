#!/usr/bin/env python3
"""Validated Alpha evaluator shared by live strategy and historical replay."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from ...models import SignalSource, TradingSignal
from ...store import KlineStore
from .signal_rules import direction_action


class AlphaRuntimeExecutor:
    """Evaluate a pinned Alpha definition from closed OHLCV bars."""

    def __init__(self):
        # Lazy import keeps alpha_research -> backtest_engine imports acyclic.
        from alpha_research import AlphaBacktestEngine
        self.engine = AlphaBacktestEngine()

    @staticmethod
    def _timestamp(value, fallback: int) -> int:
        if isinstance(value, datetime):
            return int(value.timestamp())
        try:
            return int(value)
        except (TypeError, ValueError):
            text = str(value or "").replace("Z", "+00:00")
            try:
                return int(datetime.fromisoformat(text).timestamp())
            except ValueError:
                return fallback

    def evaluate(self, bars: List[Dict], definition: Dict) -> Dict:
        rows = []
        for index, bar in enumerate(bars):
            rows.append({
                "time": self._timestamp(
                    bar.get("time") or bar.get("timestamp"), index
                ),
                "open": float(bar.get("open", 0)),
                "high": float(bar.get("high", 0)),
                "low": float(bar.get("low", 0)),
                "close": float(bar.get("close", 0)),
                "tick_volume": int(
                    bar.get("tick_volume", bar.get("volume", 0)) or 0
                ),
                "spread": int(bar.get("spread", 0) or 0),
            })
        if not rows or not definition.get("factors"):
            return self._not_ready("Alpha 定义或行情数据为空")
        frame = pd.DataFrame(rows).sort_values("time").drop_duplicates("time")
        try:
            alpha = self.engine.calculate_alpha(
                frame.reset_index(drop=True), definition, definition["params"]
            )
        except Exception as exc:
            return self._not_ready(f"Alpha 计算失败: {exc}")
        valid = alpha.dropna()
        if valid.empty:
            return self._not_ready("Alpha 指标仍在预热")
        current = float(valid.iloc[-1])
        buy_threshold = float(definition["buy_threshold"])
        sell_threshold = float(definition["sell_threshold"])
        confirmation = max(1, int(definition.get("confirmation_bars", 1)))
        recent = valid.iloc[-confirmation:]
        prior = (
            float(valid.iloc[-confirmation - 1])
            if len(valid) > confirmation else 0.0
        )
        if current >= buy_threshold:
            direction = "up"
            threshold = max(abs(buy_threshold), 1e-9)
            trigger = (
                len(recent) == confirmation
                and bool(recent.ge(buy_threshold).all())
                and prior < buy_threshold
            )
            strength = current / threshold
        elif current <= sell_threshold:
            direction = "down"
            threshold = max(abs(sell_threshold), 1e-9)
            trigger = (
                len(recent) == confirmation
                and bool(recent.le(sell_threshold).all())
                and prior > sell_threshold
            )
            strength = abs(current) / threshold
        else:
            direction = "sideways"
            trigger = False
            strength = 0.0
        confidence = (
            min(95, max(50, int(50 + min(2.0, strength) * 22.5)))
            if direction != "sideways" else 55
        )
        return {
            "ready": True,
            "direction": direction,
            "confidence": confidence,
            "alpha_value": round(current, 8),
            "is_entry_trigger": trigger,
            "reason": (
                f"Alpha={current:.4f}，买入阈值={buy_threshold:.4f}，"
                f"卖出阈值={sell_threshold:.4f}"
            ),
        }

    @staticmethod
    def _not_ready(reason: str) -> Dict:
        return {
            "ready": False, "direction": "sideways", "confidence": 0,
            "alpha_value": None, "is_entry_trigger": False, "reason": reason,
        }

    @staticmethod
    def build_signal(
        symbol: str, period: str, current_price: float, state: Dict,
        signal_time: Optional[datetime] = None,
    ) -> TradingSignal:
        period_seconds = {
            "M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400,
        }.get(period, 300)
        return TradingSignal(
            symbol=symbol,
            action=direction_action(state["direction"]),
            market_direction=state["direction"],
            state_ready=bool(state["ready"]),
            is_entry_trigger=bool(state["is_entry_trigger"]),
            confidence=int(state["confidence"]),
            source=SignalSource.ALPHA_FACTOR,
            source_period=period,
            trigger_price=current_price,
            trigger_time=signal_time,
            trigger_reason=state["reason"],
            suggested_entry=current_price,
            created_at=signal_time,
            expires_at=(
                signal_time + timedelta(seconds=period_seconds * 2)
                if signal_time else None
            ),
        )


class AlphaFactorSignalGenerator:
    def __init__(self, kline_store: KlineStore = None):
        self.kline_store = kline_store or KlineStore()
        self.executor = AlphaRuntimeExecutor()
        self._last_emitted: Dict[str, datetime] = {}

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        signals = []
        for config in strategy.get_signal_sources("alpha_factor", enabled_only=True):
            params = config.get("params") or {}
            period = config["period"]
            bars = self.kline_store.get_all_klines(symbol, period)
            state = self.executor.evaluate(bars, params.get("alpha_snapshot") or {})
            state["is_entry_trigger"] = bool(
                state["is_entry_trigger"]
                and state["confidence"] >= int(params.get("min_confidence", 60))
            )
            key = f"{strategy.strategy_id}:{config['signal_source_id']}:{symbol}"
            cooldown = max(0, int(params.get("cooldown_seconds", 180)))
            last = self._last_emitted.get(key)
            if state["is_entry_trigger"] and last and (
                datetime.now() - last
            ).total_seconds() < cooldown:
                state["is_entry_trigger"] = False
            signal = self.executor.build_signal(
                symbol, period, current_price, state, datetime.now()
            )
            signal.signal_source_id = config["signal_source_id"]
            if state["is_entry_trigger"]:
                self._last_emitted[key] = datetime.now()
            signals.append(signal)
        return signals

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        return []
