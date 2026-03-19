#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WebSocket 连接管理器
可复用的 WebSocket 客户端管理和消息广播
"""

import asyncio
import json
import threading
from typing import Set, Dict, Any, Optional


class WebSocketManager:
    """
    WebSocket 连接管理器

    职责：
    - 管理客户端连接（添加/移除）
    - 线程安全操作
    - 异步广播消息
    - 同步广播（从非异步上下文）
    """

    def __init__(self, name: str = "default"):
        self._name = name
        self._clients: Set = set()
        self._lock = threading.Lock()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        print(f"[WebSocketManager:{name}] 已初始化")

    # ==================== 事件循环 ====================

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print(f"[WebSocketManager:{self._name}] 已设置事件循环")

    # ==================== 客户端管理 ====================

    def add_client(self, client) -> int:
        """
        添加客户端连接

        Args:
            client: WebSocket 连接对象

        Returns:
            当前连接数
        """
        with self._lock:
            self._clients.add(client)
            count = len(self._clients)
        print(f"[WebSocketManager:{self._name}] 客户端已连接, 当前: {count}")
        return count

    def remove_client(self, client) -> int:
        """
        移除客户端连接

        Args:
            client: WebSocket 连接对象

        Returns:
            当前连接数
        """
        with self._lock:
            self._clients.discard(client)
            count = len(self._clients)
        print(f"[WebSocketManager:{self._name}] 客户端已断开, 当前: {count}")
        return count

    def get_client_count(self) -> int:
        """获取客户端数量"""
        with self._lock:
            return len(self._clients)

    # ==================== 消息广播 ====================

    async def broadcast(self, message: Dict[str, Any]):
        """
        异步广播消息到所有客户端

        Args:
            message: 消息字典，自动转为 JSON
        """
        with self._lock:
            clients = list(self._clients)

        if not clients:
            return

        text = json.dumps(message, ensure_ascii=False)

        for client in clients:
            try:
                await client.send_text(text)
            except Exception as e:
                print(f"[WebSocketManager:{self._name}] 发送失败: {e}")
                self.remove_client(client)

    async def send_to_client(self, client, message: Dict[str, Any]):
        """
        发送消息到单个客户端

        Args:
            client: WebSocket 连接对象
            message: 消息字典
        """
        try:
            text = json.dumps(message, ensure_ascii=False)
            await client.send_text(text)
        except Exception as e:
            print(f"[WebSocketManager:{self._name}] 发送到客户端失败: {e}")
            self.remove_client(client)

    def broadcast_sync(self, message: Dict[str, Any]):
        """
        同步方式广播（从非异步上下文调用）

        用于在线程中向主事件循环提交广播任务

        Args:
            message: 消息字典
        """
        if self._main_loop and self._main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.broadcast(message),
                self._main_loop
            )
        else:
            print(f"[WebSocketManager:{self._name}] 事件循环未运行，无法广播")

    # ==================== 状态查询 ====================

    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "name": self._name,
            "clients": self.get_client_count(),
            "loop_set": self._main_loop is not None
        }