#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易信号数据模型
纯分析结果，不含仓位资金
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional
import uuid


class SignalSource:
    """信号来源"""
    PIVOT = "pivot"              # 转折点信号
    KEY_LEVEL = "key_level"      # 关键点位信号
    AI_ENTRY = "ai_entry"        # AI入场信号


class SignalStatus:
    """信号状态"""
    ACTIVE = "active"            # 活跃
    EXPIRED = "expired"          # 已过期
    USED = "used"                # 已被使用（生成决策）


@dataclass
class TradingSignal:
    """交易信号 - 纯分析结果，不含仓位资金"""

    # ==================== 基本信息 ====================
    symbol: str                       # 品种
    action: str                       # 方向: buy/sell
    confidence: int = 50              # 置信度 0-100

    # ==================== 来源 ====================
    source: str = ""                  # pivot/key_level/ai_entry
    source_period: str = ""           # 来源周期 (H4/H1/M15/M5/M1)

    # ==================== 触发信息 ====================
    trigger_price: float = 0.0        # 触发价格
    trigger_time: datetime = None     # 触发时间
    trigger_reason: str = ""          # 触发原因

    # ==================== 建议参数 ====================
    suggested_entry: float = 0.0      # 建议入场价
    suggested_sl: float = 0.0         # 建议止损
    suggested_tp: float = 0.0         # 建议止盈
    risk_reward_ratio: float = 0.0    # 风险回报比

    # ==================== 来源特有参数 ====================
    # Pivot信号
    pivot_price: Optional[float] = None
    pivot_type: Optional[str] = None      # high/low

    # KeyLevel信号
    key_level: Optional[float] = None
    distance_pct: Optional[float] = None

    # AI Entry信号
    ai_analysis_period: Optional[str] = None

    # ==================== 自动生成字段 ====================
    signal_id: str = ""
    status: str = SignalStatus.ACTIVE
    created_at: datetime = None
    expires_at: datetime = None

    # 默认信号有效期（秒）
    DEFAULT_TTL: int = field(default=300, repr=False)  # 5分钟

    def __post_init__(self):
        if not self.signal_id:
            self.signal_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.trigger_time:
            self.trigger_time = self.created_at
        if not self.expires_at:
            self.expires_at = self.created_at + timedelta(seconds=self.DEFAULT_TTL)

    def is_expired(self) -> bool:
        """检查是否已过期"""
        return datetime.now() > self.expires_at

    def is_active(self) -> bool:
        """检查是否活跃"""
        return self.status == SignalStatus.ACTIVE and not self.is_expired()

    def mark_used(self) -> None:
        """标记为已使用"""
        self.status = SignalStatus.USED

    def mark_expired(self) -> None:
        """标记为已过期"""
        self.status = SignalStatus.EXPIRED

    def get_risk_points(self) -> float:
        """获取风险点数"""
        if self.action == "buy":
            return abs(self.suggested_entry - self.suggested_sl)
        else:
            return abs(self.suggested_sl - self.suggested_entry)

    def get_reward_points(self) -> float:
        """获取回报点数"""
        if self.action == "buy":
            return abs(self.suggested_tp - self.suggested_entry)
        else:
            return abs(self.suggested_entry - self.suggested_tp)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "action": self.action,
            "confidence": self.confidence,
            "source": self.source,
            "source_period": self.source_period,
            "trigger_price": self.trigger_price,
            "trigger_time": self.trigger_time.isoformat() if self.trigger_time else None,
            "trigger_reason": self.trigger_reason,
            "suggested_entry": self.suggested_entry,
            "suggested_sl": self.suggested_sl,
            "suggested_tp": self.suggested_tp,
            "risk_reward_ratio": self.risk_reward_ratio,
            "pivot_price": self.pivot_price,
            "pivot_type": self.pivot_type,
            "key_level": self.key_level,
            "distance_pct": self.distance_pct,
            "ai_analysis_period": self.ai_analysis_period,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "risk_points": self.get_risk_points(),
            "reward_points": self.get_reward_points(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingSignal':
        """从字典创建"""
        trigger_time = data.get('trigger_time')
        if isinstance(trigger_time, str):
            trigger_time = datetime.fromisoformat(trigger_time)

        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        expires_at = data.get('expires_at')
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        return cls(
            symbol=data.get('symbol', ''),
            action=data.get('action', ''),
            confidence=data.get('confidence', 50),
            source=data.get('source', ''),
            source_period=data.get('source_period', ''),
            trigger_price=data.get('trigger_price', 0.0),
            trigger_time=trigger_time,
            trigger_reason=data.get('trigger_reason', ''),
            suggested_entry=data.get('suggested_entry', 0.0),
            suggested_sl=data.get('suggested_sl', 0.0),
            suggested_tp=data.get('suggested_tp', 0.0),
            risk_reward_ratio=data.get('risk_reward_ratio', 0.0),
            pivot_price=data.get('pivot_price'),
            pivot_type=data.get('pivot_type'),
            key_level=data.get('key_level'),
            distance_pct=data.get('distance_pct'),
            ai_analysis_period=data.get('ai_analysis_period'),
            signal_id=data.get('signal_id', ''),
            status=data.get('status', SignalStatus.ACTIVE),
            created_at=created_at,
            expires_at=expires_at,
        )