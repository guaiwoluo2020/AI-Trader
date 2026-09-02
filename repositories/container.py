"""Per-service repository registry sharing the application MySQL pool."""

from __future__ import annotations

from typing import Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage
from .accounts import TradingAccountRepository
from .ai_signal_sources import AISignalSourceRepository
from .shared_ai_runtime import SharedAIRuntimeRepository
from .ai_suggestions import AITradeSuggestionRepository
from .llm_config import LLMConfigRepository
from .llm_access import LLMAccessRepository
from .runtime import PositionManagementEventRepository, PositionManagementPolicyRepository, RuntimeStateRepository
from .strategy import StrategyDeploymentRepository
from .strategy_config import StrategyConfigRepository
from .trade_config import TradeConfigRepository
from .trading import TradeExecutionRepository
from .platform import PlatformInstrumentMappingRepository
from .outbox import OutboxEventRepository
from market.services.outbox_dispatcher import OutboxDispatcher


class RepositoryContainer:
    """Repository instances scoped to one service/account context."""

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.deployments = StrategyDeploymentRepository(self.storage)
        self.strategies = StrategyConfigRepository(self.storage)
        self.trade_config = TradeConfigRepository(self.storage)
        self.ai_sources = AISignalSourceRepository(self.storage)
        self.ai_config = LLMConfigRepository(self.storage)
        self.ai_access = LLMAccessRepository(self.storage)
        self.ai_suggestions = AITradeSuggestionRepository(self.storage)
        self.ai_runtime = SharedAIRuntimeRepository(self.storage)
        self.platform_mappings = PlatformInstrumentMappingRepository(self.storage)
        self.trade_execution = TradeExecutionRepository(self.storage)
        self.position_events = PositionManagementEventRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)
        self.outbox = OutboxEventRepository(self.storage)
        self.outbox_dispatcher = OutboxDispatcher(self.outbox)

    def runtime(self, user_id: int, account_id: int) -> RuntimeStateRepository:
        return RuntimeStateRepository(user_id, account_id, self.storage)
