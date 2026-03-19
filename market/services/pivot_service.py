#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点服务模块
处理转折点相关的业务逻辑：检测、合并、接近检测等
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional
import threading

from ..models import KlineData, PivotPoint
from ..store import KlineStore, PivotStore


class PivotService:
    """转折点服务（处理业务逻辑）"""

    # 各周期接近阈值（千分比）
    THRESHOLDS = {
        'H4': 0.0015,   # 千分之1.5
        'H1': 0.0015,   # 千分之1.5
        'M15': 0.0015,  # 千分之1.5
        'M5': 0.0005,   # 千分之0.5
        'M1': 0.0002    # 千分之0.2
    }

    # 各周期转折强度（左右各N根K线）
    PERIOD_STRENGTH = {
        'M1': 6,
        'M5': 4,
        'M15': 3,
        'H1': 3,
        'H4': 3
    }

    def __init__(self, pivot_store: PivotStore, kline_store: KlineStore):
        self.pivot_store = pivot_store
        self.kline_store = kline_store
        self.default_strength = 3

        print("[PivotService] 转折点服务已初始化")
        print(f"[PivotService] 周期强度配置: {self.PERIOD_STRENGTH}")

    def detect_pivots(self, symbol: str, period: str, klines: List[KlineData],
                      strength: int = None) -> List[PivotPoint]:
        """
        检测转折点

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据列表
            strength: 转折强度，None则使用周期默认值

        Returns:
            检测到的转折点列表
        """
        if strength is None:
            strength = self.PERIOD_STRENGTH.get(period, self.default_strength)

        if len(klines) < 2 * strength + 1:
            return []

        pivots = []

        for i in range(strength, len(klines) - strength):
            current = klines[i]

            # 检查是否为高点（顶分型）
            is_high = True
            for j in range(1, strength + 1):
                if klines[i - j].high >= current.high or klines[i + j].high >= current.high:
                    is_high = False
                    break

            if is_high:
                pivot = PivotPoint(
                    symbol=symbol,
                    period=period,
                    timestamp=current.timestamp,
                    price=current.high,
                    direction="high",
                    strength=strength
                )
                pivots.append(pivot)

            # 检查是否为低点（底分型）
            is_low = True
            for j in range(1, strength + 1):
                if klines[i - j].low <= current.low or klines[i + j].low <= current.low:
                    is_low = False
                    break

            if is_low:
                pivot = PivotPoint(
                    symbol=symbol,
                    period=period,
                    timestamp=current.timestamp,
                    price=current.low,
                    direction="low",
                    strength=strength
                )
                pivots.append(pivot)

        return pivots

    def merge_pivots(self, pivots: List[PivotPoint]) -> List[PivotPoint]:
        """
        合并相近的转折点

        合并规则：相邻两个同方向转折点价格差距小于万分之四时合并
        """
        if len(pivots) < 2:
            return pivots

        high_pivots = [p for p in pivots if p.direction == "high"]
        low_pivots = [p for p in pivots if p.direction == "low"]

        merged_highs = self._merge_same_direction(high_pivots, "high")
        merged_lows = self._merge_same_direction(low_pivots, "low")

        return merged_highs + merged_lows

    def _merge_same_direction(self, pivots: List[PivotPoint], direction: str) -> List[PivotPoint]:
        """合并同方向的转折点"""
        if len(pivots) < 2:
            return pivots

        pivots = sorted(pivots, key=lambda p: str(p.timestamp))

        merged = []
        i = 0

        while i < len(pivots):
            current = pivots[i]
            group = [current]

            j = i + 1
            while j < len(pivots):
                next_pivot = pivots[j]

                if current.price > 0:
                    price_diff_pct = abs(next_pivot.price - current.price) / current.price
                    if price_diff_pct <= 0.0004:
                        group.append(next_pivot)
                        j += 1
                        continue

                break

            if direction == "high":
                best = max(group, key=lambda p: p.price)
            else:
                best = min(group, key=lambda p: p.price)

            merged.append(best)
            i = j

        return merged

    def update_pivots(self, symbol: str, period: str, klines: List[KlineData],
                      strength: int = None) -> int:
        """
        更新转折点数据

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据列表
            strength: 转折强度

        Returns:
            更新后的转折点数量
        """
        if strength is None:
            strength = self.PERIOD_STRENGTH.get(period, self.default_strength)

        pivots = self.detect_pivots(symbol, period, klines, strength)

        # 保存原始转折点到时间线
        timeline = sorted(pivots, key=lambda p: self._normalize_timestamp(p.timestamp))

        # 合并相近的转折点
        merged_pivots = self.merge_pivots(pivots)

        # 存储到 pivot_store
        self.pivot_store.save_pivots(symbol, period, merged_pivots, timeline)

        original_count = len(pivots)
        count = len(merged_pivots)

        if original_count != count:
            print(f"[PivotService] {symbol} {period} 检测到 {original_count} 个转折点，合并后 {count} 个")
        else:
            print(f"[PivotService] {symbol} {period} 检测到 {count} 个转折点")

        return count

    def check_near_pivot(self, symbol: str, current_price: float,
                         trend_filter: Dict[str, str] = None) -> List[Dict]:
        """
        检查当前价格是否接近某个转折点

        Args:
            symbol: 交易品种
            current_price: 当前价格
            trend_filter: 趋势过滤

        Returns:
            接近的转折点列表
        """
        near_pivots = []

        periods = self.pivot_store.get_all_periods(symbol)

        for period in periods:
            pivots = self.pivot_store.get_pivot_objects(symbol, period)
            threshold = self.THRESHOLDS.get(period, 0.001)

            trend = trend_filter.get(period) if trend_filter else None

            for pivot in pivots:
                if pivot.price == 0 or current_price == 0:
                    continue

                if trend == 'up' and pivot.direction != 'high':
                    continue
                elif trend == 'down' and pivot.direction != 'low':
                    continue

                is_near = False
                alert_type = ""

                if pivot.direction == "high":
                    if current_price < pivot.price:
                        distance_pct = (pivot.price - current_price) / current_price
                        if distance_pct <= threshold:
                            is_near = True
                            alert_type = "near_high"

                elif pivot.direction == "low":
                    if current_price > pivot.price:
                        distance_pct = (current_price - pivot.price) / current_price
                        if distance_pct <= threshold:
                            is_near = True
                            alert_type = "near_low"

                if is_near:
                    distance_pct = abs(current_price - pivot.price) / current_price
                    near_pivots.append({
                        **pivot.to_dict(),
                        "current_price": current_price,
                        "distance_pct": round(distance_pct * 100, 4),
                        "threshold_pct": round(threshold * 100, 4),
                        "distance": round(current_price - pivot.price, 2),
                        "alert_type": alert_type,
                        "trend": trend
                    })

        near_pivots.sort(key=lambda x: x['distance_pct'])
        return near_pivots

    def get_trend_direction(self, symbol: str, period: str = None) -> Dict[str, str]:
        """
        根据最近的转折点判断趋势方向

        Returns:
            {period: "up"/"down"/"unknown"}
        """
        result = {}

        periods_to_check = [period] if period else self.pivot_store.get_all_periods(symbol)

        for p in periods_to_check:
            timeline = self.pivot_store.get_timeline(symbol, p)

            if not timeline:
                result[p] = 'unknown'
                continue

            latest_pivot = timeline[-1]

            if latest_pivot.direction == 'high':
                result[p] = 'down'
            else:
                result[p] = 'up'

        return result

    def get_pivots(self, symbol: str, period: str, direction: str = None,
                   count: int = 50) -> List[Dict]:
        """获取转折点数据"""
        return self.pivot_store.get_pivots(symbol, period, direction, count)

    def find_nearest_pivot_price(self, symbol: str, direction: str,
                                  current_price: float) -> Optional[float]:
        """找到离当前价格最近的转折点价格"""
        return self.pivot_store.find_nearest_pivot_price(symbol, direction, current_price)

    def get_threshold(self, period: str) -> float:
        """获取某个周期的接近阈值"""
        return self.THRESHOLDS.get(period, 0.001)

    def get_strength(self, period: str) -> int:
        """获取某个周期的转折强度"""
        return self.PERIOD_STRENGTH.get(period, self.default_strength)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.pivot_store.get_status()

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的转折点数据"""
        self.pivot_store.clear_symbol(symbol)

    def _normalize_timestamp(self, ts) -> str:
        """标准化时间戳"""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)