#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点存储模块
存储和管理转折点数据
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional
import threading

from ..models import PivotPoint


class PivotStore:
    """转折点存储（只负责数据CRUD）"""

    def __init__(self):
        # 存储转折点: {SYMBOL: {PERIOD: [PivotPoint, ...]}}
        self._pivots = defaultdict(lambda: defaultdict(list))

        # 转折点时间线: {SYMBOL: {PERIOD: [PivotPoint, ...]}}
        self._pivots_timeline = defaultdict(lambda: defaultdict(list))

        self._lock = threading.RLock()

        print("[PivotStore] 转折点存储已初始化")

    def save_pivots(self, symbol: str, period: str, pivots: List[PivotPoint],
                    timeline: List[PivotPoint] = None):
        """
        保存转折点数据

        Args:
            symbol: 交易品种
            period: 周期
            pivots: 合并后的转折点列表
            timeline: 时间线转折点列表（可选）
        """
        with self._lock:
            self._pivots[symbol][period] = list(pivots)
            if timeline is not None:
                self._pivots_timeline[symbol][period] = list(timeline)
            else:
                self._pivots_timeline[symbol][period] = list(pivots)

    def get_pivots(self, symbol: str, period: str, direction: str = None,
                   count: int = 50) -> List[Dict]:
        """获取转折点数据"""
        with self._lock:
            pivots = list(self._pivots[symbol][period])

            if direction:
                pivots = [p for p in pivots if p.direction == direction]

            pivots = sorted(pivots, key=lambda x: str(x.timestamp), reverse=True)[:count]
            return [p.to_dict() for p in pivots]

    def get_pivot_objects(self, symbol: str, period: str = None) -> List[PivotPoint]:
        """获取转折点对象（用于内部计算）"""
        with self._lock:
            if period:
                return list(self._pivots[symbol][period])
            else:
                # 返回所有周期
                result = []
                for p in self._pivots[symbol]:
                    result.extend(self._pivots[symbol][p])
                return result

    def get_timeline(self, symbol: str, period: str) -> List[PivotPoint]:
        """获取时间线转折点（用于判断趋势）"""
        with self._lock:
            return list(self._pivots_timeline[symbol][period])

    def get_all_periods(self, symbol: str) -> List[str]:
        """获取有转折点数据的所有周期"""
        with self._lock:
            return list(self._pivots[symbol].keys())

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
                    status[symbol][period] = {"pivot_count": count}
            return status

    def find_nearest_pivot_price(self, symbol: str, direction: str,
                                  current_price: float) -> Optional[float]:
        """
        找到离当前价格最近的转折点价格

        Args:
            symbol: 交易品种
            direction: 'high' 或 'low'
            current_price: 当前价格

        Returns:
            最近的转折点价格，如果没有返回None
        """
        nearest_price = None
        min_distance = float('inf')
        total_pivots = 0
        filtered_pivots = 0

        with self._lock:
            for period in self._pivots[symbol]:
                pivots = self._pivots[symbol][period]
                total_pivots += len(pivots)

                for pivot in pivots:
                    if pivot.direction != direction:
                        continue

                    if direction == 'high' and pivot.price <= current_price:
                        filtered_pivots += 1
                        continue
                    if direction == 'low' and pivot.price >= current_price:
                        filtered_pivots += 1
                        continue

                    distance = abs(pivot.price - current_price)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_price = pivot.price

        if nearest_price is None and total_pivots > 0:
            print(f"[PivotStore] find_nearest_pivot_price: 未找到 {direction} 转折点, "
                  f"current_price={current_price:.2f}, "
                  f"total_pivots={total_pivots}, filtered={filtered_pivots}")

        return nearest_price