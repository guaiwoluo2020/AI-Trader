#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻监控模块
分析影响、推送提醒
财经日历数据由EA端通过MT5 API获取后推送
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set
import json
import threading

from .news_crawler import Jin10Crawler, get_jin10_crawler
from .news_store import CalendarEvent, FlashNews, get_news_store
from .event_config import get_high_impact_event_names
from .system_log import get_system_log


class NewsMonitor:
    """新闻监控器"""

    def __init__(self):
        self.crawler = get_jin10_crawler()  # 仅用于快讯
        self.store = get_news_store()
        self.system_log = get_system_log()

        # WebSocket客户端
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()

        # 主事件循环引用
        self._main_loop = None

        # 是否正在运行
        self._running = False

        # 高影响事件名称
        self._high_impact_names = get_high_impact_event_names()

        # 已调度的事件
        self._scheduled_events: Dict[str, asyncio.Task] = {}

        print("[NewsMonitor] 新闻监控器已初始化")

        # 记录日志
        self.system_log.add_log("news_crawler_start", message="新闻监控器已初始化（财经日历由EA推送）")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print("[NewsMonitor] 已设置主事件循环")

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            print(f"[NewsMonitor] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            print(f"[NewsMonitor] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def get_ws_client_count(self) -> int:
        """获取WebSocket客户端数量"""
        with self._ws_lock:
            return len(self._ws_clients)

    # ==================== 主循环 ====================

    async def run(self):
        """主运行循环"""
        if self._running:
            print("[NewsMonitor] 已经在运行中")
            return

        self._running = True
        print("[NewsMonitor] 开始运行...")

        # 启动多个并行任务
        await asyncio.gather(
            self._flash_news_loop(),     # 快讯监控（每30秒）
            self._event_reminder_loop(), # 事件提醒（每分钟检查）
            self._cleanup_loop(),        # 过期数据清理（每10分钟）
        )

    async def stop(self):
        """停止运行"""
        self._running = False
        await self.crawler.close()
        print("[NewsMonitor] 已停止")

    # ==================== 事件提醒循环 ====================

    async def _event_reminder_loop(self):
        """检查即将发布的事件并发送提醒"""
        while self._running:
            try:
                now = datetime.now()

                # 获取未来1小时内的重要事件
                events = self.store.get_upcoming_events(hours=1)

                for event in events:
                    if not event.publish_time:
                        continue

                    # 发布前5分钟提醒
                    time_to_publish = (event.publish_time - now).total_seconds()
                    if 0 < time_to_publish <= 300:  # 5分钟内
                        if not self.store.is_event_alerted(f"{event.id}_reminder"):
                            await self._send_event_reminder(event)
                            self.store.mark_event_alerted(f"{event.id}_reminder")

                # 每分钟检查一次
                await asyncio.sleep(60)

            except Exception as e:
                print(f"[NewsMonitor] 事件提醒检查异常: {e}")
                await asyncio.sleep(30)

    async def _send_event_reminder(self, event: CalendarEvent):
        """发送事件提醒"""
        alert = {
            "type": "event_reminder",
            "event": event.to_dict(),
            "message": f"重要数据 {event.name} 将在5分钟内发布",
            "timestamp": datetime.now().isoformat()
        }

        await self._broadcast_alert(alert)

        self.system_log.add_log("news_event_reminder", detail={
            "event_id": event.id,
            "event_name": event.name,
            "currency": event.currency
        }, message=f"事件发布前提醒: {event.name}")

        print(f"[NewsMonitor] 事件提醒: {event.name}")

    # ==================== 快讯循环 ====================

    async def _flash_news_loop(self):
        """快讯监控循环"""
        max_id = 0
        check_count = 0

        while self._running:
            try:
                check_count += 1

                # 每10次检查记录一次日志
                if check_count % 10 == 0:
                    self.system_log.add_log("news_flash_fetch", detail={
                        "check_count": check_count,
                        "max_id": max_id
                    }, message=f"快讯检查 #{check_count}")

                # 获取最新快讯
                news_list = await self.crawler.fetch_flash_news(max_id=max_id, count=20)

                if news_list:
                    self.system_log.add_log("news_flash_fetch", detail={
                        "count": len(news_list)
                    }, message=f"获取到 {len(news_list)} 条快讯")

                for news in reversed(news_list):  # 按时间顺序处理
                    # 检查是否已处理
                    if self.store.is_news_alerted(news.id):
                        continue

                    # 分析影响
                    analysis = self.crawler.analyze_news_impact(news)

                    # 只推送有影响的快讯
                    if analysis['impact'] or analysis['speaker']:
                        news.speaker = analysis['speaker']
                        news.speaker_title = analysis['speaker_title']
                        news.impact = analysis['impact']
                        news.analyzed = True
                        news.importance = 2 if analysis['speaker'] else 1

                        # 添加到存储
                        self.store.add_flash_news(news)

                        # 推送提醒
                        alert = {
                            "type": "flash_news",
                            "news": news.to_dict(),
                            "analysis": analysis,
                            "timestamp": datetime.now().isoformat()
                        }

                        await self._broadcast_alert(alert)

                        self.system_log.add_log("news_impact_analysis", detail={
                            "news_id": news.id,
                            "speaker": news.speaker,
                            "impact": news.impact
                        }, message=f"快讯影响分析: {news.speaker or '事件'} -> {list(news.impact.keys())}")

                        print(f"[NewsMonitor] 快讯已推送: {news.id} - {news.speaker}")

                    # 标记已处理
                    self.store.mark_news_alerted(news.id)

                    # 更新max_id
                    try:
                        if int(news.id) > max_id:
                            max_id = int(news.id)
                    except:
                        pass

                # 每30秒检查一次
                await asyncio.sleep(30)

            except Exception as e:
                self.system_log.add_log("news_flash_fetch_error", detail={
                    "error": str(e)
                }, message=f"快讯监控异常: {e}")
                print(f"[NewsMonitor] 快讯监控异常: {e}")
                await asyncio.sleep(10)

    # ==================== 清理循环 ====================

    async def _cleanup_loop(self):
        """定期清理过期数据"""
        while self._running:
            try:
                # 每10分钟清理一次
                await asyncio.sleep(600)

                removed = self.store.cleanup_expired_events()
                if removed > 0:
                    print(f"[NewsMonitor] 已清理 {removed} 条过期事件")

            except Exception as e:
                print(f"[NewsMonitor] 清理任务异常: {e}")
                await asyncio.sleep(60)

    # ==================== 广播消息 ====================

    async def _broadcast_alert(self, alert: Dict):
        """广播提醒到所有WebSocket客户端"""
        message = json.dumps(alert, ensure_ascii=False)

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
                    print(f"[NewsMonitor] 发送WebSocket消息失败: {e}")
        else:
            for client in clients:
                try:
                    await self._send_to_client(client, message)
                except Exception as e:
                    print(f"[NewsMonitor] 发送消息失败: {e}")

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[NewsMonitor] 发送消息到客户端失败: {e}")
            with self._ws_lock:
                self._ws_clients.discard(client)

    async def _broadcast_calendar_update(self):
        """广播日历更新"""
        calendar = self.store.get_calendar()
        message = json.dumps({
            "type": "calendar_update",
            "data": calendar
        }, ensure_ascii=False)

        with self._ws_lock:
            clients = list(self._ws_clients)

        for client in clients:
            try:
                await self._send_to_client(client, message)
            except Exception:
                pass

    # ==================== 状态查询 ====================

    def get_status(self) -> Dict:
        """获取监控状态"""
        return {
            "running": self._running,
            "ws_clients": self.get_ws_client_count(),
            "store_status": self.store.get_status(),
            "scheduled_events": len(self._scheduled_events)
        }

    def get_calendar(self, date_str: str = None) -> List[Dict]:
        """获取财经日历"""
        return self.store.get_calendar(date_str)

    def get_upcoming_events(self, hours: int = 24) -> List[Dict]:
        """获取即将发布的事件"""
        events = self.store.get_upcoming_events(hours)
        return [e.to_dict() for e in events]

    def get_recent_news(self, count: int = 20) -> List[Dict]:
        """获取最近快讯"""
        return self.store.get_flash_news(count)


# 全局单例
_news_monitor = None


def get_news_monitor() -> NewsMonitor:
    """获取新闻监控器单例"""
    global _news_monitor
    if _news_monitor is None:
        _news_monitor = NewsMonitor()
    return _news_monitor