#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线数据结构
"""

from datetime import datetime
from typing import Dict


class KlineData:
    """K线数据结构"""

    def __init__(self, symbol: str, period: str, timestamp, open_price: float,
                 high: float, low: float, close: float, volume: float = 0):
        self.symbol = symbol
        self.period = period  # H4, H1, M15, M5, M1
        self.timestamp = timestamp
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

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
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'KlineData':
        """从字典创建"""
        return cls(
            symbol=data.get('symbol', ''),
            period=data.get('period', ''),
            timestamp=data.get('timestamp') or data.get('time'),
            open_price=float(data.get('open', 0)),
            high=float(data.get('high', 0)),
            low=float(data.get('low', 0)),
            close=float(data.get('close', 0)),
            volume=float(data.get('volume', 0))
        )