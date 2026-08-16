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
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..models import LLMConfig, LLMAnalysisResult
from ..models.llm_config import (
    DEFAULT_ANALYSIS_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT,
)
from ..store import LLMStore
from .kline_service import KlineService
from sqlite_storage import AISignalSourceRepository, SharedAIRuntimeRepository
from llm_governance import AI_SIGNAL_ANALYSIS, LLMGovernanceService


class LLMRequestError(RuntimeError):
    """大模型供应商请求失败。"""


class LLMResponseFormatError(LLMRequestError):
    """大模型响应不是可用的 JSON。"""


class LLMService:
    """LLM 服务（处理业务逻辑）"""

    # 分析间隔（秒）
    ANALYZE_INTERVAL = 300  # 5分钟
    MAX_RESPONSE_ATTEMPTS = 3

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
        self._shared_runtime_repo = SharedAIRuntimeRepository()
        self._ai_signal_source_repo = AISignalSourceRepository()
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

    def set_allowed_strategy_ids(self, strategy_ids) -> None:
        """Limit live AI analysis to strategies deployed on this account."""
        self._allowed_strategy_ids = set(strategy_ids)

    def _build_ai_analysis_plan(
        self, available_symbols: List[str], due_only: bool = False,
    ) -> Dict[str, Dict]:
        """聚合同一品种多策略启用的 AI 周期和分析约束。"""
        if self._strategy_store is None:
            return {
                symbol: {
                    "periods": {
                        period: {"weight": 0}
                        for period in ['H4', 'H1', 'M15', 'M5', 'M1']
                    },
                    "strategies": [],
                }
                for symbol in available_symbols
            }

        available = set(available_symbols)
        plan: Dict[str, Dict] = {}
        seen_sources = set()
        # Build source-owned profiles first so one source is analyzed only once
        # even when several strategies or accounts bind to it.
        for source in self._ai_signal_source_repo.list(
            self.llm_store.user_id, enabled_only=True
        ):
            self._append_independent_ai_source_to_plan(
                plan, source, due_only, seen_sources, available
            )
        for strategy in self._strategy_store.get_all_strategies():
            if strategy.symbol not in available:
                continue
            if (
                self._allowed_strategy_ids is not None
                and strategy.strategy_id not in self._allowed_strategy_ids
            ):
                continue

            for source in strategy.get_signal_sources(
                "ai_entry", enabled_only=True
            ):
                self._append_ai_source_to_plan(
                    plan, strategy, source, due_only, seen_sources
                )

        for strategy in self._paper_deployed_strategies(available):
            for source in strategy.get_signal_sources(
                "ai_entry", enabled_only=True
            ):
                self._append_ai_source_to_plan(
                    plan, strategy, source, due_only, seen_sources
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
        source_key = ("independent", source_id)
        if source_key in seen_sources:
            return
        interval = max(1, int(params.get("analysis_interval_minutes", 5))) * 60
        if due_only and (
            time.monotonic() - self._source_last_analysis_at.get(source_id, -interval)
            < interval
        ):
            return
        seen_sources.add(source_key)
        symbol_plan = plan.setdefault(source["symbol"], {"periods": {}, "strategies": []})
        period = str(source.get("period") or "M5").upper()
        current = symbol_plan["periods"].get(period, {"weight": 0, "kline_count": 0})
        current["weight"] = max(current["weight"], 100)
        current["kline_count"] = max(current["kline_count"], max(10, min(500, int(params.get("kline_count", 100)))))
        symbol_plan["periods"][period] = current
        symbol_plan["strategies"].append({
            "strategy_id": "__independent__",
            "strategy_name": source.get("name") or "独立 AI 信号源",
            "signal_source_id": source_id,
            "periods": {period: 100},
            "min_confidence": int(params.get("min_confidence", 70)),
            "min_risk_reward": 1.0,
            "analysis_interval_minutes": interval // 60,
            "kline_count": int(params.get("kline_count", 100)),
            "model": str(params.get("model") or ""),
            "system_prompt": str(params.get("system_prompt") or ""),
            "analysis_prompt_template": str(params.get("analysis_prompt_template") or ""),
            "share_runtime_data": bool(source.get("share_runtime_data")),
            "reference_runtime_ids": list(params.get("reference_runtime_ids") or []),
            "signal_params": params,
            "symbol": source["symbol"],
            "strategy_lifecycle": "independent",
        })

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
        if due_only and (
            time.monotonic()
            - self._source_last_analysis_at.get(source_id, -interval)
            < interval
        ):
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
            max(10, min(500, int(params.get("kline_count", 100)))),
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
            "kline_count": int(params.get("kline_count", 100)),
            "model": str(params.get("model") or ""),
            "system_prompt": str(params.get("system_prompt") or ""),
            "analysis_prompt_template": str(
                params.get("analysis_prompt_template") or ""
            ),
            "share_runtime_data": runtime_shared,
            "reference_runtime_ids": list(
                params.get("reference_runtime_ids") or []
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
        config = self.llm_store.update_config(
            api_key, api_base, model, system_prompt, analysis_prompt_template
        )
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

        return all_klines

    # ==================== Prompt 构建 ====================

    def build_analysis_prompt(
        self,
        all_klines: Dict[str, Dict],
        analysis_plan: Optional[Dict[str, Dict]] = None,
        analysis_prompt_template: Optional[str] = None,
        reference_context: str = "",
    ) -> str:
        """构建分析提示词"""
        strategy_sections = []
        market_sections = []
        for symbol, klines_data in all_klines.items():
            constraints = [f"### {symbol}"]
            if analysis_plan and symbol in analysis_plan:
                for profile in analysis_plan[symbol]["strategies"]:
                    periods = "、".join(
                        f"{period}(权重{weight})"
                        for period, weight in self._period_weight_items(
                            profile.get("periods")
                        )
                    )
                    constraints.append(
                        f"- {profile['strategy_name']} ({profile['strategy_id']}), "
                        f"信号源ID {profile.get('signal_source_id', '')}: "
                        f"AI周期 {periods}；最低置信度 "
                        f"{profile['min_confidence']}%；最低盈亏比 "
                        f"{profile['min_risk_reward']}"
                    )
            if len(constraints) == 1:
                constraints.append("- 使用系统默认分析约束")
            strategy_sections.append("\n".join(constraints))

            market_lines = [f"### {symbol}"]
            for period, klines in klines_data.items():
                market_lines.append(f"\n#### {period} 周期（{len(klines)}根K线）")
                market_lines.append("| 时间 | 开盘 | 最高 | 最低 | 收盘 |")
                market_lines.append("|------|------|------|------|------|")
                for k in klines:
                    market_lines.append(
                        f"| {k['timestamp']} | {k['open']:.2f} | "
                        f"{k['high']:.2f} | {k['low']:.2f} | {k['close']:.2f} |"
                    )
            market_sections.append("\n".join(market_lines))

        config = self.llm_store.get_config()
        template = analysis_prompt_template or getattr(
            config, "analysis_prompt_template", DEFAULT_ANALYSIS_PROMPT_TEMPLATE
        )
        prompt = template.replace(
            "{{strategy_context}}", "\n\n".join(strategy_sections)
        ).replace("{{market_data}}", "\n\n".join(market_sections))
        if reference_context:
            prompt += (
                "\n\n## 其他用户共享的历史AI运行数据（仅供参考）\n"
                "这些数据可能来自不同账户或行情源，不得替代当前K线判断：\n"
                f"{reference_context}"
            )
        return prompt + (
            "\n\n## 策略归属硬性要求\n"
            "trade_suggestions 中每条建议必须包含 strategy_id 和 signal_source_id，"
            "且只能填写上方策略约束中列出的ID；同一周期被多个信号源启用时，必须分别"
            "输出建议，不得省略或合并。"
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
    ) -> Optional[Dict]:
        """调用 LLM API；响应格式错误时最多尝试三次。"""
        for attempt in range(1, self.MAX_RESPONSE_ATTEMPTS + 1):
            attempt_prompt = self._retry_prompt(prompt, attempt)
            try:
                return self._call_llm_once(
                    attempt_prompt, model, system_prompt,
                    scene_code, object_type, object_id,
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
        scene_code: str, object_type: str, object_id: str,
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

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": reservation["model"],
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
                "max_tokens": 4000
            }

            response = requests.post(
                f"{config.api_base}/chat/completions",
                headers=headers,
                json=data,
                timeout=120
            )

            if response.status_code != 200:
                detail = self._provider_error_detail(response)
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

        try:
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": reservation["model"],
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
                "stream": True
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
            "上一次响应为空或无法解析。请重新生成，并且只返回一个完整、合法的 "
            "JSON 对象或数组；不要输出 Markdown 代码块、解释、前后缀或截断内容。"
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
        """Normalize OpenAI-compatible text and reasoning response shapes."""
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

        # Some reasoning models keep their final structured response here.
        for key in ("reasoning_content", "reasoning", "analysis"):
            fallback = as_text(message.get(key))
            if fallback.strip():
                return fallback

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
            for key in ("text", "content", "reasoning_content"):
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
            if profile["strategy_id"].upper() in text and len(profile["periods"]) == 1:
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

    def _normalize_analysis_response(
        self, response: Dict, analysis_plan: Dict[str, Dict]
    ) -> Dict:
        """规范模型建议，并确保止盈满足对应策略的最低盈亏比。"""
        response = self._coerce_analysis_response(response, analysis_plan)
        for symbol, analysis in response.items():
            if not isinstance(analysis, dict) or symbol not in analysis_plan:
                continue

            symbol_plan = analysis_plan[symbol]
            enabled_periods = set(symbol_plan.get("periods", {}))
            normalized = []
            for suggestion in analysis.get("trade_suggestions", []):
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

                profiles = [
                    profile for profile in symbol_plan.get("strategies", [])
                    if period in dict(
                        self._period_weight_items(profile.get("periods"))
                    )
                ]
                requested_strategy_id = str(
                    suggestion.get("strategy_id") or ""
                ).strip()
                if requested_strategy_id:
                    profiles = [
                        profile for profile in profiles
                        if profile["strategy_id"] == requested_strategy_id
                    ]
                requested_source_id = str(
                    suggestion.get("signal_source_id") or ""
                ).strip()
                if requested_source_id:
                    profiles = [
                        profile for profile in profiles
                        if profile.get("signal_source_id") == requested_source_id
                    ]
                if not profiles:
                    continue

                risk = abs(entry - stop_loss)
                if risk <= 0:
                    continue
                for profile in profiles:
                    if int(suggestion.get("confidence", 0)) < int(
                        profile.get("min_confidence", 0)
                    ):
                        continue
                    strategy_suggestion = dict(suggestion)
                    required_rr = max(
                        1.0, float(profile.get("min_risk_reward", 1.0))
                    )
                    strategy_tp = take_profit
                    reward = abs(strategy_tp - entry)
                    if reward / risk < required_rr:
                        strategy_tp = (
                            entry + risk * required_rr
                            if direction == "buy"
                            else entry - risk * required_rr
                        )
                    strategy_suggestion.update({
                        "strategy_id": profile["strategy_id"],
                        "strategy_name": profile["strategy_name"],
                        "signal_source_id": profile.get("signal_source_id", ""),
                        "period": period,
                        "entry_price": entry,
                        "stop_loss": stop_loss,
                        "take_profit": round(strategy_tp, 8),
                    })
                    normalized.append(strategy_suggestion)

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
        if missing:
            raise LLMResponseFormatError(
                "大模型响应缺少必需的趋势分析：" + "、".join(missing)
            )

    def _group_analysis_plans(self, analysis_plan: Dict[str, Dict]) -> List[Dict]:
        """Group sources only when model, prompts, and references are identical."""
        config = self.llm_store.get_config()
        scene_defaults = self._scene_defaults(AI_SIGNAL_ANALYSIS)
        scene_model = scene_defaults.get("default_model_id") or getattr(config, "model", "")
        scene_system_prompt = (
            scene_defaults.get("system_prompt")
            or getattr(config, "system_prompt", DEFAULT_SYSTEM_PROMPT)
        )
        scene_template = (
            scene_defaults.get("user_prompt_template")
            or getattr(
                config,
                "analysis_prompt_template",
                DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
            )
        )
        groups: Dict[tuple, Dict] = {}
        for symbol, symbol_plan in analysis_plan.items():
            for profile in symbol_plan.get("strategies", []):
                model = profile.get("model") or scene_model
                system_prompt = profile.get("system_prompt") or scene_system_prompt
                template = (
                    profile.get("analysis_prompt_template")
                    or scene_template
                )
                references = tuple(profile.get("reference_runtime_ids") or [])
                key = (model, system_prompt, template, references)
                group = groups.setdefault(key, {
                    "model": model,
                    "system_prompt": system_prompt,
                    "analysis_prompt_template": template,
                    "reference_runtime_ids": list(references),
                    "plan": {},
                })
                target = group["plan"].setdefault(
                    symbol, {"periods": {}, "strategies": []}
                )
                target["strategies"].append(profile)
                for period, weight in self._period_weight_items(
                    profile.get("periods")
                ):
                    current = target["periods"].setdefault(
                        period, {"weight": 0, "kline_count": 0}
                    )
                    current["weight"] = max(current["weight"], int(weight))
                    current["kline_count"] = max(
                        current["kline_count"], int(profile.get("kline_count", 100))
                    )
        if not groups and analysis_plan:
            return [{
                "model": scene_model,
                "system_prompt": scene_system_prompt,
                "analysis_prompt_template": scene_template,
                "reference_runtime_ids": [],
                "plan": analysis_plan,
            }]
        return list(groups.values())

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
    def _merge_analysis_results(target: Dict, incoming: Dict) -> None:
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
            for key in ("overall_trend", "key_levels", "analyzed_at"):
                if analysis.get(key) is not None:
                    current[key] = analysis[key]

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
        strategy_id: str = "",
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
        if not result or not result.trade_suggestions:
            return matched

        for suggestion in result.trade_suggestions:
            suggestion_strategy_id = str(
                suggestion.get("strategy_id") or ""
            )
            if strategy_id and suggestion_strategy_id != strategy_id:
                continue
            entry_price = suggestion.get('entry_price')
            period = suggestion.get('period')
            direction = suggestion.get('direction')
            stop_loss = suggestion.get('stop_loss')
            take_profit = suggestion.get('take_profit')

            if not entry_price or entry_price <= 0:
                continue

            # 验证止损止盈
            if not stop_loss or not take_profit or stop_loss <= 0 or take_profit <= 0:
                print(f"[LLMService] 跳过无效建议: {period} sl={stop_loss}, tp={take_profit}")
                continue

            price_diff_pct = abs(current_price - entry_price) / entry_price

            if price_diff_pct <= threshold:
                # 检查冷却
                can_alert = self.llm_store.check_entry_alert_cooldown(
                    symbol, period, direction, entry_price, strategy_id,
                    str(suggestion.get("signal_source_id") or ""),
                    result.analyzed_at or "",
                )

                if can_alert:
                    matched.append({
                        "symbol": symbol,
                        "strategy_id": suggestion_strategy_id,
                        "strategy_name": suggestion.get("strategy_name", ""),
                        "signal_source_id": suggestion.get("signal_source_id", ""),
                        "period": period,
                        "direction": direction,
                        "entry_price": entry_price,
                        "current_price": current_price,
                        "price_diff_pct": round(price_diff_pct * 100, 4),
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": suggestion.get('confidence', 75),
                        "reason": suggestion.get('reason'),
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

        # 更新过期和休市品种状态
        for symbol in status["stale"]:
            self.llm_store.update_market_status(symbol, "stale", data_stale=True)
        for symbol in status["closed"]:
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

        groups = self._group_analysis_plans(analysis_plan)
        report(
            "analyzing",
            f"正在分析 {len(active_symbols)} 个品种，共 {len(groups)} 组模型配置...",
        )

        def on_chunk(count, content):
            if count % 50 == 0:
                report("streaming", f"正在接收分析结果... ({len(content)} 字符)")

        response: Dict = {}
        analyzed_source_ids = set()
        analyzed_periods_by_symbol: Dict[str, set] = {}
        for group in groups:
            group_plan = {
                symbol: item for symbol, item in group["plan"].items()
                if symbol in active_symbols
            }
            if not group_plan:
                continue
            all_klines = self.collect_klines_for_analysis(
                list(group_plan), group_plan
            )
            if not all_klines:
                continue
            prompt = self.build_analysis_prompt(
                all_klines,
                group_plan,
                analysis_prompt_template=group["analysis_prompt_template"],
                reference_context=self._shared_reference_context(
                    group["reference_runtime_ids"]
                ),
            )
            source_ids = sorted({
                str(profile.get("signal_source_id") or "")
                for item in group_plan.values()
                for profile in item.get("strategies", [])
                if profile.get("signal_source_id")
            })
            try:
                group_response = self.call_llm_stream(
                    prompt,
                    on_chunk,
                    model=group["model"],
                    system_prompt=group["system_prompt"],
                    response_validator=lambda payload, plan=group_plan: (
                        self._validate_analysis_response(payload, plan)
                    ),
                    object_type="ai_market_analysis",
                    object_id=",".join(source_ids),
                )
            except LLMRequestError as exc:
                message = str(exc)
                report("error", message)
                return {"status": "error", "message": message}
            group_response = self._normalize_analysis_response(
                group_response or {}, group_plan
            )
            self._merge_analysis_results(response, group_response)
            self._publish_runtime_results(group_plan, group_response)
            analyzed_source_ids.update(
                profile.get("signal_source_id")
                for item in group_plan.values()
                for profile in item.get("strategies", [])
            )
            for symbol, item in group_plan.items():
                analyzed_periods_by_symbol.setdefault(symbol, set()).update(
                    str(period).upper()
                    for period in item.get("periods", {})
                )

        if not response:
            report("error", "无K线数据可分析或模型未返回有效结果")
            return {"status": "error", "message": "模型未返回有效分析结果"}

        # 保存结果
        if response:
            for symbol, analysis in response.items():
                if isinstance(analysis, dict):
                    previous = self.llm_store.get_analysis_result(symbol)
                    if previous:
                        self._retain_previous_source_results(
                            analysis,
                            previous,
                            analyzed_source_ids,
                            analyzed_periods_by_symbol.get(symbol, set()),
                        )
                    self.llm_store.save_analysis_dict(symbol, analysis)
            analyzed_at = time.monotonic()
            for source_id in analyzed_source_ids:
                if source_id:
                    self._source_last_analysis_at[source_id] = analyzed_at

        if on_complete:
            on_complete(response)

        analyzed_symbols = list(response.keys())
        report("completed", f"分析完成，共生成 {len(analyzed_symbols)} 个品种的结果")
        return {
            "status": "ok",
            "message": "分析完成",
            "analyzed_symbols": analyzed_symbols,
        }

    # ==================== 查询 ====================

    def get_analysis(self, symbol: str = None) -> Dict:
        """获取分析结果"""
        return self.llm_store.get_analysis(symbol)

    def get_status(self) -> Dict:
        """获取状态"""
        return self.llm_store.get_status()
