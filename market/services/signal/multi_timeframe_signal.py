"""Multi-timeframe trend and entry signal generator."""

from datetime import datetime
from typing import Dict, List

from ...models import TradingSignal
from ..regime_classifier import analyze_main_period_regime
from ...store import KlineStore


class MultiTimeframeSignalGenerator:
    """Use a larger timeframe for regime and a smaller one for entry timing."""

    def __init__(self, kline_store: KlineStore = None):
        self.kline_store = kline_store or KlineStore()
        self._last_emitted: Dict[str, datetime] = {}

    @staticmethod
    def _levels(rows):
        highs = [float(x.get("high", x.get("high_price", 0)) or 0) for x in rows]
        lows = [float(x.get("low", x.get("low_price", 0)) or 0) for x in rows]
        closes = [float(x.get("close", x.get("close_price", 0)) or 0) for x in rows]
        if not closes:
            return None
        return min(lows), max(highs), closes[-1], closes

    def generate_signals_for_strategy(self, symbol: str, current_price: float, strategy) -> List[TradingSignal]:
        signals = []
        for config in strategy.get_signal_sources("multi_timeframe", enabled_only=True):
            params = config.get("params") or {}
            trend_period = str(params.get("trend_period", "M15")).upper()
            entry_period = str(params.get("entry_period", config.get("period", "M1"))).upper()
            trend_rows = self.kline_store.get_all_klines(symbol, trend_period)[-int(params.get("trend_kline_count", 70)):]
            entry_rows = self.kline_store.get_all_klines(symbol, entry_period)[-int(params.get("entry_kline_count", 40)):]
            if len(trend_rows) < 10 or len(entry_rows) < 10 or current_price <= 0:
                continue
            regime = analyze_main_period_regime(trend_rows)
            state = regime.get("regime", "transition")
            confidence = int(regime.get("confidence", 0))
            if state == "transition" or confidence < int(params.get("min_trend_confidence", 60)):
                continue
            # 小周期分层：10 根确认当前触发，20 根确定局部结构，40 根提供上下文。
            trigger_rows = entry_rows[-10:]
            structure_rows = entry_rows[-20:]
            levels = self._levels(structure_rows)
            trend_levels = self._levels(trend_rows)
            if not levels or not trend_levels:
                continue
            entry_low, entry_high, last_close, closes = levels
            trigger_context = self._levels(trigger_rows)
            if not trigger_context:
                continue
            trigger_first = trigger_context[3][0]
            trigger_last = trigger_context[3][-1]
            trigger_up = trigger_last >= trigger_first and trigger_last >= trigger_context[3][-2]
            trigger_down = trigger_last <= trigger_first and trigger_last <= trigger_context[3][-2]
            trend_low, trend_high = trend_levels[:2]
            atr = max((entry_high - entry_low) / max(10, len(entry_rows)), 1e-12)
            threshold = max(0.0, float(params.get("proximity_threshold", 0.0008)))
            mode = str(params.get("entry_mode", "pullback")).lower()
            action = None
            setup_type = ""
            trigger_level = None
            stop = None
            target = None
            if state == "up":
                support = min(entry_low, trend_low)
                near_support = abs(current_price - support) / current_price <= threshold
                reclaimed = last_close >= support and current_price >= last_close and trigger_up
                if mode == "pullback" and near_support and reclaimed:
                    action, setup_type, trigger_level = "buy", "trend_pullback", support
                elif mode == "breakout_retest" and current_price > entry_high and last_close > entry_high:
                    action, setup_type, trigger_level = "buy", "trend_breakout", entry_high
                if action == "buy":
                    stop = support - atr
                    target = current_price + abs(current_price - stop) * float(params.get("risk_reward_ratio", 2.0))
            elif state == "down":
                resistance = max(entry_high, trend_high)
                near_resistance = abs(current_price - resistance) / current_price <= threshold
                rejected = last_close <= resistance and current_price <= last_close and trigger_down
                if mode == "pullback" and near_resistance and rejected:
                    action, setup_type, trigger_level = "sell", "trend_pullback", resistance
                elif mode == "breakout_retest" and current_price < entry_low and last_close < entry_low:
                    action, setup_type, trigger_level = "sell", "trend_breakout", entry_low
                if action == "sell":
                    stop = resistance + atr
                    target = current_price - abs(stop - current_price) * float(params.get("risk_reward_ratio", 2.0))
            elif state == "sideways":
                near_low = abs(current_price - entry_low) / current_price <= threshold
                near_high = abs(current_price - entry_high) / current_price <= threshold
                if mode in {"pullback", "range_reversal"} and near_low and current_price >= last_close and trigger_up:
                    action, setup_type, trigger_level = "buy", "range_reversal", entry_low
                    stop = entry_low - atr
                    target = entry_high
                elif mode in {"pullback", "range_reversal"} and near_high and current_price <= last_close and trigger_down:
                    action, setup_type, trigger_level = "sell", "range_reversal", entry_high
                    stop = entry_high + atr
                    target = entry_low
                elif mode == "breakout_retest" and current_price > entry_high and last_close > entry_high:
                    action, setup_type, trigger_level = "buy", "range_breakout", entry_high
                    stop = entry_high - atr
                    target = current_price + abs(current_price - stop) * float(params.get("risk_reward_ratio", 2.0))
                elif mode == "breakout_retest" and current_price < entry_low and last_close < entry_low:
                    action, setup_type, trigger_level = "sell", "range_breakout", entry_low
                    stop = entry_low + atr
                    target = current_price - abs(stop - current_price) * float(params.get("risk_reward_ratio", 2.0))
            if not action or not stop or not target:
                continue
            key = f"{strategy.strategy_id}:{config['signal_source_id']}:{symbol}:{setup_type}:{trigger_level}"
            last = self._last_emitted.get(key)
            cooldown = max(0, int(params.get("cooldown_seconds", 180)))
            if last and (datetime.now() - last).total_seconds() < cooldown:
                continue
            self._last_emitted[key] = datetime.now()
            signals.append(TradingSignal(
                symbol=symbol, action=action, confidence=confidence,
                market_direction="up" if action == "buy" else "down",
                source="multi_timeframe", source_period=entry_period,
                signal_source_id=config["signal_source_id"],
                setup_family="trend_follow" if state in {"up", "down"} else "range",
                setup_type=setup_type, entry_mode="confirmation",
                trigger_price=float(current_price),
                trigger_reason=f"{trend_period}={state}，{entry_period}确认{setup_type}",
                suggested_entry=float(current_price), suggested_sl=float(stop),
                suggested_tp=float(target),
                risk_reward_ratio=abs(target - current_price) / max(abs(current_price - stop), 1e-12),
            ))
        return signals
