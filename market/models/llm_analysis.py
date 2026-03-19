#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 分析结果数据结构
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class LLMAnalysisResult:
    """LLM 分析结果"""
    symbol: str
    trend_analysis: Dict = field(default_factory=dict)
    overall_trend: Optional[Dict] = None
    key_levels: Optional[Dict] = None
    trade_suggestions: List[Dict] = field(default_factory=list)
    analyzed_at: Optional[str] = None
    data_stale: bool = False
    market_status: str = "active"  # active, stale, closed

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "trend_analysis": self.trend_analysis,
            "overall_trend": self.overall_trend,
            "key_levels": self.key_levels,
            "trade_suggestions": self.trade_suggestions,
            "analyzed_at": self.analyzed_at,
            "data_stale": self.data_stale,
            "market_status": self.market_status
        }

    @classmethod
    def from_api_response(cls, symbol: str, data: Dict) -> 'LLMAnalysisResult':
        """从 LLM API 返回的字典创建"""
        return cls(
            symbol=symbol,
            trend_analysis=data.get("trend_analysis", {}),
            overall_trend=data.get("overall_trend"),
            key_levels=data.get("key_levels"),
            trade_suggestions=data.get("trade_suggestions", []),
            analyzed_at=datetime.now().isoformat(),
            data_stale=False,
            market_status="active"
        )

    def get_trade_suggestion(self, period: str) -> Optional[Dict]:
        """获取指定周期的交易建议"""
        for ts in self.trade_suggestions:
            if ts.get("period") == period:
                return ts
        return None