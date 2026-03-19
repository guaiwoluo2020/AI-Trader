#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术分析结果存储模块
"""

from collections import defaultdict
from typing import Dict, List, Optional
import threading

from ..models import TechTrendState, TechTrendChange


class TechStore:
    """技术分析结果存储（只负责数据CRUD）"""

    def __init__(self):
        # 趋势状态: {SYMBOL: {PERIOD: TechTrendState}}
        self._trend_states: Dict[str, Dict[str, TechTrendState]] = defaultdict(lambda: defaultdict(dict))
        self._lock = threading.RLock()

        # 趋势转换历史: {SYMBOL: [TechTrendChange, ...]}
        self._trend_changes: Dict[str, List[TechTrendChange]] = defaultdict(list)

        # 最大历史记录数
        self.MAX_CHANGES = 20

        print("[TechStore] 技术分析存储已初始化")

    # ==================== 趋势状态 ====================

    def save_trend_state(self, state: TechTrendState):
        """保存趋势状态"""
        with self._lock:
            self._trend_states[state.symbol][state.period] = state

    def get_trend_state(self, symbol: str, period: str = None) -> Dict:
        """获取趋势状态"""
        with self._lock:
            if period:
                state = self._trend_states[symbol].get(period)
                return state.to_dict() if state else {}
            return {p: s.to_dict() for p, s in self._trend_states[symbol].items()}

    def get_trend_state_object(self, symbol: str, period: str) -> Optional[TechTrendState]:
        """获取趋势状态对象"""
        with self._lock:
            return self._trend_states[symbol].get(period)

    def get_all_trend_states(self, symbol: str) -> Dict[str, TechTrendState]:
        """获取某品种所有周期的趋势状态"""
        with self._lock:
            return dict(self._trend_states[symbol])

    # ==================== 趋势转换历史 ====================

    def add_trend_change(self, symbol: str, change: TechTrendChange):
        """添加趋势转换记录"""
        with self._lock:
            self._trend_changes[symbol].append(change)
            # 限制数量
            if len(self._trend_changes[symbol]) > self.MAX_CHANGES:
                self._trend_changes[symbol] = self._trend_changes[symbol][-self.MAX_CHANGES:]

    def get_trend_changes(self, symbol: str, count: int = 10) -> List[Dict]:
        """获取趋势转换历史"""
        with self._lock:
            changes = self._trend_changes[symbol][-count:]
            return [c.to_dict() for c in changes]

    # ==================== 清理 ====================

    def clear_symbol(self, symbol: str):
        """清除某品种的数据"""
        with self._lock:
            if symbol in self._trend_states:
                del self._trend_states[symbol]
            if symbol in self._trend_changes:
                del self._trend_changes[symbol]

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取状态"""
        with self._lock:
            symbols = list(self._trend_states.keys())
            total_states = sum(len(periods) for periods in self._trend_states.values())
            return {
                "symbols": symbols,
                "total_states": total_states
            }