#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键点位信号生成器
根据关键点位分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal
from .signal_rules import automatic_key_levels, build_key_level_signal


class KeyLevelSignalGenerator:
    """关键点位信号生成器"""

    def __init__(self):
        # 关键点位配置
        self._key_levels: Dict[str, List[float]] = {}

        # 阈值（价格距离关键点位的百分比）
        self.threshold = 0.0008  # 万分之八

        # 信号冷却时间（秒）
        self.cooldown = 180

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[KeyLevelSignalGenerator] 关键点位信号生成器已初始化")

    def set_key_levels(self, symbol: str, levels: List[float]) -> None:
        """设置品种的关键点位"""
        self._key_levels[symbol] = sorted(levels)

    def get_key_levels(self, symbol: str, current_price: float) -> List[float]:
        """获取关键点位（如果没有配置则自动计算）"""
        if symbol in self._key_levels:
            return self._key_levels[symbol]

        # 自动计算关键点位
        return self._auto_calculate_key_levels(current_price)

    def _auto_calculate_key_levels(self, current_price: float) -> List[float]:
        """自动计算关键点位"""
        return automatic_key_levels(current_price)

    def _check_cooldown(self, symbol: str, key_level: float) -> bool:
        """检查是否在冷却期"""
        key = f"{symbol}_{key_level}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(self, symbol: str, key_level: float) -> None:
        """设置冷却"""
        key = f"{symbol}_{key_level}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """
        生成关键点位信号

        策略逻辑：
        - 价格在关键点位上方，向下接近 → 买入（支撑位）
        - 价格在关键点位下方，向上接近 → 卖出（压力位）
        """
        signal = build_key_level_signal(
            symbol, current_price, self.get_key_levels(symbol, current_price),
            threshold=self.threshold,
        )
        if signal is None or self._check_cooldown(symbol, signal.key_level):
            return None
        self._set_cooldown(symbol, signal.key_level)
        print(
            f"[KeyLevelSignalGenerator] 生成信号: {signal.signal_id} "
            f"{signal.action} @ {current_price}, 关键位={signal.key_level}"
        )
        return signal

    def __call__(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """使对象可调用"""
        signal = self.generate_signal(symbol, current_price)
        return signal if signal else None
