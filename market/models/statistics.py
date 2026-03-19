#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict


@dataclass
class StatisticsData:
    """
    EA上报的统计数据

    用途：
    1. 获取品种价差（TechService）
    2. 获取账户信息（前端展示）
    3. 检查数据时效性
    """
    symbol: str
    timestamp: datetime

    # 价格信息
    bid_price: float
    ask_price: float
    spread: float
    spread_points: float

    # 账户信息
    balance: float
    equity: float
    margin_level: float

    # 其他
    tick_count: int = 0

    @property
    def mid_price(self) -> float:
        """中间价"""
        return (self.bid_price + self.ask_price) / 2

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "spread": self.spread,
            "spread_points": self.spread_points,
            "balance": self.balance,
            "equity": self.equity,
            "margin_level": self.margin_level,
            "tick_count": self.tick_count,
            "mid_price": self.mid_price
        }

    @classmethod
    def from_ea_data(cls, data: Dict) -> 'StatisticsData':
        """从EA上报数据创建"""
        # 解析时间戳
        timestamp = data.get('timestamp')
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except:
                timestamp = datetime.now()
        elif not isinstance(timestamp, datetime):
            timestamp = datetime.now()

        return cls(
            symbol=data.get('symbol', ''),
            timestamp=timestamp,
            bid_price=float(data.get('bidPrice', 0)),
            ask_price=float(data.get('askPrice', 0)),
            spread=float(data.get('spread', 0)),
            spread_points=float(data.get('spreadPoints', 0)),
            balance=float(data.get('balance', 0)),
            equity=float(data.get('equity', 0)),
            margin_level=float(data.get('marginLevel', 0)),
            tick_count=int(data.get('tickCount', 0))
        )