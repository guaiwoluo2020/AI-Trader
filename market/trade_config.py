#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易配置模块
"""

from typing import Dict, List
import threading
import json
import os


# 配置文件路径
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'trade_config.json')


class TradeConfig:
    """
    交易配置单例

    管理交易相关的配置参数，包括：
    - 默认手数、止损偏移
    - MT5时区偏移
    - 品种配置（手数、止损偏移、关键点位等）
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.enabled = True  # 是否启用自动生成

        # 默认配置
        self.default_volume = 0.01  # 默认手数
        self.default_sl_offset = 0.05  # 默认止损偏移（固定点数）

        # MT5服务器时区偏移（单位：小时）
        # 正数表示MT5时间比本地时间快，负数表示比本地时间慢
        # 例如：MT5服务器时间是GMT+2，本地时间是GMT+8，则偏移为 -6
        self.mt5_timezone_offset = 0

        # 按品种配置: {symbol: {"volume": 0.01, "sl_offset": 0.05, "key_levels": "5000,5100", "key_level_threshold": 0.0008}}
        self.symbol_config = {
            "GOLD#": {"volume": 0.01, "sl_offset": 0.5},
            "OILCASH#": {"volume": 0.01, "sl_offset": 0.05},
        }

        # 启动时自动加载配置文件
        self._load_from_file()

    def _load_from_file(self):
        """从配置文件加载配置"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.update(data)
                print(f"[TradeConfig] 已从配置文件加载: mt5_timezone_offset={self.mt5_timezone_offset}")
            else:
                print(f"[TradeConfig] 配置文件不存在: {CONFIG_FILE}，使用默认配置")
        except Exception as e:
            print(f"[TradeConfig] 加载配置文件失败: {e}，使用默认配置")

    def save_to_file(self):
        """保存配置到文件"""
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            print(f"[TradeConfig] 配置已保存到: {CONFIG_FILE}")
            return True
        except Exception as e:
            print(f"[TradeConfig] 保存配置文件失败: {e}")
            return False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_symbol_config(self, symbol: str) -> Dict:
        """获取品种配置，如果未配置则返回默认值"""
        if symbol in self.symbol_config:
            config = self.symbol_config[symbol]
            return {
                "volume": config.get("volume", self.default_volume),
                "sl_offset": config.get("sl_offset", self.default_sl_offset),
                "key_levels": config.get("key_levels", ""),
                "key_level_threshold": config.get("key_level_threshold", 0.0008)
            }
        return {
            "volume": self.default_volume,
            "sl_offset": self.default_sl_offset,
            "key_levels": "",
            "key_level_threshold": 0.0008
        }

    def get_key_levels(self, symbol: str) -> List[float]:
        """
        获取品种的关键点位列表

        Args:
            symbol: 品种名称

        Returns:
            关键点位列表，如 [5000, 5100, 5200]
        """
        config = self.get_symbol_config(symbol)
        key_levels_str = config.get("key_levels", "")
        if not key_levels_str:
            return []

        levels = []
        for level_str in key_levels_str.split(","):
            level_str = level_str.strip()
            if level_str:
                try:
                    levels.append(float(level_str))
                except ValueError:
                    continue
        return sorted(levels)

    def to_dict(self) -> Dict:
        return {
            "enabled": self.enabled,
            "default_volume": self.default_volume,
            "default_sl_offset": self.default_sl_offset,
            "mt5_timezone_offset": self.mt5_timezone_offset,
            "symbol_config": self.symbol_config
        }

    def update(self, data: Dict):
        if "enabled" in data:
            self.enabled = bool(data["enabled"])
        if "default_volume" in data:
            self.default_volume = float(data["default_volume"])
        if "default_sl_offset" in data:
            self.default_sl_offset = float(data["default_sl_offset"])
        if "mt5_timezone_offset" in data:
            self.mt5_timezone_offset = float(data["mt5_timezone_offset"])
        if "symbol_config" in data:
            self.symbol_config = data["symbol_config"]