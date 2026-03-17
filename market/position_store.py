#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据存储模块
接收和存储EA上报的持仓数据
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional
import threading
import json


class PositionData:
    """持仓数据结构"""

    def __init__(self, ticket: int, symbol: str, volume: float, price_open: float,
                 position_type: str, profit: float, distance_sl: float = 0,
                 distance_tp: float = 0, sl: float = 0, tp: float = 0):
        self.ticket = ticket
        self.symbol = symbol
        self.volume = volume
        self.price_open = price_open
        self.type = position_type  # "BUY" or "SELL"
        self.profit = profit
        self.distance_sl = distance_sl
        self.distance_tp = distance_tp
        self.sl = sl
        self.tp = tp
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "ticket": self.ticket,
            "symbol": self.symbol,
            "volume": self.volume,
            "price_open": self.price_open,
            "type": self.type,
            "profit": self.profit,
            "distance_sl": self.distance_sl,
            "distance_tp": self.distance_tp,
            "sl": self.sl,
            "tp": self.tp,
            "updated_at": self.updated_at.isoformat()
        }


class PositionStore:
    """持仓数据存储"""

    def __init__(self):
        # 存储结构: {SYMBOL: {TICKET: PositionData}}
        self._positions = defaultdict(dict)
        self._lock = threading.RLock()

        # 最后更新时间
        self._last_update_time = {}

        print("[PositionStore] 持仓存储已初始化")

    def update_positions(self, symbol: str, positions: List[Dict]) -> Dict:
        """
        更新持仓数据

        Args:
            symbol: 交易品种（上报的品种）
            positions: 持仓列表，每个持仓包含symbol字段

        Returns:
            {"status": "ok", "count": N}
        """
        with self._lock:
            # 上报的品种
            report_symbol = symbol

            # 获取当前品种的持仓ticket列表
            current_tickets = set(self._positions[report_symbol].keys())
            new_tickets = set()

            total_count = 0
            total_closed = 0

            for pos in positions:
                pos_symbol = pos.get('symbol', symbol)
                ticket = pos.get('ticket')
                if not ticket:
                    continue

                new_tickets.add(ticket)

                position = PositionData(
                    ticket=ticket,
                    symbol=pos_symbol,
                    volume=pos.get('volume', 0),
                    price_open=pos.get('priceOpen', 0),
                    position_type=pos.get('type', 'BUY'),
                    profit=pos.get('profit', 0),
                    distance_sl=pos.get('distanceSL', 0),
                    distance_tp=pos.get('distanceTP', 0),
                    sl=pos.get('sl', 0),
                    tp=pos.get('tp', 0)
                )
                self._positions[pos_symbol][ticket] = position

            # 删除已平仓的持仓（当前品种）
            closed_tickets = current_tickets - new_tickets
            for ticket in closed_tickets:
                del self._positions[report_symbol][ticket]

            # 更新最后更新时间
            self._last_update_time[report_symbol] = datetime.now()

            total_count = len(self._positions[report_symbol])
            total_closed = len(closed_tickets)

        print(f"[PositionStore] 更新持仓: {report_symbol}, {total_count} 个持仓, 平仓 {total_closed} 个")
        return {"status": "ok", "count": total_count, "closed": total_closed}

    def get_positions(self, symbol: str = None) -> List[Dict]:
        """
        获取持仓数据

        Args:
            symbol: 交易品种，None表示获取所有

        Returns:
            持仓列表
        """
        with self._lock:
            if symbol:
                positions = list(self._positions[symbol].values())
            else:
                positions = []
                for sym in self._positions:
                    positions.extend(self._positions[sym].values())

            return [p.to_dict() for p in positions]

    def get_position(self, symbol: str, ticket: int) -> Optional[Dict]:
        """获取单个持仓"""
        with self._lock:
            pos = self._positions[symbol].get(ticket)
            return pos.to_dict() if pos else None

    def get_summary(self, symbol: str = None) -> Dict:
        """
        获取持仓汇总

        Returns:
            {
                "total_count": 总持仓数,
                "total_profit": 总盈亏,
                "buy_count": 买单数,
                "sell_count": 卖单数,
                "positions": [...]
            }
        """
        positions = self.get_positions(symbol)

        total_profit = sum(p['profit'] for p in positions)
        buy_count = sum(1 for p in positions if p['type'] == 'BUY')
        sell_count = sum(1 for p in positions if p['type'] == 'SELL')

        return {
            "total_count": len(positions),
            "total_profit": round(total_profit, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "positions": positions,
            "last_update": self._last_update_time.get(symbol, max(self._last_update_time.values()) if self._last_update_time else None)
        }

    def clear_symbol(self, symbol: str):
        """清除某个品种的持仓数据"""
        with self._lock:
            if symbol in self._positions:
                del self._positions[symbol]
            if symbol in self._last_update_time:
                del self._last_update_time[symbol]

    def get_symbols(self) -> List[str]:
        """获取所有有持仓的品种"""
        with self._lock:
            return [s for s in self._positions if self._positions[s]]


# 全局单例
_position_store = None


def get_position_store() -> PositionStore:
    """获取持仓存储单例"""
    global _position_store
    if _position_store is None:
        _position_store = PositionStore()
    return _position_store