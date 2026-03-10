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

from .store import KlineData, normalize_symbol


class PivotPoint:
    """转折点数据结构"""

    def __init__(self, symbol: str, period: str, timestamp, price: float,
                 direction: str, strength: int = 3):
        self.symbol = normalize_symbol(symbol)
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

    def __init__(self):
        # 存储转折点: {SYMBOL: {PERIOD: [PivotPoint, ...]}}
        self._pivots = defaultdict(lambda: defaultdict(list))
        self._lock = threading.RLock()

        # 默认转折强度（左右各N根K线）
        self.default_strength = 3

        print("[PivotDetector] 转折点检测器已初始化")

    def detect_pivots(self, symbol: str, period: str, klines: List[KlineData],
                      strength: int = None) -> List[PivotPoint]:
        """
        检测转折点

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据列表
            strength: 转折强度（左右各N根K线）

        Returns:
            检测到的转折点列表
        """
        if strength is None:
            strength = self.default_strength

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
        - K线距离小于26根
        - 价格相差在万分之三范围内
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

        # 建立K线时间戳到索引的映射
        kline_index = {str(k.timestamp): i for i, k in enumerate(klines)}

        # 按时间排序
        pivots = sorted(pivots, key=lambda p: str(p.timestamp))

        # 分开处理高点和低点
        high_pivots = [p for p in pivots if p.direction == "high"]
        low_pivots = [p for p in pivots if p.direction == "low"]

        # 合并高点
        merged_highs = self._merge_same_direction(
            high_pivots, kline_index, "high"
        )

        # 合并低点
        merged_lows = self._merge_same_direction(
            low_pivots, kline_index, "low"
        )

        # 合并结果
        result = merged_highs + merged_lows
        return result

    def _merge_same_direction(self, pivots: List[PivotPoint],
                               kline_index: Dict[str, int],
                               direction: str) -> List[PivotPoint]:
        """
        合并同方向的转折点
        """
        if len(pivots) < 2:
            return pivots

        merged = []
        i = 0

        while i < len(pivots):
            current = pivots[i]
            current_idx = kline_index.get(str(current.timestamp), -1)

            if current_idx < 0:
                i += 1
                continue

            # 查找需要合并的转折点
            group = [current]

            j = i + 1
            while j < len(pivots):
                next_pivot = pivots[j]
                next_idx = kline_index.get(str(next_pivot.timestamp), -1)

                if next_idx < 0:
                    j += 1
                    continue

                # 检查K线距离
                kline_distance = abs(next_idx - current_idx)

                if kline_distance >= 26:
                    break

                # 检查价格差距（万分之三）
                if current.price > 0:
                    price_diff_pct = abs(next_pivot.price - current.price) / current.price
                    if price_diff_pct <= 0.0003:  # 万分之三
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

        Returns:
            更新后的转折点数量
        """
        symbol = normalize_symbol(symbol)

        pivots = self.detect_pivots(symbol, period, klines, strength)

        # 合并相近的转折点
        merged_pivots = self._merge_pivots(pivots, klines)

        with self._lock:
            self._pivots[symbol][period] = merged_pivots
            count = len(merged_pivots)

        original_count = len(pivots)
        if original_count != count:
            print(f"[PivotDetector] {symbol} {period} 检测到 {original_count} 个转折点，合并后 {count} 个")
        else:
            print(f"[PivotDetector] {symbol} {period} 检测到 {count} 个转折点")
        return count

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
        symbol = normalize_symbol(symbol)

        with self._lock:
            pivots = self._pivots[symbol][period]

            if direction:
                pivots = [p for p in pivots if p.direction == direction]

            # 按时间排序，返回最新的
            pivots = sorted(pivots, key=lambda x: str(x.timestamp), reverse=True)[:count]

            return [p.to_dict() for p in pivots]

    def get_recent_pivots(self, symbol: str, period: str, count: int = 10) -> List[Dict]:
        """获取最近的转折点（按时间倒序）"""
        symbol = normalize_symbol(symbol)

        with self._lock:
            pivots = self._pivots[symbol][period]
            pivots = sorted(pivots, key=lambda x: str(x.timestamp), reverse=True)[:count]
            return [p.to_dict() for p in pivots]

    def check_near_pivot(self, symbol: str, current_price: float) -> List[Dict]:
        """
        检查当前价格是否接近某个转折点

        Args:
            symbol: 交易品种
            current_price: 当前价格

        Returns:
            接近的转折点列表，包含距离信息

        预警逻辑：
        - 接近高点：当前价格 < 高点价格 且 距离在阈值范围内
        - 接近低点：当前价格 > 低点价格 且 距离在阈值范围内
        - 突破高点：当前价格超过高点价格的万分之一点二（基于实时价格）
        - 突破低点：当前价格低于低点价格的万分之一点二（基于实时价格）
        - 超过千分之一不再提示
        """
        symbol = normalize_symbol(symbol)
        near_pivots = []

        # 突破阈值：万分之一点二
        BREAKTHROUGH_THRESHOLD = 0.00012
        # 最大提示范围：千分之一
        MAX_ALERT_THRESHOLD = 0.001

        with self._lock:
            for period in self._pivots[symbol]:
                pivots = self._pivots[symbol][period]
                threshold = self.THRESHOLDS.get(period, 0.001)

                for pivot in pivots:
                    if pivot.price == 0 or current_price == 0:
                        continue

                    # 基于实时价格计算阈值
                    breakthrough_value = current_price * BREAKTHROUGH_THRESHOLD  # 万分之一点二
                    max_alert_value = current_price * MAX_ALERT_THRESHOLD  # 千分之一

                    # 判断是接近还是突破
                    is_near = False
                    is_breakthrough = False
                    alert_type = ""

                    if pivot.direction == "high":
                        # 高点转折
                        if current_price > pivot.price:
                            # 当前价格高于高点，判断是否突破
                            # 突破：超过高点的距离在万分之一点二到千分之一之间
                            distance = current_price - pivot.price
                            if distance >= breakthrough_value and distance < max_alert_value:
                                is_breakthrough = True
                                alert_type = "breakthrough_high"
                            # 超过千分之一不再提示
                        else:
                            # 当前价格低于高点
                            distance_pct = (pivot.price - current_price) / current_price
                            if distance_pct <= threshold:
                                is_near = True
                                alert_type = "near_high"

                    elif pivot.direction == "low":
                        # 低点转折
                        if current_price < pivot.price:
                            # 当前价格低于低点，判断是否突破
                            # 突破：低于低点的距离在万分之一点二到千分之一之间
                            distance = pivot.price - current_price
                            if distance >= breakthrough_value and distance < max_alert_value:
                                is_breakthrough = True
                                alert_type = "breakthrough_low"
                            # 超过千分之一不再提示
                        else:
                            # 当前价格高于低点
                            distance_pct = (current_price - pivot.price) / current_price
                            if distance_pct <= threshold:
                                is_near = True
                                alert_type = "near_low"

                    if is_near or is_breakthrough:
                        distance_pct = abs(current_price - pivot.price) / current_price
                        near_pivots.append({
                            **pivot.to_dict(),
                            "current_price": current_price,
                            "distance_pct": round(distance_pct * 100, 4),
                            "threshold_pct": round(threshold * 100, 4),
                            "distance": round(current_price - pivot.price, 2),
                            "alert_type": alert_type,
                            "is_breakthrough": is_breakthrough
                        })

        # 按距离排序，最近的优先
        near_pivots.sort(key=lambda x: x['distance_pct'])

        return near_pivots

    def get_threshold(self, period: str) -> float:
        """获取某个周期的接近阈值"""
        return self.THRESHOLDS.get(period, 0.001)

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的转折点数据"""
        symbol = normalize_symbol(symbol)
        with self._lock:
            if symbol in self._pivots:
                del self._pivots[symbol]

    def get_status(self) -> Dict:
        """获取状态"""
        with self._lock:
            status = {}
            for symbol in self._pivots:
                status[symbol] = {}
                for period in self._pivots[symbol]:
                    count = len(self._pivots[symbol][period])
                    status[symbol][period] = {"pivot_count": count}
            return status