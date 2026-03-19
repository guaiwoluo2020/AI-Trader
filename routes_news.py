#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻路由
财经日历、快讯查询和WebSocket推送
"""

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional


def create_news_routes():
    """创建新闻相关路由"""
    router = APIRouter(prefix="/api/news", tags=["新闻"])

    @router.get("/calendar")
    async def get_calendar(
        date: Optional[str] = Query(None, description="日期，格式: 2026-03-15，不传返回所有")
    ):
        """
        获取财经日历

        返回指定日期或所有日期的财经事件
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()

        calendar = monitor.get_calendar(date)

        return {
            "status": "ok",
            "date": date,
            "count": len(calendar),
            "data": calendar
        }

    @router.get("/upcoming")
    async def get_upcoming(
        hours: int = Query(24, description="未来多少小时内的事件")
    ):
        """
        获取即将发布的重要事件

        默认返回未来24小时内的重要财经事件
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()

        events = monitor.get_upcoming_events(hours)

        return {
            "status": "ok",
            "hours": hours,
            "count": len(events),
            "data": events
        }

    @router.get("/flash")
    async def get_flash_news(
        count: int = Query(20, description="获取数量，默认20")
    ):
        """
        获取最新快讯

        返回最近的有影响的快讯（关键人物讲话、重要事件）
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()

        news_list = monitor.get_recent_news(count)

        return {
            "status": "ok",
            "count": len(news_list),
            "data": news_list
        }

    @router.get("/status")
    async def get_status():
        """
        获取新闻模块状态
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()

        status = monitor.get_status()

        return {
            "status": "ok",
            "data": status
        }

    @router.websocket("/ws")
    async def news_websocket(websocket: WebSocket):
        """
        市场事件WebSocket推送

        推送内容类型:
        - calendar_event_reminder: 财经日历事件发布前提醒
        - calendar_update: 日历更新
        - flash_news: 重要快讯
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()

        await websocket.accept()
        monitor.ws_manager.add_client(websocket)

        try:
            # 发送欢迎消息
            await websocket.send_json({
                "type": "connected",
                "message": "已连接到市场事件推送服务"
            })

            # 保持连接，等待客户端消息或断开
            while True:
                # 接收客户端消息（心跳等）
                data = await websocket.receive_text()

                # 处理心跳
                if data == "ping":
                    await websocket.send_json({"type": "pong"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[MarketEventWebSocket] 连接异常: {e}")
        finally:
            monitor.ws_manager.remove_client(websocket)

    @router.get("/impact/{symbol}")
    async def get_symbol_impact(symbol: str):
        """
        获取特定品种的相关事件

        返回影响该品种的即将发布事件
        """
        from market.market_event_monitor import get_market_event_monitor
        from market.event_config import WATCH_SYMBOLS

        if symbol not in WATCH_SYMBOLS:
            return {
                "status": "error",
                "message": f"不支持的品种: {symbol}",
                "supported_symbols": WATCH_SYMBOLS
            }

        monitor = get_market_event_monitor()
        events = monitor.get_upcoming_events(72)  # 未来3天

        # 过滤相关事件
        related_events = [
            e for e in events
            if symbol in e.get('symbols', [])
        ]

        return {
            "status": "ok",
            "symbol": symbol,
            "count": len(related_events),
            "data": related_events
        }

    @router.post("/calendar/clear")
    async def clear_calendar():
        """
        清空财经日历数据

        清空后需要EA重新推送日历数据（会应用时区转换）
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()
        monitor.clear_calendar()

        return {
            "status": "ok",
            "message": "财经日历数据已清空，请让EA重新推送日历数据"
        }

    @router.delete("/calendar")
    async def clear_calendar_delete():
        """
        清空财经日历数据 (DELETE方法)

        清空后需要EA重新推送日历数据（会应用时区转换）
        """
        from market.market_event_monitor import get_market_event_monitor
        monitor = get_market_event_monitor()
        monitor.clear_calendar()

        return {
            "status": "ok",
            "message": "财经日历数据已清空，请让EA重新推送日历数据"
        }

    return router