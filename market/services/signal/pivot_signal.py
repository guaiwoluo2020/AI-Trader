#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点信号生成器
根据转折点分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import KlineData, TradingSignal
from ...store import PivotStore, KlineStore
from ...services import PivotService
from .signal_rules import build_pivot_breakout_signal, build_pivot_signal


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
        self._configured_pivot_cache: Dict[tuple, List] = {}

        print("[PivotSignalGenerator] 转折点信号生成器已初始化")

    def set_pivot_service(self, service: PivotService) -> None:
        """设置转折点服务"""
        self.pivot_service = service

    def _check_cooldown(
        self, symbol: str, period: str, pivot_price: float, strategy_id: str = "",
    ) -> bool:
        """检查是否在冷却期"""
        key = f"{strategy_id}_{symbol}_{period}_{pivot_price}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(
        self, symbol: str, period: str, pivot_price: float, strategy_id: str = "",
    ) -> None:
        """设置冷却"""
        key = f"{strategy_id}_{symbol}_{period}_{pivot_price}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float,
                        period: str = "M1",
                        strategy_id: str = "") -> Optional[TradingSignal]:
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
            if self._check_cooldown(symbol, period, pivot_price, strategy_id):
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
                self._set_cooldown(symbol, period, pivot_price, strategy_id)
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

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        """按每条转折点实例的强度、阈值和冷却独立生成信号。"""
        signals = []
        for config in strategy.get_signal_sources("pivot", enabled_only=True):
            period = config["period"]
            params = config.get("params") or {}
            raw_klines = self.kline_store.get_all_klines(symbol, period)
            strength = max(1, int(params.get("confirmation_strength", 3)))
            merge_distance = float(params.get("merge_distance", 0.0004))
            latest_time = (
                raw_klines[-1].get("timestamp") or raw_klines[-1].get("time")
                if raw_klines else None
            )
            cache_key = (
                symbol, period, str(latest_time), strength, merge_distance
            )
            pivots = self._configured_pivot_cache.get(cache_key)
            if pivots is None:
                klines = [
                    KlineData.from_dict({
                        **item, "symbol": symbol, "period": period
                    })
                    for item in raw_klines
                ]
                pivots = self.pivot_service.detect_pivots(
                    symbol, period, klines, strength
                ) if self.pivot_service else []
                pivots = self._merge_pivots(pivots, merge_distance)
                self._configured_pivot_cache[cache_key] = pivots
                if len(self._configured_pivot_cache) > 100:
                    self._configured_pivot_cache.pop(
                        next(iter(self._configured_pivot_cache))
                    )
            threshold = max(0.0, float(params.get("proximity_threshold", 0.001)))
            signal_type = params.get("signal_type", "near")
            candidates = []
            for pivot in pivots:
                distance = abs(current_price - pivot.price) / current_price
                is_near = (
                    (pivot.direction == "high" and current_price <= pivot.price)
                    or (pivot.direction == "low" and current_price >= pivot.price)
                )
                is_breakout = (
                    (pivot.direction == "high" and current_price > pivot.price)
                    or (pivot.direction == "low" and current_price < pivot.price)
                )
                if distance <= threshold and (
                    (signal_type in {"near", "both"} and is_near)
                    or (signal_type in {"breakout", "both"} and is_breakout)
                ):
                    candidates.append((distance, pivot, is_breakout))
            if not candidates:
                continue
            _, pivot, is_breakout = min(candidates, key=lambda item: item[0])
            source_id = config["signal_source_id"]
            cooldown = max(0, int(params.get("cooldown_seconds", self.cooldown)))
            key = f"{strategy.strategy_id}_{source_id}_{symbol}_{period}_{pivot.price}"
            last_time = self._signal_cooldowns.get(key)
            if last_time and (datetime.now() - last_time).total_seconds() < cooldown:
                continue
            if is_breakout:
                signal = build_pivot_breakout_signal(
                    symbol, current_price, period, pivot.price, pivot.direction
                )
            else:
                opposite = "high" if pivot.direction == "low" else "low"
                opposite_prices = [
                    item.price for item in pivots
                    if item.direction == opposite and (
                        (pivot.direction == "low" and item.price > current_price)
                        or (pivot.direction == "high" and item.price < current_price)
                    )
                ]
                nearest_opposite = min(
                    opposite_prices,
                    key=lambda price: abs(price - current_price),
                    default=None,
                )
                signal = build_pivot_signal(
                    symbol, current_price, period, pivot.price,
                    pivot.direction, nearest_opposite,
                )
            if signal:
                self._signal_cooldowns[key] = datetime.now()
                signal.signal_source_id = source_id
                signals.append(signal)
        return signals

    @staticmethod
    def _merge_pivots(pivots, merge_distance: float):
        """按实例配置合并相近的同方向转折点。"""
        merged = []
        for direction in ("high", "low"):
            items = sorted(
                (item for item in pivots if item.direction == direction),
                key=lambda item: str(item.timestamp),
            )
            index = 0
            while index < len(items):
                group = [items[index]]
                cursor = index + 1
                while cursor < len(items):
                    base = items[index].price
                    if base <= 0 or abs(items[cursor].price - base) / base > merge_distance:
                        break
                    group.append(items[cursor])
                    cursor += 1
                merged.append(
                    max(group, key=lambda item: item.price)
                    if direction == "high"
                    else min(group, key=lambda item: item.price)
                )
                index = cursor
        return merged

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用，用于注册到SignalService"""
        return self.generate_signals(symbol, current_price)
