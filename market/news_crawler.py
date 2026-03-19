#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金十数据爬虫
仅负责数据获取，不包含业务逻辑
"""

import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import os

from .models import CalendarEvent, FlashNews
from .event_config import WATCH_SYMBOLS


class Jin10Crawler:
    """金十数据爬虫（仅负责数据获取）"""

    # API地址
    FLASH_NEWS_API = "https://flash-api.jin10.com/get_flash_list"

    # 请求头
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://www.jin10.com/',
        'Origin': 'https://www.jin10.com',
    }

    def __init__(self):
        self._session = None
        print("[Jin10Crawler] 金十数据爬虫已初始化")

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取HTTP会话"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                headers=self.HEADERS,
                timeout=timeout
            )
        return self._session

    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            print("[Jin10Crawler] 会话已关闭")

    # ==================== 快讯获取 ====================

    async def fetch_flash_news(self, max_id: int = 0, count: int = 30) -> List[FlashNews]:
        """
        获取最新快讯

        Args:
            max_id: 获取ID大于此值的快讯（0表示获取最新）
            count: 获取数量

        Returns:
            快讯列表
        """
        try:
            session = await self._get_session()

            url = f"{self.FLASH_NEWS_API}?maxid={max_id}&count={count}"
            async with session.get(url) as response:
                if response.status != 200:
                    print(f"[Jin10Crawler] 获取快讯失败: HTTP {response.status}")
                    return []

                data = await response.json()
                return self._parse_flash_news(data, count)

        except Exception as e:
            print(f"[Jin10Crawler] 获取快讯异常: {e}")
            return []

    def _parse_flash_news(self, data: Dict, count: int) -> List[FlashNews]:
        """解析快讯数据"""
        news_list = []
        items = data.get('data', [])

        for item in items[:count]:
            try:
                news = FlashNews(
                    id=str(item.get('id', '')),
                    content=item.get('content', ''),
                    source='jin10',
                    time=self._parse_news_time(item.get('time')),
                    importance=item.get('importance', 0),
                    keywords=item.get('keywords', []),
                    related_symbols=self._extract_symbols(item.get('content', ''))
                )
                news_list.append(news)
            except Exception as e:
                print(f"[Jin10Crawler] 解析快讯失败: {e}")
                continue

        return news_list

    def _parse_news_time(self, time_data) -> Optional[datetime]:
        """解析快讯时间"""
        if time_data is None:
            return None

        if isinstance(time_data, datetime):
            return time_data

        if isinstance(time_data, (int, float)):
            return datetime.fromtimestamp(time_data)

        try:
            return datetime.fromisoformat(str(time_data).replace('Z', '+00:00'))
        except:
            return None

    def _extract_symbols(self, content: str) -> List[str]:
        """从内容中提取相关品种"""
        symbols = []
        content_lower = content.lower()

        symbol_keywords = {
            'GOLD': ['gold', '黄金', 'xau'],
            'OIL': ['oil', '原油', 'wti', 'brent'],
            'SPX': ['spx', 's&p', '标普'],
            'USDJPY': ['usdjpy', '日元', 'jpy'],
            'BTC': ['btc', 'bitcoin', '比特币']
        }

        for symbol, keywords in symbol_keywords.items():
            for kw in keywords:
                if kw in content_lower:
                    symbols.append(symbol)
                    break

        return symbols


# 全局单例
_jin10_crawler = None


def get_jin10_crawler() -> Jin10Crawler:
    """获取爬虫单例"""
    global _jin10_crawler
    if _jin10_crawler is None:
        _jin10_crawler = Jin10Crawler()
    return _jin10_crawler