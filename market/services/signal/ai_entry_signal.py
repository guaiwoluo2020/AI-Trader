#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI入场信号生成器
根据AI分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal
from .signal_rules import build_ai_entry_signal


class AIEntrySignalGenerator:
    """AI入场信号生成器"""

    def __init__(self):
        # LLM分析器引用
        self._llm_analyzer = None

        # 阈值（价格距离AI入场价的百分比）
        self.threshold = 0.0001  # 万分之一

        # 信号冷却时间（秒）
        self.cooldown = 300  # 5分钟

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[AIEntrySignalGenerator] AI入场信号生成器已初始化")

    def set_llm_analyzer(self, analyzer) -> None:
        """设置LLM分析器"""
        self._llm_analyzer = analyzer

    def _check_cooldown(self, symbol: str, period: str, entry_price: float, direction: str) -> bool:
        """检查是否在冷却期"""
        key = f"{symbol}_{period}_{entry_price}_{direction}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(self, symbol: str, period: str, entry_price: float, direction: str) -> None:
        """设置冷却"""
        key = f"{symbol}_{period}_{entry_price}_{direction}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """
        生成AI入场信号

        Args:
            symbol: 品种
            current_price: 当前价格

        Returns:
            TradingSignal 或 None
        """
        signals = self.generate_signals(symbol, current_price)
        return signals[0] if signals else None

    def generate_signals(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """生成所有匹配的信号"""
        if not self._llm_analyzer:
            return []

        signals = []
        matches = self._llm_analyzer.check_entry_price_nearby(
            symbol, current_price, threshold=self.threshold
        )

        for match in matches:
            period = match.get('period', '')
            entry_price = match.get('entry_price', 0)
            direction = match.get('direction', 'buy')

            # 检查冷却
            if self._check_cooldown(symbol, period, entry_price, direction):
                continue

            signal = build_ai_entry_signal(
                symbol, current_price, match, threshold=self.threshold
            )
            if signal:
                self._set_cooldown(symbol, period, entry_price, direction)
                signals.append(signal)

        return signals

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用"""
        return self.generate_signals(symbol, current_price)
