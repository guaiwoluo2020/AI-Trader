#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点信号生成器
根据转折点分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal
from ...store import PivotStore, KlineStore
from ...services import PivotService
from .signal_rules import build_pivot_signal


class PivotSignalGenerator:
    """转折点信号生成器"""

    def __init__(self, pivot_service: PivotService = None,
                 pivot_store: PivotStore = None,
                 kline_store: KlineStore = None):
        self.pivot_service = pivot_service
        self.pivot_store = pivot_store or PivotStore()
        self.kline_store = kline_store or KlineStore()

        # 信号冷却时间（秒）
        self.cooldown = 180

        # 已生成的信号冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[PivotSignalGenerator] 转折点信号生成器已初始化")

    def set_pivot_service(self, service: PivotService) -> None:
        """设置转折点服务"""
        self.pivot_service = service

    def _check_cooldown(self, symbol: str, period: str, pivot_price: float) -> bool:
        """检查是否在冷却期"""
        key = f"{symbol}_{period}_{pivot_price}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(self, symbol: str, period: str, pivot_price: float) -> None:
        """设置冷却"""
        key = f"{symbol}_{period}_{pivot_price}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float,
                        period: str = "M1") -> Optional[TradingSignal]:
        """
        生成转折点信号

        Args:
            symbol: 品种
            current_price: 当前价格
            period: 检测周期

        Returns:
            TradingSignal 或 None
        """
        if not self.pivot_service:
            return None

        # 检查是否接近转折点
        near_pivots = self.pivot_service.check_near_pivot(symbol, current_price)

        for pivot in near_pivots:
            # 只处理指定周期
            if pivot.get('period') != period:
                continue

            # 只处理接近类型（不是突破）
            alert_type = pivot.get('alert_type', '')
            if not alert_type.startswith('near_'):
                continue

            pivot_price = pivot.get('price', 0)
            pivot_type = 'low' if 'low' in alert_type else 'high'

            # 检查冷却
            if self._check_cooldown(symbol, period, pivot_price):
                continue

            opposite_type = "high" if pivot_type == "low" else "low"
            signal = build_pivot_signal(
                symbol,
                current_price,
                period,
                pivot_price,
                pivot_type,
                self.pivot_service.find_nearest_pivot_price(
                    symbol, opposite_type, current_price
                ),
            )
            if signal:
                self._set_cooldown(symbol, period, pivot_price)
                print(
                    f"[PivotSignalGenerator] 生成信号: {signal.signal_id} "
                    f"{signal.action} @ {current_price}, "
                    f"SL={signal.suggested_sl:.2f}, TP={signal.suggested_tp:.2f}"
                )
                return signal

        return None

    def generate_signals(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """生成所有周期的信号"""
        signals = []
        for period in ['M1', 'M5']:
            signal = self.generate_signal(symbol, current_price, period)
            if signal:
                signals.append(signal)
        return signals

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用，用于注册到SignalService"""
        return self.generate_signals(symbol, current_price)
