#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快讯存储模块
存储快讯数据
"""

from collections import deque
from datetime import datetime
from typing import List, Dict, Optional
import threading

from ..models import FlashNews


class FlashNewsStore:
    """快讯存储（只负责数据CRUD）"""

    # 最大保留数量
    MAX_NEWS = 100

    def __init__(self):
        # 快讯列表，最新的在前
        self._news: deque = deque(maxlen=self.MAX_NEWS)
        self._lock = threading.RLock()

        # 已提醒的快讯ID集合
        self._alerted_ids: set = set()

        print("[FlashNewsStore] 快讯存储已初始化")

    # ==================== 快讯管理 ====================

    def add_news(self, news: FlashNews) -> bool:
        """
        添加快讯

        Args:
            news: 快讯对象

        Returns:
            是否新增（False表示已存在）
        """
        with self._lock:
            # 检查是否已存在
            for existing in self._news:
                if existing.id == news.id:
                    return False

            self._news.appendleft(news)
            print(f"[FlashNewsStore] 新增快讯: {news.id}")
            return True

    def get_news(self, count: int = 20) -> List[Dict]:
        """
        获取最新快讯

        Args:
            count: 获取数量

        Returns:
            快讯字典列表
        """
        with self._lock:
            news_list = list(self._news)[:count]
            return [n.to_dict() for n in news_list]

    def get_news_objects(self, count: int = 20) -> List[FlashNews]:
        """获取快讯对象列表"""
        with self._lock:
            return list(self._news)[:count]

    def get_news_by_id(self, news_id: str) -> Optional[FlashNews]:
        """根据ID获取快讯"""
        with self._lock:
            for news in self._news:
                if news.id == news_id:
                    return news
        return None

    # ==================== 提醒状态 ====================

    def is_alerted(self, news_id: str) -> bool:
        """检查快讯是否已提醒"""
        return news_id in self._alerted_ids

    def mark_alerted(self, news_id: str) -> None:
        """标记快讯已提醒"""
        self._alerted_ids.add(news_id)

    # ==================== 分析结果更新 ====================

    def update_analysis(self, news_id: str, speaker: str, speaker_title: str, impact: Dict) -> bool:
        """
        更新快讯分析结果

        Args:
            news_id: 快讯ID
            speaker: 发言人
            speaker_title: 发言人职位
            impact: 影响分析

        Returns:
            是否更新成功
        """
        with self._lock:
            for news in self._news:
                if news.id == news_id:
                    news.speaker = speaker
                    news.speaker_title = speaker_title
                    news.impact = impact
                    news.analyzed = True
                    print(f"[FlashNewsStore] 更新快讯分析: {news_id}, 发言人={speaker}")
                    return True
        return False

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            return {
                "total_news": len(self._news),
                "alerted_news": len(self._alerted_ids)
            }

    def clear(self) -> None:
        """清空所有数据"""
        with self._lock:
            self._news.clear()
            self._alerted_ids.clear()
            print("[FlashNewsStore] 已清空所有数据")