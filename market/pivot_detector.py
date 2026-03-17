#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点检测模块
识别K线的高点和低点（分型识别）
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import threading

from .store import KlineData


class PivotPoint:
    """转折点数据结构"""

    def __init__(self, symbol: str, period: str, timestamp, price: float,
                 direction: str, strength: int = 3):
        self.symbol = symbol
        self.period = period
        self.timestamp = timestamp
        self.price = price
        self.direction = direction  # "high" 或 "low"
        self.strength = strength    # 转折强度（左右各N根K线）

    def to_dict(self) -> Dict:
        """转换为字典"""
        ts = self.timestamp
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = str(ts)

        return {
            "symbol": self.symbol,
            "period": self.period,
            "timestamp": ts_str,
            "price": self.price,
            "direction": self.direction,
            "strength": self.strength
        }


class PivotDetector:
    """转折点检测器"""

    # 各周期接近阈值（千分比）
    THRESHOLDS = {
        'H4': 0.0015,   # 千分之1.5
        'H1': 0.0015,   # 千分之1.5
        'M15': 0.0015,  # 千分之1.5
        'M5': 0.0005,   # 千分之0.5
        'M1': 0.0002    # 千分之0.2
    }

    # 各周期转折强度（左右各N根K线）
    # M1: 6根K线, M5: 4根K线, M15/H1/H4: 3根K线
    PERIOD_STRENGTH = {
        'M1': 6,
        'M5': 4,
        'M15': 3,
        'H1': 3,
        'H4': 3
    }

    def __init__(self):
        # 存储转折点: {SYMBOL: {PERIOD: [PivotPoint, ...]}}
        # 这是合并后的转折点，用于价格接近检测
        self._pivots = defaultdict(lambda: defaultdict(list))

        # 转折点时间线: {SYMBOL: {PERIOD: [PivotPoint, ...]}}
        # 这是合并前的原始转折点，按时间排序，用于判断趋势方向
        self._pivots_timeline = defaultdict(lambda: defaultdict(list))

        self._lock = threading.RLock()

        # 默认转折强度（左右各N根K线）- 仅作为后备值
        self.default_strength = 3

        print("[PivotDetector] 转折点检测器已初始化")
        print(f"[PivotDetector] 周期强度配置: {self.PERIOD_STRENGTH}")

    def detect_pivots(self, symbol: str, period: str, klines: List[KlineData],
                      strength: int = None) -> List[PivotPoint]:
        """
        检测转折点

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据列表
            strength: 转折强度（左右各N根K线），None则使用周期默认值

        Returns:
            检测到的转折点列表
        """
        # 优先使用传入的strength，否则使用周期配置的strength
        if strength is None:
            strength = self.PERIOD_STRENGTH.get(period, self.default_strength)

        if len(klines) < 2 * strength + 1:
            return []

        pivots = []

        # 遍历K线，检测分型
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

    def _merge_pivots(self, pivots: List[PivotPoint], klines: List[KlineData]) -> List[PivotPoint]:
        """
        合并相近的转折点

        合并规则：
        - 相邻两个同方向转折点
        - 价格相差在万分之四范围内
        - 高点合并：取较高的价格
        - 低点合并：取较低的价格

        Args:
            pivots: 原始转折点列表
            klines: K线数据（用于计算K线索引）

        Returns:
            合并后的转折点列表
        """
        if len(pivots) < 2:
            return pivots

        # 分开处理高点和低点
        high_pivots = [p for p in pivots if p.direction == "high"]
        low_pivots = [p for p in pivots if p.direction == "low"]

        # 合并高点
        merged_highs = self._merge_same_direction(high_pivots, "high")

        # 合并低点
        merged_lows = self._merge_same_direction(low_pivots, "low")

        # 合并结果
        result = merged_highs + merged_lows
        return result

    def _merge_same_direction(self, pivots: List[PivotPoint], direction: str) -> List[PivotPoint]:
        """
        合并同方向的转折点

        合并规则：相邻两个转折点价格差距小于万分之四时合并
        """
        if len(pivots) < 2:
            return pivots

        # 按时间排序
        pivots = sorted(pivots, key=lambda p: str(p.timestamp))

        merged = []
        i = 0

        while i < len(pivots):
            current = pivots[i]

            # 查找需要合并的转折点
            group = [current]

            j = i + 1
            while j < len(pivots):
                next_pivot = pivots[j]

                # 检查价格差距（万分之四）
                if current.price > 0:
                    price_diff_pct = abs(next_pivot.price - current.price) / current.price
                    if price_diff_pct <= 0.0004:  # 万分之四
                        group.append(next_pivot)
                        j += 1
                        continue

                break

            # 从组中选择代表性转折点
            if direction == "high":
                # 高点：取价格最高的
                best = max(group, key=lambda p: p.price)
            else:
                # 低点：取价格最低的
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
            strength: 转折强度，None则使用周期默认值

        Returns:
            更新后的转折点数量
        """
        # 使用周期配置的strength
        if strength is None:
            strength = self.PERIOD_STRENGTH.get(period, self.default_strength)

        pivots = self.detect_pivots(symbol, period, klines, strength)

        with self._lock:
            # 保存原始转折点到时间线（按时间排序，用于判断趋势）
            # 高点和低点混合在一起，按时间戳排序
            timeline = sorted(pivots, key=lambda p: self._normalize_timestamp(p.timestamp))
            self._pivots_timeline[symbol][period] = timeline

            # 合并相近的转折点（用于价格接近检测）
            merged_pivots = self._merge_pivots(pivots, klines)
            self._pivots[symbol][period] = merged_pivots
            count = len(merged_pivots)

        original_count = len(pivots)
        timeline_count = len(timeline)
        if original_count != count:
            print(f"[PivotDetector] {symbol} {period} 检测到 {original_count} 个转折点，时间线 {timeline_count} 个，合并后 {count} 个")
        else:
            print(f"[PivotDetector] {symbol} {period} 检测到 {count} 个转折点")
        return count

    def _normalize_timestamp(self, ts) -> str:
        """标准化时间戳为字符串，用于排序比较"""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)

    def get_pivots(self, symbol: str, period: str, direction: str = None,
                   count: int = 50) -> List[Dict]:
        """
        获取转折点数据

        Args:
            symbol: 交易品种
            period: 周期
            direction: "high" 或 "low"，None表示全部
            count: 返回数量

        Returns:
            转折点列表
        """
        with self._lock:
            pivots = self._pivots[symbol][period]

            if direction:
                pivots = [p for p in pivots if p.direction == direction]

            # 按时间排序，返回最新的
            pivots = sorted(pivots, key=lambda x: str(x.timestamp), reverse=True)[:count]

            return [p.to_dict() for p in pivots]

    def get_recent_pivots(self, symbol: str, period: str, count: int = 10) -> List[Dict]:
        """获取最近的转折点（按时间倒序）"""
        with self._lock:
            pivots = self._pivots[symbol][period]
            pivots = sorted(pivots, key=lambda x: str(x.timestamp), reverse=True)[:count]
            return [p.to_dict() for p in pivots]

    def check_near_pivot(self, symbol: str, current_price: float,
                         trend_filter: Dict[str, str] = None) -> List[Dict]:
        """
        检查当前价格是否接近某个转折点

        Args:
            symbol: 交易品种
            current_price: 当前价格
            trend_filter: 趋势过滤，格式 {period: "up"/"down"}
                - "up": 趋势向上，只检查高点
                - "down": 趋势向下，只检查低点
                - 不提供或"unknown": 检查所有

        Returns:
            接近的转折点列表，包含距离信息

        预警逻辑：
        - 接近高点：当前价格 < 高点价格 且 距离在阈值范围内
        - 接近低点：当前价格 > 低点价格 且 距离在阈值范围内
        """
        near_pivots = []

        with self._lock:
            for period in self._pivots[symbol]:
                pivots = self._pivots[symbol][period]
                threshold = self.THRESHOLDS.get(period, 0.001)

                # 获取该周期的趋势方向
                trend = trend_filter.get(period) if trend_filter else None

                for pivot in pivots:
                    if pivot.price == 0 or current_price == 0:
                        continue

                    # 根据趋势过滤
                    if trend == 'up' and pivot.direction != 'high':
                        # 趋势向上，只检查高点
                        continue
                    elif trend == 'down' and pivot.direction != 'low':
                        # 趋势向下，只检查低点
                        continue

                    is_near = False
                    alert_type = ""

                    if pivot.direction == "high":
                        # 高点转折：当前价格低于高点
                        if current_price < pivot.price:
                            distance_pct = (pivot.price - current_price) / current_price
                            if distance_pct <= threshold:
                                is_near = True
                                alert_type = "near_high"

                    elif pivot.direction == "low":
                        # 低点转折：当前价格高于低点
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
                            "trend": trend  # 记录趋势方向
                        })

        # 按距离排序，最近的优先
        near_pivots.sort(key=lambda x: x['distance_pct'])

        return near_pivots

    def get_threshold(self, period: str) -> float:
        """获取某个周期的接近阈值"""
        return self.THRESHOLDS.get(period, 0.001)

    def get_trend_direction(self, symbol: str, period: str = None) -> Dict[str, str]:
        """
        根据最近的转折点判断趋势方向

        原理：
        - 最近是高点 → 价格刚从高点下来 → 趋势向下 → 应检查低点
        - 最近是低点 → 价格刚从低点上去 → 趋势向上 → 应检查高点

        Args:
            symbol: 交易品种
            period: 指定周期，如果为None则判断所有周期

        Returns:
            {period: "up"/"down"/"unknown"}
            - up: 趋势向上，应检查高点
            - down: 趋势向下，应检查低点
        """
        result = {}

        periods_to_check = [period] if period else list(self._pivots_timeline[symbol].keys())

        with self._lock:
            for p in periods_to_check:
                timeline = self._pivots_timeline[symbol][p]

                if not timeline:
                    result[p] = 'unknown'
                    continue

                # 时间线已按时间排序，最后一个就是最近的转折点
                latest_pivot = timeline[-1]

                if latest_pivot.direction == 'high':
                    # 最近是高点，价格往下走，趋势向下
                    result[p] = 'down'
                else:
                    # 最近是低点，价格往上走，趋势向上
                    result[p] = 'up'

        return result

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的转折点数据"""
        with self._lock:
            if symbol in self._pivots:
                del self._pivots[symbol]
            if symbol in self._pivots_timeline:
                del self._pivots_timeline[symbol]

    def get_status(self) -> Dict:
        """获取状态"""
        with self._lock:
            status = {}
            for symbol in self._pivots:
                status[symbol] = {}
                for period in self._pivots[symbol]:
                    count = len(self._pivots[symbol][period])
                    strength = self.PERIOD_STRENGTH.get(period, self.default_strength)
                    status[symbol][period] = {
                        "pivot_count": count,
                        "strength": strength
                    }
            return status

    def get_strength(self, period: str) -> int:
        """获取某个周期的转折强度"""
        return self.PERIOD_STRENGTH.get(period, self.default_strength)