#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键点位信号生成器
根据关键点位分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal, SignalSource


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
        if current_price <= 0:
            return []

        int_part = int(current_price)
        num_digits = len(str(int_part)) if int_part > 0 else 1

        # 根据位数确定步长
        if num_digits == 1:
            step = 1
        elif num_digits == 2:
            step = 5
        elif num_digits == 3:
            step = 10
        elif num_digits == 4:
            step = 100
        else:
            step = 1000

        # 计算基础点位
        base_level = int(current_price / step) * step

        # 生成上下各3个关键点位
        levels = []
        for i in range(-3, 4):
            level = base_level + i * step
            if level > 0:
                levels.append(float(level))

        return sorted(levels)

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
        key_levels = self.get_key_levels(symbol, current_price)
        if not key_levels:
            return None

        # 找到最近的关键点位
        nearest_level = None
        min_distance_pct = float('inf')

        for level in key_levels:
            distance_pct = abs(current_price - level) / current_price
            if distance_pct < min_distance_pct:
                min_distance_pct = distance_pct
                nearest_level = level

        if nearest_level is None:
            return None

        # 检查是否在阈值范围内
        if min_distance_pct > self.threshold:
            return None

        # 检查冷却
        if self._check_cooldown(symbol, nearest_level):
            return None

        # 设置冷却
        self._set_cooldown(symbol, nearest_level)

        # 确定方向
        if current_price > nearest_level:
            # 价格在关键点位上方 → 支撑位 → 买入
            action = "buy"
            sl = nearest_level - (nearest_level * 0.006)  # 关键点位下方万分之六
            risk = current_price - sl
            tp = current_price + risk * 1.5
            trigger_reason = f"价格向下接近 {nearest_level}（支撑位）"
        else:
            # 价格在关键点位下方 → 压力位 → 卖出
            action = "sell"
            sl = nearest_level + (nearest_level * 0.006)
            risk = sl - current_price
            tp = current_price - risk * 1.5
            trigger_reason = f"价格向上接近 {nearest_level}（压力位）"

        # 计算风险回报比
        reward = abs(tp - current_price)
        rr_ratio = reward / risk if risk > 0 else 0

        print(f"[KeyLevelSignalGenerator] 生成信号: {action} @ {current_price:.2f}, key_level={nearest_level:.2f}, sl={sl:.2f}, tp={tp:.2f}, risk={risk:.2f}, rr={rr_ratio:.2f}")

        # 创建信号
        signal = TradingSignal(
            symbol=symbol,
            action=action,
            confidence=65,  # 基础置信度
            source=SignalSource.KEY_LEVEL,
            source_period="",  # 关键点位不区分周期
            trigger_price=current_price,
            trigger_reason=trigger_reason,
            suggested_entry=current_price,
            suggested_sl=round(sl, 2),
            suggested_tp=round(tp, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            key_level=nearest_level,
            distance_pct=round(min_distance_pct * 100, 4),
        )

        print(f"[KeyLevelSignalGenerator] 生成信号: {signal.signal_id} {action} @ {current_price}, 关键位={nearest_level}")
        return signal

    def __call__(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """使对象可调用"""
        signal = self.generate_signal(symbol, current_price)
        return signal if signal else None