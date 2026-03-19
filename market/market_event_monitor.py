#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场事件监控模块
负责定时调度和 WebSocket 推送
包含财经日历事件和快讯事件
"""

import asyncio
from datetime import datetime
from typing import Dict, List

from .models import CalendarEvent, FlashNews
from .store import CalendarStore, FlashNewsStore
from .services import CalendarService, FlashNewsService
from .utils.ws_manager import WebSocketManager
from .news_crawler import get_jin10_crawler
from .system_log import get_system_log


class MarketEventMonitor:
    """市场事件监控器（调度 + WebSocket 推送）"""

    def __init__(self,
                 calendar_store: CalendarStore = None,
                 flash_news_store: FlashNewsStore = None):
        # 存储
        self.calendar_store = calendar_store or CalendarStore()
        self.flash_news_store = flash_news_store or FlashNewsStore()

        # 服务
        self.calendar_service = CalendarService(self.calendar_store)
        self.flash_news_service = FlashNewsService(self.flash_news_store)

        # 爬虫
        self.crawler = get_jin10_crawler()

        # WebSocket管理器
        self.ws_manager = WebSocketManager("market_event")

        # 系统日志
        self.system_log = get_system_log()

        # 运行状态
        self._running = False

        print("[MarketEventMonitor] 市场事件监控器已初始化")

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self.ws_manager.set_event_loop(loop)
        print("[MarketEventMonitor] 已设置主事件循环")

    # ==================== 主循环 ====================

    async def run(self):
        """主运行循环"""
        if self._running:
            print("[MarketEventMonitor] 已经在运行中")
            return

        self._running = True
        print("[MarketEventMonitor] 开始运行...")

        self.system_log.add_log("market_event_monitor_start", message="市场事件监控已启动")

        await asyncio.gather(
            self._flash_news_loop(),
            self._event_reminder_loop(),
            self._cleanup_loop(),
        )

    async def stop(self):
        """停止运行"""
        self._running = False
        await self.crawler.close()
        print("[MarketEventMonitor] 已停止")

    # ==================== 快讯监控循环 ====================

    async def _flash_news_loop(self):
        """快讯监控循环"""
        max_id = 0
        check_count = 0

        while self._running:
            try:
                check_count += 1

                # 每10次检查记录一次日志
                if check_count % 10 == 0:
                    self.system_log.add_log("flash_news_fetch", detail={
                        "check_count": check_count,
                        "max_id": max_id
                    }, message=f"快讯检查 #{check_count}")

                # 获取最新快讯
                news_list = await self.crawler.fetch_flash_news(max_id=max_id, count=20)

                if news_list:
                    self.system_log.add_log("flash_news_fetch", detail={
                        "count": len(news_list)
                    }, message=f"获取到 {len(news_list)} 条快讯")

                for news in reversed(news_list):
                    # 检查是否已处理
                    if self.flash_news_store.is_alerted(news.id):
                        continue

                    # 处理快讯（分析影响）
                    analysis = self.flash_news_service.process_news(news)

                    # 只推送有影响的快讯
                    if analysis:
                        alert = {
                            "type": "flash_news",
                            "news": news.to_dict(),
                            "analysis": analysis,
                            "timestamp": datetime.now().isoformat()
                        }
                        await self.ws_manager.broadcast(alert)

                        self.system_log.add_log("flash_news_impact", detail={
                            "news_id": news.id,
                            "speaker": news.speaker,
                            "impact": news.impact
                        }, message=f"快讯影响分析: {news.speaker or '事件'} -> {list(news.impact.keys())}")

                    # 标记已处理
                    self.flash_news_store.mark_alerted(news.id)

                    # 更新max_id
                    try:
                        if int(news.id) > max_id:
                            max_id = int(news.id)
                    except:
                        pass

                # 每30秒检查一次
                await asyncio.sleep(30)

            except Exception as e:
                self.system_log.add_log("flash_news_fetch_error", detail={
                    "error": str(e)
                }, message=f"快讯监控异常: {e}")
                print(f"[MarketEventMonitor] 快讯监控异常: {e}")
                await asyncio.sleep(10)

    # ==================== 事件提醒循环 ====================

    async def _event_reminder_loop(self):
        """检查即将发布的财经日历事件并发送提醒"""
        while self._running:
            try:
                # 获取需要提醒的事件
                events = self.calendar_service.check_upcoming_reminders()

                for event in events:
                    await self._send_event_reminder(event)

                # 每分钟检查一次
                await asyncio.sleep(60)

            except Exception as e:
                print(f"[MarketEventMonitor] 事件提醒检查异常: {e}")
                await asyncio.sleep(30)

    async def _send_event_reminder(self, event: CalendarEvent):
        """发送事件提醒"""
        alert = {
            "type": "calendar_event_reminder",
            "event": event.to_dict(),
            "message": f"重要数据 {event.name} 将在5分钟内发布",
            "timestamp": datetime.now().isoformat()
        }

        await self.ws_manager.broadcast(alert)

        self.system_log.add_log("calendar_event_reminder", detail={
            "event_id": event.id,
            "event_name": event.name,
            "currency": event.currency
        }, message=f"事件发布前提醒: {event.name}")

        print(f"[MarketEventMonitor] 事件提醒: {event.name}")

    # ==================== 清理循环 ====================

    async def _cleanup_loop(self):
        """定期清理过期数据"""
        while self._running:
            try:
                await asyncio.sleep(600)  # 每10分钟

                removed = self.calendar_store.cleanup_expired()
                if removed > 0:
                    print(f"[MarketEventMonitor] 已清理 {removed} 条过期事件")

            except Exception as e:
                print(f"[MarketEventMonitor] 清理任务异常: {e}")
                await asyncio.sleep(60)

    # ==================== 数据更新接口 ====================

    def update_calendar_from_mt5(self, events_data: List[Dict]) -> int:
        """
        从MT5数据更新财经日历

        Args:
            events_data: MT5返回的事件列表

        Returns:
            更新的事件数量
        """
        count = self.calendar_store.update_from_mt5(events_data)

        # 广播日历更新
        self.ws_manager.broadcast_sync({
            "type": "calendar_update",
            "data": self.calendar_store.get_events()
        })

        return count

    # ==================== 查询接口 ====================

    def get_calendar(self, date_str: str = None) -> List[Dict]:
        """获取财经日历"""
        return self.calendar_service.get_calendar(date_str)

    def get_upcoming_events(self, hours: int = 24) -> List[Dict]:
        """获取即将发布的重要事件"""
        return self.calendar_service.get_upcoming_events(hours)

    def get_recent_news(self, count: int = 20) -> List[Dict]:
        """获取最近快讯"""
        return self.flash_news_service.get_recent_news(count)

    def get_status(self) -> Dict:
        """获取监控状态"""
        return {
            "running": self._running,
            "ws_status": self.ws_manager.get_status(),
            "calendar_status": self.calendar_service.get_status(),
            "flash_news_status": self.flash_news_service.get_status()
        }

    def clear_calendar(self) -> None:
        """清空财经日历数据"""
        self.calendar_store.clear()
        print("[MarketEventMonitor] 财经日历数据已清空")


# 全局单例
_market_event_monitor = None


def get_market_event_monitor() -> MarketEventMonitor:
    """获取市场事件监控器单例"""
    global _market_event_monitor
    if _market_event_monitor is None:
        _market_event_monitor = MarketEventMonitor()
    return _market_event_monitor