#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据模型
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from ..mt5_time import UTC, parse_ea_instant, utc_iso, utc_now


@dataclass
class PositionData:
    """
    持仓数据

    EA通过 /ea/positions 上报
    """
    ticket: int
    symbol: str
    volume: float
    price_open: float
    position_type: str  # "BUY" / "SELL"
    profit: float
    comment: str = ""

    # 止损止盈
    sl: float = 0.0
    tp: float = 0.0
    distance_sl: float = 0.0  # 距离止损的点数
    distance_tp: float = 0.0  # 距离止盈的点数

    # 元数据
    opened_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.updated_at is None:
            self.updated_at = utc_now()

    @property
    def is_buy(self) -> bool:
        """是否为买单"""
        return self.position_type.upper() == "BUY"

    @property
    def is_sell(self) -> bool:
        """是否为卖单"""
        return self.position_type.upper() == "SELL"

    @property
    def direction(self) -> str:
        """方向：buy / sell"""
        return "buy" if self.is_buy else "sell"

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "volume": self.volume,
            "price_open": self.price_open,
            "type": self.position_type,
            "profit": self.profit,
            "comment": self.comment,
            "sl": self.sl,
            "tp": self.tp,
            "distance_sl": self.distance_sl,
            "distance_tp": self.distance_tp,
            "direction": self.direction,
            "opened_at": utc_iso(self.opened_at) if self.opened_at else None,
            "open_timestamp": int(self.opened_at.timestamp()) if self.opened_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }

    @classmethod
    def from_ea_data(cls, data: Dict, symbol: str = None) -> 'PositionData':
        """从EA上报数据创建"""
        return cls(
            ticket=int(data.get('ticket', 0)),
            symbol=data.get('symbol', symbol or ''),
            volume=float(data.get('volume', 0)),
            price_open=float(data.get('priceOpen', 0)),
            position_type=data.get('type', 'BUY').upper(),
            profit=float(data.get('profit', 0)),
            comment=str(data.get('comment', '') or ''),
            sl=float(data.get('sl', 0)),
            tp=float(data.get('tp', 0)),
            distance_sl=float(data.get('distanceSL', 0)),
            distance_tp=float(data.get('distanceTP', 0)),
            opened_at=parse_ea_instant(
                data,
                utc_field="open_timestamp",
                broker_epoch_field="openTime",
            ) if data.get("open_timestamp") or data.get("openTime") else None,
        )

    @classmethod
    def from_dict(cls, data: Dict) -> 'PositionData':
        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=UTC)
        opened_at = data.get("opened_at")
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=UTC)
        return cls(
            ticket=int(data.get("ticket", 0)),
            symbol=data.get("symbol", ""),
            volume=float(data.get("volume", 0)),
            price_open=float(data.get("price_open", data.get("priceOpen", 0))),
            position_type=data.get("type", "BUY").upper(),
            profit=float(data.get("profit", 0)),
            comment=str(data.get("comment", "") or ""),
            sl=float(data.get("sl", 0)),
            tp=float(data.get("tp", 0)),
            distance_sl=float(data.get("distance_sl", data.get("distanceSL", 0))),
            distance_tp=float(data.get("distance_tp", data.get("distanceTP", 0))),
            opened_at=opened_at,
            updated_at=updated_at,
        )
