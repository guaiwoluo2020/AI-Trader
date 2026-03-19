#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易指令数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
import uuid


@dataclass
class TradingInstruction:
    """交易指令"""
    # 必填字段
    symbol: str
    action: str  # b=买入, s=卖出
    price: float  # 指令执行价格
    mount: float  # 手数

    # 可选字段
    sl: float = 0.0  # 止损
    tp: float = 0.005  # 止盈（默认值）
    reason: str = ""
    description: str = ""
    source: str = ""  # manual/pending_order_confirm/key_level/ai_entry

    # 来源追踪
    order_id: Optional[str] = None  # 来源订单ID（如果是确认订单转入）

    # 自动生成字段
    instruction_id: str = ""
    status: str = "pending"  # pending/sent/executed/cancelled
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None  # 发送给EA的时间
    executed_at: Optional[datetime] = None

    def __post_init__(self):
        if not self.instruction_id:
            self.instruction_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()
        # 确保tp有默认值
        if self.tp is None or self.tp <= 0:
            self.tp = 0.005

    def to_dict(self) -> Dict:
        """转换为字典（用于返回给EA）"""
        return {
            "symbol": self.symbol.lower(),
            "action": self.action.lower(),
            "mount": self.mount,
            "price": self.price,
            "sl": self.sl,
            "tp": self.tp,
        }

    def to_full_dict(self) -> Dict:
        """转换为完整字典（用于内部存储和查询）"""
        return {
            "instruction_id": self.instruction_id,
            "symbol": self.symbol,
            "action": self.action,
            "price": self.price,
            "mount": self.mount,
            "sl": self.sl,
            "tp": self.tp,
            "reason": self.reason,
            "description": self.description,
            "source": self.source,
            "order_id": self.order_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingInstruction':
        """从字典创建"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        sent_at = data.get('sent_at')
        if isinstance(sent_at, str):
            sent_at = datetime.fromisoformat(sent_at)

        executed_at = data.get('executed_at')
        if isinstance(executed_at, str):
            executed_at = datetime.fromisoformat(executed_at)

        return cls(
            symbol=data.get('symbol', ''),
            action=data.get('action', ''),
            price=data.get('price', 0.0),
            mount=data.get('mount', 0.0),
            sl=data.get('sl', 0.0),
            tp=data.get('tp', 0.005),
            reason=data.get('reason', ''),
            description=data.get('description', ''),
            source=data.get('source', ''),
            order_id=data.get('order_id'),
            instruction_id=data.get('instruction_id', ''),
            status=data.get('status', 'pending'),
            created_at=created_at,
            sent_at=sent_at,
            executed_at=executed_at,
        )

    @classmethod
    def from_pending_order(cls, order: 'PendingOrder') -> 'TradingInstruction':
        """从待确认订单创建"""
        return cls(
            symbol=order.symbol,
            action=order.action,
            price=order.price,
            mount=order.mount,
            sl=order.sl,
            tp=order.tp,
            reason=order.reason,
            description=order.description,
            source=f"pending_order_{order.source}",
            order_id=order.order_id,
        )