#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快讯数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class FlashNews:
    """快讯数据"""
    id: str
    content: str
    source: str = ""
    time: Optional[datetime] = None
    importance: int = 0
    keywords: List[str] = field(default_factory=list)
    related_symbols: List[str] = field(default_factory=list)

    # 分析后填充
    speaker: str = ""
    speaker_title: str = ""
    impact: Dict = field(default_factory=dict)
    analyzed: bool = False

    def to_dict(self) -> Dict:
        """转换为字典"""
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

    @classmethod
    def from_jin10_data(cls, data: Dict) -> 'FlashNews':
        """
        从金十数据创建快讯对象

        Args:
            data: 金十API返回的快讯数据

        Returns:
            FlashNews
        """
        # 解析时间
        time = None
        time_str = data.get('time') or data.get('publish_time')
        if time_str:
            if isinstance(time_str, datetime):
                time = time_str
            elif isinstance(time_str, (int, float)):
                time = datetime.fromtimestamp(time_str)
            else:
                try:
                    time = datetime.fromisoformat(str(time_str).replace('Z', '+00:00'))
                except:
                    pass

        return cls(
            id=str(data.get('id', '')),
            content=data.get('content', ''),
            source=data.get('source', 'jin10'),
            time=time,
            importance=data.get('importance', 0),
            keywords=data.get('keywords', []),
            related_symbols=data.get('related_symbols', [])
        )