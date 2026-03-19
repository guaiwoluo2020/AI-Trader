#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号服务模块
统一管理信号的生成、存储和查询
"""

from typing import List, Dict, Optional
from datetime import datetime
import threading

from ...models import TradingSignal, SignalSource
from ...store import SignalStore


class SignalService:
    """信号服务（统一管理信号）"""

    def __init__(self, signal_store: SignalStore = None):
        self.store = signal_store or SignalStore()

        # 信号生成器（注册后使用）
        self._generators: Dict[str, callable] = {}

        # 冷却管理（避免重复生成）
        self._cooldowns: Dict[str, datetime] = {}
        self._cooldown_lock = threading.Lock()

        # 默认冷却时间（秒）
        self.default_cooldown = 180  # 3分钟

        # 启动清理线程
        self._start_cleanup_thread()

        print("[SignalService] 信号服务已初始化")

    def _start_cleanup_thread(self):
        """启动清理线程"""
        def cleanup_loop():
            while True:
                try:
                    self.store.cleanup_expired()
                    self._cleanup_cooldowns()
                except Exception as e:
                    print(f"[SignalService] 清理线程异常: {e}")
                threading.Event().wait(30)

        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()

    def _cleanup_cooldowns(self):
        """清理过期的冷却记录"""
        current_time = datetime.now()
        with self._cooldown_lock:
            keys_to_remove = []
            for key, last_time in self._cooldowns.items():
                elapsed = (current_time - last_time).total_seconds()
                if elapsed > self.default_cooldown * 2:
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del self._cooldowns[key]

    def _check_cooldown(self, key: str) -> bool:
        """检查是否在冷却期内"""
        with self._cooldown_lock:
            if key in self._cooldowns:
                last_time = self._cooldowns[key]
                elapsed = (datetime.now() - last_time).total_seconds()
                return elapsed < self.default_cooldown
            return False

    def _set_cooldown(self, key: str) -> None:
        """设置冷却"""
        with self._cooldown_lock:
            self._cooldowns[key] = datetime.now()

    # ==================== 信号生成器注册 ====================

    def register_generator(self, source: str, generator: callable) -> None:
        """注册信号生成器"""
        self._generators[source] = generator
        print(f"[SignalService] 注册信号生成器: {source}")

    # ==================== 信号生成 ====================

    def generate_signals(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """
        生成信号（调用所有注册的生成器）

        Args:
            symbol: 品种
            current_price: 当前价格

        Returns:
            生成的信号列表
        """
        signals = []

        for source, generator in self._generators.items():
            try:
                generated = generator(symbol, current_price)
                if generated:
                    if isinstance(generated, list):
                        for signal in generated:
                            if isinstance(signal, TradingSignal):
                                signals.append(signal)
                    elif isinstance(generated, TradingSignal):
                        signals.append(generated)
            except Exception as e:
                print(f"[SignalService] 信号生成器 {source} 异常: {e}")

        # 存储信号
        for signal in signals:
            self.store.add_signal(signal)

        return signals

    def add_signal(self, signal: TradingSignal) -> str:
        """添加信号"""
        return self.store.add_signal(signal)

    # ==================== 信号查询 ====================

    def get_signal(self, signal_id: str) -> Optional[TradingSignal]:
        """获取信号"""
        return self.store.get_signal_by_id(signal_id)

    def get_active_signals(self, symbol: str = None) -> List[TradingSignal]:
        """获取活跃信号"""
        return self.store.get_active_signals(symbol)

    def get_active_signals_by_source(self, symbol: str, source: str) -> List[TradingSignal]:
        """获取指定来源的活跃信号"""
        return self.store.get_active_signals_by_source(symbol, source)

    def get_signals_dict(self, symbol: str = None) -> List[Dict]:
        """获取信号字典列表"""
        return self.store.get_signals_dict(symbol)

    def get_signal_count(self, symbol: str = None, source: str = None) -> int:
        """获取信号数量"""
        return self.store.get_signal_count(symbol, source)

    # ==================== 信号消费 ====================

    def consume_signal(self, signal_id: str) -> Optional[TradingSignal]:
        """
        消费信号（标记为已使用）

        Args:
            signal_id: 信号ID

        Returns:
            信号对象
        """
        signal = self.store.get_signal_by_id(signal_id)
        if signal and signal.is_active():
            self.store.mark_signal_used(signal_id)
            return signal
        return None

    def consume_signals_for_decision(self, symbol: str) -> List[TradingSignal]:
        """
        消费品种的所有活跃信号（用于决策）

        Args:
            symbol: 品种

        Returns:
            信号列表
        """
        signals = self.store.get_active_signals(symbol)
        for signal in signals:
            self.store.mark_signal_used(signal.signal_id)
        return signals

    # ==================== 统计 ====================

    def get_signal_stats(self, symbol: str) -> Dict:
        """获取信号统计"""
        return self.store.get_signal_stats(symbol)

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "store": self.store.get_status(),
            "generators": list(self._generators.keys()),
        }

    def clear_all(self) -> int:
        """清空所有信号"""
        return self.store.clear_all()