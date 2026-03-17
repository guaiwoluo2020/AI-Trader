#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统运行日志模块
保存在内存中，保留最新200条日志
"""

from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Any
import threading
import json


class SystemLog:
    """系统运行日志"""

    # 日志事件类型
    EVENT_TYPES = {
        # 大模型相关
        "llm_analysis_start": "大模型分析开始",
        "llm_analysis_complete": "大模型分析完成",
        "llm_analysis_error": "大模型分析错误",

        # EA数据推送
        "ea_statistics": "EA推送统计数据",
        "ea_kline_full": "EA推送全量K线",
        "ea_kline_incremental": "EA推送增量K线",
        "ea_kline_stale": "K线数据过期",
        "ea_trade_request": "EA请求交易指令",

        # MT5财经日历推送
        "mt5_calendar_update": "MT5财经日历上报",
        "mt5_event_result": "MT5事件结果上报",

        # 转折点相关
        "pivot_detected": "转折点检测完成",
        "pivot_alert": "转折点提醒",

        # 交易指令
        "order_generated": "交易指令生成",
        "order_confirmed": "交易指令确认",
        "order_rejected": "交易指令拒绝",
        "close_position": "平仓指令",

        # 持仓相关
        "position_update": "持仓数据更新",

        # 新闻爬虫相关
        "news_crawler_start": "新闻爬虫启动",
        "news_crawler_stop": "新闻爬虫停止",
        "news_calendar_fetch": "财经日历获取",
        "news_calendar_fetch_error": "财经日历获取失败",
        "news_calendar_update": "财经日历更新",
        "news_flash_fetch": "快讯获取",
        "news_flash_fetch_error": "快讯获取失败",
        "news_event_scheduled": "事件调度创建",
        "news_event_reminder": "事件发布前提醒",
        "news_event_result": "事件结果获取",
        "news_impact_analysis": "影响分析完成",
        "news_ws_broadcast": "新闻WebSocket推送",

        # 系统事件
        "system_startup": "系统启动",
        "system_shutdown": "系统关闭",
        "websocket_connect": "WebSocket连接",
        "websocket_disconnect": "WebSocket断开",
    }

    def __init__(self, max_size: int = 200):
        self._logs = deque(maxlen=max_size)
        self._lock = threading.RLock()
        self._ws_clients = set()
        self._ws_lock = threading.Lock()
        self._main_loop = None

        print(f"[SystemLog] 系统日志已初始化，最大保留 {max_size} 条")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop

    def add_log(self, event_type: str, detail: Dict[str, Any] = None,
                symbol: str = None, message: str = None):
        """
        添加日志

        Args:
            event_type: 事件类型
            detail: 事件详情
            symbol: 相关品种
            message: 自定义消息
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "event_name": self.EVENT_TYPES.get(event_type, event_type),
            "symbol": symbol,
            "message": message,
            "detail": detail or {}
        }

        with self._lock:
            self._logs.append(log_entry)

        # 广播到WebSocket客户端
        self._broadcast_log(log_entry)

        # 打印到控制台
        log_str = f"[SystemLog] {log_entry['timestamp']} | {log_entry['event_name']}"
        if symbol:
            log_str += f" | {symbol}"
        if message:
            log_str += f" | {message}"
        print(log_str)

    def get_logs(self, count: int = 50, event_types: List[str] = None,
                 symbol: str = None) -> List[Dict]:
        """
        获取日志

        Args:
            count: 获取数量
            event_types: 过滤事件类型列表（支持多选）
            symbol: 过滤品种

        Returns:
            日志列表（按时间倒序）
        """
        with self._lock:
            logs = list(self._logs)

        # 过滤
        if event_types:
            logs = [l for l in logs if l['event_type'] in event_types]
        if symbol:
            logs = [l for l in logs if l.get('symbol') == symbol]

        # 按时间倒序，取最新的
        logs = logs[::-1][:count]
        return logs

    def clear_logs(self):
        """清空日志"""
        with self._lock:
            self._logs.clear()
        print("[SystemLog] 日志已清空")

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)

    def _broadcast_log(self, log_entry: Dict):
        """广播日志到WebSocket客户端"""
        if not self._main_loop:
            return

        import asyncio

        message = json.dumps({
            "type": "system_log",
            "data": log_entry
        })

        with self._ws_lock:
            clients = list(self._ws_clients)

        if not clients:
            return

        # 在主事件循环中发送
        for client in clients:
            try:
                asyncio.run_coroutine_threadsafe(
                    client.send_text(message),
                    self._main_loop
                )
            except Exception as e:
                print(f"[SystemLog] 广播日志失败: {e}")


# 全局单例
_system_log = None


def get_system_log() -> SystemLog:
    """获取系统日志单例"""
    global _system_log
    if _system_log is None:
        _system_log = SystemLog()
    return _system_log