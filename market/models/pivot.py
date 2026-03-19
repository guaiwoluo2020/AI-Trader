#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点数据结构
"""

from datetime import datetime
from typing import Dict


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