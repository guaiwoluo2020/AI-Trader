#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略配置存储模块
"""

from typing import List, Dict, Optional
import threading

from ..models import StrategyLifecycle, TradingStrategy
from sqlite_storage import StrategyConfigRepository, bootstrap_runtime_storage


class StrategyStore:
    """策略配置存储"""

    def __init__(self, user_id: int = None):
        # 策略配置: {strategy_id: TradingStrategy}
        self._strategies: Dict[str, TradingStrategy] = {}

        # 线程锁
        self._lock = threading.RLock()
        self._repo = StrategyConfigRepository()
        self._user_id = user_id
        if self._user_id is None:
            runtime_user = bootstrap_runtime_storage(self._build_password_credentials)
            self._user_id = runtime_user.user_id

        # 从 SQLite 加载
        self._load_from_file()

        print("[StrategyStore] 策略配置存储已初始化")

    @staticmethod
    def _build_password_credentials(password: str):
        import hashlib
        import secrets

        salt = secrets.token_hex(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return salt, password_hash

    def _load_from_file(self) -> None:
        """从 SQLite 加载配置"""
        try:
            strategies = self._repo.get_all_strategies(self._user_id)
            self._strategies = {
                strategy.strategy_id: strategy for strategy in strategies
            }
            print(f"[StrategyStore] 从 SQLite 加载 {len(self._strategies)} 个策略配置")
        except Exception as e:
            print(f"[StrategyStore] 加载配置失败: {e}")

    def reload_from_storage(self) -> None:
        """刷新其他账户引擎持有的同一用户策略副本。"""
        with self._lock:
            self._load_from_file()

    def save_to_file(self) -> bool:
        """保存配置到 SQLite"""
        try:
            self._repo.replace_all(self._user_id, list(self._strategies.values()))
            print("[StrategyStore] 配置已保存到 SQLite")
            return True
        except Exception as e:
            print(f"[StrategyStore] 保存配置失败: {e}")
            return False

    # ==================== 策略管理 ====================

    def get_strategy(self, symbol: str) -> Optional[TradingStrategy]:
        """兼容旧调用，获取品种创建最早的策略配置。"""
        with self._lock:
            strategies = self.get_strategies(symbol)
            return strategies[0] if strategies else None

    def get_strategy_by_id(self, strategy_id: str) -> Optional[TradingStrategy]:
        with self._lock:
            return self._strategies.get(strategy_id)

    def get_strategies(self, symbol: str) -> List[TradingStrategy]:
        with self._lock:
            return [
                strategy
                for strategy in self._strategies.values()
                if strategy.symbol == symbol
            ]

    def get_or_create_strategy(self, symbol: str) -> TradingStrategy:
        """获取或创建策略配置"""
        with self._lock:
            strategy = self.get_strategy(symbol)
            if strategy is None:
                strategy = TradingStrategy(
                    symbol=symbol,
                    enabled=False,
                    lifecycle_status=StrategyLifecycle.DRAFT,
                )
                self._strategies[strategy.strategy_id] = strategy
            return strategy

    def set_strategy(self, strategy: TradingStrategy) -> None:
        """设置策略配置"""
        with self._lock:
            self._strategies[strategy.strategy_id] = strategy
            self.save_to_file()

    def create_strategy(self, symbol: str, data: Dict = None) -> TradingStrategy:
        with self._lock:
            strategy = TradingStrategy(
                symbol=symbol,
                enabled=False,
                auto_execute=False,
                lifecycle_status=StrategyLifecycle.DRAFT,
            )
            if data:
                safe_data = dict(data)
                safe_data.pop("lifecycle_status", None)
                safe_data.pop("lifecycle_history", None)
                strategy.update(safe_data)
            self._strategies[strategy.strategy_id] = strategy
            self.save_to_file()
            return strategy

    def transition_lifecycle(
        self, strategy_id: str, target_status: str, reason: str = ""
    ) -> Optional[TradingStrategy]:
        """转换生命周期并持久化。"""
        with self._lock:
            strategy = self.get_strategy_by_id(strategy_id)
            if strategy is None:
                return None
            strategy.transition_lifecycle(target_status, reason)
            self.save_to_file()
            return strategy

    def update_strategy(
        self, symbol: str, data: Dict, strategy_id: str = None
    ) -> Optional[TradingStrategy]:
        """更新策略配置"""
        with self._lock:
            strategy = (
                self.get_strategy_by_id(strategy_id)
                if strategy_id
                else self.get_or_create_strategy(symbol)
            )
            if strategy is None or strategy.symbol != symbol:
                return None
            strategy.update(data)
            self.save_to_file()
            return strategy

    def delete_strategy(self, symbol: str, strategy_id: str = None) -> bool:
        """删除策略配置"""
        with self._lock:
            if strategy_id:
                strategy = self._strategies.get(strategy_id)
                if strategy is None or strategy.symbol != symbol:
                    return False
                del self._strategies[strategy_id]
                self.save_to_file()
                return True
            matching_ids = [
                item.strategy_id
                for item in self._strategies.values()
                if item.symbol == symbol
            ]
            if matching_ids:
                for matching_id in matching_ids:
                    del self._strategies[matching_id]
                self.save_to_file()
                return True
            return False

    # ==================== 查询 ====================

    def get_all_strategies(self) -> List[TradingStrategy]:
        """获取所有策略配置"""
        with self._lock:
            return list(self._strategies.values())

    def get_all_strategies_dict(self) -> Dict[str, Dict]:
        """获取所有策略配置字典"""
        with self._lock:
            return {
                strategy_id: strategy.to_dict()
                for strategy_id, strategy in self._strategies.items()
            }

    def get_enabled_strategies(self) -> List[TradingStrategy]:
        """获取所有启用的策略"""
        with self._lock:
            return [s for s in self._strategies.values() if s.is_runnable()]

    def get_enabled_symbols(self) -> List[str]:
        """获取所有启用策略的品种"""
        with self._lock:
            return sorted({
                strategy.symbol
                for strategy in self._strategies.values()
                if strategy.is_runnable()
            })

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_strategies": len(self._strategies),
                "enabled_strategies": len(self.get_enabled_strategies()),
                "symbols": sorted({s.symbol for s in self._strategies.values()}),
            }
