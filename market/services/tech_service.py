#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术分析服务模块
处理趋势分析、共振分析、交易建议生成等业务逻辑
"""

from datetime import datetime
from typing import Dict, List, Optional

from ..models import KlineData, TechTrendState, TechTrendChange, TechResonanceResult, TechTradeSuggestion
from ..store import TechStore, KlineStore, PivotStore
from .tech_indicators import calculate_ma, calculate_adx


class TechService:
    """技术分析服务（处理业务逻辑）"""

    # 支持的周期
    PERIODS = ['H4', 'H1', 'M15', 'M5', 'M1']

    # ADX 阈值
    ADX_TREND_THRESHOLD = 25
    ADX_STRONG_THRESHOLD = 40

    # 均线周期
    MA_FAST = 10
    MA_SLOW = 20

    # 最小 K 线数量
    MIN_KLINES = 30

    def __init__(self, tech_store: TechStore, kline_store: KlineStore, pivot_store: PivotStore):
        self.tech_store = tech_store
        self.kline_store = kline_store
        self.pivot_store = pivot_store

        # 统计服务引用（用于获取价差）
        self._statistics_service = None

        print("[TechService] 技术分析服务已初始化")

    def set_statistics_service(self, statistics_service):
        """设置统计服务引用"""
        self._statistics_service = statistics_service

    def _get_symbol_spread(self, symbol: str) -> Optional[float]:
        """获取品种价差"""
        if not self._statistics_service:
            return None
        return self._statistics_service.get_spread(symbol)

    # ==================== 趋势分析 ====================

    def analyze_trend(self, symbol: str, period: str) -> Dict:
        """
        分析单个周期的趋势

        Args:
            symbol: 交易品种
            period: 周期

        Returns:
            趋势状态字典
        """
        period = period.upper()

        # 从 store 获取 K 线数据
        klines_dict = self.kline_store.get_all_klines(symbol, period)
        if not klines_dict:
            return self._create_unknown_state(symbol, period, "无K线数据")

        # 转换为 KlineData 对象
        klines = [
            KlineData(
                symbol=k.get('symbol', symbol),
                period=k.get('period', period),
                timestamp=k.get('timestamp'),
                open_price=float(k.get('open', 0)),
                high=float(k.get('high', 0)),
                low=float(k.get('low', 0)),
                close=float(k.get('close', 0)),
                volume=float(k.get('volume', 0))
            )
            for k in klines_dict
        ]

        if len(klines) < self.MIN_KLINES:
            return self._create_unknown_state(symbol, period, f"K线数据不足(需≥{self.MIN_KLINES}根)")

        # 计算技术指标
        closes = [k.close for k in klines]
        ma_fast = calculate_ma(closes, self.MA_FAST)
        ma_slow = calculate_ma(closes, self.MA_SLOW)
        current_price = closes[-1]
        adx = calculate_adx(klines)

        # 判断趋势
        trend, reason = self._determine_trend(adx, ma_fast, ma_slow, current_price)

        # 计算强度
        strength = self._calculate_strength(adx)

        # 获取之前的状态
        previous_state = self.tech_store.get_trend_state_object(symbol, period)
        previous_trend = previous_state.trend if previous_state else None
        change_signal = previous_trend and previous_trend != "unknown" and previous_trend != trend

        # 创建状态对象
        state = TechTrendState(
            symbol=symbol,
            period=period,
            trend=trend,
            strength=strength,
            adx=round(adx, 2),
            ma_fast=round(ma_fast, 4),
            ma_slow=round(ma_slow, 4),
            price=current_price,
            reason=reason,
            timestamp=datetime.now().isoformat(),
            previous_trend=previous_trend,
            change_signal=change_signal
        )

        # 保存状态
        self.tech_store.save_trend_state(state)

        # 记录趋势转换
        if change_signal:
            change = TechTrendChange(
                period=period,
                from_trend=previous_trend,
                to_trend=trend,
                price=current_price,
                timestamp=datetime.now().isoformat()
            )
            self.tech_store.add_trend_change(symbol, change)

        return state.to_dict()

    def _create_unknown_state(self, symbol: str, period: str, reason: str) -> Dict:
        """创建未知状态"""
        state = TechTrendState(
            symbol=symbol,
            period=period,
            trend="unknown",
            reason=reason,
            timestamp=datetime.now().isoformat()
        )
        return state.to_dict()

    def _determine_trend(self, adx: float, ma_fast: float, ma_slow: float, price: float) -> tuple:
        """判断趋势方向"""
        reason_parts = []

        if adx < self.ADX_TREND_THRESHOLD:
            trend = "sideways"
            reason_parts.append(f"ADX={adx:.1f}<25 无明显趋势")
        else:
            if ma_fast > ma_slow and price > ma_fast:
                trend = "up"
                reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) > MA{self.MA_SLOW}({ma_slow:.2f})")
                reason_parts.append(f"价格({price:.2f}) > MA{self.MA_FAST}")
                reason_parts.append(f"ADX={adx:.1f}≥25 确认趋势")
            elif ma_fast < ma_slow and price < ma_fast:
                trend = "down"
                reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) < MA{self.MA_SLOW}({ma_slow:.2f})")
                reason_parts.append(f"价格({price:.2f}) < MA{self.MA_FAST}")
                reason_parts.append(f"ADX={adx:.1f}≥25 确认趋势")
            else:
                trend = "sideways"
                if ma_fast > ma_slow:
                    reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) > MA{self.MA_SLOW}({ma_slow:.2f})")
                    reason_parts.append(f"但价格({price:.2f})低于MA{self.MA_FAST}")
                else:
                    reason_parts.append(f"MA{self.MA_FAST}({ma_fast:.2f}) < MA{self.MA_SLOW}({ma_slow:.2f})")
                    reason_parts.append(f"且价格({price:.2f})高于MA{self.MA_FAST}")
                reason_parts.append("信号矛盾，判定震荡")

        return trend, "；".join(reason_parts)

    def _calculate_strength(self, adx: float) -> int:
        """计算趋势强度"""
        if adx >= self.ADX_STRONG_THRESHOLD:
            return min(100, int(adx + 20))
        elif adx >= self.ADX_TREND_THRESHOLD:
            return int(adx + 10)
        else:
            return int(adx)

    # ==================== 共振分析 ====================

    def analyze_resonance(self, symbol: str) -> Dict:
        """
        分析多周期共振

        Args:
            symbol: 交易品种

        Returns:
            共振分析结果
        """
        states = self.tech_store.get_all_trend_states(symbol)

        if not states:
            result = TechResonanceResult(symbol=symbol)
            return result.to_dict()

        # 统计各趋势数量
        up_count = sum(1 for s in states.values() if s.trend == 'up')
        down_count = sum(1 for s in states.values() if s.trend == 'down')
        sideways_count = sum(1 for s in states.values() if s.trend == 'sideways')

        # 计算平均强度
        strengths = [s.strength for s in states.values() if s.trend != 'sideways']
        avg_strength = sum(strengths) / len(strengths) if strengths else 0

        # 判断共振
        total = len(states)
        if up_count >= total * 0.6:
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

        result = TechResonanceResult(
            symbol=symbol,
            resonance=resonance,
            strength=int(avg_strength),
            aligned_count=aligned_count,
            up_count=up_count,
            down_count=down_count,
            sideways_count=sideways_count,
            signal=signal,
            periods={p: s.to_dict() for p, s in states.items()}
        )

        return result.to_dict()

    # ==================== 交易建议 ====================

    def generate_trade_suggestion(self, symbol: str, current_price: float) -> Optional[Dict]:
        """
        基于趋势和转折点生成交易建议

        Args:
            symbol: 交易品种
            current_price: 当前实时价格

        Returns:
            交易建议 或 None
        """
        # 获取共振分析
        resonance = self.analyze_resonance(symbol)

        if resonance['resonance'] == 'none':
            return None

        if resonance['strength'] < 30:
            return None

        trend = resonance['resonance']

        # 从 pivot_store 获取转折点
        pivots = self.pivot_store.get_pivot_objects(symbol)
        if not pivots:
            return None

        # 按时间排序
        recent_pivots = sorted(
            [p.to_dict() for p in pivots],
            key=lambda x: str(x.get('timestamp', '')),
            reverse=True
        )[:10]

        sl = None
        tp = None
        action = None
        reason = ""

        if trend == "up":
            action = "b"
            low_pivots = [p for p in recent_pivots if p.get('direction') == 'low']
            if low_pivots:
                sl = low_pivots[0].get('price')
            if sl and current_price > sl:
                distance = current_price - sl
                tp = current_price + distance * 1.5
                reason = f"多周期向上共振，建议买入，止损参考最近低点 {sl}"
            else:
                return None

        elif trend == "down":
            action = "s"
            high_pivots = [p for p in recent_pivots if p.get('direction') == 'high']
            if high_pivots:
                sl = high_pivots[0].get('price')
            if sl and current_price < sl:
                distance = sl - current_price
                tp = current_price - distance * 1.5
                reason = f"多周期向下共振，建议卖出，止损参考最近高点 {sl}"
            else:
                return None

        if not all([action, sl, tp]):
            return None

        suggestion = TechTradeSuggestion(
            symbol=symbol,
            action=action,
            price=current_price,
            sl=round(sl, 4),
            tp=round(tp, 4),
            reason=reason,
            trend_strength=resonance['strength'],
            resonance_periods=resonance['aligned_count'],
            generated_at=datetime.now().isoformat()
        )

        return suggestion.to_dict()

    # ==================== 查询 ====================

    def get_trend_state(self, symbol: str, period: str = None) -> Dict:
        """获取趋势状态"""
        return self.tech_store.get_trend_state(symbol, period)

    def get_trend_changes(self, symbol: str, count: int = 10) -> List[Dict]:
        """获取趋势转换历史"""
        return self.tech_store.get_trend_changes(symbol, count)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.tech_store.get_status()