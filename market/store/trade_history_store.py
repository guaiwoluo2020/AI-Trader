#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易历史存储模块
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import threading

from ..models.trade_history import TradeDeal


class TradeHistoryStore:
    """
    交易历史存储

    EA通过 /trade_history 上报成交记录
    """

    def __init__(self, retention_hours: int = 24):
        """
        Args:
            retention_hours: 数据保留时长（小时）
        """
        self._deals: List[TradeDeal] = []
        self._lock = threading.RLock()
        self._retention_hours = retention_hours
        self._last_update_time: Optional[datetime] = None

        print(f"[TradeHistoryStore] 交易历史存储已初始化 (retention={retention_hours}h)")

    def add(self, deals: List[TradeDeal]) -> int:
        """
        添加成交记录

        Args:
            deals: 成交记录列表

        Returns:
            新增记录数
        """
        if not deals:
            return 0

        now = datetime.now()
        new_count = 0

        with self._lock:
            # 获取已有ticket
            existing_tickets = {d.ticket for d in self._deals}

            for deal in deals:
                if deal.ticket not in existing_tickets:
                    self._deals.append(deal)
                    new_count += 1

            # 按时间排序
            self._deals.sort(key=lambda d: d.time or datetime.min, reverse=True)

            # 清理过期数据
            cutoff = now - timedelta(hours=self._retention_hours)
            self._deals = [d for d in self._deals if d.time and d.time > cutoff]

            self._last_update_time = now

        if new_count > 0:
            print(f"[TradeHistoryStore] 新增 {new_count} 条记录，当前共 {len(self._deals)} 条")

        return new_count

    def get(self, symbol: str = None, hours: int = None) -> List[TradeDeal]:
        """
        获取成交记录

        Args:
            symbol: 品种，None表示所有品种
            hours: 最近N小时，None表示使用默认值

        Returns:
            成交记录列表
        """
        with self._lock:
            deals = self._deals

            if symbol:
                deals = [d for d in deals if d.symbol == symbol]

            if hours:
                cutoff = datetime.now() - timedelta(hours=hours)
                deals = [d for d in deals if d.time and d.time > cutoff]

            return deals

    def get_dict(self, symbol: str = None, hours: int = None) -> List[Dict]:
        """获取成交记录（字典格式）"""
        return [d.to_dict() for d in self.get(symbol, hours)]

    def get_statistics(self, symbol: str = None) -> Dict:
        """
        获取交易统计

        Returns:
            统计数据
        """
        deals = self.get(symbol)

        if not deals:
            return {
                "total_count": 0,
                "symbols": {},
                "manual_count": 0,
                "auto_count": 0,
                "sl_tp_count": 0,
                "so_count": 0,
                "total_profit": 0,
                "total_swap": 0,
                "total_commission": 0,
                "net_profit": 0
            }

        # 按品种统计
        symbols = defaultdict(lambda: {"count": 0, "profit": 0, "volume": 0})

        # 分类统计
        manual_count = 0
        auto_count = 0
        sl_tp_count = 0
        so_count = 0

        total_profit = 0
        total_swap = 0
        total_commission = 0

        for deal in deals:
            # 品种统计
            symbols[deal.symbol]["count"] += 1
            symbols[deal.symbol]["profit"] += deal.profit
            symbols[deal.symbol]["volume"] += deal.volume

            # 分类统计
            source = deal.order_source
            if source == "手动":
                manual_count += 1
            elif source in ["止损触发", "止盈触发"]:
                sl_tp_count += 1
            elif source == "强制平仓":
                so_count += 1
            else:
                auto_count += 1

            # 总计
            total_profit += deal.profit
            total_swap += deal.swap
            total_commission += deal.commission

        # 转换symbols为普通字典
        symbols_dict = {}
        for sym, data in symbols.items():
            symbols_dict[sym] = {
                "count": data["count"],
                "profit": round(data["profit"], 2),
                "volume": round(data["volume"], 2)
            }

        return {
            "total_count": len(deals),
            "symbols": symbols_dict,
            "manual_count": manual_count,
            "auto_count": auto_count,
            "sl_tp_count": sl_tp_count,
            "so_count": so_count,
            "total_profit": round(total_profit, 2),
            "total_swap": round(total_swap, 2),
            "total_commission": round(total_commission, 2),
            "net_profit": round(total_profit + total_swap - total_commission, 2),
            "last_update": self._last_update_time.isoformat() if self._last_update_time else None
        }

    def get_recent_profit(self, symbol: str = None, hours: int = 24) -> float:
        """获取最近N小时的盈亏"""
        deals = self.get(symbol, hours)
        return sum(d.profit for d in deals)

    def clear(self) -> None:
        """清空数据"""
        with self._lock:
            self._deals.clear()
            self._last_update_time = None
            print("[TradeHistoryStore] 已清空")

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "deals_count": len(self._deals),
                "last_update": self._last_update_time.isoformat() if self._last_update_time else None
            }