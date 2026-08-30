"""基于同周期结构层级的趋势延续信号。"""
from datetime import datetime, timedelta
from typing import Dict, List

from ...models import TradingSignal, SignalSource
from ...store import KlineStore
from ..market_structure_engine_v2 import analyze


class StructureContinuationSignalGenerator:
    """Swing 主结构确认后，等待 Internal 回撤结束并重新突破。"""

    def __init__(self, kline_store=None):
        self.kline_store = kline_store or KlineStore()
        self._emitted = {}

    @staticmethod
    def _price(row, name, fallback=0.0):
        try:
            return float(row.get(name, row.get(f"{name}_price", fallback)))
        except (TypeError, ValueError, AttributeError):
            return float(fallback)

    def generate_signals_for_strategy(self, symbol: str, current_price: float, strategy) -> List[TradingSignal]:
        signals = []
        for config in strategy.get_signal_sources("structure_continuation", enabled_only=True):
            params = config.get("params") or {}
            period = str(config.get("period") or "M5").upper()
            rows = self.kline_store.get_all_klines(symbol, period)
            if len(rows) < 30:
                continue
            result = analyze(symbol, period, rows)
            state = result.get("major_state") or result.get("current_state") or "undetermined"
            if state not in {"up", "down"} or (params.get("require_confirmed_structure", True) and result.get("active_candidate")):
                continue
            hierarchy = result.get("structure_hierarchy") or {}
            swing = hierarchy.get("swing") or {}
            internal = result.get("internal_state") or "undetermined"
            # Internal 重新与主结构同向，且最近一次同向 BOS 已由收盘确认。
            events = result.get("internal_events") or []
            direction = "up" if state == "up" else "down"
            event = next((e for e in reversed(events) if e.get("type") == "bos" and e.get("direction") == direction), None)
            if event is None or internal != state:
                continue
            idx = int(event.get("index", -1))
            key = f"{strategy.strategy_id}:{config.get('signal_source_id')}:{symbol}:{period}:{idx}"
            if key in self._emitted:
                continue
            price = float(current_price or 0)
            if price <= 0:
                continue
            protected = swing.get("protected_low" if state == "up" else "protected_high") or {}
            pivot = float(protected.get("price") or 0)
            if pivot <= 0:
                continue
            buffer = price * max(0.0, float(params.get("stop_buffer_ratio", 0.0005)))
            sl = pivot - buffer if state == "up" else pivot + buffer
            weak = swing.get("weak_high" if state == "up" else "weak_low") or {}
            tp = float(weak.get("price") or 0)
            risk = abs(price - sl)
            rr = max(1.0, float(params.get("risk_reward_ratio", 2.0)))
            if tp <= 0 or (tp <= price if state == "up" else tp >= price) or abs(tp - price) < risk * rr:
                tp = price + risk * rr if state == "up" else price - risk * rr
            signal = TradingSignal(
                symbol=symbol, action="buy" if state == "up" else "sell",
                market_direction=state, state_ready=True, is_entry_trigger=True,
                confidence=max(0, min(100, int(params.get("min_structure_confidence", 60)))),
                source=SignalSource.STRUCTURE_CONTINUATION,
                source_period=period, signal_source_id=config.get("signal_source_id", ""),
                setup_family="trend_follow", setup_type="structure_continuation",
                entry_mode=str(params.get("entry_mode", "internal_reversal_bos")),
                trigger_price=price, suggested_entry=price, suggested_sl=sl,
                suggested_tp=tp, risk_reward_ratio=round(abs(tp-price)/risk, 2) if risk else 0,
                trigger_reason=f"{period} Swing {('上涨' if state == 'up' else '下跌')}结构中，Internal 回撤结束并收盘确认 BOS",
                created_at=datetime.now(), expires_at=datetime.now() + timedelta(seconds=300),
            )
            self._emitted[key] = datetime.now()
            signals.append(signal)
        return signals

    def __call__(self, symbol, current_price):
        return []
