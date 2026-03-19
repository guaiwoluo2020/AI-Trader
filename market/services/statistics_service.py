#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计数据服务模块
"""

from typing import Dict, Optional, List

from ..models.statistics import StatisticsData
from ..store.statistics_store import StatisticsStore


class StatisticsService:
    """
    统计数据服务

    功能：
    1. 处理EA上报的统计数据
    2. 获取品种价差
    3. 获取账户信息
    """

    def __init__(self, store: StatisticsStore = None):
        self.store = store or StatisticsStore()

    def process_statistics(self, data: Dict) -> None:
        """
        处理EA上报的统计数据

        Args:
            data: EA上报的JSON数据
        """
        stat = StatisticsData.from_ea_data(data)
        self.store.add(stat)

    def get_latest(self, symbol: str = None) -> Optional[StatisticsData]:
        """获取最新统计数据"""
        return self.store.get_latest(symbol)

    def get_spread(self, symbol: str) -> Optional[float]:
        """获取品种价差"""
        return self.store.get_spread(symbol)

    def get_mid_price(self, symbol: str) -> Optional[float]:
        """获取品种中间价"""
        latest = self.store.get_latest(symbol)
        if latest:
            return latest.mid_price
        return None

    def get_account_info(self, symbol: str = None) -> Dict:
        """获取账户信息"""
        return self.store.get_account_info(symbol)

    def get_by_symbol(self, symbol: str, count: int = 10) -> List[StatisticsData]:
        """获取指定品种的统计数据"""
        return self.store.get_by_symbol(symbol, count)

    def get_status(self) -> Dict:
        """获取服务状态"""
        return self.store.get_status()