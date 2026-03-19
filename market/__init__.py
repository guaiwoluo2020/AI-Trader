#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情分析模块
"""

# 数据模型
from .models import (
    KlineData, PivotPoint,
    LLMConfig, LLMAnalysisResult,
    TechTrendState, TechTrendChange, TechResonanceResult, TechTradeSuggestion
)

# 存储层
from .store import KlineStore, PivotStore, LLMStore, TechStore

# 服务层
from .services import KlineService, PivotService, LLMService, TechService

# 其他模块
from .llm_analyzer import LLMAnalyzer
from .trade_config import TradeConfig

# 兼容旧代码的别名
MarketStore = KlineStore
PivotDetector = PivotService
TrendAnalyzer = TechService

__all__ = [
    # 数据模型
    'KlineData', 'PivotPoint',
    'LLMConfig', 'LLMAnalysisResult',
    'TechTrendState', 'TechTrendChange', 'TechResonanceResult', 'TechTradeSuggestion',
    # 存储层
    'KlineStore', 'PivotStore', 'LLMStore', 'TechStore',
    # 服务层
    'KlineService', 'PivotService', 'LLMService', 'TechService',
    # 其他模块
    'LLMAnalyzer', 'TradeConfig',
    # 兼容别名
    'MarketStore', 'PivotDetector', 'TrendAnalyzer'
]