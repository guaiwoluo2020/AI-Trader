#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 服务模块
处理 LLM 分析相关的业务逻辑
"""

import os
import json
import hashlib
import re
import time
import threading
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..models import LLMConfig, LLMAnalysisResult
from ..models.llm_config import (
    DEFAULT_ANALYSIS_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT,
    STRUCTURE_ANALYSIS_PROMPT_TEMPLATE,
)
from ..store import LLMStore
from .kline_service import KlineService
from repositories.ai import (
    AISignalSourceRepository, SharedAIRuntimeRepository,
)
from repositories.ai_suggestions import AITradeSuggestionRepository
from llm_governance import (
    AI_SIGNAL_ANALYSIS, LLMGovernanceService, LLMQuotaExceeded,
)


class LLMRequestError(RuntimeError):
    """大模型供应商请求失败。"""


class LLMResponseFormatError(LLMRequestError):
    """大模型响应不是可用的 JSON。"""


AI_SIGNAL_KLINE_MIN_COUNT = 10
AI_SIGNAL_KLINE_MAX_COUNT = 288


class LLMService:
    """LLM 服务（处理业务逻辑）"""

    # 分析间隔（秒）
    ANALYZE_INTERVAL = 300  # 5分钟
    MAX_RESPONSE_ATTEMPTS = 3
    # All account runtimes use the same active provider. Keep a process-wide
    # circuit breaker so a provider quota error cannot fan out across sources.
    _provider_blocked_until: Dict[str, float] = {}
    _provider_block_lock = threading.RLock()
    _quota_alert_last_sent: Dict[str, float] = {}
    _quota_alert_lock = threading.RLock()

    @staticmethod
    def _provider_key(config) -> str:
        """Use the active endpoint and model as the circuit-breaker identity.

        A provider can expose multiple models with independent quotas. Including
        the model also prevents a model switch from inheriting the old model's
        cooldown when the endpoint stays the same.
        """
        api_base = str(getattr(config, "api_base", "") or "").rstrip("/").lower()
        model = str(getattr(config, "model", "") or "").strip().lower()
        return f"{api_base}|{model}" if api_base else ""

    @classmethod
    def clear_provider_block(cls, config=None, api_base: str = "", model: str = "") -> None:
        """Clear a provider/model cooldown after configuration changes."""
        if config is not None:
            key = cls._provider_key(config)
        else:
            endpoint = str(api_base or "").rstrip("/").lower()
            key = f"{endpoint}|{str(model or '').strip().lower()}" if endpoint else ""
        if not key:
            return
        with cls._provider_block_lock:
            cls._provider_blocked_until.pop(key, None)

    @classmethod
    def _raise_if_provider_blocked(cls, config) -> None:
        key = cls._provider_key(config)
        if not key:
            return
        with cls._provider_block_lock:
            blocked_until = float(cls._provider_blocked_until.get(key, 0))
        remaining = blocked_until - time.time()
        if remaining > 0:
            minutes = max(1, int((remaining + 59) // 60))
            raise LLMRequestError(
                f"大模型供应商额度暂不可用，已暂停请求，约 {minutes} 分钟后再试"
            )

    @classmethod
    def _record_provider_throttle(cls, config, detail: str) -> None:
        """Pause the provider after 429s instead of repeatedly resending K lines."""
        wait_seconds = 15 * 60
        text = str(detail or "")
        match = re.search(
            r"reset(?:s)?\s+at\s+(\d{2})-(\d{2})\s+(\d{2}:\d{2}:\d{2})\s+UTC",
            text,
            flags=re.I,
        )
        if match:
            month, day, clock = match.groups()
            now_utc = datetime.now(timezone.utc)
            try:
                reset_at = datetime.strptime(
                    f"{now_utc.year}-{month}-{day} {clock}",
                    "%Y-%m-%d %H:%M:%S",
                ).replace(tzinfo=timezone.utc)
                if reset_at <= now_utc:
                    reset_at = reset_at.replace(year=now_utc.year + 1)
                wait_seconds = max(wait_seconds, int((reset_at - now_utc).total_seconds()))
            except ValueError:
                pass
        key = cls._provider_key(config)
        if not key:
            return
        blocked_until = time.time() + wait_seconds
        with cls._provider_block_lock:
            cls._provider_blocked_until[key] = max(
                float(cls._provider_blocked_until.get(key, 0)), blocked_until
            )
        print(
            "[LLMService] 供应商返回 429，已启用额度熔断，"
            f"暂停请求约 {max(1, int((wait_seconds + 59) // 60))} 分钟"
        )

    @staticmethod
    def _period_weight_items(periods) -> List[Tuple[str, int]]:
        """Return normalized (period, weight) pairs from dict/list period shapes."""
        if isinstance(periods, dict):
            items = []
            for period, config in periods.items():
                weight = config.get("weight", 0) if isinstance(config, dict) else config
                items.append((str(period).upper(), int(weight or 0)))
            return items
        if isinstance(periods, list):
            items = []
            for item in periods:
                if isinstance(item, dict):
                    period = item.get("period") or item.get("timeframe")
                    if period:
                        items.append((str(period).upper(), int(item.get("weight", 0) or 0)))
                elif item:
                    items.append((str(item).upper(), 0))
            return items
        return []

    @staticmethod
    def _coerce_analysis_response(response, analysis_plan: Dict[str, Dict]) -> Dict:
        """Accept common LLM response shapes without crashing replay jobs."""
        if isinstance(response, list):
            symbols = list(analysis_plan)
            if len(symbols) == 1:
                return {symbols[0]: {"trade_suggestions": response}}
            return {}
        if not isinstance(response, dict):
            return {}
        if "trade_suggestions" in response or "trend_analysis" in response:
            symbols = list(analysis_plan)
            if len(symbols) == 1:
                return {symbols[0]: response}
        return response

    # 各周期K线数量限制
    KLINE_LIMITS = {
        'H4': 20,
        'H1': 24,
        'M15': 32,
        'M5': 48,
        'M1': 60
    }

    # EA 默认约每 5 分钟同步一次完整 K 线；窗口略高于该节奏，避免
    # 两次同步之间把仍在线的行情误判为过期。
    STALE_THRESHOLD = 360  # 6分钟

    def __init__(self, llm_store: LLMStore, kline_service: KlineService):
        self.llm_store = llm_store
        self.kline_service = kline_service
        self._strategy_store = None
        self._allowed_strategy_ids = None
        self._source_last_analysis_at = {}
        self._persisted_source_results = {}
        self._shared_runtime_repo = SharedAIRuntimeRepository()
        self._ai_signal_source_repo = AISignalSourceRepository()
        self._trade_suggestion_repo = AITradeSuggestionRepository()
        self._plan_update_handler = None
        repo = getattr(self.llm_store, "_repo", None)
        self._llm_governance = (
            LLMGovernanceService(repo.storage) if repo is not None else None
        )

        # 从环境变量补充配置
        self._load_env_config()

        print("[LLMService] LLM服务已初始化")

    def set_strategy_store(self, strategy_store) -> None:
        """注入当前用户的策略仓储，用于约束分析范围。"""
        self._strategy_store = strategy_store

    def set_plan_update_handler(self, handler) -> None:
        """Notify the account runtime when an AI source publishes a new plan."""
        self._plan_update_handler = handler

    def _notify_quota_exhausted(self, source_ids: List[str], error: Exception) -> None:
        """Send one daily quota alert for this user; never block analysis cleanup."""
        key = str(getattr(self.llm_store, "user_id", "0"))
        now = time.time()
        with self._quota_alert_lock:
            last_sent = float(self._quota_alert_last_sent.get(key, 0))
            if now - last_sent < 24 * 3600:
                return
            self._quota_alert_last_sent[key] = now
        try:
            from email_verification import EmailVerificationService
            EmailVerificationService().send_admin_alert(
                "AI Trader · AI行情分析额度不足",
                (
                    "AI行情分析未执行：用户的免费大模型调用额度已用完。\n\n"
                    f"用户ID：{key}\n"
                    f"信号源：{', '.join(source_ids) or '未知'}\n"
                    f"原因：{error}\n\n"
                    "请在管理后台检查用户额度或模型调用配置。"
                ),
            )
            print(f"[LLMService] 已向管理员发送额度不足告警: user={key}")
        except Exception as notify_error:
            # 邮件故障不能影响行情分析调度主流程。
            print(f"[LLMService] 发送额度不足告警失败: {notify_error}")

    @staticmethod
    def _suggestion_signature(items: List[Dict]) -> tuple:
        """Compare material plan fields, not model wording or list order."""
        normalized = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            try:
                normalized.append((
                    str(item.get("direction") or "").lower(),
                    str(item.get("period") or "").upper(),
                    round(float(item.get("entry_price") or 0), 8),
                    round(float(item.get("stop_loss") or 0), 8),
                    round(float(item.get("take_profit") or 0), 8),
                    max(0, min(100, int(float(item.get("confidence") or 0)))),
                ))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(normalized))

    def _plan_updates_for_request(
        self, request_plan: Dict[str, Dict], response: Dict,
    ) -> List[Dict]:
        """Emit only newly published, changed or withdrawn source plans."""
        updates = []
        for symbol, plan in request_plan.items():
            analysis = response.get(symbol) if isinstance(response, dict) else None
            if not isinstance(analysis, dict):
                continue
            previous = self.llm_store.get_analysis_result(symbol)
            previous_items = list(getattr(previous, "trade_suggestions", []) or [])
            current_items = list(analysis.get("trade_suggestions") or [])
            for profile in plan.get("strategies", []):
                source_id = str(profile.get("signal_source_id") or "")
                if not source_id:
                    continue
                before = [item for item in previous_items if isinstance(item, dict) and str(item.get("signal_source_id") or "") == source_id]
                current = [item for item in current_items if isinstance(item, dict) and str(item.get("signal_source_id") or "") == source_id]
                if self._suggestion_signature(before) == self._suggestion_signature(current):
                    continue
                # Do not create a "cleared" event before this source has ever
                # produced a plan; an empty first analysis is ordinary state.
                if previous is None and not current:
                    continue
                updates.append({
                    "signal_source_id": source_id,
                    "symbol": symbol,
                    "period": str(profile.get("periods") and next(iter(profile["periods"])) or ""),
                    "suggestions": current,
                    "previous_suggestions": before,
                    "change_type": "created" if not before else ("withdrawn" if not current else "changed"),
                })
        return updates

    def set_allowed_strategy_ids(self, strategy_ids) -> None:
        """Limit live AI analysis to strategies deployed on this account."""
        self._allowed_strategy_ids = set(strategy_ids)

    def _source_is_due(self, source_id: str, interval_seconds: int) -> bool:
        """Keep per-source intervals valid across service restarts.

        The in-memory timestamp avoids a query for the normal path.  On the
        first lookup after startup, seed it from persisted LLM call records so
        a restart cannot schedule the same source again immediately.
        """
        source_id = str(source_id or "").strip()
        if not source_id:
            return False
        now = time.time()
        last_run = self._source_last_analysis_at.get(source_id)
        if last_run is None:
            storage = getattr(getattr(self.llm_store, "_repo", None), "storage", None)
            if storage is not None:
                row = storage.fetchone(
                    """
                    SELECT MAX(COALESCE(completed_at, created_at)) AS last_run_at
                    FROM llm_call_logs
                    WHERE user_id = ? AND scene_code = ?
                      AND object_type = 'ai_market_analysis'
                      AND FIND_IN_SET(?, object_id) > 0
                    """,
                    (self.llm_store.user_id, AI_SIGNAL_ANALYSIS, source_id),
                )
                last_run = float(row["last_run_at"] or 0) if row else 0.0
            else:
                last_run = 0.0
            self._source_last_analysis_at[source_id] = last_run
        return now - float(last_run or 0) >= max(1, int(interval_seconds))

    def get_persisted_source_result(
        self, source_id: str, symbol: str,
    ) -> Optional[Dict]:
        """Restore the latest successful result for one legacy source card."""
        cache_key = (str(source_id or ""), str(symbol or "").upper())
        if not all(cache_key):
            return None
        if cache_key in self._persisted_source_results:
            return self._persisted_source_results[cache_key]
        storage = getattr(getattr(self.llm_store, "_repo", None), "storage", None)
        if storage is None:
            return None
        row = storage.fetchone(
            """
            SELECT result_summary, COALESCE(completed_at, created_at) AS analyzed_at
            FROM llm_call_logs
            WHERE user_id = ? AND scene_code = ?
              AND object_type = 'ai_market_analysis' AND status = 'completed'
              AND FIND_IN_SET(?, object_id) > 0
            ORDER BY COALESCE(completed_at, created_at) DESC LIMIT 1
            """,
            (self.llm_store.user_id, AI_SIGNAL_ANALYSIS, cache_key[0]),
        )
        result = None
        if row:
            try:
                payload = json.loads(row["result_summary"] or "{}")
                result = payload.get(symbol) or payload.get(cache_key[1])
                if isinstance(result, dict):
                    result = dict(result)
                    result["analyzed_at"] = datetime.fromtimestamp(
                        float(row["analyzed_at"] or 0), timezone.utc
                    ).isoformat()
                    result.setdefault("market_status", "active")
                    result.setdefault("data_stale", False)
                else:
                    result = None
            except (TypeError, ValueError, json.JSONDecodeError, OverflowError):
                result = None
        self._persisted_source_results[cache_key] = result
        return result

    def _build_ai_analysis_plan(
        self, available_symbols: List[str], due_only: bool = False,
    ) -> Dict[str, Dict]:
        """聚合同一品种多策略启用的 AI 周期和分析约束。"""
        available = set(available_symbols)
        plan: Dict[str, Dict] = {}
        seen_sources = set()
        # AI signal sources are standalone runtimes.  A strategy only consumes
        # the source output for its own decision; deployment and bindings must
        # never decide whether the source is analyzed.
        for source in self._ai_signal_source_repo.list(
            self.llm_store.user_id, enabled_only=True
        ):
            if bool((source.get("config") or {}).get("analysis_paused")):
                continue
            self._append_independent_ai_source_to_plan(
                plan, source, due_only, seen_sources, available
            )
        return plan

    def _append_independent_ai_source_to_plan(
        self, plan: Dict[str, Dict], source: Dict, due_only: bool,
        seen_sources: set, available_symbols: set,
    ) -> None:
        if source.get("symbol") not in available_symbols:
            return
        params = dict(source.get("config") or {})
        if params.get("analysis_mode", "self_analysis") == "shared_reference":
            return
        source_id = str(source["signal_source_id"])
        source_key = ("ai_source", source_id)
        if source_key in seen_sources:
            return
        interval = max(1, int(params.get("analysis_interval_minutes", 5))) * 60
        if due_only and not self._source_is_due(source_id, interval):
            return
        seen_sources.add(source_key)
        symbol_plan = plan.setdefault(source["symbol"], {"periods": {}, "strategies": []})
        period = str(source.get("period") or "M5").upper()
        current = symbol_plan["periods"].get(period, {"weight": 0, "kline_count": 0})
        current["weight"] = max(current["weight"], 100)
        kline_count = max(
            AI_SIGNAL_KLINE_MIN_COUNT,
            min(AI_SIGNAL_KLINE_MAX_COUNT, int(params.get("kline_count", 100))),
        )
        current["kline_count"] = max(current["kline_count"], kline_count)
        symbol_plan["periods"][period] = current
        references = list(params.get("reference_market_data") or [])
        if str(params.get("signal_source_version") or "1.0") == "2.0":
            references = self._with_automatic_background_periods(
                source["symbol"], period, references, kline_count
            )
        symbol_plan["strategies"].append({
            # The runtime profile belongs to the AI source, not to a strategy.
            "strategy_id": "",
            "strategy_name": source.get("name") or "独立 AI 信号源",
            "signal_source_id": source_id,
            "periods": {period: 100},
            # Confidence is evaluated by each strategy binding, not the source.
            "min_confidence": 0,
            "analysis_interval_minutes": interval // 60,
            "forecast_horizon_bars": int(params.get("forecast_horizon_bars", 0) or 0),
            "kline_count": kline_count,
            "model": str(params.get("model") or ""),
            "system_prompt": str(params.get("system_prompt") or ""),
            "analysis_prompt_template": str(params.get("analysis_prompt_template") or ""),
            "signal_source_version": str(params.get("signal_source_version") or "1.0"),
            "analysis_template": str(params.get("analysis_template") or "custom"),
            "structure_config": {
                "range_min_touches": int(params.get("range_min_touches", 3) or 3),
                "range_min_inside_ratio": float(params.get("range_min_inside_ratio", 0.80) or 0.80),
                "range_tolerance_atr_multiplier": float(params.get("range_tolerance_atr_multiplier", 0.60) or 0.60),
                "range_tolerance_width_ratio": float(params.get("range_tolerance_width_ratio", 0.02) or 0.02),
                "range_min_width_atr": float(params.get("range_min_width_atr", 2.0) or 2.0),
                "range_max_width_atr": float(params.get("range_max_width_atr", 6.0) or 6.0),
            },
            "share_runtime_data": bool(source.get("share_runtime_data")),
            "reference_runtime_ids": list(params.get("reference_runtime_ids") or []),
            "reference_market_data": references,
            "signal_params": params,
            "symbol": source["symbol"],
            "strategy_lifecycle": "independent",
        })

    @staticmethod
    def _with_automatic_background_periods(
        symbol: str, period: str, references: List[Dict], kline_count: int,
    ) -> List[Dict]:
        """Add higher-timeframe context for 2.0 without changing its output period."""
        background_map = {
            "M1": ("M5", "M15"),
            "M5": ("M15", "H1"),
            "M15": ("H1", "H4"),
            "H1": ("H4",),
        }
        result = [dict(item) for item in (references or []) if isinstance(item, dict)]
        existing = {
            (str(item.get("symbol") or "").upper(), str(item.get("period") or "").upper())
            for item in result
        }
        for background_period in background_map.get(str(period).upper(), ()):
            key = (str(symbol).upper(), background_period)
            if key in existing:
                continue
            result.append({
                "symbol": symbol,
                "period": background_period,
                "kline_count": max(50, min(100, int(kline_count or 100))),
                "purpose": "automatic_background_filter",
            })
            existing.add(key)
        return result

    def _paper_deployed_strategies(self, available_symbols) -> List:
        repo = getattr(self.llm_store, "_repo", None)
        storage = getattr(repo, "storage", None)
        if storage is None:
            return []
        try:
            from ..models import TradingStrategy

            rows = storage.fetchall(
                """
                SELECT DISTINCT s.config_json AS runtime_strategy_json
                FROM strategy_deployments d
                JOIN trading_accounts a ON a.id = d.account_id
                JOIN user_strategy_configs s
                  ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
                WHERE d.user_id = ? AND d.execution_mode = 'paper'
                  AND d.status = 'active' AND d.symbol IN ({})
                  AND a.account_type = 'paper' AND a.status = 'active'
                  AND a.enabled = 1 AND a.trading_enabled = 1
                  AND a.auto_trading_enabled = 1
                """.format(",".join("?" for _ in available_symbols)),
                (self.llm_store.user_id, *list(available_symbols)),
            )
            return [
                TradingStrategy.from_dict(json.loads(row["runtime_strategy_json"]))
                for row in rows
                if row["runtime_strategy_json"]
            ]
        except Exception as exc:
            print(f"[LLMService] 加载模拟部署AI策略失败: {exc}")
            return []

    def _append_ai_source_to_plan(
        self, plan: Dict[str, Dict], strategy, source: Dict,
        due_only: bool, seen_sources: set,
    ) -> None:
        params = dict(source.get("params") or {})
        managed_source_id = str(params.get("ai_signal_source_id") or "").strip()
        if not managed_source_id:
            # AI configuration now belongs to the independent source library.
            return
        managed_source = self._ai_signal_source_repo.get(
            self.llm_store.user_id, managed_source_id
        )
        if managed_source is None or not managed_source.get("enabled"):
            return
        params = {
            **dict(managed_source.get("config") or {}),
            **{key: value for key, value in params.items() if key in {
                "ai_signal_source_id", "min_confidence", "entry_threshold",
                "entry_threshold_percent", "cooldown_seconds",
            }},
            "ai_signal_source_id": managed_source_id,
        }
        if params.get("analysis_mode", "self_analysis") == "shared_reference":
            return
        source_id = managed_source_id
        source_key = ("ai_source", managed_source_id)
        if source_key in seen_sources:
            self._merge_duplicate_ai_profile(
                plan, strategy, source, params,
                bool(managed_source.get("share_runtime_data")),
            )
            return
        interval = max(
            1, int(params.get("analysis_interval_minutes", 5))
        ) * 60
        if due_only and not self._source_is_due(source_id, interval):
            return
        seen_sources.add(source_key)
        period = source["period"]
        symbol_plan = plan.setdefault(
            strategy.symbol,
            {"periods": {}, "strategies": []},
        )
        current = symbol_plan["periods"].get(
            period, {"weight": 0, "kline_count": 0}
        )
        current["weight"] = max(current["weight"], int(source["weight"]))
        current["kline_count"] = max(
            current["kline_count"],
            max(
                AI_SIGNAL_KLINE_MIN_COUNT,
                min(AI_SIGNAL_KLINE_MAX_COUNT, int(params.get("kline_count", 100))),
            ),
        )
        symbol_plan["periods"][period] = current
        runtime_shared = bool(managed_source.get("share_runtime_data"))
        symbol_plan["strategies"].append({
            "strategy_id": strategy.strategy_id,
            "strategy_name": strategy.strategy_name,
            "signal_source_id": source_id,
            "periods": {period: int(source["weight"])},
            "min_confidence": int(
                params.get("min_confidence", strategy.min_confidence)
            ),
            "min_risk_reward": strategy.min_risk_reward,
            "analysis_interval_minutes": interval // 60,
            "forecast_horizon_bars": int(params.get("forecast_horizon_bars", 0) or 0),
            "kline_count": max(
                AI_SIGNAL_KLINE_MIN_COUNT,
                min(AI_SIGNAL_KLINE_MAX_COUNT, int(params.get("kline_count", 100))),
            ),
            "model": str(params.get("model") or ""),
            "system_prompt": str(params.get("system_prompt") or ""),
            "analysis_prompt_template": str(
                params.get("analysis_prompt_template") or ""
            ),
            "signal_source_version": str(params.get("signal_source_version") or "1.0"),
            "analysis_template": str(params.get("analysis_template") or "custom"),
            "structure_config": {
                "range_min_touches": int(params.get("range_min_touches", 3) or 3),
                "range_min_inside_ratio": float(params.get("range_min_inside_ratio", 0.80) or 0.80),
                "range_tolerance_atr_multiplier": float(params.get("range_tolerance_atr_multiplier", 0.60) or 0.60),
                "range_tolerance_width_ratio": float(params.get("range_tolerance_width_ratio", 0.02) or 0.02),
                "range_min_width_atr": float(params.get("range_min_width_atr", 2.0) or 2.0),
                "range_max_width_atr": float(params.get("range_max_width_atr", 6.0) or 6.0),
            },
            "share_runtime_data": runtime_shared,
            "reference_runtime_ids": list(
                params.get("reference_runtime_ids") or []
            ),
            "reference_market_data": list(
                params.get("reference_market_data") or []
            ),
            "signal_params": dict(params),
            "symbol": strategy.symbol,
            "strategy_lifecycle": strategy.lifecycle_status,
        })

    def _merge_duplicate_ai_profile(
        self, plan: Dict[str, Dict], strategy, source: Dict, params: Dict,
        runtime_shared: bool = False,
    ) -> None:
        """Merge repeated snapshots of the same AI source across deployments.

        The same strategy can be deployed to multiple paper/live accounts. Older
        snapshots should not prevent a newer snapshot from enabling runtime
        sharing for the same strategy/source pair.
        """
        source_id = source.get("signal_source_id", "")
        for symbol_plan in plan.values():
            for profile in symbol_plan.get("strategies", []):
                if (
                    profile.get("strategy_id") != strategy.strategy_id
                    or profile.get("signal_source_id") != source_id
                ):
                    continue
                profile["share_runtime_data"] = (
                    bool(profile.get("share_runtime_data"))
                    or bool(runtime_shared)
                )
                references = list(profile.get("reference_runtime_ids") or [])
                for item in params.get("reference_runtime_ids") or []:
                    if item not in references:
                        references.append(item)
                profile["reference_runtime_ids"] = references
                profile["signal_params"] = {
                    **dict(profile.get("signal_params") or {}),
                    **dict(params),
                }
                if not profile.get("model") and params.get("model"):
                    profile["model"] = str(params.get("model") or "")
                if (
                    not profile.get("system_prompt")
                    and params.get("system_prompt")
                ):
                    profile["system_prompt"] = str(
                        params.get("system_prompt") or ""
                    )
                if (
                    not profile.get("analysis_prompt_template")
                    and params.get("analysis_prompt_template")
                ):
                    profile["analysis_prompt_template"] = str(
                        params.get("analysis_prompt_template") or ""
                    )
                if not profile.get("signal_source_version"):
                    profile["signal_source_version"] = str(params.get("signal_source_version") or "1.0")
                return

    def _load_env_config(self):
        """从环境变量加载配置"""
        config = self.llm_store.get_config()

        if not config.api_key and os.environ.get("LLM_API_KEY"):
            self.llm_store.update_config(api_key=os.environ.get("LLM_API_KEY"))

        if os.environ.get("LLM_API_BASE"):
            self.llm_store.update_config(api_base=os.environ.get("LLM_API_BASE"))

        if os.environ.get("LLM_MODEL"):
            self.llm_store.update_config(model=os.environ.get("LLM_MODEL"))

    # ==================== 配置管理 ====================

    def get_config(self) -> Dict:
        """获取配置"""
        return self.llm_store.get_config().to_dict()

    def configure(
        self, api_key: str = None, api_base: str = None, model: str = None,
        system_prompt: str = None, analysis_prompt_template: str = None,
    ) -> Dict:
        """配置 LLM 参数"""
        previous_config = self.llm_store.get_config()
        config = self.llm_store.update_config(
            api_key, api_base, model, system_prompt, analysis_prompt_template
        )
        # Switching supplier/model must take effect in the live process. Do not
        # let a cooldown from the previous configuration block the new one.
        self.clear_provider_block(previous_config)
        self.clear_provider_block(config)
        return {
            "status": "ok",
            "enabled": config.enabled,
            "model": config.model,
            "api_base": config.api_base,
            "prompt_version": config.prompt_version,
        }

    def is_enabled(self) -> bool:
        """是否启用"""
        return self.llm_store.get_config().enabled

    # ==================== 数据收集 ====================

    def collect_klines_for_analysis(
        self,
        symbols: List[str],
        analysis_plan: Optional[Dict[str, Dict]] = None,
    ) -> Dict[str, Dict]:
        """
        收集指定品种的K线数据用于分析

        Returns:
            {symbol: {period: [klines]}}
        """
        all_klines = {}

        for symbol in symbols:
            klines_data = {}
            periods = (
                analysis_plan[symbol]["periods"].keys()
                if analysis_plan and symbol in analysis_plan
                else ['H4', 'H1', 'M15', 'M5', 'M1']
            )
            for period in periods:
                limit = (
                    int(analysis_plan[symbol]["periods"][period].get(
                        "kline_count", self.KLINE_LIMITS.get(period, 30)
                    ))
                    if analysis_plan and symbol in analysis_plan
                    else self.KLINE_LIMITS.get(period, 30)
                )
                klines = self.kline_service.get_klines(symbol, period, limit)
                if klines:
                    klines_data[period] = klines

            if klines_data:
                all_klines[symbol] = klines_data

        # Reference markets are optional context. They are fetched only for
        # the source currently being analyzed and never become output periods.
        references = []
        for symbol_plan in (analysis_plan or {}).values():
            for profile in symbol_plan.get("strategies", []):
                references.extend(profile.get("reference_market_data") or [])
        seen_references = set()
        for reference in references:
            ref_symbol = str(reference.get("symbol") or "").strip()
            ref_period = str(reference.get("period") or "").upper()
            key = (ref_symbol.upper(), ref_period)
            if not ref_symbol or not ref_period or key in seen_references:
                continue
            seen_references.add(key)
            # Do not overwrite primary data if a malformed/legacy config
            # happens to point at the same symbol and period.
            if ref_symbol in all_klines and ref_period in all_klines[ref_symbol]:
                continue
            klines = self.kline_service.get_klines(
                ref_symbol, ref_period,
                max(
                    AI_SIGNAL_KLINE_MIN_COUNT,
                    min(AI_SIGNAL_KLINE_MAX_COUNT, int(reference.get("kline_count", 100) or 100)),
                ),
            )
            if klines:
                all_klines.setdefault(ref_symbol, {})[ref_period] = klines

        return all_klines

    @staticmethod
    def _missing_primary_kline_data(
        all_klines: Dict[str, Dict], analysis_plan: Dict[str, Dict],
    ) -> List[str]:
        """Return primary symbol/periods that lack their requested K-line count."""
        missing = []
        for symbol, symbol_plan in analysis_plan.items():
            available_periods = all_klines.get(symbol, {})
            for period, settings in symbol_plan.get("periods", {}).items():
                required = max(10, int(settings.get("kline_count", 10) or 10))
                actual = len(available_periods.get(period, []))
                if actual < required:
                    missing.append(f"{symbol}/{period} ({actual}/{required})")
        return missing

    @staticmethod
    def _format_market_price(value) -> str:
        """Preserve broker price precision when serializing K-lines for the LLM."""
        try:
            return f"{float(value):.10f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            return str(value)

    def _current_price_context(
        self, all_klines: Dict[str, Dict], primary_keys: set,
    ) -> str:
        """Provide the latest observable quote separately from analysis K-lines."""
        quotes = []
        for symbol, _period in sorted(primary_keys):
            latest = []
            service = getattr(self, "kline_service", None)
            if service is not None:
                latest = service.get_klines(symbol, "M1", 1) or []
            if not latest:
                available = all_klines.get(symbol, {})
                latest = next((rows for rows in available.values() if rows), [])[-1:]
            if not latest:
                continue
            quote = latest[-1]
            quotes.append(
                f"- {symbol}: {self._format_market_price(quote.get('close'))} "
                f"（报价时间: {quote.get('timestamp', 'unknown')}，来源: 最新 M1）"
            )
        return "\n".join(quotes) or "当前报价暂不可用，禁止输出交易建议。"

    # ==================== Prompt 构建 ====================

    @staticmethod
    def _period_minutes(period: str) -> int:
        return {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}.get(str(period).upper(), 5)

    @classmethod
    def _observation_layers(cls, available_bars: int, period: str, interval_minutes: int, horizon_bars: int = 0) -> Dict:
        """Create observation layers from available data and source cadence."""
        available = max(0, int(available_bars))
        bar_minutes = cls._period_minutes(period)
        auto_horizon = max(3, min(48, int(round(max(interval_minutes * 2, bar_minutes * 6) / bar_minutes))))
        horizon = max(3, min(48, int(horizon_bars or auto_horizon)))
        raw = [horizon * 2, horizon * 4, horizon * 12, available]
        layers = []
        seen = set()
        for value, name, purpose in zip(raw, ("execution", "local_structure", "background", "full_context"), ("未来交易观察", "局部结构识别", "背景趋势识别", "完整上下文")):
            bars = min(available, max(10, int(value)))
            if bars >= 10 and bars not in seen:
                layers.append({"name": name, "bars": bars, "purpose": purpose})
                seen.add(bars)
        return {"period": str(period).upper(), "bar_minutes": bar_minutes, "analysis_interval_minutes": int(interval_minutes), "forecast_horizon_bars": horizon, "forecast_horizon_minutes": horizon * bar_minutes, "layers": layers}

    @staticmethod
    def _structure_features(
        klines: List[Dict], period: str = "M5", interval_minutes: int = 5,
        horizon_bars: int = 0, structure_config: Optional[Dict] = None,
    ) -> Dict:
        """Compute common and structure-specific evidence for source 2.0."""
        rows = list(klines or [])
        if len(rows) < 10:
            return {"status": "insufficient_data", "bar_count": len(rows)}
        structure_config = structure_config or {}
        min_touches = max(1, min(5, int(structure_config.get("range_min_touches", 3) or 3)))
        min_inside = max(0.5, min(1.0, float(structure_config.get("range_min_inside_ratio", 0.80) or 0.80)))
        tolerance_atr = max(0.1, min(3.0, float(structure_config.get("range_tolerance_atr_multiplier", 0.60) or 0.60)))
        tolerance_width = max(0.005, min(0.2, float(structure_config.get("range_tolerance_width_ratio", 0.02) or 0.02)))
        min_width_atr = max(0.0, min(20.0, float(structure_config.get("range_min_width_atr", 2.0) or 2.0)))
        max_width_atr = max(min_width_atr, min(50.0, float(structure_config.get("range_max_width_atr", 6.0) or 6.0)))
        layers = LLMService._observation_layers(len(rows), period, interval_minutes, horizon_bars)
        candidates = []
        for layer in layers["layers"]:
            window = rows[-layer["bars"]:]
            closes = [float(x.get("close", 0) or 0) for x in window]
            highs = [float(x.get("high", 0) or 0) for x in window]
            lows = [float(x.get("low", 0) or 0) for x in window]
            true_ranges = []
            previous_close = None
            for high, low, close in zip(highs, lows, closes):
                if previous_close is None:
                    true_range = max(0.0, high - low)
                else:
                    true_range = max(
                        0.0, high - low,
                        abs(high - previous_close),
                        abs(low - previous_close),
                    )
                true_ranges.append(true_range)
                previous_close = close
            atr_period = min(14, len(true_ranges))
            if atr_period:
                atr = sum(true_ranges[:atr_period]) / atr_period
                for true_range in true_ranges[atr_period:]:
                    # Wilder's RMA is the standard ATR smoothing method.
                    atr = ((atr * (atr_period - 1)) + true_range) / atr_period
            else:
                atr = 0.0
            upper = sorted(highs)[max(0, int(len(highs) * .90) - 1)]
            lower = sorted(lows)[min(len(lows) - 1, int(len(lows) * .10))]
            width = max(upper - lower, 1e-12)
            tolerance = max(atr * tolerance_atr, width * tolerance_width, 1e-12)
            pivot_highs, pivot_lows = [], []
            for index in range(2, len(window) - 2):
                if highs[index] >= max(highs[index - 2:index + 3]) and highs[index] > highs[index - 1]:
                    pivot_highs.append(highs[index])
                if lows[index] <= min(lows[index - 2:index + 3]) and lows[index] < lows[index - 1]:
                    pivot_lows.append(lows[index])
            touches_high = sum(abs(x - upper) <= tolerance for x in pivot_highs)
            touches_low = sum(abs(x - lower) <= tolerance for x in pivot_lows)
            inside = sum(lower - tolerance <= x <= upper + tolerance for x in closes) / len(closes)
            slope = (closes[-1] - closes[0]) / max(1, len(closes) - 1)
            first = max(1, closes[0])
            change_pct = (closes[-1] - closes[0]) / first * 100
            traveled = sum(
                abs(closes[index] - closes[index - 1])
                for index in range(1, len(closes))
            )
            efficiency_ratio = (
                abs(closes[-1] - closes[0]) / traveled if traveled > 0 else 0
            )
            higher_highs = sum(pivot_highs[i] > pivot_highs[i - 1] for i in range(1, len(pivot_highs)))
            higher_lows = sum(pivot_lows[i] > pivot_lows[i - 1] for i in range(1, len(pivot_lows)))
            lower_highs = sum(pivot_highs[i] < pivot_highs[i - 1] for i in range(1, len(pivot_highs)))
            lower_lows = sum(pivot_lows[i] < pivot_lows[i - 1] for i in range(1, len(pivot_lows)))
            upper_slope = ((pivot_highs[-1] - pivot_highs[0]) / max(1, len(pivot_highs) - 1)) if len(pivot_highs) >= 2 else 0
            lower_slope = ((pivot_lows[-1] - pivot_lows[0]) / max(1, len(pivot_lows) - 1)) if len(pivot_lows) >= 2 else 0
            half = max(2, len(window) // 2)
            first_width = max(max(highs[:half]) - min(lows[:half]), 1e-12)
            last_width = max(max(highs[-half:]) - min(lows[-half:]), 1e-12)
            width_slope = (last_width - first_width) / max(1, len(window) // 2)
            triangle_type = "none"
            if len(pivot_highs) >= 2 and len(pivot_lows) >= 2:
                if upper_slope < 0 and lower_slope > 0 and width_slope < 0:
                    triangle_type = "converging"
                elif upper_slope > 0 and lower_slope < 0 and width_slope > 0:
                    triangle_type = "diverging"
            previous_upper = max(highs[:-1]) if len(highs) > 2 else upper
            previous_lower = min(lows[:-1]) if len(lows) > 2 else lower
            breakout = "up" if closes[-1] > previous_upper + tolerance else "down" if closes[-1] < previous_lower - tolerance else "none"
            candidates.append({
                "layer": layer["name"], "bars": layer["bars"],
                "common": {"atr": round(atr, 8), "change_pct": round(change_pct, 4), "slope": round(slope, 8), "efficiency_ratio": round(efficiency_ratio, 4), "pivot_high_count": len(pivot_highs), "pivot_low_count": len(pivot_lows)},
                "range_features": {"upper": upper, "lower": lower, "width_atr": round(width / atr, 4) if atr else 0, "touch_upper": touches_high, "touch_lower": touches_low, "inside_ratio": round(inside, 4), "tolerance": round(tolerance, 8), "is_candidate": bool(touches_high >= min_touches and touches_low >= min_touches and inside >= min_inside and (not atr or min_width_atr <= width / atr <= max_width_atr))},
                "range_confirmation": {"min_touches": min_touches, "min_inside_ratio": min_inside, "tolerance_atr_multiplier": tolerance_atr, "tolerance_width_ratio": tolerance_width, "min_width_atr": min_width_atr, "max_width_atr": max_width_atr},
                "trend_features": {"higher_highs": higher_highs, "higher_lows": higher_lows, "lower_highs": lower_highs, "lower_lows": lower_lows, "trendline_slope": round(slope, 8)},
                "breakout_features": {"state": breakout, "confirmation_bars": 0 if breakout == "none" else 1},
                "triangle_features": {"type": triangle_type, "upper_slope": round(upper_slope, 8), "lower_slope": round(lower_slope, 8), "width_slope": round(width_slope, 8), "apex_distance_bars": int(width / max(abs(width_slope), 1e-12)) if triangle_type == "converging" else 0},
            })
        return {"status": "ok", "bar_count": len(rows), "observation": layers, "candidates": candidates, "last_close": float(rows[-1].get("close", 0) or 0)}

    @staticmethod
    def _classify_background_features(features: Dict) -> Dict:
        """Turn deterministic K-line evidence into an auditable background label."""
        candidates = list((features or {}).get("candidates") or [])
        if not candidates:
            return {
                "structure": "mixed", "confidence": 0,
                "reason": "高周期K线不足，无法预计算背景结构",
            }
        candidate = next(
            (item for item in candidates if item.get("layer") == "background"),
            candidates[-1],
        )
        common = candidate.get("common") or {}
        range_features = candidate.get("range_features") or {}
        trend = candidate.get("trend_features") or {}
        triangle = candidate.get("triangle_features") or {}
        change = float(common.get("change_pct") or 0)
        efficiency = float(common.get("efficiency_ratio") or 0)
        touches_upper = int(range_features.get("touch_upper") or 0)
        touches_lower = int(range_features.get("touch_lower") or 0)
        inside = float(range_features.get("inside_ratio") or 0)
        higher = int(trend.get("higher_highs") or 0) + int(trend.get("higher_lows") or 0)
        lower = int(trend.get("lower_highs") or 0) + int(trend.get("lower_lows") or 0)
        triangle_type = str(triangle.get("type") or "none")

        range_candidate = bool(range_features.get("is_candidate"))
        if triangle_type in {"converging", "diverging"}:
            structure = f"{triangle_type}_triangle"
            confidence = min(88, 60 + min(20, (higher + lower) * 3))
            reason = (
                f"高低点边界呈{'收敛' if triangle_type == 'converging' else '扩散'}，"
                f"上沿斜率 {float(triangle.get('upper_slope') or 0):.4f}，"
                f"下沿斜率 {float(triangle.get('lower_slope') or 0):.4f}"
            )
        elif range_candidate:
            structure = "range"
            confirmation = candidate.get("range_confirmation") or {}
            configured_inside = float(confirmation.get("min_inside_ratio") or 0.80)
            confidence = min(
                90, 55 + min(18, (touches_upper + touches_lower) * 3)
                + int(max(0, inside - configured_inside) * 40),
            )
            reason = (
                f"价格在箱体内比例 {inside:.0%}，"
                f"上沿触碰 {touches_upper} 次、下沿触碰 {touches_lower} 次"
            )
        elif change > 0 and (
            higher >= max(2, lower + 1) or efficiency >= 0.45
        ):
            structure = "uptrend"
            confidence = min(90, 55 + min(16, higher * 4) + min(12, int(abs(change) * 3)) + int(efficiency * 10))
            reason = f"背景涨幅 {change:.2f}%，高点/低点抬升证据 {higher} 项，方向效率 {efficiency:.0%}"
        elif change < 0 and (
            lower >= max(2, higher + 1) or efficiency >= 0.45
        ):
            structure = "downtrend"
            confidence = min(90, 55 + min(16, lower * 4) + min(12, int(abs(change) * 3)) + int(efficiency * 10))
            reason = f"背景跌幅 {change:.2f}%，高点/低点下移证据 {lower} 项，方向效率 {efficiency:.0%}"
        else:
            structure = "mixed"
            confidence = max(35, min(60, 45 + abs(higher - lower) * 2))
            reason = (
                f"趋势与箱体证据均不足：涨跌幅 {change:.2f}%，"
                f"抬升证据 {higher}，下移证据 {lower}，箱体内部比例 {inside:.0%}"
            )
        return {
            "structure": structure,
            "confidence": int(confidence),
            "reason": reason,
            "evidence": {
                "bars": int(candidate.get("bars") or 0),
                "change_pct": change,
                "touch_upper": touches_upper,
                "touch_lower": touches_lower,
                "inside_ratio": inside,
                "efficiency_ratio": efficiency,
                "higher_structure_count": higher,
                "lower_structure_count": lower,
            },
        }

    @classmethod
    def _combine_background_periods(cls, periods: Dict[str, Dict]) -> Dict:
        """Combine short and long background periods without hiding conflicts."""
        if not periods:
            return {"periods": {}, "combined": "mixed", "confidence": 0, "reason": "没有可用高周期K线"}
        ordered = sorted(
            periods.items(), key=lambda item: cls._period_minutes(item[0])
        )
        structures = [item[1].get("structure", "mixed") for item in ordered]
        directional = [
            "up" if value == "uptrend" else "down" if value == "downtrend" else value
            for value in structures
        ]
        longest = directional[-1]
        shortest = directional[0]
        if "up" in directional and "down" in directional:
            combined = "conflict"
            reason = "不同高周期趋势方向冲突，执行层不采用逆势箱体反转"
        elif all(value == "up" for value in directional):
            combined, reason = "uptrend", "所有可用高周期均为上涨背景"
        elif all(value == "down" for value in directional):
            combined, reason = "downtrend", "所有可用高周期均为下跌背景"
        elif all(value == "range" for value in directional):
            combined, reason = "range", "所有可用高周期均为震荡背景"
        elif longest == "up" and shortest == "range":
            combined, reason = "uptrend_with_local_range", "长周期上涨、较短背景周期箱体整理"
        elif longest == "down" and shortest == "range":
            combined, reason = "downtrend_with_local_range", "长周期下跌、较短背景周期箱体整理"
        else:
            combined, reason = "mixed", "高周期结构未形成一致背景"
        confidence = int(sum(int(item.get("confidence") or 0) for item in periods.values()) / max(1, len(periods)))
        if combined in {"conflict", "mixed"}:
            confidence = min(confidence, 55)
        return {"periods": periods, "combined": combined, "confidence": confidence, "reason": reason}

    def build_analysis_prompt(
        self,
        all_klines: Dict[str, Dict],
        analysis_plan: Optional[Dict[str, Dict]] = None,
        analysis_prompt_template: Optional[str] = None,
        reference_context: str = "",
    ) -> str:
        """Build a source-level prompt with one primary market and optional context."""
        market_sections = []
        reference_sections = []
        primary_keys = set()
        reference_keys = set()
        for symbol_plan in (analysis_plan or {}).values():
            for profile in symbol_plan.get("strategies", []):
                primary_symbol = str(profile.get("symbol") or "").strip().upper()
                for period in profile.get("periods") or {}:
                    primary_keys.add((primary_symbol, str(period).upper()))
                for reference in profile.get("reference_market_data") or []:
                    reference_keys.add((
                        str(reference.get("symbol") or "").strip().upper(),
                        str(reference.get("period") or "").strip().upper(),
                    ))
        for symbol, klines_data in all_klines.items():
            for period, klines in klines_data.items():
                market_lines = [f"### {symbol} / {period}（{len(klines)}根K线）"]
                market_lines.append("| 时间 | 开盘 | 最高 | 最低 | 收盘 |")
                market_lines.append("|------|------|------|------|------|")
                for k in klines:
                    market_lines.append(
                        f"| {k['timestamp']} | {self._format_market_price(k['open'])} | "
                        f"{self._format_market_price(k['high'])} | "
                        f"{self._format_market_price(k['low'])} | "
                        f"{self._format_market_price(k['close'])} |"
                    )
                section = "\n".join(market_lines)
                key = (str(symbol).upper(), str(period).upper())
                if key in primary_keys:
                    market_sections.append(section)
                elif key in reference_keys:
                    reference_sections.append(section)

        config = self.llm_store.get_config()
        template = analysis_prompt_template or getattr(
            config, "analysis_prompt_template", DEFAULT_ANALYSIS_PROMPT_TEMPLATE
        )
        v2 = any(
            str(profile.get("signal_source_version") or "1.0") == "2.0"
            for symbol_plan in (analysis_plan or {}).values()
            for profile in symbol_plan.get("strategies", [])
        )
        structure_sections = []
        background_sections = []
        if v2:
            for symbol, klines_data in all_klines.items():
                for period, klines in klines_data.items():
                    if (str(symbol).upper(), str(period).upper()) in primary_keys:
                        profile = next(
                            (item for item in (analysis_plan or {}).get(symbol, {}).get("strategies", [])
                             if str(item.get("symbol") or "").upper() == str(symbol).upper()),
                            {},
                        )
                        structure_sections.append(
                            f"### {symbol} / {period}\n" + json.dumps(
                                self._structure_features(
                                    klines, period,
                                    int(profile.get("analysis_interval_minutes", 5) or 5),
                                    int(profile.get("forecast_horizon_bars", 0) or 0),
                                    profile.get("structure_config") or {},
                                ), ensure_ascii=False
                            )
                        )
            for symbol, symbol_plan in (analysis_plan or {}).items():
                period_results = {}
                for profile in symbol_plan.get("strategies", []):
                    for reference in profile.get("reference_market_data") or []:
                        if reference.get("purpose") != "automatic_background_filter":
                            continue
                        ref_symbol = str(reference.get("symbol") or symbol)
                        ref_period = str(reference.get("period") or "").upper()
                        rows = (all_klines.get(ref_symbol) or {}).get(ref_period) or []
                        if not rows:
                            continue
                        period_results[ref_period] = self._classify_background_features(
                            self._structure_features(
                                rows, ref_period,
                                int(profile.get("analysis_interval_minutes", 5) or 5),
                                int(profile.get("forecast_horizon_bars", 0) or 0),
                                profile.get("structure_config") or {},
                            )
                        )
                combined_background = self._combine_background_periods(period_results)
                symbol_plan["background_analysis"] = combined_background
                background_sections.append(
                    f"### {symbol}\n" + json.dumps(
                        combined_background, ensure_ascii=False
                    )
                )
            template = STRUCTURE_ANALYSIS_PROMPT_TEMPLATE
        # The old placeholder is deliberately blank: a source is no longer
        # given strategy constraints, even when an old custom template has it.
        prompt = template.replace("{{strategy_context}}", "").replace(
            "{{market_data}}", "\n\n".join(market_sections)
        )
        current_price_text = self._current_price_context(all_klines, primary_keys)
        prompt = prompt.replace("{{current_price}}", current_price_text)
        prompt = prompt.replace("{{structure_features}}", "\n\n".join(structure_sections))
        prompt = prompt.replace("{{background_features}}", "\n\n".join(background_sections))
        if "{{current_price}}" not in template:
            prompt += "\n\n## 当前可交易参考价\n" + current_price_text
        reference_text = "\n\n".join(reference_sections)
        prompt = prompt.replace("{{reference_market_data}}", reference_text)
        if reference_text and "{{reference_market_data}}" not in template:
            prompt += (
                "\n\n## 可选参考行情（仅辅助判断，不生成独立交易信号）\n"
                + reference_text
            )
        if reference_context:
            prompt += (
                "\n\n## 其他用户共享的历史AI运行数据（仅供参考）\n"
                "这些数据可能来自不同账户或行情源，不得替代当前K线判断：\n"
                f"{reference_context}"
            )
        return prompt + (
            "\n\n## 信号源输出要求\n"
            "trade_suggestions 中每条建议必须包含 signal_source_id 和 period，"
            "且只能对应主行情与当前 AI 信号源。参考行情只用于上下文，不能出现在 "
            "trend_analysis 或 trade_suggestions 中。当前可交易参考价只用于理解当前位置和风险，"
            "不限制 entry_price 必须接近当前价。交易建议应是由K线结构得出的可执行价格计划："
            "区间震荡可在确认支撑附近给 buy、确认压力附近给 sell，止损置于区间外；"
            "单边趋势可在回调/反抽至趋势线、支撑或压力时给顺势入场计划，并给出失效止损。"
            "只有没有可辩护的结构化入场计划时才返回空数组。实时策略会在后续 Tick 接近 "
            "entry_price 时再决定是否形成入场信号。"
        )

    def prompt_hash(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """同时覆盖 system 和 user prompt，供回测缓存与审计使用。"""
        config = self.llm_store.get_config()
        version = getattr(config, "prompt_version", 1)
        system_prompt = system_prompt or getattr(
            config, "system_prompt", DEFAULT_SYSTEM_PROMPT
        )
        payload = f"v{version}\n{system_prompt}\n{prompt}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _scene_defaults(self, scene_code: str) -> Dict:
        if self._llm_governance is None:
            return {}
        try:
            return self._llm_governance.scene_options(
                self.llm_store.user_id, scene_code
            )
        except Exception:
            return {}

    # ==================== LLM API 调用 ====================

    def call_llm(
        self, prompt: str, model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        scene_code: str = AI_SIGNAL_ANALYSIS,
        object_type: str = "", object_id: str = "",
        max_tokens: int = 4000,
    ) -> Optional[Dict]:
        """调用 LLM API；响应格式错误时最多尝试三次。"""
        for attempt in range(1, self.MAX_RESPONSE_ATTEMPTS + 1):
            attempt_prompt = self._retry_prompt(prompt, attempt)
            try:
                return self._call_llm_once(
                    attempt_prompt, model, system_prompt,
                    scene_code, object_type, object_id, max_tokens,
                )
            except LLMResponseFormatError as exc:
                if attempt >= self.MAX_RESPONSE_ATTEMPTS:
                    raise LLMResponseFormatError(
                        f"{exc} 已尝试 {self.MAX_RESPONSE_ATTEMPTS} 次。"
                    ) from exc
                print(
                    "[LLMService] 响应格式无效，立即重试 "
                    f"({attempt + 1}/{self.MAX_RESPONSE_ATTEMPTS})"
                )
        return None

    def _call_llm_once(
        self, prompt: str, model: Optional[str], system_prompt: Optional[str],
        scene_code: str, object_type: str, object_id: str, max_tokens: int,
    ) -> Optional[Dict]:
        governance = self._llm_governance
        if governance is None:
            config = self.llm_store.get_config()
            reservation = {"model": model or config.model}
        else:
            reservation = governance.reserve_call(
                self.llm_store.user_id, scene_code, model, object_type, object_id
            )
            config = reservation["config"]
        # Resolve the effective provider first. This is important after an
        # admin switches suppliers while existing engines remain alive.
        self._raise_if_provider_blocked(config)

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": reservation["model"],
                # Structured JSON responses must use the final answer channel.
                # Thinking models may otherwise put all output in reasoning_content.
                "enable_thinking": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            system_prompt
                            or reservation.get("system_prompt")
                            or getattr(config, "system_prompt", DEFAULT_SYSTEM_PROMPT)
                        ),
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": max(1, int(max_tokens)),
                # AI signal analysis has a strict machine-readable contract.
                # JSON mode prevents reasoning text from becoming the response.
                "response_format": {"type": "json_object"},
            }

            response = requests.post(
                f"{config.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120
            )

            if response.status_code != 200:
                detail = self._provider_error_detail(response)
                if response.status_code == 429:
                    self._record_provider_throttle(config, detail)
                print(
                    f"[LLMService] API调用失败: {response.status_code} - {detail}"
                )
                raise LLMRequestError(
                    f"大模型接口返回 HTTP {response.status_code}: {detail[:300]}"
                )

            try:
                result = response.json()
            except ValueError as exc:
                raise LLMResponseFormatError(
                    "大模型接口响应不是有效 JSON。"
                    f"响应摘要: {self._content_preview(response.text)}"
                ) from exc
            content = self._response_content(result)
            parsed = self._parse_llm_response(content)
            if not parsed:
                preview = self._content_preview(content)
                response_preview = self._response_preview(result)
                print(
                    "[LLMService] 模型返回内容无法解析 "
                    f"(model={reservation['model']}, preview={preview}, "
                    f"response={response_preview})"
                )
                raise LLMResponseFormatError(
                    "大模型返回内容为空或不是有效 JSON，请检查该场景选择的模型"
                    "是否支持 Chat Completions，并要求模型只返回 JSON。"
                    f"响应摘要: {response_preview}"
                )

            if governance:
                governance.finish_call(reservation, "completed", result.get("usage"))
            return parsed

        except LLMRequestError as e:
            if governance:
                governance.finish_call(reservation, "failed", error=str(e))
            raise
        except requests.RequestException as e:
            print(f"[LLMService] 调用异常: {e}")
            if governance:
                governance.finish_call(reservation, "failed", error=str(e))
            raise LLMRequestError(f"连接大模型服务失败: {e}") from e
        except Exception as e:
            print(f"[LLMService] 调用异常: {e}")
            if governance:
                governance.finish_call(reservation, "failed", error=str(e))
            if isinstance(e, (PermissionError, ValueError)):
                raise
            raise LLMRequestError(f"处理大模型响应失败: {e}") from e

    def call_llm_stream(
        self, prompt: str, on_chunk: callable = None,
        model: Optional[str] = None, system_prompt: Optional[str] = None,
        scene_code: str = AI_SIGNAL_ANALYSIS,
        object_type: str = "", object_id: str = "",
        response_validator: callable = None,
    ) -> Optional[Dict]:
        """调用 LLM API（流式）；响应格式错误时最多尝试三次。

        Args:
            prompt: 提示词
            on_chunk: 回调函数，参数为 (chunk_count, full_content)
        """
        for attempt in range(1, self.MAX_RESPONSE_ATTEMPTS + 1):
            attempt_prompt = self._retry_prompt(prompt, attempt)
            try:
                return self._call_llm_stream_once(
                    attempt_prompt, on_chunk, model, system_prompt,
                    scene_code, object_type, object_id, response_validator,
                )
            except LLMResponseFormatError as exc:
                if attempt >= self.MAX_RESPONSE_ATTEMPTS:
                    raise LLMResponseFormatError(
                        f"{exc} 已尝试 {self.MAX_RESPONSE_ATTEMPTS} 次。"
                    ) from exc
                print(
                    "[LLMService] 流式响应格式无效，立即重试 "
                    f"({attempt + 1}/{self.MAX_RESPONSE_ATTEMPTS})"
                )
        return None

    def _call_llm_stream_once(
        self, prompt: str, on_chunk: callable,
        model: Optional[str], system_prompt: Optional[str],
        scene_code: str, object_type: str, object_id: str,
        response_validator: callable = None,
    ) -> Optional[Dict]:
        governance = self._llm_governance
        if governance is None:
            config = self.llm_store.get_config()
            reservation = {"model": model or config.model}
        else:
            reservation = governance.reserve_call(
                self.llm_store.user_id, scene_code, model, object_type, object_id
            )
            config = reservation["config"]
        self._raise_if_provider_blocked(config)

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": reservation["model"],
                # Disable reasoning for machine-readable signal/prompt responses.
                "enable_thinking": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            system_prompt
                            or reservation.get("system_prompt")
                            or getattr(config, "system_prompt", DEFAULT_SYSTEM_PROMPT)
                        ),
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
                "stream": True,
                "response_format": {"type": "json_object"},
            }

            response = requests.post(
                f"{config.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120,
                stream=True
            )

            if response.status_code != 200:
                detail = self._provider_error_detail(response)
                if response.status_code == 429:
                    self._record_provider_throttle(config, detail)
                print(
                    f"[LLMService] API调用失败: {response.status_code} - {detail}"
                )
                raise LLMRequestError(
                    f"大模型接口返回 HTTP {response.status_code}: {str(detail)[:300]}"
                )

            # 收集完整响应
            full_content = ""
            chunk_count = 0

            for line in response.iter_lines():
                if not line:
                    continue

                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str == '[DONE]':
                        break

                    try:
                        chunk_data = json.loads(data_str)
                        if 'choices' in chunk_data and len(chunk_data['choices']) > 0:
                            choice = chunk_data['choices'][0]
                            delta = choice.get('delta', {})
                            content_piece = self._message_content(delta)
                            if not content_piece:
                                content_piece = self._message_content(
                                    choice.get("message") or {}
                                )
                            if content_piece:
                                full_content += content_piece
                                chunk_count += 1

                                if on_chunk:
                                    on_chunk(chunk_count, full_content)
                    except json.JSONDecodeError:
                        continue

            print(f"[LLMService] 流式接收完成，共 {chunk_count} 个chunk，{len(full_content)} 字符")
            parsed = self._parse_llm_response(full_content)
            if not parsed:
                preview = self._content_preview(full_content)
                print(
                    "[LLMService] 流式模型返回内容无法解析 "
                    f"(model={reservation['model']}, preview={preview})"
                )
                raise LLMResponseFormatError(
                    "大模型返回内容为空或不是有效 JSON，"
                    f"响应摘要: {preview}"
                )
            if response_validator:
                response_validator(parsed)
            if governance:
                governance.finish_call(
                    reservation, "completed",
                    result_summary=json.dumps(parsed, ensure_ascii=False),
                )
            return parsed

        except LLMResponseFormatError as exc:
            if governance:
                governance.finish_call(reservation, "failed", error=str(exc))
            # Preserve format/contract failures so call_llm_stream retries them.
            raise
        except LLMRequestError as exc:
            if governance:
                governance.finish_call(reservation, "failed", error=str(exc))
            raise
        except requests.RequestException as e:
            print(f"[LLMService] 流式调用异常: {e}")
            if governance:
                governance.finish_call(reservation, "failed", error=str(e))
            raise LLMRequestError(f"连接大模型服务失败: {e}") from e
        except Exception as e:
            print(f"[LLMService] 流式调用异常: {e}")
            if governance:
                governance.finish_call(reservation, "failed", error=str(e))
            if isinstance(e, (PermissionError, ValueError)):
                raise
            raise LLMRequestError(f"处理大模型响应失败: {e}") from e

    @classmethod
    def _retry_prompt(cls, prompt: str, attempt: int) -> str:
        if attempt <= 1:
            return prompt
        return prompt + (
            "\n\n## 响应格式纠正\n"
            "上一次响应未满足完整 JSON 契约。请重新生成，并且只返回一个完整、合法的 "
            "JSON 对象；不要输出推理过程、Markdown 代码块、解释、前后缀或截断内容。"
        )

    def _parse_llm_response(self, content: str) -> Optional[Dict]:
        """解析 LLM 响应"""
        if not isinstance(content, str) or not content.strip():
            print("[LLMService] 模型返回内容为空")
            return None

        json_text = self._extract_json_text(content)
        if json_text is None:
            print(
                "[LLMService] 未找到可解析JSON内容: "
                f"{self._content_preview(content)}"
            )
            return None

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print(f"[LLMService] JSON解析失败: {e}")
            return None

    @staticmethod
    def _message_content(message: Dict) -> str:
        """Extract only final response content from OpenAI-compatible payloads."""
        if not isinstance(message, dict):
            return ""

        def as_text(value) -> str:
            if isinstance(value, str):
                return value
            if not isinstance(value, list):
                return ""
            chunks = []
            for item in value:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if isinstance(text, dict):
                        text = text.get("value") or ""
                    if isinstance(text, str):
                        chunks.append(text)
            return "".join(chunks)

        content = as_text(message.get("content"))
        if content.strip():
            return content

        # Tool-call style providers may put the JSON payload in arguments.
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            arguments = (call.get("function") or {}).get("arguments")
            if isinstance(arguments, str) and arguments.strip():
                return arguments
        return ""

    @classmethod
    def _response_content(cls, payload: Dict) -> str:
        """Extract text from OpenAI-compatible and Responses-like payloads."""
        if not isinstance(payload, dict):
            return ""

        for key in ("output_text", "text", "content"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value

        choices = payload.get("choices") or []
        if choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            content = cls._message_content(choice.get("message") or {})
            if content.strip():
                return content
            for key in ("text", "content"):
                value = choice.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            delta_content = cls._message_content(choice.get("delta") or {})
            if delta_content.strip():
                return delta_content

        output = payload.get("output")
        if isinstance(output, list):
            chunks = []
            for item in output:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    chunks.append(cls._message_content(item))
                    for part in item.get("content") or []:
                        if isinstance(part, dict):
                            text = part.get("text") or part.get("content")
                            if isinstance(text, str):
                                chunks.append(text)
            content = "".join(chunks)
            if content.strip():
                return content
        return ""

    @classmethod
    def _response_preview(cls, payload, limit: int = 700) -> str:
        """Keep enough provider shape to debug empty-content failures."""
        try:
            safe = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            safe = str(payload)
        return cls._content_preview(safe, limit)

    @staticmethod
    def _provider_error_detail(response) -> str:
        detail = response.text
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    detail = error.get("message") or error.get("code") or detail
                else:
                    detail = payload.get("message") or payload.get("detail") or detail
        except (ValueError, AttributeError):
            pass
        return str(detail)

    @staticmethod
    def _content_preview(content: str, limit: int = 500) -> str:
        text = str(content or "").replace("\n", "\\n").strip()
        return text[:limit] or "<empty>"

    @classmethod
    def _extract_json_text(cls, content: str) -> Optional[str]:
        text = content.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()

        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

        for start_char, end_char in (("{", "}"), ("[", "]")):
            start = text.find(start_char)
            if start < 0:
                continue
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == start_char:
                    depth += 1
                elif char == end_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:index + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
        return None

    @staticmethod
    def _canonical_period(value, symbol_plan: Dict) -> Optional[str]:
        """将模型生成的自然语言周期归一化为策略使用的周期代码。"""
        text = str(value or "").upper()
        for profile in symbol_plan.get("strategies", []):
            source_id = str(profile.get("signal_source_id") or "").upper()
            if source_id and source_id in text and len(profile["periods"]) == 1:
                return next(iter(profile["periods"]))

        match = re.search(r"(?<![A-Z0-9])(M15|M5|M1|H4|H1)(?![A-Z0-9])", text)
        if match:
            return match.group(1)

        chinese_periods = (
            ("15分钟", "M15"),
            ("5分钟", "M5"),
            ("1分钟", "M1"),
            ("4小时", "H4"),
            ("1小时", "H1"),
        )
        for label, period in chinese_periods:
            if label in str(value or ""):
                return period
        return None

    @staticmethod
    def _normalize_confidence(value) -> int:
        """Accept providers that return confidence as either 0-1 or 0-100."""
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0
        if 0 < confidence <= 1:
            confidence *= 100
        return max(0, min(100, int(round(confidence))))

    def _normalize_analysis_response(
        self, response: Dict, analysis_plan: Dict[str, Dict]
    ) -> Dict:
        """Normalize one AI source's suggestions without strategy side effects."""
        response = self._coerce_analysis_response(response, analysis_plan)
        for symbol, analysis in response.items():
            if not isinstance(analysis, dict) or symbol not in analysis_plan:
                continue

            symbol_plan = analysis_plan[symbol]
            enabled_periods = set(symbol_plan.get("periods", {}))
            trends = analysis.get("trend_analysis") or {}
            if isinstance(trends, dict):
                for trend in trends.values():
                    if isinstance(trend, dict):
                        trend["confidence"] = self._normalize_confidence(
                            trend.get("confidence")
                        )
            structure = analysis.get("market_structure") or {}
            system_background = symbol_plan.get("background_analysis") or {}
            model_background = analysis.get("background_analysis") or {}
            # Preserve deterministic per-period evidence. The model may refine
            # the combined label/reason after reviewing raw K-lines, but may
            # not erase which inputs the system actually calculated.
            analysis["background_analysis"] = {
                "periods": system_background.get("periods") or {},
                "combined": str(
                    model_background.get("combined")
                    or system_background.get("combined") or "mixed"
                ),
                "confidence": self._normalize_confidence(
                    model_background.get("confidence")
                    if model_background.get("confidence") is not None
                    else system_background.get("confidence")
                ),
                "reason": str(
                    model_background.get("reason")
                    or system_background.get("reason") or ""
                ),
                "system_combined": str(system_background.get("combined") or "mixed"),
                "system_confidence": int(system_background.get("confidence") or 0),
            }
            template_type = str(structure.get("template_type") or "").strip().lower()
            triangle = template_type in {"converging_triangle", "diverging_triangle"}
            breakout_confirmed = bool(
                structure.get("triangle", {}).get("breakout_confirmed")
                or structure.get("breakout_confirmed")
                or template_type == "breakout_retest"
            )
            # 三角形必须先确认突破，none 结构不允许产生交易建议；这是后端
            # 硬约束，避免模型在结构不完整时自由发单。
            suggestions_input = analysis.get("trade_suggestions", [])
            if template_type == "none" or (triangle and not breakout_confirmed):
                suggestions_input = []
            normalized = []
            for suggestion in suggestions_input:
                if not isinstance(suggestion, dict):
                    continue
                period = self._canonical_period(suggestion.get("period"), symbol_plan)
                if period not in enabled_periods:
                    continue

                try:
                    entry = float(suggestion.get("entry_price", 0))
                    stop_loss = float(suggestion.get("stop_loss", 0))
                    take_profit = float(suggestion.get("take_profit", 0))
                except (TypeError, ValueError):
                    continue

                direction = str(suggestion.get("direction", "")).lower()
                valid_levels = (
                    direction == "buy" and stop_loss < entry < take_profit
                ) or (
                    direction == "sell" and take_profit < entry < stop_loss
                )
                if entry <= 0 or stop_loss <= 0 or take_profit <= 0 or not valid_levels:
                    continue

                profiles = symbol_plan.get("strategies") or []
                if len(profiles) != 1:
                    continue
                profile = profiles[0]
                source_suggestion = dict(suggestion)
                setup_type = str(suggestion.get("setup_type") or "").strip().lower()
                if not setup_type:
                    if template_type == "range":
                        setup_type = "range_reversal"
                    elif template_type == "uptrend_pullback":
                        setup_type = "trend_pullback"
                    elif template_type == "downtrend_rebound":
                        setup_type = "trend_rebound"
                    elif template_type in {"converging_triangle", "diverging_triangle", "breakout_retest"}:
                        setup_type = "triangle_breakout" if triangle else "range_breakout"
                    else:
                        setup_type = "none"
                entry_mode = str(suggestion.get("entry_mode") or "").strip().lower()
                if entry_mode not in {"touch_or_near", "breakout"}:
                    entry_mode = "breakout" if setup_type in {
                        "range_breakout", "triangle_breakout"
                    } else "touch_or_near"
                confirmation = str(suggestion.get("confirmation") or "none").strip().lower()
                if confirmation not in {"none", "close_confirmed", "retest_confirmed"}:
                    confirmation = "none"
                activation_status = str(
                    suggestion.get("activation_status") or ""
                ).strip().lower()
                if activation_status not in {"active", "pending_confirmation"}:
                    activation_status = (
                        "pending_confirmation"
                        if entry_mode == "breakout" and confirmation == "none"
                        else "active"
                    )
                # A breakout plan is never executable before a close/retest
                # confirmation, even if the model forgot to mark it pending.
                if entry_mode == "breakout" and confirmation == "none":
                    activation_status = "pending_confirmation"
                source_suggestion.update({
                    "signal_source_id": profile.get("signal_source_id", ""),
                    "period": period,
                    "confidence": self._normalize_confidence(
                        suggestion.get("confidence")
                    ),
                    "entry_price": entry,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "setup_type": setup_type,
                    "entry_mode": entry_mode,
                    "confirmation": confirmation,
                    "activation_status": activation_status,
                    "market_structure": analysis.get("market_structure") or {},
                    "background_analysis": analysis.get("background_analysis") or {},
                    "trade_horizon": analysis.get("trade_horizon") or {},
                })
                now_ts = int(time.time())
                horizon = source_suggestion.get("trade_horizon") or {}
                horizon_minutes = int(horizon.get("minutes") or 0)
                if horizon_minutes <= 0:
                    horizon_minutes = max(
                        30,
                        int(profile.get("analysis_interval_minutes", 5) or 5) * 3,
                    )
                fingerprint_payload = "|".join((
                    str(profile.get("signal_source_id") or ""), period,
                    setup_type, direction, entry_mode,
                    f"{entry:.8f}", f"{stop_loss:.8f}", f"{take_profit:.8f}",
                ))
                source_suggestion.update({
                    "plan_id": hashlib.sha256(
                        fingerprint_payload.encode("utf-8")
                    ).hexdigest()[:20],
                    "valid_from": now_ts,
                    "expires_at": now_ts + horizon_minutes * 60,
                    "status": (
                        "pending_confirmation"
                        if activation_status == "pending_confirmation"
                        else "active"
                    ),
                    "triggered_at": 0,
                    "invalidated_reason": "",
                })
                # Model output is source-owned. A binding strategy applies its
                # own confidence, risk and position-management rules later.
                source_suggestion.pop("strategy_id", None)
                source_suggestion.pop("strategy_name", None)
                normalized.append(source_suggestion)

            # A valid range is a two-sided plan. If the model returned only
            # one reversal side, synthesize the missing boundary plan from
            # the same box and risk distance so the strategy can evaluate
            # both future directions on subsequent Ticks.
            if template_type == "range" and normalized:
                box = structure.get("range") or {}
                try:
                    upper = float(box.get("upper") or 0)
                    lower = float(box.get("lower") or 0)
                except (TypeError, ValueError):
                    upper = lower = 0.0
                directions = {str(item.get("direction") or "").lower() for item in normalized}
                if upper > lower > 0:
                    base = normalized[0]
                    entry = float(base.get("entry_price") or 0)
                    stop = float(base.get("stop_loss") or 0)
                    risk = abs(entry - stop) if entry > 0 and stop > 0 else max((upper - lower) * 0.2, 0.00000001)
                    source_id = str(base.get("signal_source_id") or "")
                    period = str(base.get("period") or "")
                    confidence = int(base.get("confidence") or 0)
                    if "buy" not in directions:
                        normalized.append({
                            **base, "direction": "buy", "entry_price": lower,
                            "stop_loss": lower - risk, "take_profit": upper,
                            "setup_type": "range_reversal", "entry_mode": "touch_or_near",
                            "confirmation": "none", "activation_status": "active",
                            "reason": f"箱体下沿 {lower:g} 反转买入计划，止损放在下沿下方，止盈看向上沿。",
                            "signal_source_id": source_id, "period": period,
                            "confidence": confidence,
                        })
                    if "sell" not in directions:
                        normalized.append({
                            **base, "direction": "sell", "entry_price": upper,
                            "stop_loss": upper + risk, "take_profit": lower,
                            "setup_type": "range_reversal", "entry_mode": "touch_or_near",
                            "confirmation": "none", "activation_status": "active",
                            "reason": f"箱体上沿 {upper:g} 反转卖出计划，止损放在上沿上方，止盈看向下沿。",
                            "signal_source_id": source_id, "period": period,
                            "confidence": confidence,
                        })
                    # Keep both pending breakout plans alongside the reversal
                    # plans. They activate only after close/retest confirmation.
                    breakout_directions = {
                        str(item.get("direction") or "").lower()
                        for item in normalized
                        if item.get("setup_type") == "range_breakout"
                    }
                    buffer = max((upper - lower) * 0.01, risk * 0.5, 1e-8)
                    if "buy" not in breakout_directions:
                        normalized.append({
                            **base, "direction": "buy", "entry_price": upper + buffer,
                            "stop_loss": upper - buffer, "take_profit": upper + (upper - lower),
                            "setup_type": "range_breakout", "entry_mode": "breakout",
                            "confirmation": "none", "activation_status": "pending_confirmation",
                            "reason": f"若收盘有效突破箱体上沿 {upper:g}，回踩确认后买入，止损回到上沿下方，止盈按箱体高度投射。",
                            "signal_source_id": source_id, "period": period,
                            "confidence": confidence,
                        })
                    if "sell" not in breakout_directions:
                        normalized.append({
                            **base, "direction": "sell", "entry_price": lower - buffer,
                            "stop_loss": lower + buffer, "take_profit": lower - (upper - lower),
                            "setup_type": "range_breakout", "entry_mode": "breakout",
                            "confirmation": "none", "activation_status": "pending_confirmation",
                            "reason": f"若收盘有效跌破箱体下沿 {lower:g}，反抽确认后卖出，止损回到下沿上方，止盈按箱体高度投射。",
                            "signal_source_id": source_id, "period": period,
                            "confidence": confidence,
                        })

            # Recompute lifecycle metadata after range completion so every
            # synthesized side receives its own stable plan identity.
            now_ts = int(time.time())
            for item in normalized:
                setup = str(item.get("setup_type") or "none").lower()
                direction = str(item.get("direction") or "").lower()
                mode = str(item.get("entry_mode") or "touch_or_near").lower()
                try:
                    item_entry = float(item.get("entry_price") or 0)
                    item_stop = float(item.get("stop_loss") or 0)
                    item_take = float(item.get("take_profit") or 0)
                except (TypeError, ValueError):
                    continue
                horizon = item.get("trade_horizon") or {}
                horizon_minutes = int(horizon.get("minutes") or 0)
                if horizon_minutes <= 0:
                    horizon_minutes = max(
                        30,
                        int(profile.get("analysis_interval_minutes", 5) or 5) * 3,
                    )
                fingerprint_payload = "|".join((
                    str(item.get("signal_source_id") or ""),
                    str(item.get("period") or "").upper(), setup, direction, mode,
                    f"{item_entry:.8f}", f"{item_stop:.8f}", f"{item_take:.8f}",
                ))
                pending = str(item.get("activation_status") or "") == "pending_confirmation"
                item.update({
                    "plan_id": hashlib.sha256(
                        fingerprint_payload.encode("utf-8")
                    ).hexdigest()[:20],
                    "valid_from": now_ts,
                    "expires_at": now_ts + horizon_minutes * 60,
                    "status": "pending_confirmation" if pending else "active",
                    "triggered_at": 0,
                    "invalidated_reason": "",
                })

            analysis["trade_suggestions"] = normalized
        return response

    def _validate_analysis_response(
        self, response: Dict, analysis_plan: Dict[str, Dict],
    ) -> None:
        """Require a trend result for every period requested in this LLM call."""
        response = self._coerce_analysis_response(response, analysis_plan)
        missing = []
        for symbol, symbol_plan in analysis_plan.items():
            analysis = response.get(symbol)
            trends = (
                analysis.get("trend_analysis")
                if isinstance(analysis, dict)
                else None
            )
            trends = trends if isinstance(trends, dict) else {}
            for period in symbol_plan.get("periods", {}):
                canonical = str(period).upper()
                if not isinstance(trends.get(canonical), dict):
                    missing.append(f"{symbol}/{canonical}")
            if any(
                str(profile.get("signal_source_version") or "1.0") == "2.0"
                for profile in symbol_plan.get("strategies", [])
            ) and not isinstance((analysis or {}).get("market_structure"), dict):
                missing.append(f"{symbol}/market_structure")
        if missing:
            raise LLMResponseFormatError(
                "大模型响应缺少必需的趋势分析：" + "、".join(missing)
            )

    def _build_individual_analysis_requests(
        self, analysis_plan: Dict[str, Dict],
    ) -> List[Dict]:
        """Build one isolated LLM request per AI source profile.

        The method keeps its historical name for the backtest caller, but it no
        longer groups sources by model, prompt, symbol, or period.
        """
        config = self.llm_store.get_config()
        scene_defaults = self._scene_defaults(AI_SIGNAL_ANALYSIS)
        scene_model = scene_defaults.get("default_model_id") or getattr(config, "model", "")
        scene_models = set(scene_defaults.get("models") or [])
        requests: List[Dict] = []
        for symbol, symbol_plan in analysis_plan.items():
            for profile in symbol_plan.get("strategies", []):
                model = profile.get("model") or scene_model
                if scene_models and model not in scene_models:
                    print(
                        "[LLMService] AI信号源模型不在当前场景可用列表，"
                        f"已回退到场景默认模型: {model} -> {scene_model}"
                    )
                    model = scene_model
                system_prompt = str(profile.get("system_prompt") or "").strip()
                template = str(profile.get("analysis_prompt_template") or "").strip()
                version = str(profile.get("signal_source_version") or "1.0")
                if version == "2.0":
                    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
                    template = STRUCTURE_ANALYSIS_PROMPT_TEMPLATE
                if not system_prompt or not template:
                    # Source records created before the dedicated prompt flow are
                    # intentionally not allowed to silently use a generic prompt.
                    print(
                        "[LLMService] 跳过缺少专属提示词的 AI 信号源: "
                        f"{profile.get('signal_source_id') or 'unknown'}"
                    )
                    continue
                references = list(profile.get("reference_runtime_ids") or [])
                periods = {
                    period: {
                        "weight": int(weight),
                        "kline_count": int(profile.get("kline_count", 100)),
                    }
                    for period, weight in self._period_weight_items(
                        profile.get("periods")
                    )
                }
                requests.append({
                    "model": model,
                    "system_prompt": system_prompt,
                    "analysis_prompt_template": template,
                    "signal_source_version": version,
                    "reference_runtime_ids": references,
                    "plan": {
                        symbol: {
                            "periods": periods,
                            "strategies": [profile],
                        }
                    },
                })
        return requests

    @staticmethod
    def _append_response_contract(prompt: str, analysis_plan: Dict[str, Dict]) -> str:
        """Make the source-specific JSON shape explicit for smaller reasoning models."""
        contracts = []
        for symbol, symbol_plan in analysis_plan.items():
            periods = [str(period).upper() for period in symbol_plan.get("periods", {})]
            profiles = symbol_plan.get("strategies") or []
            source_ids = [str(item.get("signal_source_id") or "") for item in profiles]
            trend_shape = {
                period: {"trend": "趋势类型", "confidence": 0, "reason": "理由"}
                for period in periods
            }
            example = {
                symbol: {
                    "trend_analysis": trend_shape,
                    "overall_trend": {
                        "direction": "方向", "strength": 0, "summary": "总结",
                    },
                    "key_levels": {"resistance": [], "support": []},
                    "context_observations": [],
                    "trade_suggestions": [],
                }
            }
            contracts.append(
                "- 顶层键必须且只能使用 %s；trend_analysis 必须包含键 %s；"
                "trade_suggestions 可以是 []，如有建议，period 必须是 %s，"
                "signal_source_id 必须是 %s。\n"
                "  最小有效 JSON 结构：%s"
                % (
                    json.dumps(symbol, ensure_ascii=False),
                    json.dumps(periods, ensure_ascii=False),
                    json.dumps(periods, ensure_ascii=False),
                    json.dumps(source_ids, ensure_ascii=False),
                    json.dumps(example, ensure_ascii=False),
                )
            )
        return prompt + (
            "\n\n## 本次调用的强制 JSON 契约\n"
            "以下契约优先于前文的通用输出说明。字段均区分大小写，必须完整出现；"
            "trade_suggestions 可给出未来价格计划，只有不存在可辩护计划时才返回 []；"
            "不要输出思考过程。\n"
            + "\n".join(contracts)
        )

    def _shared_reference_context(self, share_ids: List[str]) -> str:
        references = []
        for share_id in share_ids[:10]:
            item = self._shared_runtime_repo.get_shared(share_id)
            if not item:
                continue
            references.append({
                "symbol": item["symbol"],
                "period": item["period"],
                "model": item["model"],
                "signal_params": item["signal_params"],
                "strategy_name": item["strategy_name"],
                "strategy_lifecycle": item["strategy_lifecycle"],
                "result": item["result"],
                "last_run_at": item["last_run_at"],
            })
        return json.dumps(references, ensure_ascii=False)[:30000]

    @staticmethod
    def _accumulate_symbol_result(target: Dict, incoming: Dict) -> None:
        """Accumulate isolated source results for the symbol-level read model.

        Each incoming payload was generated by exactly one source request. This
        only preserves the existing symbol-level API/storage shape; it does not
        combine prompts or split a model response.
        """
        for symbol, analysis in (incoming or {}).items():
            if not isinstance(analysis, dict):
                continue
            if symbol not in target:
                target[symbol] = analysis
                continue
            current = target[symbol]
            current.setdefault("trend_analysis", {}).update(
                analysis.get("trend_analysis") or {}
            )
            current.setdefault("trade_suggestions", []).extend(
                analysis.get("trade_suggestions") or []
            )
            current.setdefault("source_results", {}).update(
                analysis.get("source_results") or {}
            )
            for key in ("market_structure", "trade_horizon", "overall_trend", "key_levels", "analyzed_at"):
                if analysis.get(key) is not None:
                    current[key] = analysis[key]

    @staticmethod
    def _attach_source_results(response: Dict, plan: Dict) -> None:
        """Attach the isolated model response to its signal-source instance."""
        analyzed_at = datetime.now().isoformat()
        for symbol, symbol_plan in plan.items():
            analysis = response.get(symbol)
            if not isinstance(analysis, dict):
                continue
            snapshots = analysis.setdefault("source_results", {})
            for profile in symbol_plan.get("strategies", []):
                source_id = str(profile.get("signal_source_id") or "")
                if not source_id:
                    continue
                periods = {
                    str(period).upper()
                    for period in (profile.get("periods") or {})
                }
                snapshots[source_id] = {
                    "trend_analysis": {
                        period: value
                        for period, value in (analysis.get("trend_analysis") or {}).items()
                        if str(period).upper() in periods
                    },
                    "overall_trend": analysis.get("overall_trend"),
                    "key_levels": analysis.get("key_levels"),
                    "market_structure": analysis.get("market_structure"),
                    "trade_horizon": analysis.get("trade_horizon"),
                    "trade_suggestions": [
                        item for item in (analysis.get("trade_suggestions") or [])
                        if str(item.get("signal_source_id") or "") == source_id
                    ],
                    "analyzed_at": analyzed_at,
                    "data_stale": False,
                    "market_status": "active",
                }

    @staticmethod
    def _retain_previous_source_results(
        analysis: Dict, previous: LLMAnalysisResult,
        analyzed_source_ids: set, analyzed_periods: set,
    ) -> None:
        """Keep results for independent sources that were not due this run."""
        retained_trends = {
            period: value
            for period, value in (previous.trend_analysis or {}).items()
            if period not in analyzed_periods
        }
        analysis["trend_analysis"] = {
            **retained_trends,
            **(analysis.get("trend_analysis") or {}),
        }
        retained_suggestions = [
            item for item in (previous.trade_suggestions or [])
            if item.get("signal_source_id") not in analyzed_source_ids
        ]
        analysis["trade_suggestions"] = (
            retained_suggestions + analysis.get("trade_suggestions", [])
        )
        retained_source_results = {
            source_id: value
            for source_id, value in (previous.source_results or {}).items()
            if source_id not in analyzed_source_ids
        }
        analysis["source_results"] = {
            **retained_source_results,
            **(analysis.get("source_results") or {}),
        }

    def _publish_runtime_results(self, plan: Dict, response: Dict) -> None:
        config = self.llm_store.get_config()
        for symbol, symbol_plan in plan.items():
            analysis = response.get(symbol)
            if not isinstance(analysis, dict):
                continue
            for profile in symbol_plan.get("strategies", []):
                source_id = profile.get("signal_source_id", "")
                if not profile.get("share_runtime_data"):
                    self._shared_runtime_repo.remove_for_source(
                        self.llm_store.user_id,
                        profile.get("strategy_id", ""),
                        source_id,
                    )
                    continue
                source_result = dict(analysis)
                source_result["trade_suggestions"] = [
                    item for item in analysis.get("trade_suggestions", [])
                    if item.get("signal_source_id") == source_id
                ]
                self._shared_runtime_repo.publish(
                    self.llm_store.user_id,
                    {
                        "strategy_id": profile.get("strategy_id", ""),
                        "strategy_name": profile.get("strategy_name", ""),
                        "symbol": symbol,
                        "lifecycle_status": profile.get(
                            "strategy_lifecycle", "draft"
                        ),
                    },
                    {
                        "signal_source_id": source_id,
                        "period": next(iter(profile.get("periods") or {}), ""),
                        "params": profile.get("signal_params") or {},
                    },
                    source_result,
                    profile.get("model") or config.model,
                    profile.get("system_prompt") or config.system_prompt,
                    profile.get("analysis_prompt_template")
                    or config.analysis_prompt_template,
                )

    # ==================== 入场价检测 ====================

    def check_entry_price_nearby(
        self, symbol: str, current_price: float, threshold: float = 0.0008,
        strategy_id: str = "", signal_source_id: str = "",
    ) -> List[Dict]:
        """
        检查当前价格是否接近 AI 建议的入场价

        Args:
            symbol: 交易品种
            current_price: 当前价格
            threshold: 价格接近阈值，默认万分之一

        Returns:
            匹配的交易建议列表
        """
        matched = []

        result = self.llm_store.get_analysis_result(symbol)
        # AI 信号源的行情账户可能与策略部署账户不同。部署账户缓存没有
        # 分析时，按具体信号源回退到其绑定的行情账户运行快照。
        if (not result or not result.trade_suggestions) and signal_source_id:
            result = self.llm_store.get_analysis_result_for_source(
                symbol, signal_source_id,
            )
        if not result or not result.trade_suggestions:
            return matched

        # Never consume a plan from an analysis that has already gone stale.
        # Ticks can continue arriving while the scheduler/LLM provider is
        # unavailable; using the last range boundary after that point turns a
        # valid historical plan into a late, usually adverse, entry.
        if bool(getattr(result, "data_stale", False)):
            return matched

        for suggestion in result.trade_suggestions:
            suggestion_source_id = str(suggestion.get("signal_source_id") or "")
            if signal_source_id and suggestion_source_id != signal_source_id:
                continue
            entry_price = suggestion.get('entry_price')
            period = suggestion.get('period')
            direction = suggestion.get('direction')
            stop_loss = suggestion.get('stop_loss')
            take_profit = suggestion.get('take_profit')
            entry_mode = str(suggestion.get("entry_mode") or "touch_or_near").lower()
            confirmation = str(suggestion.get("confirmation") or "none").lower()
            activation_status = str(
                suggestion.get("activation_status")
                or ("pending_confirmation" if entry_mode == "breakout" else "active")
            ).lower()
            plan_status = str(
                suggestion.get("status") or activation_status
            ).lower()

            now_ts = int(time.time())
            try:
                plan_expires_at = int(suggestion.get("expires_at") or 0)
            except (TypeError, ValueError):
                plan_expires_at = 0
            if plan_expires_at and now_ts > plan_expires_at:
                suggestion["status"] = "expired"
                suggestion["invalidated_reason"] = "交易计划已超过有效期"
                continue
            if plan_status in {"expired", "invalidated", "triggered"}:
                continue

            # Higher-timeframe background is a hard filter for range-reversal
            # entries. Breakout plans are handled by close/retest confirmation
            # because a confirmed breakout can itself change the background.
            setup_type = str(suggestion.get("setup_type") or "").lower()
            structure = suggestion.get("market_structure") or {}
            background_analysis = suggestion.get("background_analysis") or {}
            background = str(
                background_analysis.get("combined")
                or structure.get("background_structure")
                or (suggestion.get("overall_trend") or {}).get("direction")
                or "none"
            ).lower()
            background_direction = (
                "up" if "uptrend" in background
                else "down" if "downtrend" in background
                else background
            )
            countertrend_range = (
                setup_type == "range_reversal"
                and (
                    background == "conflict"
                    or (background_direction == "up" and direction == "sell")
                    or (background_direction == "down" and direction == "buy")
                )
            )
            if countertrend_range:
                continue

            if not entry_price or entry_price <= 0:
                continue

            # A touch plan is invalid once price has already moved a material
            # part of the way from entry toward its structural stop. Entering
            # there would mean buying a failed support or selling a failed
            # resistance before the formal stop is reached.
            risk_distance = abs(float(entry_price) - float(stop_loss or 0))
            adverse_distance = (
                float(entry_price) - float(current_price)
                if direction == "buy"
                else float(current_price) - float(entry_price)
            )
            if (
                (direction == "buy" and current_price <= stop_loss)
                or (direction == "sell" and current_price >= stop_loss)
                or (
                    entry_mode != "breakout" and risk_distance > 0
                    and adverse_distance >= risk_distance * 0.25
                )
            ):
                suggestion["status"] = "invalidated"
                suggestion["invalidated_reason"] = (
                    "价格已向止损方向穿越入场位，原交易结构失效"
                )
                continue

            # A plan is valid for a bounded number of bars, not indefinitely.
            # Keep a generous floor for slow providers while tying the upper
            # bound to the analysis period (three analysis windows).
            analyzed_at = result.analyzed_at or ""
            if analyzed_at:
                try:
                    analyzed_dt = datetime.fromisoformat(
                        str(analyzed_at).replace("Z", "+00:00")
                    )
                    if analyzed_dt.tzinfo is not None:
                        now_dt = datetime.now(analyzed_dt.tzinfo)
                    else:
                        now_dt = datetime.now()
                    period_minutes = {
                        "M1": 1, "M5": 5, "M15": 15,
                        "H1": 60, "H4": 240,
                    }.get(str(period or "").upper(), 5)
                    max_age_seconds = max(30 * 60, period_minutes * 3 * 60)
                    if (now_dt - analyzed_dt).total_seconds() > max_age_seconds:
                        continue
                except (TypeError, ValueError):
                    # Legacy timestamps are not allowed to break tick
                    # processing; the scheduler's data_stale flag remains the
                    # fallback guard for those records.
                    pass

            # 验证止损止盈
            if not stop_loss or not take_profit or stop_loss <= 0 or take_profit <= 0:
                print(f"[LLMService] 跳过无效建议: {period} sl={stop_loss}, tp={take_profit}")
                continue

            # TICK only activates a normal near-entry plan. A breakout plan
            # may reach its trigger price first, but must wait for the AI
            # analysis to report a confirmed close or retest.
            if entry_mode == "breakout" and (
                activation_status != "active"
                or confirmation not in {"close_confirmed", "retest_confirmed"}
            ):
                continue

            price_diff_pct = abs(current_price - entry_price) / entry_price

            # Reaching a planned level is different from chasing after it.
            # Once price has moved in the intended direction beyond half of
            # the configured near-entry tolerance, wait for a return to the
            # planned level or for a fresh analysis instead of paying up.
            favorable_chase = (
                current_price > entry_price
                if direction == "buy" else current_price < entry_price
            )
            max_chase_pct = min(float(threshold) * 0.5, 0.0004)
            if favorable_chase and price_diff_pct > max_chase_pct:
                continue

            if price_diff_pct <= threshold:
                # 检查冷却
                # Structured plans are deduplicated persistently at the
                # account/deployment order guard. Do not suppress a second
                # account bound to the same strategy in this user-level store.
                can_alert = bool(suggestion.get("plan_id")) or (
                    self.llm_store.check_entry_alert_cooldown(
                        symbol, period, direction, entry_price, strategy_id,
                        str(suggestion.get("signal_source_id") or ""),
                        result.analyzed_at or "",
                    )
                )

                if can_alert:
                    matched.append({
                        "symbol": symbol,
                        "strategy_id": strategy_id,
                        "strategy_name": "",
                        "signal_source_id": suggestion_source_id,
                        "period": period,
                        "direction": direction,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "price_diff_pct": round(price_diff_pct * 100, 4),
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "setup_type": suggestion.get("setup_type"),
                        "entry_mode": entry_mode,
                        "confirmation": confirmation,
                        "activation_status": activation_status,
                        "plan_id": suggestion.get("plan_id"),
                        "valid_from": suggestion.get("valid_from"),
                        "expires_at": suggestion.get("expires_at"),
                        # Trigger state is strategy-scoped. Do not mutate the
                        # shared source plan or the first strategy would hide
                        # it from other deployments bound to the same source.
                        "status": "triggered",
                        "triggered_at": now_ts,
                        "confidence": suggestion.get('confidence', 75),
                        "reason": suggestion.get('reason'),
                        "trend": (result.trend_analysis or {}).get(str(period).upper()) or {},
                        "overall_trend": result.overall_trend or {},
                        "background_analysis": background_analysis,
                        "analyzed_at": result.analyzed_at
                    })
                    print(f"[LLMService] 价格接近AI入场价: {symbol} {period} "
                          f"入场价 {entry_price:.2f}, 当前价 {current_price:.2f}")

        # 清理过期记录
        self.llm_store.cleanup_entry_alerts()

        return matched

    # ==================== 分析执行 ====================

    def run_analysis(
        self, on_status: callable = None, on_complete: callable = None,
        due_only: bool = False,
    ) -> Dict:
        """
        执行分析

        Args:
            on_status: 状态回调
            on_complete: 完成回调

        Returns:
            分析结果
        """
        def report(status: str, message: str):
            self.llm_store.set_analysis_status(status, message)
            if on_status:
                on_status(status, message)

        if not self.is_enabled():
            report("error", "大模型分析未启用")
            return {"status": "error", "message": "大模型分析未启用"}

        # 获取品种列表
        symbols = self.kline_service.get_symbols()
        if not symbols:
            report("error", "没有品种数据")
            return {"status": "error", "message": "没有品种数据"}

        analysis_plan = self._build_ai_analysis_plan(symbols, due_only=due_only)
        if not analysis_plan:
            report("skipped", "没有启用大模型入场信号的策略，跳过 AI 分析")
            return {
                "status": "skipped",
                "message": "没有启用大模型入场信号的策略，跳过 AI 分析",
            }

        strategy_symbols = list(analysis_plan.keys())
        report("analyzing", f"正在检查 {len(strategy_symbols)} 个策略品种...")

        # 检查数据状态
        status = self.kline_service.check_symbols_status(
            strategy_symbols, self.STALE_THRESHOLD
        )
        active_symbols = status["active"]

        # 更新过期和休市品种状态。
        #
        # K 线目前由 EA 推送到进程内缓存。服务重启后，EA 可能仍认为自己
        # 已完成初始化，不会再次发送全量 K 线；此时 ``closed`` 只表示
        # “本进程尚未收到 K 线”，不能证明交易市场真的休市。若在这里把
        # 已保存的 AI 分析标记为 stale/closed，分析卡片会永久显示过期，
        # 后续 Tick 也会被信号规则拦截，直到 EA 人工重启。
        # 只有已经初始化过 M1 的品种，才允许用本轮时效检查覆盖分析状态。
        for symbol in status["stale"]:
            if self.kline_service.is_initialized(symbol, "M1"):
                self.llm_store.update_market_status(symbol, "stale", data_stale=True)
        for symbol in status["closed"]:
            if self.kline_service.is_initialized(symbol, "M1"):
                self.llm_store.update_market_status(symbol, "closed", data_stale=True)

        if not active_symbols:
            stale_minutes = self.STALE_THRESHOLD // 60
            report(
                "stale",
                f"所有品种行情均超过 {stale_minutes} 分钟未更新，暂不发起 AI 分析",
            )
            return {
                "status": "stale",
                "message": (
                    f"所有品种行情均超过 {stale_minutes} 分钟未更新，"
                    "暂不发起 AI 分析"
                ),
            }

        requests = self._build_individual_analysis_requests(analysis_plan)
        report(
            "analyzing",
            f"正在分析 {len(active_symbols)} 个品种，共 {len(requests)} 个 AI 信号源...",
        )

        def on_chunk(count, content):
            if count % 50 == 0:
                report("streaming", f"正在接收分析结果... ({len(content)} 字符)")

        response: Dict = {}
        plan_updates: List[Dict] = []
        analyzed_source_ids = set()
        analyzed_periods_by_symbol: Dict[str, set] = {}
        failed_sources: List[Dict] = []
        for request in requests:
            request_plan = {
                symbol: item for symbol, item in request["plan"].items()
                if symbol in active_symbols
            }
            if not request_plan:
                continue
            source_ids = sorted({
                str(profile.get("signal_source_id") or "")
                for item in request_plan.values()
                for profile in item.get("strategies", [])
                if profile.get("signal_source_id")
            })
            all_klines = self.collect_klines_for_analysis(
                list(request_plan), request_plan
            )
            if not all_klines:
                continue
            missing_klines = self._missing_primary_kline_data(
                all_klines, request_plan
            )
            if missing_klines:
                message = "主行情K线不足: " + "、".join(missing_klines)
                failed_sources.append({
                    "source_ids": source_ids,
                    "message": message,
                })
                print(f"[LLMService] 跳过AI分析: {message}")
                report("warning", message)
                continue
            prompt = self.build_analysis_prompt(
                all_klines,
                request_plan,
                analysis_prompt_template=request["analysis_prompt_template"],
                reference_context=self._shared_reference_context(
                    request["reference_runtime_ids"]
                ),
            )
            prompt = self._append_response_contract(prompt, request_plan)
            try:
                request_response = self.call_llm_stream(
                    prompt,
                    on_chunk,
                    model=request["model"],
                    system_prompt=request["system_prompt"],
                    response_validator=lambda payload, plan=request_plan: (
                        self._validate_analysis_response(payload, plan)
                    ),
                    object_type="ai_market_analysis",
                    object_id=",".join(source_ids),
                )
            except LLMQuotaExceeded as exc:
                message = str(exc)
                failed_sources.append({
                    "source_ids": source_ids,
                    "message": message,
                })
                self._notify_quota_exhausted(source_ids, exc)
                print(
                    "[LLMService] AI行情分析额度不足，跳过当前信号源: "
                    f"{','.join(source_ids) or 'unknown'}: {message}"
                )
                report("warning", "AI行情分析额度不足，已通知管理员")
                continue
            except LLMRequestError as exc:
                message = str(exc)
                failed_sources.append({
                    "source_ids": source_ids,
                    "message": message,
                })
                # 一个信号源失败不能阻断同一轮的其他周期和品种。
                print(
                    "[LLMService] 独立信号源分析失败，继续处理后续信号源: "
                    f"{','.join(source_ids) or 'unknown'}: {message}"
                )
                report(
                    "warning",
                    f"信号源 {','.join(source_ids) or 'unknown'} 分析失败，继续处理其他信号源",
                )
                continue
            if not request_response:
                message = "模型未返回有效分析结果"
                failed_sources.append({
                    "source_ids": source_ids,
                    "message": message,
                })
                print(
                    "[LLMService] 独立信号源返回空结果，继续处理后续信号源: "
                    f"{','.join(source_ids) or 'unknown'}"
                )
                continue
            request_response = self._normalize_analysis_response(
                request_response or {}, request_plan
            )
            self._attach_source_results(request_response, request_plan)
            plan_updates.extend(
                self._plan_updates_for_request(request_plan, request_response)
            )
            self._accumulate_symbol_result(response, request_response)
            self._publish_runtime_results(request_plan, request_response)
            analyzed_source_ids.update(
                profile.get("signal_source_id")
                for item in request_plan.values()
                for profile in item.get("strategies", [])
            )
            for symbol, item in request_plan.items():
                analyzed_periods_by_symbol.setdefault(symbol, set()).update(
                    str(period).upper()
                    for period in item.get("periods", {})
                )

        # 失败源只在本轮结束后更新失败时间，避免调度器在下一秒立即重复轰炸。
        if failed_sources:
            failed_at = time.time()
            for item in failed_sources:
                for source_id in item.get("source_ids", []):
                    if source_id:
                        self._source_last_analysis_at[source_id] = failed_at

        if not response:
            failure_message = "；".join(
                item["message"] for item in failed_sources if item.get("message")
            )
            message = failure_message or "无K线数据可分析或模型未返回有效结果"
            report("error", message)
            return {"status": "error", "message": message}

        # 保存结果
        if response:
            analysis_at = int(time.time())
            for symbol, analysis in response.items():
                if isinstance(analysis, dict):
                    # Persist only suggestions returned for this invocation.
                    # _retain_previous_source_results below appends still-valid
                    # results from sources that were not due this time.
                    self._trade_suggestion_repo.record_many(
                        self.llm_store.user_id,
                        symbol,
                        analysis.get("trade_suggestions") or [],
                        analysis_at,
                    )
                    previous = self.llm_store.get_analysis_result(symbol)
                    if previous:
                        self._retain_previous_source_results(
                            analysis,
                            previous,
                            analyzed_source_ids,
                            analyzed_periods_by_symbol.get(symbol, set()),
                        )
                    self.llm_store.save_analysis_dict(symbol, analysis)
            analyzed_at = time.time()
            for source_id in analyzed_source_ids:
                if source_id:
                    self._source_last_analysis_at[source_id] = analyzed_at

        if plan_updates and self._plan_update_handler:
            try:
                self._plan_update_handler(plan_updates)
            except Exception as exc:
                # A decision-audit failure must never discard the analysis.
                print(f"[LLMService] AI计划评估记录失败: {exc}")

        if on_complete:
            on_complete(response)

        analyzed_symbols = list(response.keys())
        completion_message = f"分析完成，共生成 {len(analyzed_symbols)} 个品种的结果"
        if failed_sources:
            completion_message += f"，另有 {len(failed_sources)} 个信号源失败"
        report("completed", completion_message)
        return {
            "status": "ok",
            "message": completion_message,
            "analyzed_symbols": analyzed_symbols,
            "failed_sources": failed_sources,
        }

    # ==================== 查询 ====================

    def get_analysis(self, symbol: str = None) -> Dict:
        """获取分析结果"""
        return self.llm_store.get_analysis(symbol)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.llm_store.get_status()
