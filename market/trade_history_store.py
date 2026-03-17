#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易历史存储模块
存储EA上报的交易历史数据
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import threading
from dataclasses import dataclass, field


@dataclass
class TradeDeal:
    """成交记录"""
    ticket: int
    order: int
    symbol: str
    type: int  # 0=买入, 1=卖出
    entry: int  # 0=开仓, 1=平仓, 2=反向
    volume: float
    price: float
    profit: float
    swap: float
    commission: float
    time: datetime
    comment: str

    def to_dict(self) -> Dict:
        return {
            "ticket": self.ticket,
            "order": self.order,
            "symbol": self.symbol,
            "type": self.type,
            "type_text": "买入" if self.type == 0 else "卖出",
            "entry": self.entry,
            "entry_text": self._get_entry_text(),
            "volume": self.volume,
            "price": self.price,
            "profit": self.profit,
            "swap": self.swap,
            "commission": self.commission,
            "time": self.time.strftime("%Y-%m-%d %H:%M:%S") if self.time else None,
            "comment": self.comment,
            "is_auto": self._is_auto_order(),
            "order_source": self._get_order_source()
        }

    def _get_entry_text(self) -> str:
        if self.entry == 0:
            return "开仓"
        elif self.entry == 1:
            return "平仓"
        elif self.entry == 2:
            return "反向"
        else:
            return "未知"

    def _is_auto_order(self) -> bool:
        """判断是否为自动下单（排除MT5系统标记）"""
        if not self.comment or not self.comment.strip():
            return False
        # 排除MT5系统标记
        comment = self.comment.strip()
        if comment.startswith('[sl') or comment.startswith('[tp') or comment.startswith('[so'):
            return False
        return True

    def _get_order_source(self) -> str:
        """获取订单来源"""
        if not self.comment or not self.comment.strip():
            return "手动"
        comment = self.comment.strip()
        if comment.startswith('[sl'):
            return "止损触发"
        if comment.startswith('[tp'):
            return "止盈触发"
        if comment.startswith('[so'):
            return "强制平仓"
        return "自动"


class TradeHistoryStore:
    """交易历史存储"""

    def __init__(self):
        # 存储成交记录
        self._deals: List[TradeDeal] = []
        self._lock = threading.RLock()

        # 上次更新时间
        self._last_update_time: Optional[datetime] = None

        print("[TradeHistoryStore] 交易历史存储已初始化")

    def update_from_ea(self, deals_data: List[Dict]) -> int:
        """
        从EA数据更新交易历史

        Args:
            deals_data: EA返回的成交列表

        Returns:
            更新的成交数量
        """
        if not deals_data:
            return 0

        now = datetime.now()
        new_deals = []

        with self._lock:
            # 获取现有票据集合
            existing_tickets = {d.ticket for d in self._deals}

            for deal_data in deals_data:
                ticket = deal_data.get('ticket')
                if ticket in existing_tickets:
                    continue

                # 解析时间
                deal_time = deal_data.get('time')
                if isinstance(deal_time, str):
                    try:
                        deal_time = datetime.strptime(deal_time, '%Y.%m.%d %H:%M:%S')
                    except:
                        try:
                            deal_time = datetime.strptime(deal_time, '%Y-%m-%d %H:%M:%S')
                        except:
                            deal_time = now
                elif not isinstance(deal_time, datetime):
                    deal_time = now

                deal = TradeDeal(
                    ticket=ticket,
                    order=deal_data.get('order', 0),
                    symbol=deal_data.get('symbol', ''),
                    type=deal_data.get('type', 0),
                    entry=deal_data.get('entry', 0),
                    volume=deal_data.get('volume', 0),
                    price=deal_data.get('price', 0),
                    profit=deal_data.get('profit', 0),
                    swap=deal_data.get('swap', 0),
                    commission=deal_data.get('commission', 0),
                    time=deal_time,
                    comment=deal_data.get('comment', '')
                )
                new_deals.append(deal)

            # 添加新记录
            self._deals.extend(new_deals)

            # 按时间排序
            self._deals.sort(key=lambda d: d.time or datetime.min, reverse=True)

            # 保留最近24小时的数据
            cutoff = now - timedelta(hours=24)
            self._deals = [d for d in self._deals if d.time and d.time > cutoff]

            self._last_update_time = now

        if new_deals:
            print(f"[TradeHistoryStore] 新增 {len(new_deals)} 条成交记录，当前共 {len(self._deals)} 条")

        return len(new_deals)

    def get_all_deals(self) -> List[Dict]:
        """获取所有成交记录"""
        with self._lock:
            return [d.to_dict() for d in self._deals]

    def get_statistics(self) -> Dict:
        """
        获取交易统计

        Returns:
            统计数据
        """
        with self._lock:
            total_count = len(self._deals)
            if total_count == 0:
                return {
                    "total_count": 0,
                    "symbols": {},
                    "manual_count": 0,
                    "auto_count": 0,
                    "sl_tp_count": 0,
                    "so_count": 0,
                    "auto_categories": {},
                    "total_profit": 0,
                    "total_swap": 0,
                    "total_commission": 0,
                    "net_profit": 0,
                    "last_update": None
                }

            # 按品种统计
            symbols = defaultdict(lambda: {"count": 0, "profit": 0, "volume": 0})

            # 手动/自动/止损止盈/强制平仓统计
            manual_count = 0
            auto_count = 0
            sl_tp_count = 0  # 止损/止盈触发
            so_count = 0     # 强制平仓
            auto_categories = defaultdict(lambda: {"count": 0, "profit": 0})

            total_profit = 0
            total_swap = 0
            total_commission = 0

            for deal in self._deals:
                # 品种统计
                symbols[deal.symbol]["count"] += 1
                symbols[deal.symbol]["profit"] += deal.profit
                symbols[deal.symbol]["volume"] += deal.volume

                # 分类统计
                comment = deal.comment.strip() if deal.comment else ""

                if not comment:
                    # 无备注：手动单
                    manual_count += 1
                elif comment.startswith('[sl') or comment.startswith('[tp'):
                    # 止损/止盈触发
                    sl_tp_count += 1
                elif comment.startswith('[so'):
                    # 强制平仓
                    so_count += 1
                else:
                    # 自动单：使用完整备注作为分类
                    auto_count += 1
                    auto_categories[comment]["count"] += 1
                    auto_categories[comment]["profit"] += deal.profit

                # 总计
                total_profit += deal.profit
                total_swap += deal.swap
                total_commission += deal.commission

            net_profit = total_profit + total_swap - total_commission

            # 转换auto_categories为普通字典并计算
            auto_categories_dict = {}
            for cat, data in auto_categories.items():
                auto_categories_dict[cat] = {
                    "count": data["count"],
                    "profit": round(data["profit"], 2),
                    "percentage": round(data["count"] / auto_count * 100, 1) if auto_count > 0 else 0
                }

            # 转换symbols为普通字典
            symbols_dict = {}
            for sym, data in symbols.items():
                symbols_dict[sym] = {
                    "count": data["count"],
                    "profit": round(data["profit"], 2),
                    "volume": round(data["volume"], 2)
                }

            return {
                "total_count": total_count,
                "symbols": symbols_dict,
                "manual_count": manual_count,
                "auto_count": auto_count,
                "sl_tp_count": sl_tp_count,
                "so_count": so_count,
                "auto_categories": auto_categories_dict,
                "total_profit": round(total_profit, 2),
                "total_swap": round(total_swap, 2),
                "total_commission": round(total_commission, 2),
                "net_profit": round(net_profit, 2),
                "last_update": self._last_update_time.isoformat() if self._last_update_time else None
            }

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "deals_count": len(self._deals),
                "last_update": self._last_update_time.isoformat() if self._last_update_time else None
            }

    def clear(self) -> None:
        """清空数据"""
        with self._lock:
            self._deals.clear()
            self._last_update_time = None
            print("[TradeHistoryStore] 已清空交易历史数据")


# 全局单例
_trade_history_store = None


def get_trade_history_store() -> TradeHistoryStore:
    """获取交易历史存储单例"""
    global _trade_history_store
    if _trade_history_store is None:
        _trade_history_store = TradeHistoryStore()
    return _trade_history_store