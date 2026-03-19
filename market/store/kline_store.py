#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K线数据存储模块
按周期和Symbol存储K线数据
"""

from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional
import threading


class KlineStore:
    """K线数据存储（只负责数据CRUD，不包含业务逻辑）"""

    # 支持的周期
    PERIODS = ['H4', 'H1', 'M15', 'M5', 'M1']

    # 各周期最大存储条数
    MAX_KLINES = {
        'H4': 1500,   # 4小时，6个月约1100根，留余量
        'H1': 1000,   # 1小时，1个月约720根
        'M15': 500,   # 15分钟，3天约288根
        'M5': 400,    # 5分钟，24小时288根
        'M1': 100     # 1分钟，1小时60根
    }

    # 各周期时间间隔（秒）
    PERIOD_INTERVALS = {
        'H4': 4 * 60 * 60,    # 4小时
        'H1': 1 * 60 * 60,    # 1小时
        'M15': 15 * 60,       # 15分钟
        'M5': 5 * 60,         # 5分钟
        'M1': 1 * 60          # 1分钟
    }

    def __init__(self):
        # 存储结构: {SYMBOL: {PERIOD: [KlineData, ...]}}
        # 这里存储的是字典格式，由 KlineService 转换
        self._klines = defaultdict(lambda: defaultdict(list))
        self._lock = threading.RLock()

        # 标记每个symbol每个周期是否已收到全量数据
        self._initialized = defaultdict(lambda: defaultdict(bool))

        # 记录每个symbol的M1数据最后更新时间（本地时间）
        self._m1_update_time = {}

        print("[KlineStore] K线存储已初始化")

    def save_klines(self, symbol: str, period: str, klines: List[Dict],
                    is_full: bool = False) -> Dict:
        """
        保存K线数据（纯存储操作）

        Args:
            symbol: 交易品种
            period: 周期
            klines: K线字典列表
            is_full: 是否为全量数据

        Returns:
            {"status": "ok", "count": N, "total": M, "is_full": bool}
        """
        period = period.upper()

        if period not in self.PERIODS:
            return {"status": "error", "message": f"不支持的周期: {period}"}

        with self._lock:
            if is_full:
                self._klines[symbol][period] = []
                print(f"[KlineStore] 收到 {symbol} {period} 全量数据，清空该周期历史数据")

            new_count = 0
            update_count = 0

            for k in klines:
                # 检查是否已存在相同时间戳的数据
                existing = self._klines[symbol][period]
                ts = self._normalize_timestamp(k.get('timestamp') or k.get('time'))

                found_idx = -1
                for i, existing_kline in enumerate(existing):
                    if self._normalize_timestamp(existing_kline.get('timestamp') or existing_kline.get('time')) == ts:
                        found_idx = i
                        break

                if found_idx >= 0:
                    existing[found_idx] = k
                    update_count += 1
                else:
                    existing.append(k)
                    new_count += 1

            # 按时间排序
            self._klines[symbol][period].sort(
                key=lambda x: self._normalize_timestamp(x.get('timestamp') or x.get('time'))
            )

            # 限制最大条数
            max_count = self.MAX_KLINES.get(period, 500)
            if len(self._klines[symbol][period]) > max_count:
                self._klines[symbol][period] = self._klines[symbol][period][-max_count:]

            self._initialized[symbol][period] = True

            if period == 'M1' and (new_count > 0 or update_count > 0):
                self._m1_update_time[symbol] = datetime.now()

            total = len(self._klines[symbol][period])
            print(f"[KlineStore] {symbol} {period} 保存了 {new_count} 条新数据, 更新 {update_count} 条, 当前共 {total} 条")

            return {
                "status": "ok",
                "count": new_count,
                "total": total,
                "is_full": is_full
            }

    def get_klines(self, symbol: str, period: str, count: int = 100) -> List[Dict]:
        """获取K线数据"""
        period = period.upper()
        with self._lock:
            klines = self._klines[symbol][period][-count:]
            return list(klines)

    def get_all_klines(self, symbol: str, period: str) -> List[Dict]:
        """获取所有K线数据"""
        period = period.upper()
        with self._lock:
            return list(self._klines[symbol][period])

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """获取最新价格（从K线的最新close，优先M1）"""
        with self._lock:
            actual_symbol = None
            if symbol in self._klines:
                actual_symbol = symbol
            else:
                symbol_base = symbol.replace('#', '')
                for s in self._klines:
                    if s.replace('#', '') == symbol_base:
                        actual_symbol = s
                        break

            if not actual_symbol:
                return None

            for period in ['M1', 'M5', 'M15', 'H1', 'H4']:
                klines = self._klines[actual_symbol][period]
                if klines:
                    return float(klines[-1].get('close', 0))
            return None

    def is_initialized(self, symbol: str, period: str) -> bool:
        """检查某个周期的数据是否已初始化"""
        period = period.upper()
        return self._initialized[symbol][period]

    def check_all_initialized(self, symbol: str) -> bool:
        """检查所有周期是否都已初始化"""
        return all(self._initialized[symbol][p] for p in self.PERIODS)

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的数据"""
        with self._lock:
            if symbol in self._klines:
                del self._klines[symbol]
            if symbol in self._initialized:
                del self._initialized[symbol]

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            status = {}
            for symbol in self._klines:
                status[symbol] = {}
                for period in self.PERIODS:
                    count = len(self._klines[symbol][period])
                    initialized = self._initialized[symbol][period]
                    status[symbol][period] = {
                        "count": count,
                        "initialized": initialized
                    }
            return status

    def get_symbols(self) -> List[str]:
        """获取所有有实际数据的symbol列表"""
        with self._lock:
            symbols = []
            for symbol in self._klines:
                has_data = False
                for period in self.PERIODS:
                    if len(self._klines[symbol][period]) > 0:
                        has_data = True
                        break
                if has_data:
                    symbols.append(symbol)
            return symbols

    def get_latest_kline_time(self, symbol: str, period: str = 'M1') -> Optional[datetime]:
        """获取指定品种和周期的最新K线时间戳"""
        period = period.upper()
        with self._lock:
            klines = self._klines[symbol][period]
            if not klines:
                return None

            latest_ts = klines[-1].get('timestamp') or klines[-1].get('time')
            return self._parse_timestamp(latest_ts)

    def check_m1_updated_within(self, symbol: str, seconds: int = 180) -> Dict:
        """检查M1 K线是否在指定秒数内更新"""
        with self._lock:
            has_m1_data = len(self._klines[symbol]['M1']) > 0

            if not has_m1_data:
                return {
                    "has_data": False,
                    "latest_time": None,
                    "update_time": None,
                    "seconds_ago": None,
                    "is_stale": True,
                    "market_status": "closed"
                }

            latest_time = self.get_latest_kline_time(symbol, 'M1')
            update_time = self._m1_update_time.get(symbol)

            if update_time is None:
                return {
                    "has_data": True,
                    "latest_time": latest_time,
                    "update_time": None,
                    "seconds_ago": None,
                    "is_stale": True,
                    "market_status": "closed"
                }

            now = datetime.now()
            seconds_ago = int((now - update_time).total_seconds())

            if seconds_ago > seconds:
                market_status = "stale"
            else:
                market_status = "active"

            return {
                "has_data": True,
                "latest_time": latest_time,
                "update_time": update_time,
                "seconds_ago": seconds_ago,
                "is_stale": seconds_ago > seconds,
                "market_status": market_status
            }

    def get_period_interval(self, period: str) -> int:
        """获取周期时间间隔（秒）"""
        return self.PERIOD_INTERVALS.get(period.upper(), 60)

    def get_m1_update_time(self, symbol: str) -> Optional[datetime]:
        """获取M1数据最后更新时间"""
        return self._m1_update_time.get(symbol)

    def _normalize_timestamp(self, ts) -> str:
        """标准化时间戳为字符串"""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts) if ts else ""

    def _parse_timestamp(self, ts) -> Optional[datetime]:
        """解析时间戳为datetime对象"""
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