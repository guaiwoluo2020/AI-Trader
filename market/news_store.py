#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新闻数据存储模块
存储财经日历、快讯和事件数据
"""

from collections import deque
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import threading
from dataclasses import dataclass, field


@dataclass
class CalendarEvent:
    """财经日历事件"""
    id: str
    name: str
    name_en: str = ""
    country: str = ""
    currency: str = ""  # 货币代码
    importance: int = 0  # 0-3
    publish_time: datetime = None
    forecast: str = ""
    previous: str = ""
    actual: str = ""
    unit: str = ""
    symbols: List[str] = field(default_factory=list)
    event_type: str = ""  # 事件类型（指标、讲话等）

    # 发布后填充
    result: str = ""  # better/worse/in_line
    impact: Dict = field(default_factory=dict)  # {symbol: {direction, reason}}
    analyzed: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en,
            "country": self.country,
            "currency": self.currency,
            "importance": self.importance,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
            "unit": self.unit,
            "symbols": self.symbols,
            "event_type": self.event_type,
            "result": self.result,
            "impact": self.impact,
            "analyzed": self.analyzed
        }


@dataclass
class FlashNews:
    """快讯数据"""
    id: str
    content: str
    source: str = ""
    time: datetime = None
    importance: int = 0
    keywords: List[str] = field(default_factory=list)
    related_symbols: List[str] = field(default_factory=list)

    # 分析后填充
    speaker: str = ""
    speaker_title: str = ""
    impact: Dict = field(default_factory=dict)
    analyzed: bool = False

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "time": self.time.isoformat() if self.time else None,
            "importance": self.importance,
            "keywords": self.keywords,
            "related_symbols": self.related_symbols,
            "speaker": self.speaker,
            "speaker_title": self.speaker_title,
            "impact": self.impact,
            "analyzed": self.analyzed
        }


class NewsStore:
    """新闻存储"""

    # 过期数据清理阈值（小时）
    EXPIRY_HOURS = 6

    def __init__(self):
        # 财经日历: 使用列表存储所有事件，按时间排序
        # 不再按日期分片，直接存储在内存中
        self._calendar_events: List[CalendarEvent] = []
        self._calendar_lock = threading.RLock()

        # 快讯历史: deque[FlashNews]
        # 保留最新100条
        self._flash_news: deque = deque(maxlen=100)
        self._news_lock = threading.RLock()

        # 已提醒的事件ID
        self._alerted_events: set = set()
        self._alerted_news: set = set()

        # 即将发布的重要事件（用于调度）
        self._upcoming_events: Dict[str, CalendarEvent] = {}

        print("[NewsStore] 新闻存储已初始化")

    # ==================== 财经日历 ====================

    def update_calendar_from_mt5(self, events: List[Dict]) -> int:
        """
        从MT5数据更新财经日历

        Args:
            events: MT5返回的事件列表

        Returns:
            更新的事件数量
        """
        now = datetime.now()
        expiry_threshold = now - timedelta(hours=self.EXPIRY_HOURS)

        with self._calendar_lock:
            # 1. 清理过期数据
            self._calendar_events = [
                e for e in self._calendar_events
                if e.publish_time and e.publish_time > expiry_threshold
            ]

            # 2. 构建现有事件的ID集合
            existing_ids = {e.id for e in self._calendar_events}

            # 3. 添加或更新事件
            new_count = 0
            update_count = 0

            for event_data in events:
                event_id = str(event_data.get('id', ''))

                # 解析发布时间
                publish_time = event_data.get('publish_time')
                if isinstance(publish_time, str):
                    try:
                        # 尝试ISO格式
                        publish_time = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
                    except:
                        try:
                            # 尝试MQL5 TimeToString格式: "2026.03.16 20:30:00"
                            publish_time = datetime.strptime(publish_time, '%Y.%m.%d %H:%M:%S')
                        except Exception as e:
                            print(f"[NewsStore] 无法解析时间 '{publish_time}': {e}")
                            continue
                elif not isinstance(publish_time, datetime):
                    print(f"[NewsStore] 事件 {event_id} 缺少有效的publish_time")
                    continue

                # 跳过过期数据
                if publish_time < expiry_threshold:
                    continue

                # 创建事件对象
                event = CalendarEvent(
                    id=event_id,
                    name=event_data.get('name', ''),
                    name_en=event_data.get('name_en', ''),
                    country=event_data.get('country', ''),
                    currency=event_data.get('currency', ''),
                    importance=event_data.get('importance', 0),
                    publish_time=publish_time,
                    forecast=event_data.get('forecast', ''),
                    previous=event_data.get('previous', ''),
                    actual=event_data.get('actual', ''),
                    unit=event_data.get('unit', ''),
                    symbols=event_data.get('symbols', []),
                    event_type=event_data.get('event_type', '')
                )

                if event_id in existing_ids:
                    # 更新现有事件
                    for i, e in enumerate(self._calendar_events):
                        if e.id == event_id:
                            self._calendar_events[i] = event
                            update_count += 1
                            break
                else:
                    # 添加新事件
                    self._calendar_events.append(event)
                    new_count += 1

            # 4. 按时间排序
            self._calendar_events.sort(key=lambda x: x.publish_time or datetime.min)

            total = len(self._calendar_events)
            print(f"[NewsStore] MT5财经日历更新: 新增{new_count}条, 更新{update_count}条, 当前共{total}条")

            return new_count + update_count

    def get_calendar(self, date_str: str = None) -> List[Dict]:
        """
        获取财经日历

        Args:
            date_str: 日期，None返回所有

        Returns:
            事件列表
        """
        with self._calendar_lock:
            if date_str:
                # 过滤指定日期
                filtered = [
                    e for e in self._calendar_events
                    if e.publish_time and e.publish_time.strftime('%Y-%m-%d') == date_str
                ]
                return [e.to_dict() for e in filtered]
            else:
                return [e.to_dict() for e in self._calendar_events]

    def get_upcoming_events(self, hours: int = 24) -> List[CalendarEvent]:
        """
        获取即将发布的重要事件

        Args:
            hours: 未来多少小时内

        Returns:
            事件列表
        """
        now = datetime.now()
        upcoming = []

        with self._calendar_lock:
            for event in self._calendar_events:
                if event.publish_time and event.importance >= 2:
                    delta = event.publish_time - now
                    if 0 < delta.total_seconds() <= hours * 3600:
                        upcoming.append(event)

        return sorted(upcoming, key=lambda x: x.publish_time)

    def get_event_by_id(self, event_id: str) -> Optional[CalendarEvent]:
        """根据ID获取事件"""
        with self._calendar_lock:
            for event in self._calendar_events:
                if event.id == event_id:
                    return event
        return None

    def update_event_result(self, event_id: str, actual: str, result: str, impact: Dict) -> None:
        """更新事件结果"""
        with self._calendar_lock:
            event = self.get_event_by_id(event_id)
            if event:
                event.actual = actual
                event.result = result
                event.impact = impact
                event.analyzed = True
                print(f"[NewsStore] 更新事件结果: {event.name}, 实际值={actual}, 结果={result}")

    def is_event_alerted(self, event_id: str) -> bool:
        """检查事件是否已提醒"""
        return event_id in self._alerted_events

    def mark_event_alerted(self, event_id: str) -> None:
        """标记事件已提醒"""
        self._alerted_events.add(event_id)

    def cleanup_expired_events(self) -> int:
        """
        清理过期超过6小时的事件

        Returns:
            清理的事件数量
        """
        now = datetime.now()
        expiry_threshold = now - timedelta(hours=self.EXPIRY_HOURS)

        with self._calendar_lock:
            before_count = len(self._calendar_events)
            self._calendar_events = [
                e for e in self._calendar_events
                if e.publish_time and e.publish_time > expiry_threshold
            ]
            removed = before_count - len(self._calendar_events)

            if removed > 0:
                print(f"[NewsStore] 清理过期事件: {removed}条")

            return removed

    # ==================== 快讯 ====================

    def add_flash_news(self, news: FlashNews) -> bool:
        """
        添加快讯

        Returns:
            是否新增（False表示已存在）
        """
        with self._news_lock:
            # 检查是否已存在
            for existing in self._flash_news:
                if existing.id == news.id:
                    return False

            self._flash_news.appendleft(news)
            print(f"[NewsStore] 新增快讯: {news.id}")
            return True

    def get_flash_news(self, count: int = 20) -> List[Dict]:
        """获取最新快讯"""
        with self._news_lock:
            news_list = list(self._flash_news)[:count]
            return [n.to_dict() for n in news_list]

    def is_news_alerted(self, news_id: str) -> bool:
        """检查快讯是否已提醒"""
        return news_id in self._alerted_news

    def mark_news_alerted(self, news_id: str) -> None:
        """标记快讯已提醒"""
        self._alerted_news.add(news_id)

    def update_news_analysis(self, news_id: str, speaker: str, speaker_title: str, impact: Dict) -> None:
        """更新快讯分析结果"""
        with self._news_lock:
            for news in self._flash_news:
                if news.id == news_id:
                    news.speaker = speaker
                    news.speaker_title = speaker_title
                    news.impact = impact
                    news.analyzed = True
                    break

    # ==================== 统计 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._calendar_lock, self._news_lock:
            return {
                "calendar_events": len(self._calendar_events),
                "flash_news_count": len(self._flash_news),
                "alerted_events": len(self._alerted_events),
                "alerted_news": len(self._alerted_news)
            }

    def clear(self) -> None:
        """清空所有数据"""
        with self._calendar_lock, self._news_lock:
            self._calendar_events.clear()
            self._flash_news.clear()
            self._alerted_events.clear()
            self._alerted_news.clear()
            print("[NewsStore] 已清空所有数据")


# 全局单例
_news_store = None


def get_news_store() -> NewsStore:
    """获取新闻存储单例"""
    global _news_store
    if _news_store is None:
        _news_store = NewsStore()
    return _news_store