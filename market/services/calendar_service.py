#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经日历服务模块
处理事件影响分析、提醒等业务逻辑
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..models import CalendarEvent
from ..store import CalendarStore
from ..event_config import get_high_impact_event_names, DATA_IMPACT_RULES


class CalendarService:
    """财经日历服务（处理业务逻辑）"""

    # 提醒时间（发布前多少秒）
    REMINDER_SECONDS = 300  # 5分钟

    def __init__(self, calendar_store: CalendarStore):
        self.store = calendar_store

        # 高影响事件名称
        self._high_impact_names = get_high_impact_event_names()

        print("[CalendarService] 财经日历服务已初始化")

    # ==================== 事件查询 ====================

    def get_calendar(self, date_str: str = None) -> List[Dict]:
        """获取财经日历"""
        return self.store.get_events(date_str)

    def get_upcoming_events(self, hours: int = 24) -> List[Dict]:
        """获取即将发布的重要事件"""
        events = self.store.get_upcoming_events(hours)
        return [e.to_dict() for e in events]

    def get_event_by_id(self, event_id: str) -> Optional[CalendarEvent]:
        """根据ID获取事件"""
        return self.store.get_event_by_id(event_id)

    # ==================== 事件提醒 ====================

    def check_upcoming_reminders(self) -> List[CalendarEvent]:
        """
        检查即将发布的事件提醒

        Returns:
            需要提醒的事件列表
        """
        now = datetime.now()
        reminders = []

        events = self.store.get_upcoming_events(hours=1, min_importance=2)

        for event in events:
            if not event.publish_time:
                continue

            time_to_publish = (event.publish_time - now).total_seconds()

            # 发布前5分钟内
            if 0 < time_to_publish <= self.REMINDER_SECONDS:
                reminder_key = f"{event.id}_reminder"

                if not self.store.is_alerted(reminder_key):
                    reminders.append(event)
                    self.store.mark_alerted(reminder_key)

        return reminders

    # ==================== 影响分析 ====================

    def analyze_event_impact(self, event: CalendarEvent) -> Dict:
        """
        分析事件对相关品种的影响

        Args:
            event: 事件对象

        Returns:
            影响分析结果 {symbol: {direction, reason}}
        """
        impact = {}

        # 检查是否有结果
        if not event.result or not event.actual:
            return impact

        for symbol in event.symbols:
            # 查找影响规则
            rules = DATA_IMPACT_RULES.get(symbol, {})
            event_rules = rules.get(event.name) or rules.get(event.name_en)

            if not event_rules:
                continue

            direction = event_rules.get(event.result)
            reason_key = f"reason_{event.result}"
            reason = event_rules.get(reason_key, "")

            if direction:
                impact[symbol] = {
                    "direction": direction,
                    "reason": reason,
                    "event_name": event.name,
                    "actual": event.actual,
                    "forecast": event.forecast,
                    "result": event.result
                }

        return impact

    def update_event_result(self, event_id: str, actual: str, result: str) -> bool:
        """
        更新事件结果并分析影响

        Args:
            event_id: 事件ID
            actual: 实际值
            result: 结果类型 (better/worse/in_line)

        Returns:
            是否更新成功
        """
        event = self.store.get_event_by_id(event_id)
        if not event:
            return False

        # 分析影响
        event.actual = actual
        event.result = result
        impact = self.analyze_event_impact(event)

        # 更新存储
        return self.store.update_event_result(event_id, actual, result, impact)

    # ==================== 高影响事件判断 ====================

    def is_high_impact_event(self, event: CalendarEvent) -> bool:
        """判断是否为高影响事件"""
        if event.importance >= 3:
            return True
        if event.name in self._high_impact_names:
            return True
        if event.name_en in self._high_impact_names:
            return True
        return False

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "store_status": self.store.get_status(),
            "high_impact_event_names": len(self._high_impact_names)
        }