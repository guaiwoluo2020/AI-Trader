#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略配置存储模块
"""

from typing import List, Dict, Optional
import threading

from ..models import TradingStrategy
from sqlite_storage import StrategyConfigRepository, bootstrap_runtime_storage


class StrategyStore:
    """策略配置存储"""

    def __init__(self, user_id: int = None):
        # 策略配置: {symbol: TradingStrategy}
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
            self._strategies = {strategy.symbol: strategy for strategy in strategies}
            print(f"[StrategyStore] 从 SQLite 加载 {len(self._strategies)} 个策略配置")
        except Exception as e:
            print(f"[StrategyStore] 加载配置失败: {e}")

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
        """获取品种的策略配置"""
        with self._lock:
            return self._strategies.get(symbol)

    def get_or_create_strategy(self, symbol: str) -> TradingStrategy:
        """获取或创建策略配置"""
        with self._lock:
            if symbol not in self._strategies:
                self._strategies[symbol] = TradingStrategy(symbol=symbol)
            return self._strategies[symbol]

    def set_strategy(self, strategy: TradingStrategy) -> None:
        """设置策略配置"""
        with self._lock:
            self._strategies[strategy.symbol] = strategy
            self.save_to_file()

    def update_strategy(self, symbol: str, data: Dict) -> Optional[TradingStrategy]:
        """更新策略配置"""
        with self._lock:
            strategy = self.get_or_create_strategy(symbol)
            strategy.update(data)
            self.save_to_file()
            return strategy

    def delete_strategy(self, symbol: str) -> bool:
        """删除策略配置"""
        with self._lock:
            if symbol in self._strategies:
                del self._strategies[symbol]
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
                symbol: strategy.to_dict()
                for symbol, strategy in self._strategies.items()
            }

    def get_enabled_strategies(self) -> List[TradingStrategy]:
        """获取所有启用的策略"""
        with self._lock:
            return [s for s in self._strategies.values() if s.enabled]

    def get_enabled_symbols(self) -> List[str]:
        """获取所有启用策略的品种"""
        with self._lock:
            return [symbol for symbol, strategy in self._strategies.items() if strategy.enabled]

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_strategies": len(self._strategies),
                "enabled_strategies": len(self.get_enabled_strategies()),
                "symbols": list(self._strategies.keys()),
            }
