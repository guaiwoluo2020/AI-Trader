#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势分析模块
基于均线和ADX判断趋势方向和强度
"""

from collections import defaultdict
from typing import List, Dict, Optional
from datetime import datetime
import threading

from .store import KlineData, normalize_symbol


class TrendAnalyzer:
    """趋势分析器"""

    # 支持的周期
    PERIODS = ['H4', 'H1', 'M15', 'M5', 'M1']

    # ADX阈值
    ADX_TREND_THRESHOLD = 25      # ADX > 25 表示有趋势
    ADX_STRONG_THRESHOLD = 40     # ADX > 40 表示强趋势

    # 均线周期
    MA_FAST = 10                   # 快线周期
    MA_SLOW = 20                   # 慢线周期

    def __init__(self):
        # 存储各周期趋势状态: {SYMBOL: {PERIOD: TrendState}}
        self._trend_states = defaultdict(lambda: defaultdict(dict))
        self._lock = threading.RLock()

        # 趋势转换历史
        self._trend_changes = defaultdict(list)

        print("[TrendAnalyzer] 趋势分析器已初始化")

    def analyze_trend(self, symbol: str, period: str, klines: List[KlineData]) -> Dict:
        """
        分析单个周期的趋势

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据

        Returns:
            {
                "trend": "up" / "down" / "sideways",
                "strength": 0-100,
                "adx": float,
                "ma_fast": float,
                "ma_slow": float,
                "price": float,
                "change_signal": bool,  # 是否发生趋势转换
                "timestamp": str
            }
        """
        if len(klines) < 30:  # 至少需要30根K线
            return {
                "trend": "unknown",
                "strength": 0,
                "adx": 0,
                "ma_fast": 0,
                "ma_slow": 0,
                "price": 0,
                "change_signal": False,
                "reason": "K线数据不足(需≥30根)",
                "timestamp": datetime.now().isoformat()
            }

        # 计算均线
        closes = [k.close for k in klines]
        ma_fast = self._calculate_ma(closes, self.MA_FAST)
        ma_slow = self._calculate_ma(closes, self.MA_SLOW)
        current_price = closes[-1]

        # 计算ADX
        adx = self._calculate_adx(klines)

        # 判断趋势方向和原因
        reason_parts = []

        if adx < self.ADX_TREND_THRESHOLD:
            # ADX较低，震荡行情
            trend = "sideways"
            reason_parts.append(f"ADX={adx:.1f}<25 无明显趋势")
        else:
            # 根据均线和价格判断方向
            if ma_fast > ma_slow and current_price > ma_fast:
                trend = "up"
                reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) > MA{self.MA_SLOW}({ma_slow:.2f})")
                reason_parts.append(f"价格({current_price:.2f}) > MA{self.MA_FAST}")
                reason_parts.append(f"ADX={adx:.1f}≥25 确认趋势")
            elif ma_fast < ma_slow and current_price < ma_fast:
                trend = "down"
                reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) < MA{self.MA_SLOW}({ma_slow:.2f})")
                reason_parts.append(f"价格({current_price:.2f}) < MA{self.MA_FAST}")
                reason_parts.append(f"ADX={adx:.1f}≥25 确认趋势")
            else:
                trend = "sideways"
                if ma_fast > ma_slow:
                    reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) > MA{self.MA_SLOW}({ma_slow:.2f})")
                    reason_parts.append(f"但价格({current_price:.2f})低于MA{self.MA_FAST}")
                else:
                    reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) < MA{self.MA_SLOW}({ma_slow:.2f})")
                    reason_parts.append(f"且价格({current_price:.2f})高于MA{self.MA_FAST}")
                reason_parts.append("信号矛盾，判定震荡")

        reason = "；".join(reason_parts)

        # 计算趋势强度 (基于ADX)
        if adx >= self.ADX_STRONG_THRESHOLD:
            strength = min(100, int(adx + 20))
        elif adx >= self.ADX_TREND_THRESHOLD:
            strength = int(adx + 10)
        else:
            strength = int(adx)

        # 检查趋势转换
        symbol_key = normalize_symbol(symbol)
        change_signal = False
        previous_trend = None

        with self._lock:
            if period in self._trend_states[symbol_key]:
                previous_trend = self._trend_states[symbol_key][period].get('trend')
                if previous_trend and previous_trend != trend and previous_trend != "unknown":
                    change_signal = True
                    # 记录转换历史
                    self._trend_changes[symbol_key].append({
                        "period": period,
                        "from_trend": previous_trend,
                        "to_trend": trend,
                        "timestamp": datetime.now().isoformat(),
                        "price": current_price
                    })
                    # 只保留最近20条
                    if len(self._trend_changes[symbol_key]) > 20:
                        self._trend_changes[symbol_key] = self._trend_changes[symbol_key][-20:]

            # 更新状态
            self._trend_states[symbol_key][period] = {
                "trend": trend,
                "strength": strength,
                "adx": round(adx, 2),
                "ma_fast": round(ma_fast, 4),
                "ma_slow": round(ma_slow, 4),
                "price": current_price,
                "change_signal": change_signal,
                "previous_trend": previous_trend,
                "reason": reason,
                "timestamp": datetime.now().isoformat()
            }

        return self._trend_states[symbol_key][period]

    def analyze_resonance(self, symbol: str) -> Dict:
        """
        分析多周期共振

        Returns:
            {
                "resonance": "up" / "down" / "none",
                "strength": 0-100,
                "periods": {period: trend_state},
                "aligned_count": int,
                "signal": str
            }
        """
        symbol_key = normalize_symbol(symbol)

        with self._lock:
            states = dict(self._trend_states[symbol_key])

        if not states:
            return {
                "resonance": "none",
                "strength": 0,
                "periods": {},
                "aligned_count": 0,
                "signal": "等待数据"
            }

        # 统计各趋势数量
        up_count = sum(1 for s in states.values() if s.get('trend') == 'up')
        down_count = sum(1 for s in states.values() if s.get('trend') == 'down')
        sideways_count = sum(1 for s in states.values() if s.get('trend') == 'sideways')

        # 计算平均强度
        strengths = [s.get('strength', 0) for s in states.values() if s.get('trend') != 'sideways']
        avg_strength = sum(strengths) / len(strengths) if strengths else 0

        # 判断共振
        total = len(states)
        if up_count >= total * 0.6:  # 60%以上周期趋势一致
            resonance = "up"
            aligned_count = up_count
            signal = f"多周期向上共振 ({up_count}/{total})"
        elif down_count >= total * 0.6:
            resonance = "down"
            aligned_count = down_count
            signal = f"多周期向下共振 ({down_count}/{total})"
        else:
            resonance = "none"
            aligned_count = max(up_count, down_count)
            signal = f"趋势分歧 (↑{up_count} ↓{down_count} →{sideways_count})"

        return {
            "resonance": resonance,
            "strength": int(avg_strength),
            "periods": states,
            "aligned_count": aligned_count,
            "up_count": up_count,
            "down_count": down_count,
            "sideways_count": sideways_count,
            "signal": signal
        }

    def get_trend_state(self, symbol: str, period: str = None) -> Dict:
        """获取趋势状态"""
        symbol_key = normalize_symbol(symbol)

        with self._lock:
            if period:
                return self._trend_states[symbol_key].get(period, {})
            return dict(self._trend_states[symbol_key])

    def get_trend_changes(self, symbol: str, count: int = 10) -> List[Dict]:
        """获取趋势转换历史"""
        symbol_key = normalize_symbol(symbol)

        with self._lock:
            return self._trend_changes[symbol_key][-count:]

    def _calculate_ma(self, data: List[float], period: int) -> float:
        """计算移动平均线"""
        if len(data) < period:
            return data[-1] if data else 0
        return sum(data[-period:]) / period

    def _calculate_adx(self, klines: List[KlineData], period: int = 14) -> float:
        """
        计算ADX (Average Directional Index)

        ADX > 25: 有趋势
        ADX > 40: 强趋势
        ADX < 20: 无明显趋势
        """
        if len(klines) < period + 1:
            return 0

        # 计算 +DM 和 -DM
        plus_dm = []
        minus_dm = []
        tr_list = []

        for i in range(1, len(klines)):
            high = klines[i].high
            low = klines[i].low
            prev_high = klines[i-1].high
            prev_low = klines[i-1].low
            prev_close = klines[i-1].close

            # +DM
            up_move = high - prev_high
            down_move = prev_low - low

            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0)

            # -DM
            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0)

            # True Range
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_list.append(tr)

        if len(tr_list) < period:
            return 0

        # 计算平滑值
        atr = sum(tr_list[-period:]) / period
        smoothed_plus_dm = sum(plus_dm[-period:]) / period
        smoothed_minus_dm = sum(minus_dm[-period:]) / period

        # 计算 +DI 和 -DI
        if atr == 0:
            return 0

        plus_di = (smoothed_plus_dm / atr) * 100
        minus_di = (smoothed_minus_dm / atr) * 100

        # 计算 DX
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0

        dx = abs(plus_di - minus_di) / di_sum * 100

        return dx

    def generate_trade_suggestion(self, symbol: str, pivots: List[Dict],
                                   current_price: float) -> Optional[Dict]:
        """
        基于趋势和转折点生成交易建议

        Args:
            symbol: 交易品种
            pivots: 转折点数据
            current_price: 当前价格

        Returns:
            交易建议 或 None
        """
        symbol_key = normalize_symbol(symbol)

        # 获取趋势状态
        resonance = self.analyze_resonance(symbol)

        if resonance['resonance'] == 'none':
            return None

        if resonance['strength'] < 30:
            return None

        trend = resonance['resonance']

        # 根据趋势找最近的转折点作为止损止盈
        recent_pivots = sorted(pivots, key=lambda x: x['timestamp'], reverse=True)[:10]

        sl = None
        tp = None
        action = None
        reason = ""

        if trend == "up":
            # 上升趋势，找最近的低点作为止损
            action = "b"
            low_pivots = [p for p in recent_pivots if p['direction'] == 'low']
            if low_pivots:
                # 找最近的低点作为止损
                sl = low_pivots[0]['price']
            # 止盈设为止损的1.5-2倍距离
            if sl and current_price > sl:
                distance = current_price - sl
                tp = current_price + distance * 1.5
                reason = f"多周期向上共振，建议买入，止损参考最近低点 {sl}"
            else:
                return None

        elif trend == "down":
            # 下降趋势，找最近的高点作为止损
            action = "s"
            high_pivots = [p for p in recent_pivots if p['direction'] == 'high']
            if high_pivots:
                sl = high_pivots[0]['price']
            if sl and current_price < sl:
                distance = sl - current_price
                tp = current_price - distance * 1.5
                reason = f"多周期向下共振，建议卖出，止损参考最近高点 {sl}"
            else:
                return None

        if not all([action, sl, tp]):
            return None

        return {
            "symbol": symbol_key,
            "action": action,
            "price": current_price,
            "sl": round(sl, 4),
            "tp": round(tp, 4),
            "reason": reason,
            "trend_strength": resonance['strength'],
            "resonance_periods": resonance['aligned_count'],
            "generated_at": datetime.now().isoformat()
        }