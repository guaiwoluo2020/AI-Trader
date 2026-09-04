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
from .pending_order_service import PendingOrderService
from .trading_instruction_service import TradingInstructionService
from .execution_result import ExecutionResult, EXECUTION_STATUSES, normalize_execution_status
from .strategy_execution_coordinator import StrategyExecutionCoordinator
from .position_action_applier import apply_action
from .position_management_coordinator import PositionManagementCoordinator
from .event_bus import EventBus
from .market_tick_ingress import MarketTickIngress
from .events import ApplicationEvent
from .event_audit_bridge import EventAuditBridge
from .plan_execution_service import PlanExecutionService
from .outbox_dispatcher import OutboxDispatcher
from .plan_lifecycle_service import PlanLifecycleService
from .kline_ingestion_coordinator import KlineIngestionCoordinator

# 信号服务
from .signal import (
    SignalService, KeyLevelSignalGenerator,
    AIEntrySignalGenerator, MovingAverageSignalGenerator,
    AlphaFactorSignalGenerator, AlphaRuntimeExecutor,
    PivotSignalGenerator, StructurePlanSignalGenerator, StructurePlanBuilder,
)

# 策略服务
from .strategy import StrategyService, RiskManager

# 统计、持仓、交易历史服务
from .statistics_service import StatisticsService
from .position_service import PositionService
from .trade_history_service import TradeHistoryService
from .position_manager import PositionManager

__all__ = [
    'KlineService', 'PivotService', 'LLMService', 'TechService',
    'PendingOrderService', 'TradingInstructionService', 'ExecutionResult',
    'EXECUTION_STATUSES', 'normalize_execution_status',
    'StrategyExecutionCoordinator',
    'apply_action',
    'PositionManagementCoordinator',
    'EventBus', 'ApplicationEvent',
    'MarketTickIngress',
    'EventAuditBridge',
    'PlanExecutionService',
    'OutboxDispatcher',
    'PlanLifecycleService',
    'KlineIngestionCoordinator',
    'SignalService', 'KeyLevelSignalGenerator',
    'AIEntrySignalGenerator', 'MovingAverageSignalGenerator',
    'AlphaFactorSignalGenerator', 'AlphaRuntimeExecutor',
    'PivotSignalGenerator',
    'StructurePlanSignalGenerator', 'StructurePlanBuilder',
    'StrategyService', 'RiskManager',
    'StatisticsService', 'PositionService', 'TradeHistoryService', 'PositionManager',
    'calculate_ma', 'calculate_adx', 'calculate_rsi', 'calculate_macd', 'calculate_bollinger_bands'
]
