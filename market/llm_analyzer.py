#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 分析器
负责定时调度和 WebSocket 广播
"""

import threading
import asyncio
import json
from datetime import datetime
from typing import Dict, Set, Optional

from .services import LLMService
class LLMAnalyzer:
    """LLM 分析器（调度 + WebSocket 广播）"""

    # 分析间隔（秒）
    ANALYZE_INTERVAL = 300  # 5分钟

    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service

        # WebSocket 连接管理
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()

        # 主事件循环引用
        self._main_loop = None
        self._analysis_lock = threading.Lock()

        if self.llm_service.is_enabled():
            print("[LLMAnalyzer] 大模型分析器已初始化（已启用）")
        else:
            print("[LLMAnalyzer] 大模型分析器已初始化（未配置API Key，功能禁用）")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print(f"[LLMAnalyzer] 已设置主事件循环")

    def stop(self):
        """清理 WebSocket 客户端。"""
        with self._ws_lock:
            self._ws_clients.clear()

    def _run_analysis(self, due_only: bool = False):
        """执行分析"""
        def on_status(status: str, message: str):
            self._broadcast_analysis_status(status, message)

        def on_complete(response):
            if response:
                self._broadcast_analysis_update()

        try:
            return self.llm_service.run_analysis(
                on_status=on_status,
                on_complete=on_complete,
                due_only=due_only,
            )
        except TypeError as exc:
            if "due_only" not in str(exc):
                raise
            return self.llm_service.run_analysis(
                on_status=on_status,
                on_complete=on_complete,
            )

    def run_scheduled_analysis(self) -> bool:
        """由共享调度器触发，避免同一账户并发分析。"""
        if not self.llm_service.is_enabled():
            return False
        if not self._analysis_lock.acquire(blocking=False):
            return False
        try:
            self._run_analysis(due_only=True)
            return True
        finally:
            self._analysis_lock.release()

    # ==================== 手动触发 ====================

    def trigger_analysis(self) -> Dict:
        """在后台线程中手动触发分析，避免阻塞 HTTP 请求。"""
        if not self.llm_service.is_enabled():
            return {"status": "error", "message": "大模型分析未启用"}

        if not self._analysis_lock.acquire(blocking=False):
            return {"status": "busy", "message": "大模型分析正在进行中"}

        self.llm_service.llm_store.set_analysis_status("queued", "分析任务已提交")

        def run_in_background():
            print("[LLMAnalyzer] 手动触发分析...")
            try:
                self._run_analysis()
            except Exception as e:
                message = f"分析任务异常: {e}"
                self.llm_service.llm_store.set_analysis_status("error", message)
                self._broadcast_analysis_status("error", message)
                print(f"[LLMAnalyzer] 手动触发分析失败: {e}")
            finally:
                self._analysis_lock.release()

        threading.Thread(
            target=run_in_background,
            name="llm-manual-analysis",
            daemon=True,
        ).start()
        return {"status": "accepted", "message": "分析任务已提交"}

    # ==================== 配置 ====================

    def configure(
        self, api_key: str = None, api_base: str = None, model: str = None,
        system_prompt: str = None, analysis_prompt_template: str = None,
    ) -> Dict:
        """配置 LLM 参数"""
        result = self.llm_service.configure(
            api_key, api_base, model, system_prompt, analysis_prompt_template
        )

        return result

    def get_config(self) -> Dict:
        """获取配置"""
        return self.llm_service.get_config()

    # ==================== 查询 ====================

    def get_analysis(self, symbol: str = None) -> Dict:
        """获取分析结果"""
        return self.llm_service.get_analysis(symbol)

    def get_status(self) -> Dict:
        """获取状态"""
        status = self.llm_service.get_status()
        status["interval_seconds"] = self.ANALYZE_INTERVAL
        return status

    def check_entry_price_nearby(
        self, symbol: str, current_price: float, threshold: float = 0.0001,
        strategy_id: str = "",
    ) -> list:
        """检查入场价是否接近"""
        return self.llm_service.check_entry_price_nearby(
            symbol, current_price, threshold, strategy_id
        )

    # ==================== WebSocket 管理 ====================

    def add_ws_client(self, client):
        """添加 WebSocket 客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            print(f"[LLMAnalyzer] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除 WebSocket 客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            print(f"[LLMAnalyzer] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def _broadcast_analysis_update(self):
        """广播分析更新通知"""
        message = json.dumps({
            "type": "llm_analysis_update",
            "timestamp": self.llm_service.llm_store.get_last_analysis_time(),
            "symbols": self.llm_service.llm_store.get_analyzed_symbols()
        })
        self._broadcast_message(message)

    def _broadcast_analysis_status(self, status: str, message: str):
        """广播分析状态更新"""
        msg = json.dumps({
            "type": "llm_analysis_status",
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        self._broadcast_message(msg)

    def _broadcast_message(self, message: str):
        """广播消息到所有 WebSocket 客户端"""
        with self._ws_lock:
            clients = list(self._ws_clients)

        if not clients:
            return

        if self._main_loop and self._main_loop.is_running():
            for client in clients:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._send_to_client(client, message),
                        self._main_loop
                    )
                except Exception as e:
                    print(f"[LLMAnalyzer] 广播消息失败: {e}")
        else:
            print(f"[LLMAnalyzer] 事件循环未就绪，跳过广播")

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[LLMAnalyzer] 发送消息到客户端失败: {e}")
            with self._ws_lock:
                self._ws_clients.discard(client)
