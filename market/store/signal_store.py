#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号存储模块
"""

from typing import List, Dict, Optional
from datetime import datetime
import threading
from collections import defaultdict

from ..models import TradingSignal, SignalSource, SignalStatus


class SignalStore:
    """信号存储（只负责数据CRUD）"""

    def __init__(self, default_ttl: int = 300):
        # 按品种分类的信号: {symbol: [TradingSignal, ...]}
        self._signals_by_symbol: Dict[str, List[TradingSignal]] = defaultdict(list)

        # 按ID索引
        self._signals_by_id: Dict[str, TradingSignal] = {}

        # 线程锁
        self._lock = threading.RLock()

        # 默认信号有效期
        self.default_ttl = default_ttl

        print("[SignalStore] 信号存储已初始化")

    # ==================== 添加信号 ====================

    def add_signal(self, signal: TradingSignal) -> str:
        """添加信号"""
        with self._lock:
            signal.DEFAULT_TTL = self.default_ttl
            self._signals_by_symbol[signal.symbol].append(signal)
            self._signals_by_id[signal.signal_id] = signal

            print(f"[SignalStore] 添加信号: {signal.signal_id} {signal.symbol} {signal.action} (来源: {signal.source})")
            return signal.signal_id

    # ==================== 查询信号 ====================

    def get_signal_by_id(self, signal_id: str) -> Optional[TradingSignal]:
        """根据ID获取信号"""
        with self._lock:
            return self._signals_by_id.get(signal_id)

    def get_active_signals(self, symbol: str = None) -> List[TradingSignal]:
        """获取活跃信号"""
        with self._lock:
            if symbol:
                signals = self._signals_by_symbol.get(symbol, [])
            else:
                signals = list(self._signals_by_id.values())

            # 过滤活跃信号
            active = [s for s in signals if s.is_active()]
            return sorted(active, key=lambda x: x.created_at, reverse=True)

    def get_active_signals_by_source(self, symbol: str, source: str) -> List[TradingSignal]:
        """获取指定来源的活跃信号"""
        signals = self.get_active_signals(symbol)
        return [s for s in signals if s.source == source]

    def get_signals_dict(self, symbol: str = None) -> List[Dict]:
        """获取信号字典列表"""
        signals = self.get_active_signals(symbol)
        return [s.to_dict() for s in signals]

    def get_signal_count(self, symbol: str = None, source: str = None) -> int:
        """获取信号数量"""
        if source:
            return len(self.get_active_signals_by_source(symbol or "", source))
        return len(self.get_active_signals(symbol))

    # ==================== 更新信号状态 ====================

    def mark_signal_used(self, signal_id: str) -> bool:
        """标记信号为已使用"""
        with self._lock:
            signal = self._signals_by_id.get(signal_id)
            if signal:
                signal.mark_used()
                return True
            return False

    def mark_signal_expired(self, signal_id: str) -> bool:
        """标记信号为已过期"""
        with self._lock:
            signal = self._signals_by_id.get(signal_id)
            if signal:
                signal.mark_expired()
                return True
            return False

    # ==================== 清理过期信号 ====================

    def cleanup_expired(self) -> int:
        """清理过期信号"""
        with self._lock:
            expired_ids = []
            for signal_id, signal in self._signals_by_id.items():
                if signal.is_expired() and signal.status == SignalStatus.ACTIVE:
                    signal.mark_expired()
                    expired_ids.append(signal_id)

            # 从存储中移除过期信号
            for signal_id in expired_ids:
                signal = self._signals_by_id[signal_id]
                symbol = signal.symbol
                self._signals_by_symbol[symbol] = [
                    s for s in self._signals_by_symbol[symbol] if s.signal_id != signal_id
                ]
                del self._signals_by_id[signal_id]

            if expired_ids:
                print(f"[SignalStore] 清理过期信号: {len(expired_ids)}条")

            return len(expired_ids)

    # ==================== 清空 ====================

    def clear_by_symbol(self, symbol: str) -> int:
        """清空指定品种的信号"""
        with self._lock:
            signals = self._signals_by_symbol.get(symbol, [])
            count = len(signals)

            for signal in signals:
                if signal.signal_id in self._signals_by_id:
                    del self._signals_by_id[signal.signal_id]

            if symbol in self._signals_by_symbol:
                del self._signals_by_symbol[symbol]

            return count

    def clear_all(self) -> int:
        """清空所有信号"""
        with self._lock:
            count = len(self._signals_by_id)
            self._signals_by_symbol.clear()
            self._signals_by_id.clear()
            return count

    # ==================== 统计 ====================

    def get_signal_stats(self, symbol: str) -> Dict:
        """获取信号统计"""
        signals = self.get_active_signals(symbol)

        buy_signals = [s for s in signals if s.action == "buy"]
        sell_signals = [s for s in signals if s.action == "sell"]

        by_source = {}
        for source in [SignalSource.PIVOT, SignalSource.KEY_LEVEL, SignalSource.AI_ENTRY]:
            by_source[source] = len([s for s in signals if s.source == source])

        return {
            "symbol": symbol,
            "total": len(signals),
            "buy_count": len(buy_signals),
            "sell_count": len(sell_signals),
            "by_source": by_source,
        }

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_signals": len(self._signals_by_id),
                "symbols": list(self._signals_by_symbol.keys()),
            }