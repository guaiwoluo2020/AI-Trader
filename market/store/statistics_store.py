#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计数据存储模块
"""

from collections import deque, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import threading

from ..models.statistics import StatisticsData


class StatisticsStore:
    """
    统计数据存储

    保留最近的数据用于：
    1. 获取品种价差
    2. 获取账户信息
    """

    def __init__(self, max_per_symbol: int = 10, max_total: int = 100):
        """
        Args:
            max_per_symbol: 每个品种保留的最大记录数
            max_total: 总共保留的最大记录数
        """
        # 按品种分组存储
        self._by_symbol: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_per_symbol))

        # 全局存储（用于获取最新账户信息）
        self._all_data: deque = deque(maxlen=max_total)

        # 线程锁
        self._lock = threading.RLock()

        print(f"[StatisticsStore] 统计数据存储已初始化 (max_per_symbol={max_per_symbol}, max_total={max_total})")

    def add(self, data: StatisticsData) -> None:
        """添加统计数据"""
        with self._lock:
            self._by_symbol[data.symbol].append(data)
            self._all_data.append(data)

    def get_latest(self, symbol: str = None) -> Optional[StatisticsData]:
        """获取最新的统计数据"""
        with self._lock:
            if symbol:
                if self._by_symbol[symbol]:
                    return self._by_symbol[symbol][-1]
                return None
            else:
                if self._all_data:
                    return self._all_data[-1]
                return None

    def get_by_symbol(self, symbol: str, count: int = 10) -> List[StatisticsData]:
        """获取指定品种的统计数据"""
        with self._lock:
            data = list(self._by_symbol[symbol])[-count:]
            return data

    def get_spread(self, symbol: str) -> Optional[float]:
        """获取品种价差"""
        with self._lock:
            # 规范化品种名称（去掉#后缀）
            symbol_normalized = symbol.replace('#', '')
            for stat in reversed(list(self._all_data)):
                stat_normalized = stat.symbol.replace('#', '')
                if stat_normalized == symbol_normalized:
                    if stat.spread > 0:
                        return stat.spread
            return None

    def get_account_info(self, symbol: str = None) -> Dict:
        """获取账户信息"""
        latest = self.get_latest(symbol)
        if not latest:
            return {
                "balance": 0,
                "equity": 0,
                "margin_level": 0
            }
        return {
            "balance": latest.balance,
            "equity": latest.equity,
            "margin_level": latest.margin_level
        }

    def get_all_recent(self, count: int = 10) -> List[StatisticsData]:
        """获取最近的所有统计数据"""
        with self._lock:
            return list(self._all_data)[-count:]

    def clear(self) -> None:
        """清空数据"""
        with self._lock:
            self._by_symbol.clear()
            self._all_data.clear()

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_count": len(self._all_data),
                "symbol_count": len(self._by_symbol),
                "symbols": list(self._by_symbol.keys())
            }