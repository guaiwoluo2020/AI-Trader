#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
待确认订单存储模块
"""

from typing import List, Dict, Optional
from datetime import datetime
import threading
from collections import defaultdict

from ..models import PendingOrder


class PendingOrderStore:
    """待确认订单存储（只负责数据CRUD）"""

    def __init__(self, timeout_seconds: int = 180):
        # 按品种分类的订单: {symbol: [PendingOrder, ...]}
        self._orders_by_symbol: Dict[str, List[PendingOrder]] = defaultdict(list)

        # 按ID索引
        self._orders_by_id: Dict[str, PendingOrder] = {}

        # 线程锁
        self._lock = threading.RLock()

        # 超时时间
        self.timeout_seconds = timeout_seconds

        print("[PendingOrderStore] 待确认订单存储已初始化")

    # ==================== 添加订单 ====================

    def add_order(self, order: PendingOrder) -> str:
        """
        添加待确认订单

        Args:
            order: 待确认订单对象

        Returns:
            订单ID
        """
        with self._lock:
            # 设置超时时间
            order.TIMEOUT_SECONDS = self.timeout_seconds
            order.expires_at = order.created_at + __import__('datetime').timedelta(seconds=self.timeout_seconds)

            # 存储到两个字典
            self._orders_by_symbol[order.symbol].append(order)
            self._orders_by_id[order.order_id] = order

            print(f"[PendingOrderStore] 添加订单: {order.order_id} {order.symbol} {order.action}")
            return order.order_id

    def add_order_from_dict(self, data: Dict) -> str:
        """从字典添加订单"""
        order = PendingOrder.from_dict(data)
        return self.add_order(order)

    # ==================== 查询订单 ====================

    def get_order_by_id(self, order_id: str) -> Optional[PendingOrder]:
        """根据ID获取订单"""
        with self._lock:
            return self._orders_by_id.get(order_id)

    def get_pending_orders(self, symbol: str = None) -> List[PendingOrder]:
        """
        获取待确认订单列表

        Args:
            symbol: 品种，None返回所有

        Returns:
            订单列表
        """
        with self._lock:
            if symbol:
                orders = list(self._orders_by_symbol.get(symbol, []))
            else:
                orders = list(self._orders_by_id.values())

            # 按创建时间倒序
            return sorted(orders, key=lambda x: x.created_at, reverse=True)

    def get_pending_orders_dict(self, symbol: str = None) -> List[Dict]:
        """获取待确认订单字典列表"""
        orders = self.get_pending_orders(symbol)
        return [o.to_dict() for o in orders]

    def get_pending_count(self, symbol: str = None) -> int:
        """获取待确认订单数量"""
        with self._lock:
            if symbol:
                return len([o for o in self._orders_by_symbol.get(symbol, []) if o.is_pending()])
            return len([o for o in self._orders_by_id.values() if o.is_pending()])

    # ==================== 更新订单状态 ====================

    def confirm_order(self, order_id: str) -> Optional[PendingOrder]:
        """
        确认订单

        Args:
            order_id: 订单ID

        Returns:
            确认后的订单，不存在返回None
        """
        with self._lock:
            order = self._orders_by_id.get(order_id)
            if not order:
                return None

            # 从存储中移除
            symbol = order.symbol
            self._orders_by_symbol[symbol] = [
                o for o in self._orders_by_symbol[symbol] if o.order_id != order_id
            ]
            del self._orders_by_id[order_id]

            # 标记确认
            order.confirm()

            print(f"[PendingOrderStore] 订单已确认: {order_id}")
            return order

    def reject_order(self, order_id: str) -> Optional[PendingOrder]:
        """
        拒绝订单

        Args:
            order_id: 订单ID

        Returns:
            被拒绝的订单，不存在返回None
        """
        with self._lock:
            order = self._orders_by_id.get(order_id)
            if not order:
                return None

            # 从存储中移除
            symbol = order.symbol
            self._orders_by_symbol[symbol] = [
                o for o in self._orders_by_symbol[symbol] if o.order_id != order_id
            ]
            del self._orders_by_id[order_id]

            # 标记拒绝
            order.reject()

            print(f"[PendingOrderStore] 订单已拒绝: {order_id}")
            return order

    # ==================== 清理过期订单 ====================

    def cleanup_expired(self) -> List[PendingOrder]:
        """
        清理过期订单

        Returns:
            过期的订单列表
        """
        expired_orders = []
        current_time = datetime.now()

        with self._lock:
            expired_ids = []
            for order_id, order in self._orders_by_id.items():
                if order.is_expired() and order.status == "pending":
                    order.mark_expired()
                    expired_orders.append(order)
                    expired_ids.append(order_id)

            # 从存储中移除
            for order in expired_orders:
                symbol = order.symbol
                self._orders_by_symbol[symbol] = [
                    o for o in self._orders_by_symbol[symbol] if o.order_id != order.order_id
                ]
                del self._orders_by_id[order.order_id]

        if expired_orders:
            print(f"[PendingOrderStore] 清理过期订单: {len(expired_orders)}条")

        return expired_orders

    # ==================== 清空 ====================

    def clear_all(self) -> int:
        """清空所有待确认订单"""
        with self._lock:
            count = len(self._orders_by_id)
            self._orders_by_symbol.clear()
            self._orders_by_id.clear()
            print(f"[PendingOrderStore] 已清空所有订单: {count}条")
            return count

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            pending_count = len([o for o in self._orders_by_id.values() if o.is_pending()])
            return {
                "total_orders": len(self._orders_by_id),
                "pending_count": pending_count,
                "symbols": list(self._orders_by_symbol.keys()),
            }