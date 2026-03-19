#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易历史服务模块
"""

from typing import Dict, List

from ..models.trade_history import TradeDeal
from ..store.trade_history_store import TradeHistoryStore


class TradeHistoryService:
    """
    交易历史服务

    功能：
    1. 处理EA上报的交易历史
    2. 提供交易统计分析
    """

    def __init__(self, store: TradeHistoryStore = None):
        self.store = store or TradeHistoryStore()

    def process_deals(self, deals_data: List[Dict]) -> int:
        """
        处理EA上报的成交记录

        Args:
            deals_data: EA上报的成交数据列表

        Returns:
            新增记录数
        """
        deals = [TradeDeal.from_ea_data(data) for data in deals_data]
        return self.store.add(deals)

    def get_deals(self, symbol: str = None, hours: int = None) -> List[Dict]:
        """获取成交记录"""
        return self.store.get_dict(symbol, hours)

    def get_statistics(self, symbol: str = None) -> Dict:
        """获取交易统计"""
        return self.store.get_statistics(symbol)

    def get_recent_profit(self, symbol: str = None, hours: int = 24) -> float:
        """获取最近N小时的盈亏"""
        return self.store.get_recent_profit(symbol, hours)

    def get_status(self) -> Dict:
        """获取服务状态"""
        return self.store.get_status()