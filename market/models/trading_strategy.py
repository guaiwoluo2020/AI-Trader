#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易策略数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json
import os
import uuid


class ConsistencyRequirement:
    """一致性要求"""
    ANY = "any"                  # 任一信号即可
    MAJORITY = "majority"        # 多数信号一致
    ALL = "all"                  # 所有信号一致


class ConflictResolution:
    """冲突解决策略"""
    HIGHEST_CONFIDENCE = "highest_confidence"    # 最高置信度
    HIGHEST_WEIGHT = "highest_weight"            # 最高权重
    SKIP = "skip"                                # 跳过冲突


class VolumeMode:
    """手数模式"""
    FIXED = "fixed"                          # 固定手数
    RISK_PERCENT = "risk_percent"            # 风险百分比


class StopLossMode:
    """止损模式"""
    SIGNAL = "signal"                        # 使用信号建议
    FIXED_POINTS = "fixed_points"            # 固定点数
    ATR_PERCENT = "atr_percent"              # ATR百分比


class TakeProfitMode:
    """止盈模式"""
    SIGNAL = "signal"                        # 使用信号建议
    FIXED_POINTS = "fixed_points"            # 固定点数
    RISK_REWARD = "risk_reward"              # 风险回报比


class PositionConflict:
    """持仓冲突处理"""
    ALLOW_OPPOSITE = "allow_opposite"        # 允许反向
    ALLOW_SAME = "allow_same"                # 允许同向
    ALLOW_BOTH = "allow_both"                # 都允许
    BLOCK = "block"                          # 有持仓则阻止


@dataclass
class TradingStrategy:
    """交易策略 - 绑定品种，配置信号权重和决策规则"""

    # ==================== 基本信息 ====================
    symbol: str                       # 绑定的品种
    strategy_name: str = ""           # 策略名称

    # ==================== 启用状态 ====================
    enabled: bool = True              # 是否启用

    # ==================== 信号源配置（新版：支持周期级别控制）====================
    # 信号源配置结构：
    # {
    #   "pivot": {
    #     "enabled": true,
    #     "periods": {"M1": {"enabled": true, "weight": 15}, "M5": {"enabled": true, "weight": 20}, ...}
    #   },
    #   "key_level": {"enabled": true, "weight": 40},  # key_level 不区分周期
    #   "ai_entry": {
    #     "enabled": true,
    #     "periods": {"M5": {"enabled": true, "weight": 20}, ...}
    #   }
    # }
    signal_config: Dict = field(default_factory=lambda: {
        "pivot": {
            "enabled": True,
            "periods": {
                "M1": {"enabled": True, "weight": 15},
                "M5": {"enabled": True, "weight": 20},
                "M15": {"enabled": False, "weight": 25},
                "H1": {"enabled": False, "weight": 20},
                "H4": {"enabled": False, "weight": 20}
            }
        },
        "key_level": {
            "enabled": True,
            "weight": 40
        },
        "ai_entry": {
            "enabled": True,
            "periods": {
                "M1": {"enabled": False, "weight": 15},
                "M5": {"enabled": True, "weight": 20},
                "M15": {"enabled": True, "weight": 30},
                "H1": {"enabled": True, "weight": 25},
                "H4": {"enabled": False, "weight": 20}
            }
        }
    })

    # ==================== 信号权重配置（兼容旧版，已废弃）===================
    signal_weights: Dict[str, int] = field(default_factory=lambda: {
        "pivot": 30,
        "key_level": 40,
        "ai_entry": 30,
    })

    period_weights: Dict[str, int] = field(default_factory=lambda: {
        "H4": 20,
        "H1": 20,
        "M15": 25,
        "M5": 20,
        "M1": 15,
    })

    # ==================== 信号过滤规则 ====================
    min_confidence: int = 50
    consistency_requirement: str = ConsistencyRequirement.MAJORITY
    conflict_resolution: str = ConflictResolution.HIGHEST_WEIGHT

    # ==================== 仓位管理 ====================
    fixed_volume: float = 0.01
    volume_mode: str = VolumeMode.FIXED
    risk_percent: float = 1.0
    max_risk_points: float = 50.0

    max_positions: int = 3
    max_same_direction: int = 2

    # ==================== 止损止盈规则 ====================
    sl_mode: str = StopLossMode.SIGNAL
    sl_fixed_points: float = 20.0
    sl_atr_multiplier: float = 1.5

    tp_mode: str = TakeProfitMode.SIGNAL
    tp_fixed_points: float = 40.0
    tp_risk_reward: float = 2.0

    # ==================== 过滤条件 ====================
    min_risk_reward: float = 1.0
    max_risk_reward: float = 5.0
    min_sl_points: float = 5.0
    max_sl_points: float = 100.0

    # ==================== 时间过滤 ====================
    trading_hours: Dict = field(default_factory=lambda: {
        "start": "00:00",
        "end": "23:59",
        "exclude_hours": []
    })

    # ==================== 持仓冲突处理 ====================
    position_conflict: str = PositionConflict.ALLOW_OPPOSITE

    # ==================== 自动生成字段 ====================
    strategy_id: str = ""
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if not self.strategy_id:
            self.strategy_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not self.strategy_name:
            self.strategy_name = f"Strategy_{self.symbol}"

    def update(self, data: Dict) -> None:
        """更新配置"""
        if "enabled" in data:
            self.enabled = bool(data["enabled"])
        if "signal_config" in data:
            self.signal_config = data["signal_config"]
        if "signal_weights" in data:
            self.signal_weights = data["signal_weights"]
        if "period_weights" in data:
            self.period_weights = data["period_weights"]
        if "min_confidence" in data:
            self.min_confidence = int(data["min_confidence"])
        if "consistency_requirement" in data:
            self.consistency_requirement = data["consistency_requirement"]
        if "conflict_resolution" in data:
            self.conflict_resolution = data["conflict_resolution"]
        if "fixed_volume" in data:
            self.fixed_volume = float(data["fixed_volume"])
        if "volume_mode" in data:
            self.volume_mode = data["volume_mode"]
        if "risk_percent" in data:
            self.risk_percent = float(data["risk_percent"])
        if "max_positions" in data:
            self.max_positions = int(data["max_positions"])
        if "max_same_direction" in data:
            self.max_same_direction = int(data["max_same_direction"])
        if "sl_mode" in data:
            self.sl_mode = data["sl_mode"]
        if "tp_mode" in data:
            self.tp_mode = data["tp_mode"]
        if "min_risk_reward" in data:
            self.min_risk_reward = float(data["min_risk_reward"])
        if "max_risk_reward" in data:
            self.max_risk_reward = float(data["max_risk_reward"])
        if "position_conflict" in data:
            self.position_conflict = data["position_conflict"]
        if "trading_hours" in data:
            self.trading_hours = data["trading_hours"]

        self.updated_at = datetime.now()

    def get_signal_weight(self, source: str, period: str = None) -> int:
        """
        获取信号源权重（支持周期级别）

        Args:
            source: 信号源 (pivot/key_level/ai_entry)
            period: 周期 (M1/M5/M15/H1/H4)，key_level 不需要周期

        Returns:
            权重值
        """
        # 优先使用新的 signal_config
        if self.signal_config and source in self.signal_config:
            config = self.signal_config[source]
            if not config.get("enabled", True):
                return 0

            # key_level 不区分周期
            if source == "key_level":
                return config.get("weight", 0)

            # 其他信号源区分周期
            if period and "periods" in config:
                period_config = config["periods"].get(period, {})
                if not period_config.get("enabled", False):
                    return 0
                return period_config.get("weight", 0)

            # 如果没有 period 配置，返回 0
            return 0

        # 兼容旧版 signal_weights
        return self.signal_weights.get(source, 0)

    def is_signal_enabled(self, source: str, period: str = None) -> bool:
        """
        检查信号源是否启用

        Args:
            source: 信号源
            period: 周期（key_level 不需要）

        Returns:
            是否启用
        """
        if not self.signal_config or source not in self.signal_config:
            # 兼容旧版：signal_weights 中有配置就认为启用
            return source in self.signal_weights and self.signal_weights[source] > 0

        config = self.signal_config[source]
        if not config.get("enabled", True):
            return False

        # key_level 不区分周期
        if source == "key_level":
            return True

        # 其他信号源需要检查周期
        if period and "periods" in config:
            period_config = config["periods"].get(period, {})
            return period_config.get("enabled", False)

        return False

    def get_period_weight(self, period: str) -> int:
        """获取周期权重（兼容旧版）"""
        return self.period_weights.get(period, 0)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "enabled": self.enabled,
            "signal_config": self.signal_config,
            "signal_weights": self.signal_weights,
            "period_weights": self.period_weights,
            "min_confidence": self.min_confidence,
            "consistency_requirement": self.consistency_requirement,
            "conflict_resolution": self.conflict_resolution,
            "fixed_volume": self.fixed_volume,
            "volume_mode": self.volume_mode,
            "risk_percent": self.risk_percent,
            "max_risk_points": self.max_risk_points,
            "max_positions": self.max_positions,
            "max_same_direction": self.max_same_direction,
            "sl_mode": self.sl_mode,
            "sl_fixed_points": self.sl_fixed_points,
            "sl_atr_multiplier": self.sl_atr_multiplier,
            "tp_mode": self.tp_mode,
            "tp_fixed_points": self.tp_fixed_points,
            "tp_risk_reward": self.tp_risk_reward,
            "min_risk_reward": self.min_risk_reward,
            "max_risk_reward": self.max_risk_reward,
            "min_sl_points": self.min_sl_points,
            "max_sl_points": self.max_sl_points,
            "trading_hours": self.trading_hours,
            "position_conflict": self.position_conflict,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingStrategy':
        """从字典创建"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = data.get('updated_at')
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        # 默认 signal_config
        default_signal_config = {
            "pivot": {
                "enabled": True,
                "periods": {
                    "M1": {"enabled": True, "weight": 15},
                    "M5": {"enabled": True, "weight": 20},
                    "M15": {"enabled": False, "weight": 25},
                    "H1": {"enabled": False, "weight": 20},
                    "H4": {"enabled": False, "weight": 20}
                }
            },
            "key_level": {
                "enabled": True,
                "weight": 40
            },
            "ai_entry": {
                "enabled": True,
                "periods": {
                    "M1": {"enabled": False, "weight": 15},
                    "M5": {"enabled": True, "weight": 20},
                    "M15": {"enabled": True, "weight": 30},
                    "H1": {"enabled": True, "weight": 25},
                    "H4": {"enabled": False, "weight": 20}
                }
            }
        }

        return cls(
            symbol=data.get('symbol', ''),
            strategy_name=data.get('strategy_name', ''),
            enabled=data.get('enabled', True),
            signal_config=data.get('signal_config', default_signal_config),
            signal_weights=data.get('signal_weights', {"pivot": 30, "key_level": 40, "ai_entry": 30}),
            period_weights=data.get('period_weights', {"H4": 20, "H1": 20, "M15": 25, "M5": 20, "M1": 15}),
            min_confidence=data.get('min_confidence', 50),
            consistency_requirement=data.get('consistency_requirement', ConsistencyRequirement.MAJORITY),
            conflict_resolution=data.get('conflict_resolution', ConflictResolution.HIGHEST_WEIGHT),
            fixed_volume=data.get('fixed_volume', 0.01),
            volume_mode=data.get('volume_mode', VolumeMode.FIXED),
            risk_percent=data.get('risk_percent', 1.0),
            max_risk_points=data.get('max_risk_points', 50.0),
            max_positions=data.get('max_positions', 3),
            max_same_direction=data.get('max_same_direction', 2),
            sl_mode=data.get('sl_mode', StopLossMode.SIGNAL),
            sl_fixed_points=data.get('sl_fixed_points', 20.0),
            sl_atr_multiplier=data.get('sl_atr_multiplier', 1.5),
            tp_mode=data.get('tp_mode', TakeProfitMode.SIGNAL),
            tp_fixed_points=data.get('tp_fixed_points', 40.0),
            tp_risk_reward=data.get('tp_risk_reward', 2.0),
            min_risk_reward=data.get('min_risk_reward', 1.0),
            max_risk_reward=data.get('max_risk_reward', 5.0),
            min_sl_points=data.get('min_sl_points', 5.0),
            max_sl_points=data.get('max_sl_points', 100.0),
            trading_hours=data.get('trading_hours', {"start": "00:00", "end": "23:59", "exclude_hours": []}),
            position_conflict=data.get('position_conflict', PositionConflict.ALLOW_OPPOSITE),
            strategy_id=data.get('strategy_id', ''),
            created_at=created_at,
            updated_at=updated_at,
        )


@dataclass
class TradingDecision:
    """交易决策 - 策略层输出"""

    # ==================== 基本信息 ====================
    symbol: str                       # 品种
    strategy_id: str                  # 来源策略ID

    # ==================== 决策结果 ====================
    action: str = ""                  # buy/sell/none
    decision_type: str = ""           # signal_combined / single_signal / manual

    # ==================== 信号汇总 ====================
    signals: List[Dict] = field(default_factory=list)
    signal_summary: Dict = field(default_factory=dict)

    # ==================== 执行参数 ====================
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    volume: float = 0.01

    risk_points: float = 0.0
    reward_points: float = 0.0
    risk_reward_ratio: float = 0.0

    # ==================== 决策理由 ====================
    decision_reason: str = ""
    confidence_score: float = 0.0

    # ==================== 检查结果 ====================
    position_check: Dict = field(default_factory=dict)
    risk_check: Dict = field(default_factory=dict)

    # ==================== 状态 ====================
    decision_id: str = ""
    status: str = "pending"           # pending/confirmed/rejected/expired
    created_at: datetime = None

    # ==================== 关联 ====================
    order_id: Optional[str] = None

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "action": self.action,
            "decision_type": self.decision_type,
            "signals": self.signals,
            "signal_summary": self.signal_summary,
            "entry_price": self.entry_price,
            "sl": self.sl,
            "tp": self.tp,
            "volume": self.volume,
            "risk_points": self.risk_points,
            "reward_points": self.reward_points,
            "risk_reward_ratio": self.risk_reward_ratio,
            "decision_reason": self.decision_reason,
            "confidence_score": self.confidence_score,
            "position_check": self.position_check,
            "risk_check": self.risk_check,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "order_id": self.order_id,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingDecision':
        """从字典创建"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return cls(
            symbol=data.get('symbol', ''),
            strategy_id=data.get('strategy_id', ''),
            action=data.get('action', ''),
            decision_type=data.get('decision_type', ''),
            signals=data.get('signals', []),
            signal_summary=data.get('signal_summary', {}),
            entry_price=data.get('entry_price', 0.0),
            sl=data.get('sl', 0.0),
            tp=data.get('tp', 0.0),
            volume=data.get('volume', 0.01),
            risk_points=data.get('risk_points', 0.0),
            reward_points=data.get('reward_points', 0.0),
            risk_reward_ratio=data.get('risk_reward_ratio', 0.0),
            decision_reason=data.get('decision_reason', ''),
            confidence_score=data.get('confidence_score', 0.0),
            position_check=data.get('position_check', {}),
            risk_check=data.get('risk_check', {}),
            decision_id=data.get('decision_id', ''),
            status=data.get('status', 'pending'),
            created_at=created_at,
            order_id=data.get('order_id'),
        )