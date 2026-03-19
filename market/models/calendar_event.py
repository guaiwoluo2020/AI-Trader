#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经日历事件数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re


# MT5中表示无效值的特殊数值
MT5_INVALID_VALUE = -9223372036854775808.0

# MT5时区偏移（相对于北京时间）
# MT5服务器通常是 GMT+2，北京时间是 GMT+8
# 所以 MT5时间 + 6小时 = 北京时间
MT5_TIMEZONE_OFFSET_HOURS = 6


def clean_invalid_value(value: str) -> str:
    """清理MT5返回的无效值"""
    if not value:
        return ""
    try:
        num = float(value)
        if num == MT5_INVALID_VALUE or num < -1e15:
            return ""
        if num == int(num):
            return str(int(num))
        return value
    except (ValueError, TypeError):
        return value


def clean_text(text: str) -> str:
    """清理文本中的控制字符和无效Unicode"""
    if not text:
        return ""
    cleaned = re.sub(r'[\x00-\x1f\x7f]', '', text)
    result = []
    for char in cleaned:
        code = ord(char)
        if (0x20 <= code <= 0x7E or
            0x4E00 <= code <= 0x9FFF or
            0x3000 <= code <= 0x303F or
            0xFF00 <= code <= 0xFFEF or
            code > 0x9FFF):
            result.append(char)
    return ''.join(result)


def is_valid_name(name: str) -> bool:
    """检查名称是否有效（不是乱码）"""
    if not name or len(name) < 2:
        return False
    printable_count = 0
    for char in name:
        code = ord(char)
        if (0x20 <= code <= 0x7E or
            0x4E00 <= code <= 0x9FFF or
            0x3000 <= code <= 0x303F or
            0xFF00 <= code <= 0xFFEF):
            printable_count += 1
    ratio = printable_count / len(name) if name else 0
    return ratio >= 0.7


@dataclass
class CalendarEvent:
    """财经日历事件"""
    id: str
    name: str
    name_en: str = ""
    country: str = ""
    currency: str = ""
    importance: int = 0  # 0-3
    publish_time: Optional[datetime] = None
    forecast: str = ""
    previous: str = ""
    actual: str = ""
    unit: str = ""
    symbols: List[str] = field(default_factory=list)
    event_type: str = ""

    # 发布后填充
    result: str = ""  # better/worse/in_line
    impact: Dict = field(default_factory=dict)
    analyzed: bool = False

    def to_dict(self) -> Dict:
        """转换为字典"""
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

    @classmethod
    def from_mt5_data(cls, event_data: Dict) -> Optional['CalendarEvent']:
        """
        从MT5数据创建事件对象

        Args:
            event_data: MT5返回的事件数据

        Returns:
            CalendarEvent 或 None（如果数据无效）
        """
        event_id = str(event_data.get('id', ''))
        if not event_id:
            return None

        # 解析发布时间
        publish_time = event_data.get('publish_time')
        if isinstance(publish_time, str):
            try:
                publish_time = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
            except:
                try:
                    publish_time = datetime.strptime(publish_time, '%Y.%m.%d %H:%M:%S')
                except:
                    return None
        elif not isinstance(publish_time, datetime):
            return None

        # 将 MT5 时间转换为北京时间（GMT+8）
        # MT5服务器时间通常是 GMT+2，北京时间是 GMT+8，差6小时
        publish_time = publish_time + timedelta(hours=MT5_TIMEZONE_OFFSET_HOURS)

        # 清理并检查名称有效性
        cleaned_name = clean_text(event_data.get('name', ''))
        if not is_valid_name(cleaned_name):
            return None

        return cls(
            id=event_id,
            name=cleaned_name,
            name_en=clean_text(event_data.get('name_en', '')),
            country=clean_text(event_data.get('country', '')),
            currency=event_data.get('currency', ''),
            importance=event_data.get('importance', 0),
            publish_time=publish_time,
            forecast=clean_invalid_value(event_data.get('forecast', '')),
            previous=clean_invalid_value(event_data.get('previous', '')),
            actual=clean_invalid_value(event_data.get('actual', '')),
            unit=event_data.get('unit', ''),
            symbols=event_data.get('symbols', []),
            event_type=event_data.get('event_type', '')
        )