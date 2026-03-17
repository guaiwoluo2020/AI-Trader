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


class KlineData:
    """K线数据结构"""

    def __init__(self, symbol: str, period: str, timestamp, open_price: float,
                 high: float, low: float, close: float, volume: float = 0):
        self.symbol = symbol
        self.period = period  # H4, H1, M15, M5, M1
        self.timestamp = timestamp
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def to_dict(self) -> Dict:
        """转换为字典"""
        ts = self.timestamp
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = str(ts)

        return {
            "symbol": self.symbol,
            "period": self.period,
            "timestamp": ts_str,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume
        }


class MarketStore:
    """K线数据存储"""

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
        self._klines = defaultdict(lambda: defaultdict(list))
        self._lock = threading.RLock()

        # 标记每个symbol每个周期是否已收到全量数据
        # 结构: {SYMBOL: {PERIOD: True/False}}
        self._initialized = defaultdict(lambda: defaultdict(bool))

        # 记录每个symbol的M1数据最后更新时间（本地时间，用于判断数据是否过期）
        # 结构: {SYMBOL: datetime}
        self._m1_update_time = {}

        print("[MarketStore] K线存储已初始化")

    def save_klines(self, symbol: str, period: str, klines: List[Dict],
                    is_full: bool = False) -> Dict:
        """
        保存K线数据

        Args:
            symbol: 交易品种
            period: 周期 (H4/H1/M15/M5/M1)
            klines: K线数据列表
            is_full: 是否为全量数据

        Returns:
            {"status": "ok", "count": N, "is_full": bool}
        """
        period = period.upper()

        if period not in self.PERIODS:
            return {"status": "error", "message": f"不支持的周期: {period}"}

        with self._lock:
            # 注意：EA推送全量时会按顺序推送所有周期（H4→H1→M15→M5→M1）
            # 每个周期单独推送，is_full=true
            # 所以这里只清空当前周期的数据，其他周期等待各自的推送
            if is_full:
                # 全量数据，清空该品种当前周期的历史数据
                self._klines[symbol][period] = []
                print(f"[MarketStore] 收到 {symbol} {period} 全量数据，清空该周期历史数据")

            # 解析并存储K线数据
            new_count = 0
            update_count = 0  # 记录更新的数据条数
            for k in klines:
                kline = KlineData(
                    symbol=symbol,
                    period=period,
                    timestamp=k.get('timestamp') or k.get('time'),
                    open_price=float(k.get('open', 0)),
                    high=float(k.get('high', 0)),
                    low=float(k.get('low', 0)),
                    close=float(k.get('close', 0)),
                    volume=float(k.get('volume', 0))
                )

                # 检查是否已存在相同时间戳的数据
                existing = self._klines[symbol][period]
                ts = kline.timestamp

                # 查找是否已存在
                found_idx = -1
                for i, existing_kline in enumerate(existing):
                    if self._normalize_timestamp(existing_kline.timestamp) == self._normalize_timestamp(ts):
                        found_idx = i
                        break

                if found_idx >= 0:
                    # 更新已有数据
                    existing[found_idx] = kline
                    update_count += 1
                else:
                    # 添加新数据
                    existing.append(kline)
                    new_count += 1

            # 按时间排序
            self._klines[symbol][period].sort(
                key=lambda x: self._normalize_timestamp(x.timestamp)
            )

            # 限制最大条数，保留最新的
            max_count = self.MAX_KLINES.get(period, 500)
            if len(self._klines[symbol][period]) > max_count:
                self._klines[symbol][period] = self._klines[symbol][period][-max_count:]

            # 标记已初始化
            self._initialized[symbol][period] = True

            # 如果是M1数据，更新最后更新时间（有新数据或更新数据都算）
            if period == 'M1' and (new_count > 0 or update_count > 0):
                self._m1_update_time[symbol] = datetime.now()

            total = len(self._klines[symbol][period])
            print(f"[MarketStore] {symbol} {period} 保存了 {new_count} 条新数据, 当前共 {total} 条")

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
            return [k.to_dict() for k in klines]

    def get_all_klines(self, symbol: str, period: str) -> List[Dict]:
        """获取所有K线数据"""
        period = period.upper()

        with self._lock:
            return [k.to_dict() for k in self._klines[symbol][period]]

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """获取最新价格（从K线的最新close，优先M1，依次尝试其他周期）"""
        with self._lock:
            # 尝试找到匹配的symbol
            actual_symbol = None
            if symbol in self._klines:
                actual_symbol = symbol
            else:
                # 尝试模糊匹配（去除#后缀）
                symbol_base = symbol.replace('#', '')
                for s in self._klines:
                    if s.replace('#', '') == symbol_base:
                        actual_symbol = s
                        break

            if not actual_symbol:
                return None

            # 按优先级尝试各周期（M1优先，然后更短周期）
            for period in ['M1', 'M5', 'M15', 'H1', 'H4']:
                klines = self._klines[actual_symbol][period]
                if klines:
                    return klines[-1].close
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
                # 检查是否有实际数据（任一周期有K线数据）
                has_data = False
                for period in self.PERIODS:
                    if len(self._klines[symbol][period]) > 0:
                        has_data = True
                        break
                if has_data:
                    symbols.append(symbol)
            return symbols

    def _normalize_timestamp(self, ts) -> str:
        """标准化时间戳为字符串"""
        if isinstance(ts, datetime):
            return ts.strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)

    def get_latest_kline_time(self, symbol: str, period: str = 'M1') -> Optional[datetime]:
        """
        获取指定品种和周期的最新K线时间戳

        Args:
            symbol: 品种名称
            period: 周期，默认M1

        Returns:
            最新K线时间戳，如果没有数据返回None
        """
        period = period.upper()

        with self._lock:
            klines = self._klines[symbol][period]
            if not klines:
                return None

            latest_ts = klines[-1].timestamp
            if isinstance(latest_ts, datetime):
                return latest_ts
            else:
                # 尝试解析字符串时间戳（支持多种格式）
                ts_str = str(latest_ts)
                for fmt in ["%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
                    try:
                        return datetime.strptime(ts_str, fmt)
                    except:
                        continue
                return None

    def check_m1_updated_within(self, symbol: str, seconds: int = 180) -> Dict:
        """
        检查M1 K线是否在指定秒数内更新

        Args:
            symbol: 品种名称
            seconds: 秒数，默认180秒（3分钟）

        Returns:
            {
                "has_data": bool,  # 是否有M1数据
                "latest_time": datetime,  # 最新K线时间（MT5服务器时间）
                "update_time": datetime,  # 服务端收到更新的时间（本地时间）
                "seconds_ago": int,  # 距今多少秒（基于本地更新时间）
                "is_stale": bool,  # 是否过期（超过指定秒数）
                "market_status": str  # 市场状态: "active", "stale", "closed"
            }
        """
        with self._lock:
            # 检查是否有M1数据
            has_m1_data = len(self._klines[symbol]['M1']) > 0

            if not has_m1_data:
                return {
                    "has_data": False,
                    "latest_time": None,
                    "update_time": None,
                    "seconds_ago": None,
                    "is_stale": True,
                    "market_status": "closed"  # 无数据，可能休市
                }

            # 获取最新K线时间（MT5服务器时间，仅用于显示）
            latest_time = self.get_latest_kline_time(symbol, 'M1')

            # 获取服务端收到更新的时间（本地时间，用于判断过期）
            update_time = self._m1_update_time.get(symbol)

            if update_time is None:
                # 有数据但没有更新时间记录，说明是服务重启前的旧数据
                # 这种情况也认为是休市，等下次推送数据时再处理
                return {
                    "has_data": True,
                    "latest_time": latest_time,
                    "update_time": None,
                    "seconds_ago": None,
                    "is_stale": True,
                    "market_status": "closed"  # 无新数据推送，可能休市
                }

            now = datetime.now()
            seconds_ago = int((now - update_time).total_seconds())

            if seconds_ago > seconds:
                market_status = "stale"  # 数据过期
            else:
                market_status = "active"  # 活跃

            return {
                "has_data": True,
                "latest_time": latest_time,
                "update_time": update_time,
                "seconds_ago": seconds_ago,
                "is_stale": seconds_ago > seconds,
                "market_status": market_status
            }

    def check_kline_continuity(self, symbol: str, period: str, new_klines: List[Dict]) -> Dict:
        """
        检查增量K线数据是否连续

        Args:
            symbol: 品种名称
            period: 周期
            new_klines: 新推送的K线数据列表

        Returns:
            {
                "is_continuous": bool,  # 是否连续
                "gap_count": int,       # 缺失的K线数量
                "last_existing_time": datetime,  # 现有数据最后时间
                "first_new_time": datetime,      # 新数据最早时间
                "expected_gap": int     # 期望的间隔（周期数）
            }
        """
        period = period.upper()

        if not new_klines:
            return {"is_continuous": True, "gap_count": 0}

        # 获取周期时间间隔（秒）
        interval = self.PERIOD_INTERVALS.get(period, 60)
        # 允许的间隔倍数（现有数据+1周期）
        max_allowed_gap = interval * 2  # 允许最多1个周期的间隔

        with self._lock:
            existing = self._klines[symbol][period]
            if not existing:
                # 没有历史数据，需要检查是否初始化
                return {"is_continuous": True, "gap_count": 0}

            # 获取现有数据最后时间
            last_existing = existing[-1]
            last_existing_time = self._parse_timestamp(last_existing.timestamp)
            if last_existing_time is None:
                return {"is_continuous": True, "gap_count": 0}

            # 获取新数据最早时间（新数据可能有多条，取最早的）
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

            # 如果新数据时间早于或等于现有数据，是更新操作，算连续
            if time_diff <= 0:
                return {
                    "is_continuous": True,
                    "gap_count": 0,
                    "last_existing_time": last_existing_time,
                    "first_new_time": first_new_time
                }

            # 计算间隔的周期数
            gap_periods = int(time_diff / interval)

            return {
                "is_continuous": gap_periods <= 1,  # 允许最多1个周期的间隔
                "gap_count": max(0, gap_periods - 1),  # 缺失的周期数
                "last_existing_time": last_existing_time,
                "first_new_time": first_new_time,
                "expected_gap": gap_periods
            }

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