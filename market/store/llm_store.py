#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 分析结果存储模块
"""

import os
import json
from datetime import datetime
from typing import Dict, Optional, List
import threading

from ..models import LLMConfig, LLMAnalysisResult


class LLMStore:
    """LLM 分析结果存储（只负责数据CRUD）"""

    # 配置文件路径
    CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "llm_config.json")

    # 入场价提醒冷却时间（秒）
    ENTRY_ALERT_COOLDOWN = 300  # 5分钟

    def __init__(self):
        # 分析结果: {SYMBOL: LLMAnalysisResult}
        self._analysis_results: Dict[str, LLMAnalysisResult] = {}
        self._lock = threading.RLock()

        # 配置
        self._config = LLMConfig()

        # 入场价提醒记录: {(symbol, period, direction, entry_price): datetime}
        self._alerted_entries: Dict[tuple, datetime] = {}
        self._entry_alert_lock = threading.Lock()

        # 最后分析时间
        self._last_analysis_time: Optional[str] = None

        # 加载配置文件
        self._load_config_from_file()

        print("[LLMStore] LLM存储已初始化")

    # ==================== 配置管理 ====================

    def get_config(self) -> LLMConfig:
        """获取配置"""
        return self._config

    def update_config(self, api_key: str = None, api_base: str = None, model: str = None) -> LLMConfig:
        """更新配置"""
        if api_key is not None:
            self._config.api_key = api_key
        if api_base is not None:
            self._config.api_base = api_base
        if model is not None:
            self._config.model = model

        self._save_config_to_file()
        return self._config

    def _load_config_from_file(self):
        """从文件加载配置"""
        try:
            if os.path.exists(self.CONFIG_FILE):
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._config = LLMConfig.from_dict(data)
                print(f"[LLMStore] 已从文件加载配置: {self.CONFIG_FILE}")
        except Exception as e:
            print(f"[LLMStore] 加载配置文件失败: {e}")

    def _save_config_to_file(self):
        """保存配置到文件"""
        try:
            config_dir = os.path.dirname(self.CONFIG_FILE)
            os.makedirs(config_dir, exist_ok=True)

            data = {
                "api_key": self._config.api_key,
                "api_base": self._config.api_base,
                "model": self._config.model
            }
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"[LLMStore] 配置已保存到文件")
        except Exception as e:
            print(f"[LLMStore] 保存配置文件失败: {e}")

    # ==================== 分析结果管理 ====================

    def save_analysis(self, result: LLMAnalysisResult):
        """保存分析结果"""
        with self._lock:
            self._analysis_results[result.symbol] = result
            self._last_analysis_time = datetime.now().isoformat()

    def save_analysis_dict(self, symbol: str, analysis: Dict):
        """从字典保存分析结果"""
        result = LLMAnalysisResult.from_api_response(symbol, analysis)
        self.save_analysis(result)

    def get_analysis(self, symbol: str = None) -> Optional[Dict]:
        """获取分析结果"""
        with self._lock:
            if symbol:
                result = self._analysis_results.get(symbol)
                return result.to_dict() if result else None
            return {s: r.to_dict() for s, r in self._analysis_results.items()}

    def get_analysis_result(self, symbol: str) -> Optional[LLMAnalysisResult]:
        """获取分析结果对象"""
        with self._lock:
            return self._analysis_results.get(symbol)

    def update_market_status(self, symbol: str, market_status: str, data_stale: bool = False,
                              stale_seconds: int = None):
        """更新市场状态"""
        with self._lock:
            if symbol in self._analysis_results:
                self._analysis_results[symbol].market_status = market_status
                self._analysis_results[symbol].data_stale = data_stale

    def set_stale_status(self, symbol: str, stale: bool, seconds_ago: int = None):
        """设置数据过期状态"""
        with self._lock:
            if symbol in self._analysis_results:
                self._analysis_results[symbol].data_stale = stale

    def get_analyzed_symbols(self) -> List[str]:
        """获取已分析的品种列表"""
        with self._lock:
            return list(self._analysis_results.keys())

    def get_last_analysis_time(self) -> Optional[str]:
        """获取最后分析时间"""
        return self._last_analysis_time

    # ==================== 入场价提醒管理 ====================

    def check_entry_alert_cooldown(self, symbol: str, period: str, direction: str,
                                    entry_price: float) -> bool:
        """
        检查入场价提醒是否在冷却期

        Returns:
            True 表示可以提醒，False 表示在冷却期
        """
        key = (symbol, period, direction, entry_price)
        current_time = datetime.now()

        with self._entry_alert_lock:
            if key in self._alerted_entries:
                last_alert = self._alerted_entries[key]
                elapsed = (current_time - last_alert).total_seconds()

                if elapsed < self.ENTRY_ALERT_COOLDOWN:
                    return False

            # 记录提醒时间
            self._alerted_entries[key] = current_time
            return True

    def cleanup_entry_alerts(self):
        """清理过期的入场价提醒记录"""
        current_time = datetime.now()

        with self._entry_alert_lock:
            keys_to_remove = []
            for key, alert_time in self._alerted_entries.items():
                elapsed = (current_time - alert_time).total_seconds()
                if elapsed > self.ENTRY_ALERT_COOLDOWN * 2:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._alerted_entries[key]

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取状态"""
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "model": self._config.model,
                "api_base": self._config.api_base,
                "last_analysis_time": self._last_analysis_time,
                "symbols_analyzed": list(self._analysis_results.keys())
            }