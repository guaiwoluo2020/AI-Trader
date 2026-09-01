"""Per-service repository registry sharing the application MySQL pool."""

from __future__ import annotations

from typing import Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage
from .accounts import TradingAccountRepository
from .ai import AISignalSourceRepository, SharedAIRuntimeRepository
from .runtime import PositionManagementEventRepository, PositionManagementPolicyRepository, RuntimeStateRepository
from .strategy import StrategyDeploymentRepository
from .trading import TradeExecutionRepository


class RepositoryContainer:
    """Repository instances scoped to one service/account context."""

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.deployments = StrategyDeploymentRepository(self.storage)
        self.ai_sources = AISignalSourceRepository(self.storage)
        self.ai_runtime = SharedAIRuntimeRepository(self.storage)
        self.trade_execution = TradeExecutionRepository(self.storage)
        self.position_events = PositionManagementEventRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)

    def runtime(self, user_id: int, account_id: int) -> RuntimeStateRepository:
        return RuntimeStateRepository(user_id, account_id, self.storage)
