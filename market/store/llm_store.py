#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 分析结果存储模块
"""

from datetime import datetime
from typing import Dict, Optional, List
import threading

from ..models import LLMConfig, LLMAnalysisResult
from sqlite_storage import (
    LLMConfigRepository,
    RuntimeStateRepository,
    bootstrap_runtime_storage,
)


class LLMStore:
    """LLM 分析结果存储（只负责数据CRUD）"""

    # 入场价提醒冷却时间（秒）
    ENTRY_ALERT_COOLDOWN = 300  # 5分钟

    def __init__(self, user_id: int = None, account_id: int = None):
        # 分析结果: {SYMBOL: LLMAnalysisResult}
        self._analysis_results: Dict[str, LLMAnalysisResult] = {}
        self._lock = threading.RLock()

        # 配置
        self._config = LLMConfig()
        self._repo = LLMConfigRepository()
        self._user_id = user_id
        if self._user_id is None:
            runtime_user = bootstrap_runtime_storage(self._build_password_credentials)
            self._user_id = runtime_user.user_id
        self._account_id = int(account_id or 0)
        self._runtime_repo = RuntimeStateRepository(
            self._user_id, self._account_id
        )

        # 入场价提醒记录: {(symbol, period, direction, entry_price): datetime}
        self._alerted_entries: Dict[tuple, datetime] = {}
        self._entry_alert_lock = threading.Lock()

        # 最后分析时间
        self._last_analysis_time: Optional[str] = None
        self._analysis_status = "idle"
        self._analysis_message = "尚未开始分析"

        # 加载配置文件
        self._load_config_from_file()
        self._load_analysis_results()

        print("[LLMStore] LLM存储已初始化")

    @staticmethod
    def _build_password_credentials(password: str):
        import hashlib
        import secrets

        salt = secrets.token_hex(16)
        password_hash = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return salt, password_hash

    # ==================== 配置管理 ====================

    @property
    def user_id(self) -> int:
        return int(self._user_id)

    def get_config(self) -> LLMConfig:
        """获取当前用户的有效配置，审批变化无需重启引擎。"""
        with self._lock:
            self._config = self._repo.get_effective_config(self._user_id)
            return self._config

    def update_config(
        self, api_key: str = None, api_base: str = None, model: str = None,
        system_prompt: str = None, analysis_prompt_template: str = None,
    ) -> LLMConfig:
        """更新配置"""
        if api_key is not None:
            self._config.api_key = api_key
        if api_base is not None:
            self._config.api_base = api_base
        if model is not None:
            self._config.model = model
        if system_prompt is not None:
            self._config.system_prompt = system_prompt
        if analysis_prompt_template is not None:
            self._config.analysis_prompt_template = analysis_prompt_template

        self._save_config_to_file()
        return self._config

    def _load_config_from_file(self):
        """从 SQLite 加载配置"""
        try:
            self._config = self._repo.get_effective_config(self._user_id)
            print("[LLMStore] 已从 SQLite 加载配置")
        except Exception as e:
            print(f"[LLMStore] 加载配置失败: {e}")

    def _save_config_to_file(self):
        """保存配置到 SQLite"""
        try:
            self._config = self._repo.save_config(
                self._user_id,
                api_key=self._config.api_key,
                api_base=self._config.api_base,
                model=self._config.model,
                system_prompt=self._config.system_prompt,
                analysis_prompt_template=self._config.analysis_prompt_template,
            )
            print("[LLMStore] 配置已保存到 SQLite")
        except Exception as e:
            print(f"[LLMStore] 保存配置失败: {e}")

    # ==================== 分析结果管理 ====================

    def _load_analysis_results(self) -> None:
        for payload in self._runtime_repo.list_entities("llm_analysis"):
            try:
                result = LLMAnalysisResult.from_dict(payload)
            except (TypeError, ValueError):
                continue
            self._analysis_results[result.symbol] = result
            if result.analyzed_at and (
                self._last_analysis_time is None
                or result.analyzed_at > self._last_analysis_time
            ):
                self._last_analysis_time = result.analyzed_at

    def set_scope(self, user_id: int, account_id: int) -> None:
        """切换账户范围并恢复该账户最近的 AI 分析。"""
        self._user_id = int(user_id)
        self._account_id = int(account_id or 0)
        self._runtime_repo.set_scope(self._user_id, self._account_id)
        with self._lock:
            self._analysis_results.clear()
            self._last_analysis_time = None
            self._load_analysis_results()

    def save_analysis(self, result: LLMAnalysisResult):
        """保存分析结果"""
        with self._lock:
            self._analysis_results[result.symbol] = result
            self._last_analysis_time = datetime.now().isoformat()
            self._runtime_repo.upsert_entity(
                "llm_analysis",
                result.symbol,
                result.to_dict(),
                symbol=result.symbol,
                status=result.market_status,
            )
            self._runtime_repo.trim_entities("llm_analysis", 50)

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
                result = self._analysis_results[symbol]
                result.market_status = market_status
                result.data_stale = data_stale
                self._runtime_repo.upsert_entity(
                    "llm_analysis", symbol, result.to_dict(),
                    symbol=symbol, status=market_status,
                )

    def set_stale_status(self, symbol: str, stale: bool, seconds_ago: int = None):
        """设置数据过期状态"""
        with self._lock:
            if symbol in self._analysis_results:
                result = self._analysis_results[symbol]
                result.data_stale = stale
                self._runtime_repo.upsert_entity(
                    "llm_analysis", symbol, result.to_dict(),
                    symbol=symbol, status=result.market_status,
                )

    def get_analyzed_symbols(self) -> List[str]:
        """获取已分析的品种列表"""
        with self._lock:
            return list(self._analysis_results.keys())

    def get_last_analysis_time(self) -> Optional[str]:
        """获取最后分析时间"""
        return self._last_analysis_time

    def set_analysis_status(self, status: str, message: str):
        """保存最近一次分析运行状态，供页面轮询展示。"""
        with self._lock:
            self._analysis_status = status
            self._analysis_message = message

    # ==================== 入场价提醒管理 ====================

    def check_entry_alert_cooldown(
        self, symbol: str, period: str, direction: str, entry_price: float,
        strategy_id: str = "", signal_source_id: str = "",
        analysis_id: str = "",
    ) -> bool:
        """
        检查入场价提醒是否在冷却期

        Returns:
            True 表示可以提醒，False 表示在冷却期
        """
        key = (
            analysis_id, strategy_id, signal_source_id, symbol, period,
            direction, entry_price
        )
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
            config = self.get_config()
            return {
                "enabled": config.enabled,
                "model": config.model,
                "api_base": config.api_base,
                "last_analysis_time": self._last_analysis_time,
                "symbols_analyzed": list(self._analysis_results.keys()),
                "analysis_status": self._analysis_status,
                "analysis_message": self._analysis_message,
            }
