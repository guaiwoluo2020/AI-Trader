#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线服务模块
处理K线相关的业务逻辑：时效性检查、连续性检查、格式转换等
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional

from ..store import KlineStore
from ..models import KlineData


class KlineService:
    """K线服务（处理业务逻辑）"""

    def __init__(self, store: KlineStore):
        self.store = store

    def process_kline_data(self, symbol: str, period: str, klines: List[Dict],
                           is_full: bool = False) -> Dict:
        """
        处理K线数据（包含业务逻辑）

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据列表
            is_full: 是否为全量数据

        Returns:
            处理结果
        """
        period = period.upper()

        # 保存数据
        result = self.store.save_klines(symbol, period, klines, is_full)

        return result

    def check_staleness(self, symbol: str, period: str, klines: List[Dict],
                        timezone_offset_hours: float = 0) -> Dict:
        """
        检查K线时效性

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线数据
            timezone_offset_hours: 时区偏移

        Returns:
            {
                "is_stale": bool,
                "latest_kline_time": datetime,
                "kline_time_local": datetime,
                "time_diff_seconds": int
            }
        """
        if not klines:
            return {"is_stale": False, "message": "无数据"}

        period_interval = self.store.get_period_interval(period)
        latest_kline = klines[-1] if klines else None

        if not latest_kline:
            return {"is_stale": False}

        ts = latest_kline.get('timestamp') or latest_kline.get('time')
        latest_kline_time = self._parse_timestamp(ts)

        if not latest_kline_time:
            return {"is_stale": False}

        now_local = datetime.now()
        kline_time_local = latest_kline_time - timedelta(hours=timezone_offset_hours)
        time_diff = (now_local - kline_time_local).total_seconds()

        return {
            "is_stale": time_diff > period_interval,
            "latest_kline_time": latest_kline_time,
            "kline_time_local": kline_time_local,
            "time_diff_seconds": int(time_diff),
            "period_interval": period_interval
        }

    def check_continuity(self, symbol: str, period: str, new_klines: List[Dict]) -> Dict:
        """
        检查增量K线数据是否连续

        Args:
            symbol: 品种名称
            period: 周期
            new_klines: 新推送的K线数据列表

        Returns:
            {
                "is_continuous": bool,
                "gap_count": int,
                "last_existing_time": datetime,
                "first_new_time": datetime
            }
        """
        period = period.upper()

        if not new_klines:
            return {"is_continuous": True, "gap_count": 0}

        interval = self.store.get_period_interval(period)

        existing = self.store.get_all_klines(symbol, period)
        if not existing:
            return {"is_continuous": True, "gap_count": 0}

        # 获取现有数据最后时间
        last_existing_time = self._parse_timestamp(
            existing[-1].get('timestamp') or existing[-1].get('time')
        )
        if last_existing_time is None:
            return {"is_continuous": True, "gap_count": 0}

        # 获取新数据最早时间
        first_new_time = None
        for k in new_klines:
            ts = self._parse_timestamp(k.get('timestamp') or k.get('time'))
            if ts:
                if first_new_time is None or ts < first_new_time:
                    first_new_time = ts

        if first_new_time is None:
            return {"is_continuous": True, "gap_count": 0}

        # 计算时间差
        time_diff = (first_new_time - last_existing_time).total_seconds()

        if time_diff <= 0:
            return {
                "is_continuous": True,
                "gap_count": 0,
                "last_existing_time": last_existing_time,
                "first_new_time": first_new_time
            }

        gap_periods = int(time_diff / interval)

        return {
            "is_continuous": gap_periods <= 1,
            "gap_count": max(0, gap_periods - 1),
            "last_existing_time": last_existing_time,
            "first_new_time": first_new_time,
            "expected_gap": gap_periods
        }

    def convert_to_kline_objects(self, klines: List[Dict], symbol: str, period: str) -> List[KlineData]:
        """
        将K线字典列表转换为KlineData对象列表

        Args:
            klines: K线字典列表
            symbol: 品种
            period: 周期

        Returns:
            KlineData对象列表
        """
        return [
            KlineData(
                symbol=symbol,
                period=period,
                timestamp=k.get('timestamp') or k.get('time'),
                open_price=float(k.get('open', 0)),
                high=float(k.get('high', 0)),
                low=float(k.get('low', 0)),
                close=float(k.get('close', 0)),
                volume=float(k.get('volume', 0))
            )
            for k in klines
        ]

    def get_klines(self, symbol: str, period: str, count: int = 100) -> List[Dict]:
        """获取K线数据"""
        return self.store.get_klines(symbol, period, count)

    def get_all_klines(self, symbol: str, period: str) -> List[Dict]:
        """获取所有K线数据"""
        return self.store.get_all_klines(symbol, period)

    def get_all_kline_objects(self, symbol: str, period: str) -> List[KlineData]:
        """获取所有K线数据并转换为KlineData对象"""
        klines = self.store.get_all_klines(symbol, period)
        return self.convert_to_kline_objects(klines, symbol, period)

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """获取最新价格"""
        return self.store.get_latest_price(symbol)

    def is_initialized(self, symbol: str, period: str) -> bool:
        """检查是否已初始化"""
        return self.store.is_initialized(symbol, period)

    def get_symbols(self) -> List[str]:
        """获取所有品种"""
        return self.store.get_symbols()

    def get_status(self) -> Dict:
        """获取状态"""
        return self.store.get_status()

    def check_m1_updated_within(self, symbol: str, seconds: int = 180) -> Dict:
        """检查M1数据更新情况"""
        return self.store.check_m1_updated_within(symbol, seconds)

    def get_period_interval(self, period: str) -> int:
        """获取周期时间间隔"""
        return self.store.get_period_interval(period)

    def check_symbols_status(self, symbols: List[str], stale_threshold: int = 180) -> Dict[str, List[str]]:
        """
        检查多个品种的数据更新状态

        Args:
            symbols: 品种列表
            stale_threshold: 过期阈值（秒），默认180秒（3分钟）

        Returns:
            {"active": [...], "stale": [...], "closed": [...]}
            - active: 指定秒数内有数据更新
            - stale: 超过指定秒数未更新
            - closed: 无数据
        """
        result = {"active": [], "stale": [], "closed": []}

        for symbol in symbols:
            m1_status = self.store.check_m1_updated_within(symbol, stale_threshold)
            market_status = m1_status.get("market_status", "closed")

            if market_status == "active":
                result["active"].append(symbol)
            elif market_status == "stale":
                result["stale"].append(symbol)
            else:
                result["closed"].append(symbol)

        return result

    def _parse_timestamp(self, ts) -> Optional[datetime]:
        """解析时间戳"""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        ts_str = str(ts)
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
            try:
                return datetime.strptime(ts_str, fmt)
            except:
                continue
        return None