#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
待确认订单管理模块
存储交易员待确认的交易指令
"""

from collections import defaultdict
from typing import List, Dict, Optional, Callable
from datetime import datetime, timedelta
import threading
import uuid


class PendingOrderManager:
    """待确认订单管理器"""

    # 订单超时时间（秒）
    ORDER_TIMEOUT = 180  # 3分钟

    def __init__(self):
        # 待确认订单: {SYMBOL: [Order, ...]}
        self._pending_orders = defaultdict(list)
        self._lock = threading.RLock()

        # 订单ID到订单的映射
        self._order_by_id = {}

        # 订单确认回调（确认后将订单加入交易队列）
        self._confirm_callback: Optional[Callable] = None

        # 启动超时清理线程
        self._start_cleanup_thread()

        print("[PendingOrderManager] 待确认订单管理器已初始化")

    def set_confirm_callback(self, callback: Callable):
        """设置订单确认回调函数"""
        self._confirm_callback = callback

    def _start_cleanup_thread(self):
        """启动超时清理线程"""
        def cleanup_loop():
            while True:
                try:
                    self._cleanup_expired_orders()
                except Exception as e:
                    print(f"[PendingOrderManager] 清理线程异常: {e}")
                threading.Event().wait(10)  # 每10秒检查一次

        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()

    def _cleanup_expired_orders(self):
        """清理超时订单"""
        current_time = datetime.now()
        expired_orders = []

        with self._lock:
            for order_id, order in list(self._order_by_id.items()):
                created_at = datetime.fromisoformat(order['created_at'])
                elapsed = (current_time - created_at).total_seconds()

                if elapsed > self.ORDER_TIMEOUT:
                    expired_orders.append(order_id)

            for order_id in expired_orders:
                order = self._order_by_id[order_id]
                symbol = order.get('symbol', 'UNKNOWN')

                self._pending_orders[symbol] = [
                    o for o in self._pending_orders[symbol] if o['order_id'] != order_id
                ]
                del self._order_by_id[order_id]

                print(f"[PendingOrderManager] 订单超时自动移除: {order_id}")

    def add_order(self, order: Dict) -> str:
        """
        添加待确认订单

        Args:
            order: 订单信息

        Returns:
            订单ID
        """
        # 生成订单ID
        order_id = str(uuid.uuid4())[:8]

        order_with_id = {
            **order,
            "order_id": order_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(seconds=self.ORDER_TIMEOUT)).isoformat()
        }

        symbol = order.get('symbol', 'UNKNOWN')

        with self._lock:
            self._pending_orders[symbol].append(order_with_id)
            self._order_by_id[order_id] = order_with_id

        print(f"[PendingOrderManager] 添加待确认订单: {order_id} {symbol} {order.get('action')}")

        return order_id

    def confirm_order(self, order_id: str) -> Optional[Dict]:
        """
        确认订单（交易员确认后调用）

        Returns:
            确认后的订单，用于加入正式交易队列
        """
        with self._lock:
            if order_id not in self._order_by_id:
                return None

            order = self._order_by_id[order_id]
            symbol = order.get('symbol', 'UNKNOWN')

            # 从待确认列表中移除
            self._pending_orders[symbol] = [
                o for o in self._pending_orders[symbol] if o['order_id'] != order_id
            ]
            del self._order_by_id[order_id]

            # 标记为已确认
            order['status'] = 'confirmed'
            order['confirmed_at'] = datetime.now().isoformat()

            print(f"[PendingOrderManager] 订单已确认: {order_id}")

        # 调用确认回调（将订单加入交易队列）
        if self._confirm_callback:
            try:
                self._confirm_callback(order)
                print(f"[PendingOrderManager] 订单已加入交易队列: {order_id}")
            except Exception as e:
                print(f"[PendingOrderManager] 加入交易队列失败: {e}")

        return order

    def reject_order(self, order_id: str) -> bool:
        """
        拒绝订单（交易员点击放弃）

        Returns:
            是否成功
        """
        with self._lock:
            if order_id not in self._order_by_id:
                return False

            order = self._order_by_id[order_id]
            symbol = order.get('symbol', 'UNKNOWN')

            # 从待确认列表中移除
            self._pending_orders[symbol] = [
                o for o in self._pending_orders[symbol] if o['order_id'] != order_id
            ]
            del self._order_by_id[order_id]

            print(f"[PendingOrderManager] 订单已拒绝: {order_id}")

            return True

    def get_pending_orders(self, symbol: str = None) -> List[Dict]:
        """获取待确认订单列表"""
        with self._lock:
            if symbol:
                return list(self._pending_orders.get(symbol, []))
            else:
                # 返回所有
                orders = []
                for sym, order_list in self._pending_orders.items():
                    orders.extend(order_list)
                return sorted(orders, key=lambda x: x['created_at'], reverse=True)

    def get_order_by_id(self, order_id: str) -> Optional[Dict]:
        """根据ID获取订单"""
        with self._lock:
            return self._order_by_id.get(order_id)

    def get_pending_count(self, symbol: str = None) -> int:
        """获取待确认订单数量"""
        with self._lock:
            if symbol:
                return len(self._pending_orders.get(symbol, []))
            return len(self._order_by_id)

    def clear_all(self) -> int:
        """清空所有待确认订单"""
        with self._lock:
            count = len(self._order_by_id)
            self._pending_orders.clear()
            self._order_by_id.clear()
            return count