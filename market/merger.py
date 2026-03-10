#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线合并模块
处理增量K线数据的合并逻辑
"""

from typing import List, Dict, Optional
from datetime import datetime

from .store import KlineData


class KlineMerger:
    """K线合并器"""

    @staticmethod
    def merge_klines(existing: List[KlineData], new_klines: List[KlineData]) -> List[KlineData]:
        """
        合并K线数据

        Args:
            existing: 现有K线数据
            new_klines: 新增K线数据

        Returns:
            合并后的K线数据
        """
        if not new_klines:
            return existing

        if not existing:
            return new_klines

        # 使用字典来去重，以时间戳为key
        kline_dict = {}

        # 添加现有数据
        for k in existing:
            ts = KlineMerger._normalize_timestamp(k.timestamp)
            kline_dict[ts] = k

        # 添加或更新新数据
        for k in new_klines:
            ts = KlineMerger._normalize_timestamp(k.timestamp)
            kline_dict[ts] = k

        # 按时间排序
        merged = sorted(kline_dict.values(), key=lambda x: KlineMerger._normalize_timestamp(x.timestamp))

        return merged

    @staticmethod
    def _normalize_timestamp(ts) -> str:
        """标准化时间戳"""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)

    @staticmethod
    def detect_gaps(klines: List[KlineData], period: str) -> List[Dict]:
        """
        检测K线数据缺口

        Args:
            klines: K线数据
            period: 周期

        Returns:
            缺口列表
        """
        if len(klines) < 2:
            return []

        # 各周期对应的分钟数
        period_minutes = {
            'H4': 240,
            'H1': 60,
            'M15': 15,
            'M5': 5,
            'M1': 1
        }

        interval = period_minutes.get(period, 1)
        gaps = []

        for i in range(1, len(klines)):
            prev_ts = KlineMerger._parse_timestamp(klines[i-1].timestamp)
            curr_ts = KlineMerger._parse_timestamp(klines[i].timestamp)

            if prev_ts and curr_ts:
                expected_diff = interval * 60  # 秒
                actual_diff = (curr_ts - prev_ts).total_seconds()

                # 如果实际差值大于预期的1.5倍，认为有缺口
                if actual_diff > expected_diff * 1.5:
                    gaps.append({
                        "start": klines[i-1].timestamp,
                        "end": klines[i].timestamp,
                        "missing_bars": int(actual_diff / expected_diff) - 1
                    })

        return gaps

    @staticmethod
    def _parse_timestamp(ts):
        """解析时间戳"""
        if isinstance(ts, datetime):
            return ts

        if isinstance(ts, str):
            try:
                return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    return datetime.strptime(ts, "%Y-%m-%d %H:%M")
                except:
                    return None

        return None