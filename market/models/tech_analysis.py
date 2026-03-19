#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术分析相关数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class TechTrendState:
    """单周期趋势状态"""
    symbol: str
    period: str
    trend: str = "unknown"           # "up" / "down" / "sideways" / "unknown"
    strength: int = 0                 # 0-100
    adx: float = 0.0
    ma_fast: float = 0.0
    ma_slow: float = 0.0
    price: float = 0.0
    reason: str = ""
    timestamp: Optional[str] = None
    previous_trend: Optional[str] = None
    change_signal: bool = False

    def to_dict(self) -> Dict:
        return {
            "trend": self.trend,
            "strength": self.strength,
            "adx": self.adx,
            "ma_fast": self.ma_fast,
            "ma_slow": self.ma_slow,
            "price": self.price,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "previous_trend": self.previous_trend,
            "change_signal": self.change_signal
        }


@dataclass
class TechTrendChange:
    """趋势转换记录"""
    period: str
    from_trend: str
    to_trend: str
    price: float
    timestamp: str

    def to_dict(self) -> Dict:
        return {
            "period": self.period,
            "from_trend": self.from_trend,
            "to_trend": self.to_trend,
            "price": self.price,
            "timestamp": self.timestamp
        }


@dataclass
class TechResonanceResult:
    """多周期共振结果"""
    symbol: str
    resonance: str = "none"           # "up" / "down" / "none"
    strength: int = 0
    aligned_count: int = 0
    up_count: int = 0
    down_count: int = 0
    sideways_count: int = 0
    signal: str = "等待数据"
    periods: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "resonance": self.resonance,
            "strength": self.strength,
            "aligned_count": self.aligned_count,
            "up_count": self.up_count,
            "down_count": self.down_count,
            "sideways_count": self.sideways_count,
            "signal": self.signal,
            "periods": {p: s.to_dict() if hasattr(s, 'to_dict') else s for p, s in self.periods.items()}
        }


@dataclass
class TechTradeSuggestion:
    """技术分析交易建议"""
    symbol: str
    action: str                       # "b" / "s"
    price: float
    sl: float
    tp: float
    reason: str
    trend_strength: int
    resonance_periods: int
    generated_at: str

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "price": self.price,
            "mount": 0.01,  # 默认手数
            "sl": self.sl,
            "tp": self.tp,
            "reason": self.reason,
            "trend_strength": self.trend_strength,
            "resonance_periods": self.resonance_periods,
            "generated_at": self.generated_at
        }