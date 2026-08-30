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
    MOVING_AVERAGE = "moving_average"  # 均线交叉信号
    ALPHA_FACTOR = "alpha_factor"  # 已验证 Alpha 因子信号
    STRUCTURE_CONTINUATION = "structure_continuation"  # 结构趋势延续


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
    market_direction: str = ""        # 市场方向: up/sideways/down
    state_ready: bool = True          # 当前信号源是否具备足够数据
    is_entry_trigger: bool = True      # 是否出现新的可入场触发事件

    # ==================== 来源 ====================
    source: str = ""                  # pivot/key_level/ai_entry
    source_period: str = ""           # 来源周期 (H4/H1/M15/M5/M1)
    strategy_id: str = ""             # 归属策略ID，空值兼容历史公共信号
    strategy_name: str = ""           # 归属策略名称
    signal_source_id: str = ""        # 归属信号源实例ID
    setup_family: str = "generic"      # 通用持仓管理场景族
    setup_type: str = "generic_entry"  # 具体交易形态
    entry_mode: str = "touch_or_near"  # touch_or_near/breakout/confirmation

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
    pivot_confirmation_count: int = 0
    pivot_age_bars: float = 0
    pivot_score: int = 0

    # KeyLevel信号
    key_level: Optional[float] = None
    distance_pct: Optional[float] = None

    # AI Entry信号
    ai_analysis_period: Optional[str] = None
    ai_trend: str = ""
    ai_trend_confidence: int = 0
    ai_trend_reason: str = ""
    ai_overall_trend: Dict = field(default_factory=dict)
    ai_market_structure: Dict = field(default_factory=dict)
    ai_background_analysis: Dict = field(default_factory=dict)
    ai_trade_horizon: Dict = field(default_factory=dict)
    ai_original_entry: float = 0.0
    ai_plan_id: str = ""
    ai_setup_type: str = ""
    ai_entry_mode: str = ""
    ai_plan_status: str = ""
    ai_plan_valid_from: int = 0
    ai_plan_expires_at: int = 0

    # MovingAverage信号
    fast_ma: Optional[float] = None
    slow_ma: Optional[float] = None

    # ==================== 自动生成字段 ====================
    signal_id: str = ""
    status: str = SignalStatus.ACTIVE
    created_at: datetime = None
    expires_at: datetime = None

    # 默认信号有效期（秒）
    DEFAULT_TTL: int = field(default=300, repr=False)  # 5分钟

    def __post_init__(self):
        if not self.market_direction:
            self.market_direction = {
                "buy": "up", "sell": "down", "none": "sideways",
            }.get(str(self.action).lower(), "sideways")
        if self.market_direction not in {"up", "sideways", "down"}:
            self.market_direction = "sideways"
        if self.action not in {"buy", "sell", "none"}:
            self.action = {
                "up": "buy", "down": "sell", "sideways": "none",
            }[self.market_direction]
        self.confidence = max(0, min(100, int(self.confidence)))
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
            "market_direction": self.market_direction,
            "state_ready": self.state_ready,
            "is_entry_trigger": self.is_entry_trigger,
            "source": self.source,
            "source_period": self.source_period,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "signal_source_id": self.signal_source_id,
            "setup_family": self.setup_family,
            "setup_type": self.setup_type,
            "entry_mode": self.entry_mode,
            "trigger_price": self.trigger_price,
            "trigger_time": self.trigger_time.isoformat() if self.trigger_time else None,
            "trigger_reason": self.trigger_reason,
            "suggested_entry": self.suggested_entry,
            "suggested_sl": self.suggested_sl,
            "suggested_tp": self.suggested_tp,
            "risk_reward_ratio": self.risk_reward_ratio,
            "pivot_price": self.pivot_price,
            "pivot_type": self.pivot_type,
            "pivot_confirmation_count": self.pivot_confirmation_count,
            "pivot_age_bars": self.pivot_age_bars,
            "pivot_score": self.pivot_score,
            "key_level": self.key_level,
            "distance_pct": self.distance_pct,
            "ai_analysis_period": self.ai_analysis_period,
            "ai_trend": self.ai_trend,
            "ai_trend_confidence": self.ai_trend_confidence,
            "ai_trend_reason": self.ai_trend_reason,
            "ai_overall_trend": self.ai_overall_trend,
            "ai_market_structure": self.ai_market_structure,
            "ai_background_analysis": self.ai_background_analysis,
            "ai_trade_horizon": self.ai_trade_horizon,
            "ai_original_entry": self.ai_original_entry,
            "ai_plan_id": self.ai_plan_id,
            "ai_setup_type": self.ai_setup_type,
            "ai_entry_mode": self.ai_entry_mode,
            "ai_plan_status": self.ai_plan_status,
            "ai_plan_valid_from": self.ai_plan_valid_from,
            "ai_plan_expires_at": self.ai_plan_expires_at,
            "fast_ma": self.fast_ma,
            "slow_ma": self.slow_ma,
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
            market_direction=data.get('market_direction', ''),
            state_ready=bool(data.get('state_ready', True)),
            is_entry_trigger=bool(data.get('is_entry_trigger', True)),
            source=data.get('source', ''),
            source_period=data.get('source_period', ''),
            strategy_id=data.get('strategy_id', ''),
            strategy_name=data.get('strategy_name', ''),
            signal_source_id=data.get('signal_source_id', ''),
            setup_family=str(data.get('setup_family') or 'generic'),
            setup_type=str(data.get('setup_type') or 'generic_entry'),
            entry_mode=str(data.get('entry_mode') or 'touch_or_near'),
            trigger_price=data.get('trigger_price', 0.0),
            trigger_time=trigger_time,
            trigger_reason=data.get('trigger_reason', ''),
            suggested_entry=data.get('suggested_entry', 0.0),
            suggested_sl=data.get('suggested_sl', 0.0),
            suggested_tp=data.get('suggested_tp', 0.0),
            risk_reward_ratio=data.get('risk_reward_ratio', 0.0),
            pivot_price=data.get('pivot_price'),
            pivot_type=data.get('pivot_type'),
            pivot_confirmation_count=int(data.get('pivot_confirmation_count') or 0),
            pivot_age_bars=float(data.get('pivot_age_bars') or 0),
            pivot_score=int(data.get('pivot_score') or 0),
            key_level=data.get('key_level'),
            distance_pct=data.get('distance_pct'),
            ai_analysis_period=data.get('ai_analysis_period'),
            ai_trend=data.get('ai_trend', ''),
            ai_trend_confidence=int(data.get('ai_trend_confidence', 0) or 0),
            ai_trend_reason=data.get('ai_trend_reason', ''),
            ai_overall_trend=data.get('ai_overall_trend') or {},
            ai_market_structure=data.get('ai_market_structure') or {},
            ai_background_analysis=data.get('ai_background_analysis') or {},
            ai_trade_horizon=data.get('ai_trade_horizon') or {},
            ai_original_entry=float(data.get('ai_original_entry', 0) or 0),
            ai_plan_id=str(data.get('ai_plan_id') or ''),
            ai_setup_type=str(data.get('ai_setup_type') or ''),
            ai_entry_mode=str(data.get('ai_entry_mode') or ''),
            ai_plan_status=str(data.get('ai_plan_status') or ''),
            ai_plan_valid_from=int(data.get('ai_plan_valid_from', 0) or 0),
            ai_plan_expires_at=int(data.get('ai_plan_expires_at', 0) or 0),
            fast_ma=data.get('fast_ma'),
            slow_ma=data.get('slow_ma'),
            signal_id=data.get('signal_id', ''),
            status=data.get('status', SignalStatus.ACTIVE),
            created_at=created_at,
            expires_at=expires_at,
        )
