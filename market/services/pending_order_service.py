#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
待确认订单服务模块
"""

from typing import List, Dict, Optional, Callable
from datetime import datetime
import threading

from ..models import PendingOrder
from ..store import PendingOrderStore


class PendingOrderService:
    """待确认订单服务（处理业务逻辑）"""

    def __init__(self, pending_order_store: PendingOrderStore = None):
        self.store = pending_order_store or PendingOrderStore()

        # 订单确认回调（确认后将指令加入交易队列）
        self._confirm_callback: Optional[Callable] = None

        # 启动超时清理线程
        self._start_cleanup_thread()

        print("[PendingOrderService] 待确认订单服务已初始化")

    def set_confirm_callback(self, callback: Callable):
        """
        设置订单确认回调函数

        回调签名: callback(order: PendingOrder) -> None
        """
        self._confirm_callback = callback

    def _start_cleanup_thread(self):
        """启动超时清理线程"""
        def cleanup_loop():
            while True:
                try:
                    expired = self.store.cleanup_expired()
                    for order in expired:
                        print(f"[PendingOrderService] 订单超时自动移除: {order.order_id}")
                except Exception as e:
                    print(f"[PendingOrderService] 清理线程异常: {e}")
                threading.Event().wait(10)  # 每10秒检查一次

        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()

    # ==================== 创建订单 ====================

    def create_order(self, symbol: str, action: str, price: float,
                     mount: float, sl: float, tp: float,
                     reason: str = "", description: str = "",
                     source: str = "", **kwargs) -> str:
        """
        创建待确认订单

        Args:
            symbol: 品种
            action: 方向 (b/s)
            price: 入场价
            mount: 手数
            sl: 止损
            tp: 止盈
            reason: 原因
            description: 描述
            source: 来源
            **kwargs: 其他字段（pivot_price, key_level, ai_period等）

        Returns:
            订单ID
        """
        order = PendingOrder(
            symbol=symbol,
            action=action,
            price=price,
            mount=mount,
            sl=sl,
            tp=tp,
            reason=reason,
            description=description,
            source=source,
            **kwargs
        )
        return self.store.add_order(order)

    def create_order_from_dict(self, data: Dict) -> str:
        """从字典创建订单"""
        return self.store.add_order_from_dict(data)

    # ==================== 查询订单 ====================

    def get_order(self, order_id: str) -> Optional[PendingOrder]:
        """获取订单"""
        return self.store.get_order_by_id(order_id)

    def get_orders(self, symbol: str = None) -> List[PendingOrder]:
        """获取订单列表"""
        return self.store.get_pending_orders(symbol)

    def get_orders_dict(self, symbol: str = None) -> List[Dict]:
        """获取订单字典列表"""
        return self.store.get_pending_orders_dict(symbol)

    # 兼容旧方法名
    def get_pending_orders_dict(self, symbol: str = None) -> List[Dict]:
        """获取订单字典列表（兼容旧方法名）"""
        return self.get_orders_dict(symbol)

    def get_pending_count(self, symbol: str = None) -> int:
        """获取待确认订单数量"""
        return self.store.get_pending_count(symbol)

    # ==================== 确认/拒绝订单 ====================

    def confirm_order(self, order_id: str, updates: Dict = None) -> Optional[PendingOrder]:
        """
        确认订单

        Args:
            order_id: 订单ID
            updates: 更新字段（如 mount, sl, tp）

        Returns:
            确认后的订单
        """
        # 先获取订单
        order = self.store.get_order_by_id(order_id)
        if not order:
            return None

        # 应用更新
        if updates:
            if 'mount' in updates:
                order.mount = updates['mount']
            if 'sl' in updates:
                order.sl = updates['sl']
            if 'tp' in updates:
                order.tp = updates['tp']

        # 确认订单（从存储中移除）
        confirmed_order = self.store.confirm_order(order_id)
        if not confirmed_order:
            return None

        # 调用确认回调
        if self._confirm_callback:
            try:
                self._confirm_callback(confirmed_order)
            except Exception as e:
                print(f"[PendingOrderService] 确认回调执行失败: {e}")

        return confirmed_order

    def reject_order(self, order_id: str) -> Optional[PendingOrder]:
        """拒绝订单"""
        return self.store.reject_order(order_id)

    # ==================== 清理 ====================

    def clear_all(self) -> int:
        """清空所有待确认订单"""
        return self.store.clear_all()

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "store": self.store.get_status(),
            "callback_set": self._confirm_callback is not None,
        }