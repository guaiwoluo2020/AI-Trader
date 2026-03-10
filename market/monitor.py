#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点监控模块
实时监控价格与转折点的接近程度，并通过WebSocket推送提醒
"""

from typing import Dict, List, Optional, Set
from datetime import datetime
import threading
import asyncio
import json

from .store import MarketStore, normalize_symbol
from .pivot_detector import PivotDetector
from .pending_orders import PendingOrderManager


# 交易配置
class TradeConfig:
    """交易配置"""
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.enabled = True  # 是否启用自动生成

        # 默认配置
        self.default_volume = 0.01  # 默认手数
        self.default_sl_offset = 0.05  # 默认止损偏移（固定点数）

        # 按品种配置: {symbol: {"volume": 0.01, "sl_offset": 0.05}}
        self.symbol_config = {
            "GOLD#": {"volume": 0.01, "sl_offset": 0.5},
            "OILCASH#": {"volume": 0.01, "sl_offset": 0.05},
        }

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_symbol_config(self, symbol: str) -> Dict:
        """获取品种配置，如果未配置则返回默认值"""
        symbol = symbol.upper()
        if symbol in self.symbol_config:
            config = self.symbol_config[symbol]
            return {
                "volume": config.get("volume", self.default_volume),
                "sl_offset": config.get("sl_offset", self.default_sl_offset)
            }
        return {
            "volume": self.default_volume,
            "sl_offset": self.default_sl_offset
        }

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "default_volume": self.default_volume,
            "default_sl_offset": self.default_sl_offset,
            "symbol_config": self.symbol_config
        }

    def update(self, data: Dict):
        if "enabled" in data:
            self.enabled = bool(data["enabled"])
        if "default_volume" in data:
            self.default_volume = float(data["default_volume"])
        if "default_sl_offset" in data:
            self.default_sl_offset = float(data["default_sl_offset"])
        if "symbol_config" in data:
            self.symbol_config = data["symbol_config"]


class PivotMonitor:
    """转折点监控器"""

    def __init__(self, store: MarketStore, detector: PivotDetector,
                 pending_orders: PendingOrderManager = None):
        self.store = store
        self.detector = detector
        self.pending_orders = pending_orders
        self.trade_config = TradeConfig.get_instance()

        # WebSocket连接管理
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()

        # 已提醒的转折点（避免重复提醒）
        # 结构: {(symbol, period, timestamp, price): datetime}
        self._alerted_pivots: Dict[tuple, datetime] = {}
        self._alert_lock = threading.Lock()

        # 提醒冷却时间（秒）
        self.alert_cooldown = 300  # 5分钟内同一转折点不重复提醒

        print("[PivotMonitor] 转折点监控器已初始化")

    def check_and_alert(self, symbol: str, current_price: float) -> List[Dict]:
        """
        检查价格是否接近转折点，并发送提醒

        Args:
            symbol: 交易品种
            current_price: 当前价格

        Returns:
            接近的转折点列表
        """
        symbol = normalize_symbol(symbol)

        # 检查是否接近转折点
        near_pivots = self.detector.check_near_pivot(symbol, current_price)

        if not near_pivots:
            return []

        # 过滤已提醒过的转折点
        new_alerts = []
        current_time = datetime.now()

        with self._alert_lock:
            for pivot in near_pivots:
                key = (
                    pivot['symbol'],
                    pivot['period'],
                    pivot['timestamp'],
                    pivot['price']
                )

                # 检查是否已提醒过
                if key in self._alerted_pivots:
                    last_alert = self._alerted_pivots[key]
                    elapsed = (current_time - last_alert).total_seconds()

                    # 如果在冷却时间内，跳过
                    if elapsed < self.alert_cooldown:
                        continue

                # 记录提醒时间
                self._alerted_pivots[key] = current_time

                # 构建提醒消息
                is_breakthrough = pivot.get('is_breakthrough', False)
                alert_type = pivot.get('alert_type', '')
                period = pivot['period']

                # 根据类型生成不同的消息
                if is_breakthrough:
                    if 'high' in alert_type:
                        message = f"{pivot['symbol']} {period} 已突破高点 {pivot['price']}, 当前价格 {pivot['current_price']}"
                    else:
                        message = f"{pivot['symbol']} {period} 已突破低点 {pivot['price']}, 当前价格 {pivot['current_price']}"
                else:
                    if 'high' in alert_type:
                        message = f"{pivot['symbol']} {period} 接近高点 {pivot['price']}, 当前价格 {pivot['current_price']}, 距离 {pivot['distance_pct']}%"
                    else:
                        message = f"{pivot['symbol']} {period} 接近低点 {pivot['price']}, 当前价格 {pivot['current_price']}, 距离 {pivot['distance_pct']}%"

                alert = {
                    "type": "pivot_alert",
                    "symbol": pivot['symbol'],
                    "period": period,
                    "direction": pivot['direction'],
                    "pivot_price": pivot['price'],
                    "current_price": pivot['current_price'],
                    "distance_pct": pivot['distance_pct'],
                    "threshold_pct": pivot['threshold_pct'],
                    "timestamp": current_time.isoformat(),
                    "alert_type": alert_type,
                    "is_breakthrough": is_breakthrough,
                    "message": message
                }

                # M1和M5周期接近转折点时，自动生成交易指令
                pending_order = None
                if period in ['M1', 'M5'] and not is_breakthrough:
                    pending_order = self._auto_generate_order(pivot, current_time)

                # 如果生成了订单，加入通知中
                if pending_order:
                    alert["pending_order"] = pending_order

                new_alerts.append(alert)

                # 异步推送WebSocket消息
                self._broadcast_alert(alert)

        # 清理过期的提醒记录
        self._cleanup_alerted()

        return new_alerts

    def _auto_generate_order(self, pivot: Dict, current_time: datetime) -> Optional[Dict]:
        """
        M1周期接近转折点时，自动生成交易指令

        Args:
            pivot: 转折点信息
            current_time: 当前时间

        Returns:
            生成的订单信息，包含order_id
        """
        if not self.pending_orders:
            return None

        if not self.trade_config.enabled:
            return None

        symbol = pivot['symbol']
        current_price = pivot['current_price']
        pivot_price = pivot['price']
        direction = pivot['direction']
        alert_type = pivot['alert_type']

        # 只处理"接近"类型（near_high, near_low）
        if not alert_type.startswith('near_'):
            return None

        # 获取品种配置
        config = self.trade_config.get_symbol_config(symbol)
        volume = config["volume"]
        sl_offset = config["sl_offset"]  # 固定点数偏移

        order = None

        if alert_type == 'near_low':
            # 接近低点 → 买入
            # 止损 = 低点 - 配置的偏移
            sl = pivot_price - sl_offset
            # 止盈 = 最近的高点
            tp = self._find_nearest_pivot_price(symbol, 'high', current_price)

            if tp and tp > current_price:
                order = {
                    "symbol": symbol,
                    "action": "b",  # 买入
                    "price": current_price,
                    "mount": volume,
                    "sl": round(sl, 2),
                    "tp": round(tp, 2),
                    "reason": f"M1接近低点{pivot_price:.2f}，建议买入，止损{sl:.2f}，止盈{tp:.2f}",
                    "source": "auto_pivot_m1",
                    "pivot_price": pivot_price,
                    "generated_at": current_time.isoformat()
                }

        elif alert_type == 'near_high':
            # 接近高点 → 卖出
            # 止损 = 高点 + 配置的偏移
            sl = pivot_price + sl_offset
            # 止盈 = 最近的低点
            tp = self._find_nearest_pivot_price(symbol, 'low', current_price)

            if tp and tp < current_price:
                order = {
                    "symbol": symbol,
                    "action": "s",  # 卖出
                    "price": current_price,
                    "mount": volume,
                    "sl": round(sl, 2),
                    "tp": round(tp, 2),
                    "reason": f"M1接近高点{pivot_price:.2f}，建议卖出，止损{sl:.2f}，止盈{tp:.2f}",
                    "source": "auto_pivot_m1",
                    "pivot_price": pivot_price,
                    "generated_at": current_time.isoformat()
                }

        if order:
            order_id = self.pending_orders.add_order(order)
            print(f"[PivotMonitor] 自动生成交易指令: {order_id} - {order['action']} {symbol} @ {current_price}")
            # 返回订单信息（包含order_id）
            order["order_id"] = order_id
            return order

        return None

    def _find_nearest_pivot_price(self, symbol: str, direction: str,
                                   current_price: float) -> Optional[float]:
        """
        找到离当前价格最近的转折点价格

        Args:
            symbol: 交易品种
            direction: 'high' 或 'low'
            current_price: 当前价格

        Returns:
            最近的转折点价格，如果没有返回None
        """
        symbol = normalize_symbol(symbol)
        nearest_price = None
        min_distance = float('inf')

        with self.detector._lock:
            for period in self.detector._pivots[symbol]:
                pivots = self.detector._pivots[symbol][period]

                for pivot in pivots:
                    if pivot.direction != direction:
                        continue

                    # 对于高点，只考虑价格高于当前价的
                    # 对于低点，只考虑价格低于当前价的
                    if direction == 'high' and pivot.price <= current_price:
                        continue
                    if direction == 'low' and pivot.price >= current_price:
                        continue

                    distance = abs(pivot.price - current_price)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_price = pivot.price

        return nearest_price

    def _broadcast_new_order(self, order_id: str, order: Dict) -> None:
        """广播新订单通知"""
        message = json.dumps({
            "type": "new_order",
            "order_id": order_id,
            "order": order
        })

        with self._ws_lock:
            clients = list(self._ws_clients)

        for client in clients:
            try:
                asyncio.create_task(self._send_to_client(client, message))
            except Exception as e:
                print(f"[PivotMonitor] 发送新订单通知失败: {e}")

    def _cleanup_alerted(self):
        """清理过期的提醒记录"""
        current_time = datetime.now()

        with self._alert_lock:
            keys_to_remove = []
            for key, alert_time in self._alerted_pivots.items():
                elapsed = (current_time - alert_time).total_seconds()
                if elapsed > self.alert_cooldown * 2:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._alerted_pivots[key]

    def _broadcast_alert(self, alert: Dict):
        """广播提醒到所有WebSocket客户端"""
        message = json.dumps(alert)

        with self._ws_lock:
            clients = list(self._ws_clients)

        # 在事件循环中发送消息
        for client in clients:
            try:
                asyncio.create_task(self._send_to_client(client, message))
            except Exception as e:
                print(f"[PivotMonitor] 发送WebSocket消息失败: {e}")

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[PivotMonitor] 发送消息到客户端失败: {e}")
            # 移除失效的客户端
            with self._ws_lock:
                self._ws_clients.discard(client)

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            print(f"[PivotMonitor] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            print(f"[PivotMonitor] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def get_ws_client_count(self) -> int:
        """获取WebSocket客户端数量"""
        with self._ws_lock:
            return len(self._ws_clients)

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的提醒记录"""
        symbol = normalize_symbol(symbol)

        with self._alert_lock:
            keys_to_remove = [k for k in self._alerted_pivots if k[0] == symbol]
            for key in keys_to_remove:
                del self._alerted_pivots[key]

    def get_status(self) -> Dict:
        """获取监控状态"""
        with self._alert_lock:
            alerted_count = len(self._alerted_pivots)

        return {
            "ws_clients": self.get_ws_client_count(),
            "alerted_pivots": alerted_count,
            "alert_cooldown": self.alert_cooldown
        }