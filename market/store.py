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


def normalize_symbol(symbol: str) -> str:
    """
    标准化品种名称（保持原样）
    """
    return symbol if symbol else ""


class KlineData:
    """K线数据结构"""

    def __init__(self, symbol: str, period: str, timestamp, open_price: float,
                 high: float, low: float, close: float, volume: float = 0):
        self.symbol = normalize_symbol(symbol)
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

    def __init__(self):
        # 存储结构: {SYMBOL: {PERIOD: [KlineData, ...]}}
        self._klines = defaultdict(lambda: defaultdict(list))
        self._lock = threading.RLock()

        # 标记每个symbol每个周期是否已收到全量数据
        # 结构: {SYMBOL: {PERIOD: True/False}}
        self._initialized = defaultdict(lambda: defaultdict(bool))

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
        symbol = normalize_symbol(symbol)
        period = period.upper()

        if period not in self.PERIODS:
            return {"status": "error", "message": f"不支持的周期: {period}"}

        with self._lock:
            if is_full:
                # 全量数据，直接覆盖
                self._klines[symbol][period] = []

            # 解析并存储K线数据
            new_count = 0
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
        symbol = normalize_symbol(symbol)
        period = period.upper()

        with self._lock:
            klines = self._klines[symbol][period][-count:]
            return [k.to_dict() for k in klines]

    def get_all_klines(self, symbol: str, period: str) -> List[Dict]:
        """获取所有K线数据"""
        symbol = normalize_symbol(symbol)
        period = period.upper()

        with self._lock:
            return [k.to_dict() for k in self._klines[symbol][period]]

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """获取最新价格（从K线的最新close，优先M1，依次尝试其他周期）"""
        symbol = normalize_symbol(symbol)

        with self._lock:
            # 尝试找到匹配的symbol（支持带#后缀的symbol）
            actual_symbol = None
            if symbol in self._klines:
                actual_symbol = symbol
            else:
                # 尝试添加#后缀
                for s in self._klines:
                    if s.upper().startswith(symbol.upper()):
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
        symbol = normalize_symbol(symbol)
        period = period.upper()
        return self._initialized[symbol][period]

    def check_all_initialized(self, symbol: str) -> bool:
        """检查所有周期是否都已初始化"""
        symbol = normalize_symbol(symbol)
        return all(self._initialized[symbol][p] for p in self.PERIODS)

    def clear_symbol(self, symbol: str):
        """清除某个Symbol的数据"""
        symbol = normalize_symbol(symbol)
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