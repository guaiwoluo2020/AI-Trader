#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务模块
"""

from .kline_service import KlineService
from .pivot_service import PivotService
from .llm_service import LLMService
from .tech_indicators import calculate_ma, calculate_adx, calculate_rsi, calculate_macd, calculate_bollinger_bands
from .tech_service import TechService
from .calendar_service import CalendarService
from .flash_news_service import FlashNewsService
from .pending_order_service import PendingOrderService
from .trading_instruction_service import TradingInstructionService

# 信号服务
from .signal import SignalService, PivotSignalGenerator, KeyLevelSignalGenerator, AIEntrySignalGenerator

# 策略服务
from .strategy import StrategyService, RiskManager

# 统计、持仓、交易历史服务
from .statistics_service import StatisticsService
from .position_service import PositionService
from .trade_history_service import TradeHistoryService

__all__ = [
    'KlineService', 'PivotService', 'LLMService', 'TechService',
    'CalendarService', 'FlashNewsService',
    'PendingOrderService', 'TradingInstructionService',
    'SignalService', 'PivotSignalGenerator', 'KeyLevelSignalGenerator', 'AIEntrySignalGenerator',
    'StrategyService', 'RiskManager',
    'StatisticsService', 'PositionService', 'TradeHistoryService',
    'calculate_ma', 'calculate_adx', 'calculate_rsi', 'calculate_macd', 'calculate_bollinger_bands'
]