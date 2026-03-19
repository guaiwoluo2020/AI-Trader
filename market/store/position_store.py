#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据存储模块
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional
import threading

from ..models.position import PositionData


class PositionStore:
    """
    持仓数据存储

    EA通过 /ea/positions 上报持仓数据
    """

    def __init__(self):
        # 存储结构: {symbol: {ticket: PositionData}}
        self._positions: Dict[str, Dict[int, PositionData]] = defaultdict(dict)
        self._lock = threading.RLock()

        # 最后更新时间
        self._last_update_time: Dict[str, datetime] = {}

        print("[PositionStore] 持仓存储已初始化")

    def update(self, symbol: str, positions: List[PositionData]) -> Dict:
        """
        更新持仓数据

        Args:
            symbol: 上报的品种
            positions: 持仓列表

        Returns:
            {"status": "ok", "count": N, "closed": M}
        """
        with self._lock:
            # 获取当前品种的持仓ticket
            current_tickets = set(self._positions[symbol].keys())
            new_tickets = set()

            for pos in positions:
                new_tickets.add(pos.ticket)
                # 使用持仓数据中的symbol（可能与上报品种不同）
                pos_symbol = pos.symbol or symbol
                self._positions[pos_symbol][pos.ticket] = pos

            # 删除已平仓的持仓
            closed_tickets = current_tickets - new_tickets
            for ticket in closed_tickets:
                if ticket in self._positions[symbol]:
                    del self._positions[symbol][ticket]

            # 更新最后更新时间
            self._last_update_time[symbol] = datetime.now()

            result = {
                "status": "ok",
                "count": len(self._positions[symbol]),
                "closed": len(closed_tickets)
            }

            print(f"[PositionStore] {symbol}: {result['count']} 持仓, {result['closed']} 平仓")
            return result

    def get(self, symbol: str = None) -> List[PositionData]:
        """获取持仓数据"""
        with self._lock:
            if symbol:
                return list(self._positions[symbol].values())
            else:
                result = []
                for sym in self._positions:
                    result.extend(self._positions[sym].values())
                return result

    def get_dict(self, symbol: str = None) -> List[Dict]:
        """获取持仓数据（字典格式）"""
        return [p.to_dict() for p in self.get(symbol)]

    def get_by_ticket(self, symbol: str, ticket: int) -> Optional[PositionData]:
        """根据ticket获取持仓"""
        with self._lock:
            return self._positions[symbol].get(ticket)

    def remove(self, symbol: str, ticket: int) -> bool:
        """删除持仓"""
        with self._lock:
            if ticket in self._positions[symbol]:
                del self._positions[symbol][ticket]
                return True
            return False

    def get_count(self, symbol: str = None) -> int:
        """获取持仓数量"""
        return len(self.get(symbol))

    def get_count_by_direction(self, symbol: str, direction: str) -> int:
        """获取指定方向的持仓数量"""
        with self._lock:
            count = 0
            direction = direction.lower()
            for pos in self._positions[symbol].values():
                if direction == "buy" and pos.is_buy:
                    count += 1
                elif direction == "sell" and pos.is_sell:
                    count += 1
            return count

    def get_summary(self, symbol: str = None) -> Dict:
        """获取持仓汇总"""
        positions = self.get(symbol)

        total_profit = sum(p.profit for p in positions)
        buy_count = sum(1 for p in positions if p.is_buy)
        sell_count = sum(1 for p in positions if p.is_sell)

        return {
            "total_count": len(positions),
            "total_profit": round(total_profit, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "positions": [p.to_dict() for p in positions],
            "last_update": self._last_update_time.get(symbol) or max(self._last_update_time.values(), default=None)
        }

    def get_symbols(self) -> List[str]:
        """获取所有有持仓的品种"""
        with self._lock:
            return [s for s in self._positions if self._positions[s]]

    def clear_symbol(self, symbol: str) -> None:
        """清除指定品种的持仓"""
        with self._lock:
            if symbol in self._positions:
                del self._positions[symbol]
            if symbol in self._last_update_time:
                del self._last_update_time[symbol]

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            total_count = sum(len(pos) for pos in self._positions.values())
            return {
                "total_positions": total_count,
                "symbol_count": len([s for s in self._positions if self._positions[s]]),
                "symbols": self.get_symbols()
            }