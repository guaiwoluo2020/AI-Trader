#!/usr/bin/env python3
"""Persistent structured system events with tenant-safe live delivery."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, List, Optional

from system_event_log import SystemEventLogRepository


EVENT_METADATA = {
    "llm_analysis_start": ("大模型分析开始", "ai", "info"),
    "llm_analysis_complete": ("大模型分析完成", "ai", "info"),
    "llm_analysis_error": ("大模型分析错误", "ai", "error"),
    "ea_statistics": ("EA 账户数据上报", "integration", "info"),
    "ea_kline_full": ("EA 全量 K 线上报", "integration", "info"),
    "ea_kline_incremental": ("EA 增量 K 线上报", "integration", "info"),
    "ea_kline_stale": ("K 线数据过期", "integration", "warning"),
    "ea_trade_request": ("EA 获取交易指令", "integration", "info"),
    "trade_execution_success": ("MT5 成交成功", "trading", "info"),
    "trade_execution_failed": ("MT5 下单失败", "trading", "error"),
    "trade_history_update": ("交易历史上报", "integration", "info"),
    "pivot_detected": ("转折点检测完成", "market", "info"),
    "pivot_alert": ("转折点提醒", "market", "warning"),
    "order_generated": ("交易指令生成", "trading", "info"),
    "strategy_decision_created": ("策略决策生成", "trading", "info"),
    "structure_plan_created": ("结构交易计划生成", "market", "info"),
    "structure_plan_invalidated": ("结构交易计划失效", "market", "warning"),
    "order_confirmed": ("交易指令确认", "trading", "info"),
    "order_rejected": ("交易指令拒绝", "trading", "warning"),
    "close_position": ("平仓指令", "trading", "warning"),
    "position_update": ("持仓数据更新", "trading", "info"),
    "risk_blocked": ("风控拦截", "risk", "warning"),
    "system_startup": ("系统启动", "system", "info"),
    "system_shutdown": ("系统关闭", "system", "warning"),
    "websocket_connect": ("WebSocket 连接", "integration", "info"),
    "websocket_disconnect": ("WebSocket 断开", "integration", "warning"),
}


class SystemLogBroadcaster:
    def __init__(self):
        self._clients: Dict[Any, Dict] = {}
        self._lock = threading.RLock()
        self._loop = None

    def set_event_loop(self, loop) -> None:
        self._loop = loop

    def add(self, client, user_id: int, account_id: Optional[int], is_admin: bool) -> None:
        with self._lock:
            self._clients[client] = {
                "user_id": int(user_id), "account_id": account_id,
                "is_admin": bool(is_admin),
            }

    def remove(self, client) -> None:
        with self._lock:
            self._clients.pop(client, None)

    def publish(self, event: Dict) -> None:
        if self._loop is None:
            return
        with self._lock:
            clients = list(self._clients.items())
        message = json.dumps({"type": "system_event", "data": event}, ensure_ascii=False)
        for client, scope in clients:
            visible = scope["is_admin"] or (
                event.get("user_id") == scope["user_id"]
                and (
                    scope["account_id"] is None
                    or event.get("account_id") == scope["account_id"]
                )
            )
            if not visible:
                continue
            try:
                asyncio.run_coroutine_threadsafe(client.send_text(message), self._loop)
            except Exception:
                self.remove(client)


_broadcaster = SystemLogBroadcaster()


def get_system_log_broadcaster() -> SystemLogBroadcaster:
    return _broadcaster


class SystemLog:
    """Compatibility facade backed by the persistent event repository."""

    EVENT_TYPES = {key: value[0] for key, value in EVENT_METADATA.items()}

    def __init__(
        self, max_size: int = 200, user_id: int = None, account_id: int = None,
    ):
        self.user_id = user_id
        self.account_id = account_id
        self.repository = SystemEventLogRepository()
        self._legacy_ws_clients = set()
        print("[SystemLog] MySQL 事件日志已初始化")

    def set_scope(self, user_id: int = None, account_id: int = None):
        self.user_id = user_id
        self.account_id = account_id

    def set_event_loop(self, loop):
        _broadcaster.set_event_loop(loop)

    def add_log(
        self, event_type: str, detail: Dict[str, Any] = None,
        symbol: str = None, message: str = None, **context,
    ) -> Dict:
        event_name, category, default_level = EVENT_METADATA.get(
            event_type,
            (event_type, self._infer_category(event_type), self._infer_level(event_type)),
        )
        detail = detail or {}
        try:
            event = self.repository.add({
                "user_id": self.user_id,
                "account_id": self.account_id,
                "event_type": event_type,
                "event_name": event_name,
                "category": context.get("category") or category,
                "level": context.get("level") or default_level,
                "symbol": symbol,
                "message": message,
                "detail": detail,
                "status": context.get("status") or detail.get("status") or "",
                "actor_type": context.get("actor_type") or "system",
                "actor_id": context.get("actor_id") or "",
                "entity_type": context.get("entity_type") or "",
                "entity_id": context.get("entity_id") or detail.get("order_id") or "",
                "correlation_id": context.get("correlation_id") or detail.get("correlation_id") or detail.get("order_id") or "",
                "request_id": context.get("request_id") or "",
            })
        except Exception as exc:
            print(f"[SystemLog] 事件写入失败，不阻断业务流程: {exc}")
            return {}
        _broadcaster.publish(event)
        scope = f"user={self.user_id}, account={self.account_id}" if self.user_id else "platform"
        print(f"[SystemLog:{scope}] {event['timestamp']} | {event_name} | {message or ''}")
        return event

    def get_logs(
        self, count: int = 50, event_types: List[str] = None,
        symbol: str = None,
    ) -> List[Dict]:
        return self.repository.list({
            "user_id": self.user_id,
            "account_id": self.account_id,
            "event_types": event_types or [],
            "symbol": symbol,
            "page_size": count,
        })["items"]

    def clear_logs(self):
        raise PermissionError("持久化审计日志不支持按账户清空")

    def add_ws_client(self, client):
        self._legacy_ws_clients.add(client)

    def remove_ws_client(self, client):
        self._legacy_ws_clients.discard(client)

    def close(self):
        self._legacy_ws_clients.clear()

    @staticmethod
    def _infer_level(event_type: str) -> str:
        value = str(event_type).lower()
        if any(word in value for word in ("error", "failed", "failure")):
            return "error"
        if any(word in value for word in ("warning", "stale", "rejected", "blocked")):
            return "warning"
        return "info"

    @staticmethod
    def _infer_category(event_type: str) -> str:
        value = str(event_type).lower()
        if value.startswith("llm") or value.startswith("ai_"):
            return "ai"
        if value.startswith(("order", "trade", "position", "close")):
            return "trading"
        if value.startswith("ea_") or "websocket" in value:
            return "integration"
        if value.startswith("risk"):
            return "risk"
        return "system"


_system_log = None


def get_system_log() -> SystemLog:
    global _system_log
    if _system_log is None:
        _system_log = SystemLog()
    return _system_log
