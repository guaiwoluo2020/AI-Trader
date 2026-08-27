#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点信号生成器
根据转折点分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime
import time

from ...models import KlineData, TradingSignal
from ...store import PivotStore, KlineStore
from ...services import PivotService
from .signal_rules import build_pivot_breakout_signal, build_pivot_signal
from .pivot_repository import (
    ConfiguredPivotRepository, PERIOD_SECONDS, calculate_pivot_score,
    pivot_config_fingerprint, pivot_timestamp,
)


class PivotSignalGenerator:
    """转折点信号生成器"""

    def __init__(self, pivot_service: PivotService = None,
                 pivot_store: PivotStore = None,
                 kline_store: KlineStore = None, user_id: int = 0,
                 account_id: int = 0, repository=None):
        self.pivot_service = pivot_service
        self.pivot_store = pivot_store or PivotStore()
        self.kline_store = kline_store or KlineStore()
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)
        self.repository = repository

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
            fingerprint = pivot_config_fingerprint(period, params)
            latest_time = (
                raw_klines[-1].get("timestamp") or raw_klines[-1].get("time")
                if raw_klines else None
            )
            cache_key = (
                strategy.strategy_id, config["signal_source_id"], symbol,
                period, str(latest_time), len(raw_klines), fingerprint,
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
                max_age_bars = max(1, int(params.get("max_age_bars", 120)))
                now = int(time.time())
                period_seconds = PERIOD_SECONDS.get(period, 300)
                market_now = pivot_timestamp(latest_time) or now
                pivots = [
                    pivot for pivot in pivots
                    if pivot_timestamp(pivot.timestamp)
                    and market_now - (
                        pivot_timestamp(pivot.timestamp) + strength * period_seconds
                    ) <= max_age_bars * period_seconds
                ]
                repository = self._repository()
                complete_window = len(raw_klines) >= 2 * strength + 1
                if repository is not None and complete_window:
                    repository.replace_scope(
                        self.user_id, self.account_id, strategy.strategy_id,
                        config["signal_source_id"], symbol, period, fingerprint,
                        pivots, strength, max_age_bars,
                        reference_time=market_now,
                    )
                elif repository is not None and not complete_window:
                    pivots = repository.list_active(
                        self.user_id, self.account_id, strategy.strategy_id,
                        config["signal_source_id"], fingerprint, now,
                    )
                self._configured_pivot_cache[cache_key] = pivots
                if len(self._configured_pivot_cache) > 100:
                    self._configured_pivot_cache.pop(
                        next(iter(self._configured_pivot_cache))
                    )
            threshold = max(0.0, float(params.get("proximity_threshold", 0.001)))
            signal_type = params.get("signal_type", "near")
            stop_buffer_ratio = max(
                0.0, float(params.get("stop_buffer_ratio", 0.0005))
            )
            risk_reward_ratio = max(
                1.0, float(params.get("risk_reward_ratio", 2.0))
            )
            candidates = []
            now = int(time.time())
            period_seconds = PERIOD_SECONDS.get(period, 300)
            latest_kline_time = (
                pivot_timestamp(raw_klines[-1].get("timestamp") or raw_klines[-1].get("time"))
                if raw_klines else 0
            )
            market_now = latest_kline_time or now
            half_life = max(1, int(params.get("recency_half_life_bars", 30)))
            max_age = max(1, int(params.get("max_age_bars", 120)))
            candidate_limit = max(1, int(params.get("candidate_limit", 10)))
            min_confirmations = max(
                1, int(params.get("min_confirmation_count", 1))
            )
            min_pivot_score = max(
                0, min(100, int(params.get("min_pivot_score", 0)))
            )
            pivots = sorted(
                pivots, key=lambda item: pivot_timestamp(item.timestamp), reverse=True
            )[:candidate_limit]
            for pivot in pivots:
                pivot_time = pivot_timestamp(pivot.timestamp)
                confirmed_at = pivot_time + strength * period_seconds
                age_bars = max(0.0, (market_now - confirmed_at) / period_seconds)
                if not pivot_time or age_bars > max_age:
                    continue
                confirmation_count = max(
                    1, int(getattr(pivot, "confirmation_count", 1) or 1)
                )
                if confirmation_count < min_confirmations:
                    continue
                pivot_score, recency_score = calculate_pivot_score(
                    age_bars, confirmation_count, half_life
                )
                if pivot_score < min_pivot_score:
                    continue
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
                    candidates.append((
                        pivot_score, distance, pivot, is_breakout,
                        age_bars, confirmation_count, recency_score,
                    ))
            if not candidates:
                continue
            (
                pivot_score, _, pivot, is_breakout,
                age_bars, confirmation_count, recency_score,
            ) = min(candidates, key=lambda item: (-item[0], item[1]))
            source_id = config["signal_source_id"]
            cooldown = max(0, int(params.get("cooldown_seconds", self.cooldown)))
            key = f"{strategy.strategy_id}_{source_id}_{symbol}_{period}_{pivot.price}"
            last_time = self._signal_cooldowns.get(key)
            if last_time and (datetime.now() - last_time).total_seconds() < cooldown:
                continue
            if is_breakout:
                confidence = min(
                    95, 60 + min(20, (confirmation_count - 1) * 7)
                    + round(15 * recency_score)
                )
                signal = build_pivot_breakout_signal(
                    symbol, current_price, period, pivot.price, pivot.direction,
                    stop_buffer_ratio=stop_buffer_ratio,
                    risk_reward_ratio=risk_reward_ratio,
                    confidence=confidence,
                    confirmation_count=confirmation_count,
                    age_bars=age_bars,
                    pivot_score=pivot_score,
                )
            else:
                confidence = min(
                    95, 55 + min(20, (confirmation_count - 1) * 7)
                    + round(15 * recency_score)
                )
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
                    stop_buffer_ratio=stop_buffer_ratio,
                    risk_reward_ratio=risk_reward_ratio,
                    confidence=confidence,
                    confirmation_count=confirmation_count,
                    age_bars=age_bars,
                    pivot_score=pivot_score,
                )
            if signal:
                self._signal_cooldowns[key] = datetime.now()
                signal.signal_source_id = source_id
                signals.append(signal)
        return signals

    def _repository(self):
        if not self.user_id or not self.account_id:
            return self.repository
        if self.repository is None:
            self.repository = ConfiguredPivotRepository()
        return self.repository

    @staticmethod
    def _merge_pivots(pivots, merge_distance: float):
        """按实例配置合并相近的同方向转折点。"""
        merged = []
        for direction in ("high", "low"):
            items = sorted(
                (item for item in pivots if item.direction == direction),
                key=lambda item: str(item.timestamp),
            )
            groups = []
            for item in items:
                matching = next((
                    group for group in groups
                    if group[0].price > 0
                    and abs(item.price - group[0].price) / group[0].price
                    <= merge_distance
                ), None)
                if matching is None:
                    groups.append([item])
                else:
                    matching.append(item)
            for group in groups:
                merged.append(
                    max(group, key=lambda item: item.price)
                    if direction == "high"
                    else min(group, key=lambda item: item.price)
                )
                merged[-1].confirmation_count = sum(
                    max(1, int(getattr(item, "confirmation_count", 1) or 1))
                    for item in group
                )
        return sorted(merged, key=lambda item: str(item.timestamp))

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用，用于注册到SignalService"""
        return self.generate_signals(symbol, current_price)
