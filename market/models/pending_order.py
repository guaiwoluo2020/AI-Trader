#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
待确认订单数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional
import uuid


@dataclass
class PendingOrder:
    """待确认订单"""
    # 必填字段
    symbol: str
    action: str  # b=买入, s=卖出
    price: float  # 入场价
    mount: float  # 手数
    sl: float  # 止损
    tp: float  # 止盈

    # 可选字段
    reason: str = ""
    description: str = ""
    source: str = ""  # auto_pivot_m1/key_level/ai_entry_nearby/manual

    # 策略相关字段
    pivot_price: Optional[float] = None  # 转折点价格
    key_level: Optional[float] = None  # 关键点位
    ai_period: Optional[str] = None  # AI分析周期
    ai_entry_price: Optional[float] = None  # AI入场价
    ai_direction: Optional[str] = None  # AI方向

    # AI方向一致性分析
    ai_directions: Optional[Dict] = None
    direction_consistent: bool = False
    consistent_periods: list = field(default_factory=list)
    inconsistent_periods: list = field(default_factory=list)
    recommendation: str = ""
    recommendation_color: str = ""

    # 自动生成字段
    order_id: str = ""
    status: str = "pending"  # pending/confirmed/rejected/expired
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None

    # 超时时间（秒）
    TIMEOUT_SECONDS: int = field(default=180, repr=False)

    def __post_init__(self):
        if not self.order_id:
            self.order_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.expires_at:
            self.expires_at = self.created_at + timedelta(seconds=self.TIMEOUT_SECONDS)

    def is_expired(self) -> bool:
        """检查是否已过期"""
        return datetime.now() > self.expires_at

    def is_pending(self) -> bool:
        """检查是否待处理"""
        return self.status == "pending" and not self.is_expired()

    def confirm(self) -> None:
        """确认订单"""
        self.status = "confirmed"
        self.confirmed_at = datetime.now()

    def reject(self) -> None:
        """拒绝订单"""
        self.status = "rejected"

    def mark_expired(self) -> None:
        """标记为过期"""
        self.status = "expired"

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "action": self.action,
            "price": self.price,
            "mount": self.mount,
            "sl": self.sl,
            "tp": self.tp,
            "reason": self.reason,
            "description": self.description,
            "source": self.source,
            "pivot_price": self.pivot_price,
            "key_level": self.key_level,
            "ai_period": self.ai_period,
            "ai_entry_price": self.ai_entry_price,
            "ai_direction": self.ai_direction,
            "ai_directions": self.ai_directions,
            "direction_consistent": self.direction_consistent,
            "consistent_periods": self.consistent_periods,
            "inconsistent_periods": self.inconsistent_periods,
            "recommendation": self.recommendation,
            "recommendation_color": self.recommendation_color,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'PendingOrder':
        """从字典创建"""
        # 处理时间字段
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        expires_at = data.get('expires_at')
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        confirmed_at = data.get('confirmed_at')
        if isinstance(confirmed_at, str):
            confirmed_at = datetime.fromisoformat(confirmed_at)

        return cls(
            symbol=data.get('symbol', ''),
            action=data.get('action', ''),
            price=data.get('price', 0.0),
            mount=data.get('mount', 0.0),
            sl=data.get('sl', 0.0),
            tp=data.get('tp', 0.0),
            reason=data.get('reason', ''),
            description=data.get('description', ''),
            source=data.get('source', ''),
            pivot_price=data.get('pivot_price'),
            key_level=data.get('key_level'),
            ai_period=data.get('ai_period'),
            ai_entry_price=data.get('ai_entry_price'),
            ai_direction=data.get('ai_direction'),
            ai_directions=data.get('ai_directions'),
            direction_consistent=data.get('direction_consistent', False),
            consistent_periods=data.get('consistent_periods', []),
            inconsistent_periods=data.get('inconsistent_periods', []),
            recommendation=data.get('recommendation', ''),
            recommendation_color=data.get('recommendation_color', ''),
            order_id=data.get('order_id', ''),
            status=data.get('status', 'pending'),
            created_at=created_at,
            expires_at=expires_at,
            confirmed_at=confirmed_at,
        )