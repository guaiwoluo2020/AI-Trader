#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经日历存储模块
存储财经日历事件数据
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
import threading

from ..models import CalendarEvent


class CalendarStore:
    """财经日历存储（只负责数据CRUD）"""

    # 过期数据清理阈值（小时）
    EXPIRY_HOURS = 6

    def __init__(self):
        # 事件列表，按时间排序
        self._events: List[CalendarEvent] = []
        self._lock = threading.RLock()

        # 已提醒的事件ID集合
        self._alerted_ids: set = set()

        print("[CalendarStore] 财经日历存储已初始化")

    # ==================== 事件管理 ====================

    def save_events(self, events: List[CalendarEvent]) -> Dict:
        """
        保存事件（全量替换）

        Args:
            events: 事件列表

        Returns:
            {"added": N, "updated": M, "total": T}
        """
        with self._lock:
            # 清理过期数据
            self._cleanup_expired()

            # 构建现有事件ID集合
            existing_ids = {e.id for e in self._events}

            new_count = 0
            update_count = 0

            for event in events:
                if event.id in existing_ids:
                    # 更新现有事件
                    for i, e in enumerate(self._events):
                        if e.id == event.id:
                            self._events[i] = event
                            update_count += 1
                            break
                else:
                    # 添加新事件
                    self._events.append(event)
                    new_count += 1

            # 按时间排序
            self._events.sort(key=lambda x: x.publish_time or datetime.min)

            total = len(self._events)
            print(f"[CalendarStore] 保存事件: 新增{new_count}条, 更新{update_count}条, 当前共{total}条")

            return {"added": new_count, "updated": update_count, "total": total}

    def update_from_mt5(self, events_data: List[Dict]) -> int:
        """
        从MT5数据更新财经日历

        Args:
            events_data: MT5返回的事件列表

        Returns:
            更新的事件数量
        """
        now = datetime.now()
        expiry_threshold = now - timedelta(hours=self.EXPIRY_HOURS)

        with self._lock:
            # 清理过期数据
            self._events = [
                e for e in self._events
                if e.publish_time and e.publish_time > expiry_threshold
            ]

            existing_ids = {e.id for e in self._events}
            new_count = 0
            update_count = 0

            for event_data in events_data:
                event = CalendarEvent.from_mt5_data(event_data)
                if event is None:
                    continue

                # 跳过过期数据
                if event.publish_time and event.publish_time < expiry_threshold:
                    continue

                if event.id in existing_ids:
                    for i, e in enumerate(self._events):
                        if e.id == event.id:
                            self._events[i] = event
                            update_count += 1
                            break
                else:
                    self._events.append(event)
                    new_count += 1

            self._events.sort(key=lambda x: x.publish_time or datetime.min)

            total = len(self._events)
            print(f"[CalendarStore] MT5更新: 新增{new_count}条, 更新{update_count}条, 当前共{total}条")

            return new_count + update_count

    def get_events(self, date_str: str = None) -> List[Dict]:
        """
        获取事件列表

        Args:
            date_str: 日期字符串，None返回所有

        Returns:
            事件字典列表
        """
        with self._lock:
            if date_str:
                filtered = [
                    e for e in self._events
                    if e.publish_time and e.publish_time.strftime('%Y-%m-%d') == date_str
                ]
                return [e.to_dict() for e in filtered]
            return [e.to_dict() for e in self._events]

    def get_event_objects(self, date_str: str = None) -> List[CalendarEvent]:
        """获取事件对象列表"""
        with self._lock:
            if date_str:
                return [
                    e for e in self._events
                    if e.publish_time and e.publish_time.strftime('%Y-%m-%d') == date_str
                ]
            return list(self._events)

    def get_event_by_id(self, event_id: str) -> Optional[CalendarEvent]:
        """根据ID获取事件"""
        with self._lock:
            for event in self._events:
                if event.id == event_id:
                    return event
        return None

    def get_upcoming_events(self, hours: int = 24, min_importance: int = 2) -> List[CalendarEvent]:
        """
        获取即将发布的重要事件

        Args:
            hours: 未来多少小时内
            min_importance: 最小重要级别

        Returns:
            事件列表
        """
        now = datetime.now()
        upcoming = []

        with self._lock:
            for event in self._events:
                if event.publish_time and event.importance >= min_importance:
                    delta = event.publish_time - now
                    if 0 < delta.total_seconds() <= hours * 3600:
                        upcoming.append(event)

        return sorted(upcoming, key=lambda x: x.publish_time)

    # ==================== 提醒状态 ====================

    def is_alerted(self, event_id: str) -> bool:
        """检查事件是否已提醒"""
        return event_id in self._alerted_ids

    def mark_alerted(self, event_id: str) -> None:
        """标记事件已提醒"""
        self._alerted_ids.add(event_id)

    # ==================== 事件结果更新 ====================

    def update_event_result(self, event_id: str, actual: str, result: str, impact: Dict) -> bool:
        """
        更新事件结果

        Args:
            event_id: 事件ID
            actual: 实际值
            result: 结果类型 (better/worse/in_line)
            impact: 影响分析

        Returns:
            是否更新成功
        """
        with self._lock:
            event = self.get_event_by_id(event_id)
            if event:
                event.actual = actual
                event.result = result
                event.impact = impact
                event.analyzed = True
                print(f"[CalendarStore] 更新事件结果: {event.name}, 实际值={actual}")
                return True
        return False

    # ==================== 清理 ====================

    def cleanup_expired(self) -> int:
        """清理过期事件"""
        with self._lock:
            return self._cleanup_expired()

    def _cleanup_expired(self) -> int:
        """内部清理方法（不加锁）"""
        now = datetime.now()
        expiry_threshold = now - timedelta(hours=self.EXPIRY_HOURS)

        before_count = len(self._events)
        self._events = [
            e for e in self._events
            if e.publish_time and e.publish_time > expiry_threshold
        ]
        removed = before_count - len(self._events)

        if removed > 0:
            print(f"[CalendarStore] 清理过期事件: {removed}条")

        return removed

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_events": len(self._events),
                "alerted_events": len(self._alerted_ids)
            }

    def clear(self) -> None:
        """清空所有数据"""
        with self._lock:
            self._events.clear()
            self._alerted_ids.clear()
            print("[CalendarStore] 已清空所有数据")