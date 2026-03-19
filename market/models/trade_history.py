#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易历史数据模型
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
import re


@dataclass
class TradeDeal:
    """
    成交记录

    EA通过 /trade_history 上报
    """
    ticket: int
    order: int
    symbol: str
    deal_type: int  # 0=买入, 1=卖出
    entry_type: int  # 0=开仓, 1=平仓, 2=反向
    volume: float
    price: float
    profit: float
    swap: float
    commission: float
    time: datetime
    comment: str

    @property
    def is_buy(self) -> bool:
        """是否为买入"""
        return self.deal_type == 0

    @property
    def is_sell(self) -> bool:
        """是否为卖出"""
        return self.deal_type == 1

    @property
    def is_entry(self) -> bool:
        """是否为开仓"""
        return self.entry_type == 0

    @property
    def is_exit(self) -> bool:
        """是否为平仓"""
        return self.entry_type == 1

    @property
    def deal_type_text(self) -> str:
        """成交类型文本"""
        return "买入" if self.is_buy else "卖出"

    @property
    def entry_type_text(self) -> str:
        """入场类型文本"""
        if self.entry_type == 0:
            return "开仓"
        elif self.entry_type == 1:
            return "平仓"
        elif self.entry_type == 2:
            return "反向"
        return "未知"

    @property
    def order_source(self) -> str:
        """订单来源"""
        if not self.comment or not self.comment.strip():
            return "手动"

        comment = self.comment.strip()
        if comment.startswith('[sl'):
            return "止损触发"
        if comment.startswith('[tp'):
            return "止盈触发"
        if comment.startswith('[so'):
            return "强制平仓"
        return "自动"

    @property
    def is_auto(self) -> bool:
        """是否为自动订单"""
        if not self.comment or not self.comment.strip():
            return False
        comment = self.comment.strip()
        # 排除MT5系统标记
        if comment.startswith('[sl') or comment.startswith('[tp') or comment.startswith('[so'):
            return False
        return True

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "ticket": self.ticket,
            "order": self.order,
            "symbol": self.symbol,
            "type": self.deal_type,
            "type_text": self.deal_type_text,
            "entry": self.entry_type,
            "entry_text": self.entry_type_text,
            "volume": self.volume,
            "price": self.price,
            "profit": self.profit,
            "swap": self.swap,
            "commission": self.commission,
            "time": self.time.strftime("%Y-%m-%d %H:%M:%S") if self.time else None,
            "comment": self.comment,
            "is_auto": self.is_auto,
            "order_source": self.order_source
        }

    @classmethod
    def from_ea_data(cls, data: Dict) -> 'TradeDeal':
        """从EA上报数据创建"""
        # 解析时间
        deal_time = data.get('time')
        if isinstance(deal_time, str):
            for fmt in ["%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                try:
                    deal_time = datetime.strptime(deal_time, fmt)
                    break
                except:
                    pass
        if not isinstance(deal_time, datetime):
            deal_time = datetime.now()

        return cls(
            ticket=int(data.get('ticket', 0)),
            order=int(data.get('order', 0)),
            symbol=data.get('symbol', ''),
            deal_type=int(data.get('type', 0)),
            entry_type=int(data.get('entry', 0)),
            volume=float(data.get('volume', 0)),
            price=float(data.get('price', 0)),
            profit=float(data.get('profit', 0)),
            swap=float(data.get('swap', 0)),
            commission=float(data.get('commission', 0)),
            time=deal_time,
            comment=data.get('comment', '')
        )