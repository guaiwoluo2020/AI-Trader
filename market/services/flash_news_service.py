#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快讯服务模块
处理快讯影响分析等业务逻辑
"""

from typing import Dict, List, Optional

from ..models import FlashNews
from ..store import FlashNewsStore
from ..event_config import KEY_SPEAKERS, KEY_EVENTS, WATCH_SYMBOLS


class FlashNewsService:
    """快讯服务（处理业务逻辑）"""

    def __init__(self, flash_news_store: FlashNewsStore):
        self.store = flash_news_store

        print("[FlashNewsService] 快讯服务已初始化")

    # ==================== 快讯查询 ====================

    def get_recent_news(self, count: int = 20) -> List[Dict]:
        """获取最近快讯"""
        return self.store.get_news(count)

    def get_news_by_id(self, news_id: str) -> Optional[FlashNews]:
        """根据ID获取快讯"""
        return self.store.get_news_by_id(news_id)

    # ==================== 影响分析 ====================

    def analyze_news_impact(self, news: FlashNews) -> Dict:
        """
        分析快讯对市场的影响

        Args:
            news: 快讯对象

        Returns:
            {
                "speaker": 发言人名称,
                "speaker_title": 发言人职位,
                "impact": {symbol: direction},
                "topics": [相关话题]
            }
        """
        result = {
            "speaker": "",
            "speaker_title": "",
            "impact": {},
            "topics": []
        }

        content = news.content.lower()

        # 1. 检查关键人物讲话
        for speaker_config in KEY_SPEAKERS:
            keywords = speaker_config.get('keywords', [])
            matched = False

            for keyword in keywords:
                if keyword.lower() in content:
                    matched = True
                    break

            if matched:
                result["speaker"] = speaker_config['name']
                result["speaker_title"] = speaker_config['title']

                # 分析关注话题
                watch_topics = speaker_config.get('watch_topics', [])
                impact_symbols = speaker_config.get('impact_symbols', [])
                default_impact = speaker_config.get('default_impact', {})

                for topic in watch_topics:
                    if topic in content:
                        result["topics"].append(topic)

                        # 应用默认影响
                        for symbol in impact_symbols:
                            if symbol in default_impact:
                                topic_impact = default_impact[symbol]
                                if topic in topic_impact:
                                    result["impact"][symbol] = {
                                        "direction": topic_impact[topic],
                                        "reason": f"{speaker_config['name']}提及{topic}"
                                    }

                break

        # 2. 检查关键事件
        for event_config in KEY_EVENTS:
            watch_keywords = event_config.get('watch_keywords', [])
            matched = False

            for keyword in watch_keywords:
                if keyword.lower() in content:
                    matched = True
                    break

            if matched:
                for symbol in event_config.get('symbols', []):
                    if symbol not in result["impact"]:
                        result["impact"][symbol] = {
                            "direction": "不确定",
                            "reason": f"{event_config['name']}相关新闻"
                        }
                break

        return result

    def process_news(self, news: FlashNews) -> Optional[Dict]:
        """
        处理快讯（分析影响并存储）

        Args:
            news: 快讯对象

        Returns:
            如果有影响则返回分析结果，否则返回None
        """
        # 分析影响
        analysis = self.analyze_news_impact(news)

        # 只处理有影响的快讯
        if not analysis["impact"] and not analysis["speaker"]:
            return None

        # 更新快讯对象
        news.speaker = analysis["speaker"]
        news.speaker_title = analysis["speaker_title"]
        news.impact = analysis["impact"]
        news.analyzed = True
        news.importance = 2 if analysis["speaker"] else 1

        # 添加到存储
        self.store.add_news(news)

        return analysis

    # ==================== 提醒状态 ====================

    def should_alert(self, news_id: str) -> bool:
        """
        判断是否应该推送提醒

        Args:
            news_id: 快讯ID

        Returns:
            是否应该提醒
        """
        if self.store.is_alerted(news_id):
            return False

        self.store.mark_alerted(news_id)
        return True

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "store_status": self.store.get_status()
        }