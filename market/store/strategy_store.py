#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略配置存储模块
"""

from typing import List, Dict, Optional
import threading
import json
import os

from ..models import TradingStrategy


# 配置文件路径
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data')


class StrategyStore:
    """策略配置存储"""

    def __init__(self):
        # 策略配置: {symbol: TradingStrategy}
        self._strategies: Dict[str, TradingStrategy] = {}

        # 线程锁
        self._lock = threading.RLock()

        # 从文件加载
        self._load_from_file()

        print("[StrategyStore] 策略配置存储已初始化")

    def _get_config_file(self) -> str:
        """获取配置文件路径"""
        return os.path.join(CONFIG_DIR, 'strategy_config.json')

    def _load_from_file(self) -> None:
        """从文件加载配置"""
        try:
            config_file = self._get_config_file()
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for symbol, strategy_data in data.get('strategies', {}).items():
                        self._strategies[symbol] = TradingStrategy.from_dict(strategy_data)
                print(f"[StrategyStore] 从文件加载 {len(self._strategies)} 个策略配置")
        except Exception as e:
            print(f"[StrategyStore] 加载配置文件失败: {e}")

    def save_to_file(self) -> bool:
        """保存配置到文件"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            config_file = self._get_config_file()

            data = {
                "strategies": {
                    symbol: strategy.to_dict()
                    for symbol, strategy in self._strategies.items()
                }
            }

            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            print(f"[StrategyStore] 配置已保存到: {config_file}")
            return True
        except Exception as e:
            print(f"[StrategyStore] 保存配置文件失败: {e}")
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