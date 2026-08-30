#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情相关的接口路由
包括K线数据接收、查询、WebSocket推送等
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from calendar import timegm
import asyncio
import json
import time
import uuid
import threading

from auth import AuthUser, get_auth_manager, require_admin, require_auth
from ea_auth import EAIdentity, require_ea_auth
from market.services import PivotService
from market.models.llm_config import (
    DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
    DEFAULT_SYSTEM_PROMPT,
    STRUCTURE_ANALYSIS_PROMPT_TEMPLATE,
)
from llm_governance import (
    AI_SIGNAL_ANALYSIS, AI_SIGNAL_PROMPT_GENERATION,
    LLMGovernanceError, LLMGovernanceService,
)
from market.services.llm_service import LLMRequestError
from market.mt5_time import broker_wall_epoch_to_utc, normalize_epoch
from membership import MembershipService
from sqlite_storage import (
    get_storage,
    LLMAccessRepository,
    LLMConfigRepository,
    AISignalSourceRepository,
    AITradeSuggestionRepository,
    SharedAIRuntimeRepository,
    PositionManagementPolicyRepository,
    PlatformInstrumentMappingRepository,
    StrategyConfigRepository,
    StrategyDeploymentRepository,
    TradeConfigRepository,
    TradeExecutionRepository,
    PositionManagementEventRepository,
    RuntimeStateRepository,
    TradingAccountRepository,
    UserRepository,
)
from market.models.trading_strategy import StrategyLifecycle
from trading_engine_manager import TradingEngineManager
from strategy_admission import StrategyAdmissionService
from web_account_context import resolve_web_engine
from user_quotas import UserQuotaService
from configuration_impact import ConfigurationImpactService
from alpha_research import AlphaLibraryRepository
from system_event_log import SystemEventLogRepository
from market.services.market_structure_service import analyze as analyze_market_structure
from market.services.market_structure_engine_v2 import analyze_incremental as analyze_market_structure_v2, restore_snapshot as restore_market_structure_snapshot, DEFAULT_CONFIG as MARKET_STRUCTURE_DEFAULT_CONFIG
from market.services.market_structure_snapshot_store import current_path as market_structure_snapshot_path, load_current as load_market_structure_snapshot, save_checkpoint as save_market_structure_checkpoint
from market.store.structure_plan_store import StructureTradePlanRepository
from market.services.signal.structure_plan_signal import StructurePlanBuilder, STRUCTURE_PLAN_DEFAULT_CONFIG, resolve_structure_plan_config
from market_data_source_policy import MarketDataSourcePolicy


def _compact_market_structure_snapshot(result: Dict) -> Dict:
    """Persist state-machine context without overflowing MySQL TEXT payloads."""
    if not isinstance(result, dict):
        return {}
    keep = {
        "symbol", "period", "engine_version", "config", "config_signature",
        "window_signature", "calculation_mode", "previous_last_bar_time",
        "last_bar_time", "analyzed_at", "atr", "major_state", "external_state",
        "internal_state", "active_candidate", "locked_segment_count",
        "structure_levels", "machine_context", "swings", "pivot_levels",
        "trendlines", "events", "internal_events", "major_events", "external_events",
        "candidates", "structure_hierarchy", "local_patterns", "evidence", "state_detail",
    }
    snapshot = {key: value for key, value in result.items() if key in keep}
    # Keep enough chart facts for a refresh, but never persist the full
    # calculation window and all intermediate objects. This bounded snapshot
    # avoids MySQL payload failures while preserving visible overlays.
    snapshot["swings"] = list(result.get("swings") or [])[-120:]
    snapshot["pivot_levels"] = {
        name: list(items or [])[-80:]
        for name, items in (result.get("pivot_levels") or {}).items()
    }
    for field, limit in (("events", 120), ("internal_events", 120),
                         ("major_events", 120), ("external_events", 120),
                         ("candidates", 30), ("trendlines", 20),
                         ("local_patterns", 20), ("segment_history", 10)):
        snapshot[field] = list(result.get(field) or [])[-limit:]
    snapshot["segments"] = list(result.get("segments") or [])[-5:]
    if len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")) <= 60000:
        return snapshot
    snapshot.pop("structure_levels", None)
    for segment in snapshot.get("segment_history", []) + snapshot.get("segments", []):
        if isinstance(segment, dict) and isinstance(segment.get("evidence"), dict):
            segment["evidence"] = {
                key: value for key, value in segment["evidence"].items()
                if key not in {"bars", "pivots", "closes", "highs", "lows"}
            }
    return snapshot


def _historical_kline_timestamp(value):
    """统一 EA K 线时间为 epoch 秒，便于跨时区回放查询。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number >= 10**12 else number
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M"):
        try:
            return timegm(datetime.strptime(text, fmt).timetuple())
        except ValueError:
            continue
    return None


def _deployment_is_running(deployment, account, trade_config_enabled=True, now=None):
    """Return whether a deployment can currently receive strategy decisions."""
    if (
        not account
        or str(deployment.get("status") or "") != "active"
        or str(deployment.get("execution_mode") or "") not in {"paper", "live"}
        or not trade_config_enabled
        or account.status != "active"
        or not account.enabled
        or not account.trading_enabled
        or not account.auto_trading_enabled
    ):
        return False
    if str(deployment.get("execution_mode")) == "live":
        current_time = int(time.time() if now is None else now)
        return bool(
            account.account_type == "mt5"
            and account.last_seen_at
            and current_time - int(account.last_seen_at) <= 120
        )
    return account.account_type == "paper"


def _persist_historical_klines(
    identity, symbol, period, klines, broker_utc_offset_seconds=0
):
    rows = []
    now = int(time.time())
    for item in klines or []:
        if not isinstance(item, dict):
            continue
        timestamp = _historical_kline_timestamp(item.get("timestamp") or item.get("time"))
        if timestamp is None:
            continue
        offset = item.get(
            "broker_utc_offset_seconds", broker_utc_offset_seconds
        )
        utc_timestamp = normalize_epoch(item.get("timestamp_utc"))
        # Do not label a legacy broker-wall epoch as UTC when no offset was
        # supplied. EA 2.07 always sends timestamp_utc explicitly.
        if utc_timestamp is None and int(offset or 0) != 0:
            utc_timestamp = broker_wall_epoch_to_utc(timestamp, offset)
        try:
            rows.append((identity.user_id, 0, symbol, period, timestamp,
                         int(utc_timestamp or 0), int(offset or 0),
                         float(item.get("open", 0)), float(item.get("high", 0)),
                         float(item.get("low", 0)), float(item.get("close", 0)),
                         float(item.get("volume", 0) or 0), now))
        except (TypeError, ValueError):
            continue
    if not rows:
        return
    storage = get_storage()
    storage.executemany(
        """
        INSERT INTO historical_klines
          (user_id, account_id, symbol, period, timestamp, timestamp_utc,
           broker_utc_offset_seconds, open_price, high_price,
           low_price, close_price, volume, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE
          timestamp_utc=VALUES(timestamp_utc),
          broker_utc_offset_seconds=VALUES(broker_utc_offset_seconds),
          open_price=VALUES(open_price), high_price=VALUES(high_price),
          low_price=VALUES(low_price), close_price=VALUES(close_price),
          volume=VALUES(volume), updated_at=VALUES(updated_at)
        """, rows,
    )
    # 上传时顺便执行轻量清理；即使没有行情上传，下一次上传也会清理。


def _restore_kline_memory(identity, symbol, period, kline_service) -> int:
    """Restore recent persistent bars after a backend restart."""
    if kline_service.is_initialized(symbol, period):
        return len(kline_service.get_all_klines(symbol, period))
    limits = {"H4": 1100, "H1": 720, "M15": 1200, "M5": 1200, "M1": 1200}
    rows = get_storage().fetchall(
        """
        SELECT timestamp, timestamp_utc, broker_utc_offset_seconds,
               open_price, high_price, low_price, close_price, volume
        FROM historical_klines
        WHERE user_id = ? AND account_id = ? AND symbol = ? AND period = ?
        ORDER BY timestamp DESC LIMIT ?
        """,
        (identity.user_id, 0, symbol, period, limits.get(period, 1200)),
    )
    if not rows:
        return 0
    bars = [{
        "timestamp": int(row["timestamp"]),
        "timestamp_utc": int(row["timestamp_utc"] or 0),
        "broker_utc_offset_seconds": int(row["broker_utc_offset_seconds"] or 0),
        "open": float(row["open_price"]),
        "high": float(row["high_price"]),
        "low": float(row["low_price"]),
        "close": float(row["close_price"]),
        "volume": float(row["volume"] or 0),
    } for row in reversed(rows)]
    kline_service.process_kline_data(symbol, period, bars, True)
    return len(bars)
from market.system_log import get_system_log_broadcaster
from shared_notifications import SharedReferenceNotificationService
from instrument_price_store import get_instrument_price_store


_STRATEGY_REVIEW_JOBS = {}
_STRATEGY_REVIEW_JOBS_LOCK = threading.RLock()
_STRATEGY_REVIEW_JOB_TTL = 2 * 3600


class _StrategyReviewRequest:
    """Minimal request adapter used when the existing review handler runs in a worker."""
    def __init__(self, payload):
        self.payload = payload
        self._strategy_review_background = True

    async def json(self):
        return self.payload


AI_SIGNAL_SOURCE_RUNTIME_CONTRACT = {
    "runtime_variables": [
        {
            "name": "{{market_data}}",
            "required": True,
            "description": "主品种、主周期的 K 线 Markdown 表格。只有配置数量的主 K 线完整可用时才会调用模型。",
            "fields": ["timestamp", "open", "high", "low", "close"],
        },
        {
            "name": "{{current_price}}",
            "required": True,
            "description": "主品种最新可观测报价，来自最新 M1 K 线的 close；用于理解当前市场位置和风险，不限制交易建议必须立即入场。",
            "fields": ["symbol", "price", "timestamp", "source_period"],
            "restriction": "报价不可用时仅能输出趋势分析，不能输出交易建议。",
        },
        {
            "name": "{{reference_market_data}}",
            "required": False,
            "description": "配置后的参考品种或周期 K 线表格；未配置时为空。仅能辅助主行情判断。",
            "fields": ["timestamp", "open", "high", "low", "close"],
            "restriction": "不得据此单独输出趋势或交易建议。",
        },
    ],
    "output_contract": {
        "top_level": "必须以实际主品种名称作为唯一顶层键。",
        "required": [
            "trend_analysis[主周期].trend",
            "trend_analysis[主周期].confidence（0-100）",
            "trend_analysis[主周期].reason",
            "trade_suggestions（没有机会时为 []）",
        ],
        "trade_suggestion": [
            "signal_source_id", "period（必须为主周期）", "direction（buy 或 sell）",
            "confidence（0-100）", "entry_price", "stop_loss", "take_profit", "reason",
        ],
        "price_rules": [
            "buy: stop_loss < entry_price < take_profit",
            "sell: take_profit < entry_price < stop_loss",
        ],
    },
    "strategy_usage": [
        "trend_analysis 用于策略的趋势共识判断。",
        "trade_suggestions 是基于K线结构的未来价格计划；区间可给支撑买入/压力卖出，趋势可给回调或反抽顺势入场。策略只会在实际 Tick 接近 entry_price 时转为入场信号。",
        "随后仍会校验止损止盈方向、最低盈亏比、最大风险和策略仓位限制。",
    ],
}
AI_SIGNAL_KLINE_MIN_COUNT = 10
AI_SIGNAL_KLINE_MAX_COUNT = 288


def collect_ai_signal_symbols(
    user_id: int,
    engine_manager: TradingEngineManager,
    trade_config_repo,
    strategy_repo,
    ai_signal_source_repo,
    account_repo=None,
) -> List[str]:
    """Merge every durable user symbol with symbols currently reported by MT5.

    A strategy can be created before the selected MT5 engine has received its
    first tick (and engines are intentionally in-memory).  Therefore this
    list must not be derived from one primary engine alone.
    """
    symbols = set()

    def add_engine_symbols(engine) -> None:
        try:
            symbols.update(engine.kline_service.get_symbols())
        except Exception:
            # Configuration remains usable while an account engine is starting
            # or has been evicted after its idle timeout.
            pass

    try:
        add_engine_symbols(engine_manager.get_market_engine(user_id))
    except Exception:
        pass

    config = trade_config_repo.get_config(user_id)
    symbols.update(config.get("symbol_config", {}).keys())
    symbols.update(
        strategy.symbol for strategy in strategy_repo.get_all_strategies(user_id)
        if strategy.symbol
    )
    symbols.update(
        source["symbol"] for source in ai_signal_source_repo.list(user_id)
        if source.get("symbol")
    )
    return sorted(symbols)


def create_market_routes(
    engine_manager: TradingEngineManager,
) -> APIRouter:
    """创建按当前用户或 EA 动态解析引擎的行情路由。"""
    router = APIRouter()
    protected_router = APIRouter(dependencies=[Depends(require_auth)])

    trade_config_repo = TradeConfigRepository()
    strategy_repo = StrategyConfigRepository()
    strategy_deployment_repo = StrategyDeploymentRepository()
    account_repo = TradingAccountRepository()
    position_policy_repo = PositionManagementPolicyRepository()
    llm_config_repo = LLMConfigRepository()
    llm_access_repo = LLMAccessRepository()
    shared_ai_runtime_repo = SharedAIRuntimeRepository()
    instrument_mapping_repo = PlatformInstrumentMappingRepository()
    ai_signal_source_repo = AISignalSourceRepository()
    quota_service = UserQuotaService()
    admission_service = StrategyAdmissionService(engine_manager.paper_trading)
    alpha_library = AlphaLibraryRepository()
    llm_governance = LLMGovernanceService()
    event_logs = SystemEventLogRepository()
    memberships = MembershipService()
    shared_notifications = SharedReferenceNotificationService()
    impact_service = ConfigurationImpactService()
    market_source_policy = MarketDataSourcePolicy()

    def strategy_payload(strategy, user_id: int) -> Dict:
        payload = strategy.to_dict()
        payload["readonly_reference"] = bool(strategy.source_owner_user_id)
        payload["deployment_count"] = strategy_repo.strategy_deployment_count(
            int(user_id), strategy.strategy_id,
        )
        if payload["readonly_reference"]:
            for source in payload.get("signal_sources") or []:
                source["params"] = {}
        return payload

    def shared_strategy_payload(strategy: Dict, viewer_user_id: int) -> Dict:
        payload = dict(strategy)
        options = instrument_mapping_repo.target_options(
            int(payload["owner_user_id"]), payload.get("symbol", ""), viewer_user_id,
        )
        payload["target_symbol_options"] = options
        payload["mapping_notice"] = (
            "可选择平台已关联的交易商品种；未配置映射时仅支持同名品种。"
        )
        return payload

    def enrich_shared_ai_items(
        items: List[Dict], user_id: int,
        target_symbols: Optional[List[str]] = None,
        target_server: str = "",
    ) -> List[Dict]:
        """Keep every shared result and rank its fit for the viewer's symbols."""
        targets = list(dict.fromkeys(
            str(value or "").strip()
            for value in (target_symbols or [])
            if str(value or "").strip()
        ))
        enriched = []
        for item in items:
            item = dict(item)
            source_server = instrument_mapping_repo.source_server(
                item["owner_user_id"], item.get("symbol", ""),
            )
            item["broker_server"] = source_server
            recommendations = []
            for target_symbol in targets:
                similarity = shared_ai_runtime_repo.symbol_similarity(
                    item.get("symbol", ""), target_symbol,
                )
                compatible = (
                    instrument_mapping_repo.compatible(
                        source_server, item.get("symbol", ""),
                        target_server, target_symbol,
                    )
                    if target_server else
                    instrument_mapping_repo.user_can_use_symbol(
                        item["owner_user_id"], item.get("symbol", ""),
                        user_id, target_symbol,
                    )
                )
                exact = (
                    instrument_mapping_repo._normalize(item.get("symbol", ""))
                    == instrument_mapping_repo._normalize(target_symbol)
                )
                recommendations.append({
                    "target_symbol": target_symbol,
                    "applicable": bool(compatible),
                    "match_type": (
                        "exact" if exact else
                        "platform_mapping" if compatible else
                        "similar" if similarity >= 0.85 else "unmatched"
                    ),
                    "similarity": similarity,
                })
            recommendations.sort(key=lambda value: (
                -int(value["applicable"]), -float(value["similarity"]),
                value["target_symbol"],
            ))
            item["symbol_recommendations"] = recommendations
            item["recommended_symbols"] = [
                value["target_symbol"] for value in recommendations
                if value["applicable"]
            ]
            item["similar_symbols"] = [
                value["target_symbol"] for value in recommendations
                if not value["applicable"] and value["match_type"] == "similar"
            ]
            item["mapping_compatible"] = bool(item["recommended_symbols"])
            item["symbol_similarity"] = max(
                (float(value["similarity"]) for value in recommendations),
                default=0.0,
            )
            enriched.append(item)
        enriched.sort(key=lambda item: (
            -int(item["mapping_compatible"]),
            -float(item["symbol_similarity"]),
            -int(item.get("updated_at") or 0),
            item.get("share_id", ""),
        ))
        return enriched

    def add_audit_event(
        user: AuthUser, event_type: str, event_name: str, message: str,
        detail: Optional[Dict] = None, entity_type: str = "",
        entity_id: str = "",
    ) -> None:
        event_logs.add({
            "level": "info", "category": "audit",
            "event_type": event_type, "event_name": event_name,
            "user_id": user.user_id, "actor_type": "user",
            "actor_id": str(user.user_id), "message": message,
            "status": "completed", "detail": detail or {},
            "entity_type": entity_type, "entity_id": entity_id,
            "correlation_id": entity_id,
        })

    def notify_strategy_references(user: AuthUser, strategy, action: str) -> None:
        recipients = strategy_repo.list_strategy_references(
            user.user_id, strategy.strategy_id
        )
        if not recipients:
            return
        for item in recipients:
            engine_manager.refresh_user_strategies(int(item["user_id"]))
        shared_notifications.notify(
            recipients,
            f"AI Trader 共享策略已{action}",
            (
                f"你正在引用的共享策略「{strategy.strategy_name}」已由原作者{action}。\n"
                "该策略是动态引用，变更会自动同步到你的策略执行、模拟、回测与实盘链路。"
            ),
        )

    def assert_strategy_not_locked(
        user: AuthUser, strategy, allow_hot_reload: bool = False,
    ) -> None:
        if (
            strategy.visibility == "shared"
            and strategy_repo.strategy_application_count(user.user_id, strategy.strategy_id)
            and not allow_hot_reload
        ):
            raise ValueError("该共享策略已被应用，不能继续修改；请复制为新策略后再调整")

    def assert_strategy_material_edit_allowed(
        strategy, data: Dict, allow_hot_reload: bool = False,
    ) -> None:
        material_fields = {
            "signal_config", "signal_sources", "signal_weights", "period_weights",
            "min_confidence", "consistency_requirement",
            "conflict_resolution", "fixed_volume", "volume_mode",
            "risk_percent", "max_positions",
            "max_same_direction", "position_management_policy_id",
            "min_risk_reward", "max_risk_reward", "trading_hours",
            "position_conflict",
        }
        if (
            strategy.lifecycle_status in {"paper_trading", "production"}
            and any(field in data for field in material_fields)
            and not allow_hot_reload
        ):
            raise ValueError("策略进入模拟盘或实盘后不能直接修改核心配置；请复制为新策略重新验证")

    def bind_alpha_signal_snapshots(user: AuthUser, data: Dict) -> None:
        """Resolve Alpha metadata but keep execution linked to the live library row."""
        for source in data.get("signal_sources") or []:
            if source.get("source") != "alpha_factor":
                continue
            params = source.setdefault("params", {})
            alpha_id = str(params.get("alpha_id") or "").strip()
            alpha = alpha_library.get_visible(user.user_id, alpha_id)
            if alpha is None or alpha.get("status") != "validated":
                raise ValueError("选择的 Alpha 不存在、未通过验证或已停用")
            definition = alpha.get("definition") or {}
            period = str(definition.get("timeframe") or "").upper()
            if not period:
                raise ValueError("Alpha 缺少可执行周期")
            source["period"] = period
            params.update({
                "alpha_id": alpha["alpha_id"],
                "alpha_owner_user_id": int(alpha.get("user_id") or user.user_id),
                "alpha_version": int(alpha.get("version") or 1),
                "alpha_name": alpha.get("name") or "Validated Alpha",
                "alpha_snapshot": {},
            })

    def validate_ai_signal_configuration(user: AuthUser, data: Dict) -> None:
        ai_sources = [
            source for source in (data.get("signal_sources") or [])
            if source.get("source") == "ai_entry"
        ]
        if not ai_sources:
            return
        access = llm_access_repo.get_status(user.user_id, user.role)
        effective_config = llm_config_repo.get_effective_config(user.user_id)
        access = {
            **access,
            "service_configured": effective_config.enabled,
            "feature_enabled": (
                access["access_granted"] and effective_config.enabled
            ),
        }
        for source in ai_sources:
            params = source.get("params") or {}
            managed_source_id = str(params.get("ai_signal_source_id") or "").strip()
            if not managed_source_id:
                raise ValueError("请选择独立管理的 AI 信号源")
            owner_user_id = int(
                params.get("ai_signal_source_owner_id") or user.user_id
            )
            managed_source = ai_signal_source_repo.get_visible(
                user.user_id, managed_source_id, owner_user_id,
            )
            if managed_source is None or not managed_source.get("enabled"):
                raise ValueError("选择的 AI 信号源不存在、未共享或已停用")
            if str(source.get("period", "")).upper() != managed_source["period"]:
                raise ValueError("策略中的 AI 信号周期必须与独立信号源一致")
            source["signal_source_id"] = managed_source_id
            params["ai_signal_source_owner_id"] = int(managed_source["user_id"])
            if int(managed_source["user_id"]) != user.user_id:
                if not instrument_mapping_repo.user_can_use_symbol(
                    int(managed_source["user_id"]), managed_source["symbol"],
                    user.user_id, data.get("symbol", ""),
                ):
                    raise ValueError("该共享 AI 信号源与当前策略品种不兼容")
                continue
            if not access["access_granted"]:
                raise ValueError("自主AI分析仅对已开通大模型分析的付费用户开放")
            options = llm_governance.scene_options(
                user.user_id, AI_SIGNAL_ANALYSIS
            )
            if str((managed_source.get("config") or {}).get("model") or "") not in options["models"]:
                raise ValueError("AI入场信号选择的模型不在管理员开放列表中")
            for share_id in (managed_source.get("config") or {}).get("reference_runtime_ids") or []:
                shared = shared_ai_runtime_repo.get_shared(str(share_id))
                if shared is None:
                    raise ValueError("选择的共享AI运行数据不存在或已取消共享")

    # ==================== EA端接口 ====================

    @router.get("/ea/kline_cursor/{period}")
    async def get_ea_kline_cursor(
        period: str,
        symbol: str = Query(...),
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """返回服务端已持久化的最后一根K线时间，作为断线补传游标。"""
        period = period.upper()
        if period not in {"H4", "H1", "M15", "M5", "M1"}:
            raise HTTPException(status_code=400, detail=f"不支持的周期: {period}")
        market_policy = market_source_policy.resolve(
            identity.user_id, identity.account_id, symbol,
        )
        if market_policy.get("mode") == "blocked":
            raise HTTPException(status_code=409, detail=market_policy)
        engine = engine_manager.get_engine_for_ea(identity)
        restored_count = _restore_kline_memory(
            identity, symbol, period, engine.kline_service,
        )
        row = get_storage().fetchone(
            """
            SELECT MAX(timestamp) AS last_bar_time, COUNT(*) AS bar_count
            FROM historical_klines
            WHERE user_id = ? AND account_id = ? AND symbol = ? AND period = ?
            """,
            (identity.user_id, 0, symbol, period),
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "server_last_bar_time": int(row["last_bar_time"]) if row and row["last_bar_time"] else 0,
            "stored_bar_count": int(row["bar_count"] or 0) if row else 0,
            "memory_restored_count": restored_count,
            "market_source": market_policy,
        }

    @router.post("/ea/kline/{period}")
    async def receive_kline(
        period: str,
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """
        EA推送K线数据
        """
        period = period.upper()
        engine = engine_manager.get_engine_for_ea(identity)
        kline_store = engine.kline_store
        kline_service = engine.kline_service
        pivot_service = engine.pivot_service

        if period not in ['H4', 'H1', 'M15', 'M5', 'M1']:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"不支持的周期: {period}"}
            )

        try:
            data = await request.json()
            symbol = data.get('symbol', 'GOLD')
            is_full = data.get('is_full', False)
            klines = data.get('klines', [])
            market_policy = market_source_policy.resolve(
                identity.user_id, identity.account_id, symbol,
            )
            if market_policy.get("mode") == "blocked":
                return JSONResponse(
                    status_code=409,
                    content={"status": "error", "code": "MARKET_SOURCE_CONFLICT", **market_policy},
                )
            if market_policy.get("mode") == "reuse":
                return {
                    "status": "ok", "count": 0, "ignored": True,
                    "message": market_policy.get("message"),
                    "market_source": market_policy,
                }

            # 服务重启后先用MySQL恢复内存行情，再接受缺口增量。
            if not kline_service.is_initialized(symbol, period):
                _restore_kline_memory(identity, symbol, period, kline_service)

            if not klines:
                return {"status": "ok", "count": 0, "message": "无数据"}

            staleness = None

            # 全量数据时检查K线时效性。历史数据即使过期也要入库，
            # 时效性只作为告警，避免券商服务器时区差异导致整批数据丢失。
            if is_full:
                staleness = kline_service.check_staleness(symbol, period, klines)

                if staleness.get('latest_kline_time'):
                    trade_config = engine.trade_config
                    timezone_offset_hours = trade_config.mt5_timezone_offset

                    staleness = kline_service.check_staleness(
                        symbol, period, klines, timezone_offset_hours
                    )

                    if staleness.get('is_stale'):
                        system_log = engine.system_log
                        system_log.add_log(
                            "ea_kline_stale",
                            {
                                "period": period,
                                "latest_kline_time": staleness.get('latest_kline_time').isoformat() if staleness.get('latest_kline_time') else None,
                                "kline_time_local": staleness.get('kline_time_local').isoformat() if staleness.get('kline_time_local') else None,
                                "time_diff_seconds": staleness.get('time_diff_seconds'),
                                "period_interval": staleness.get('period_interval')
                            },
                            symbol=symbol,
                            message=f"K线数据过期，最新K线距当前 {staleness.get('time_diff_seconds')}秒，可能休市"
                        )
                        print(f"[MarketAPI] {symbol} {period} 全量K线数据过期")

            # 检查是否需要全量数据
            if not is_full and not kline_service.is_initialized(symbol, period):
                print(f"[MarketAPI] {symbol} {period} 未初始化，需要全量数据")
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "code": 8888,
                        "message": "需要全量数据"
                    }
                )

            # 增量数据时检查连续性
            if not is_full and kline_service.is_initialized(symbol, period):
                continuity = kline_service.check_continuity(symbol, period, klines)
                if not continuity["is_continuous"]:
                    # 周末、休市和券商停盘本来就没有K线，不能仅按墙钟间隔
                    # 认定丢失并强制全量。EA会按服务端持久化游标补传券商
                    # 实际存在的所有bar；此处保留告警但继续幂等写入。
                    print(
                        f"[MarketAPI] {symbol} {period} 时间间隔跨越 "
                        f"{continuity['gap_count']} 个理论周期，按EA游标数据接收"
                    )

            # 保存K线数据
            result = kline_service.process_kline_data(symbol, period, klines, is_full)
            _persist_historical_klines(
                identity, symbol, period, klines,
                data.get("broker_utc_offset_seconds", 0),
            )
            if staleness and staleness.get("is_stale"):
                result.update({
                    "stale": True,
                    "message": "K线数据已接收，最新时间可能受 MT5 服务器时区或休市影响",
                    "latest_kline_time": (
                        staleness["latest_kline_time"].isoformat()
                        if staleness.get("latest_kline_time")
                        else None
                    ),
                    "time_diff_seconds": staleness.get("time_diff_seconds"),
                })

            # 记录日志
            if is_full:
                system_log = engine.system_log
                event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                system_log.add_log(
                    event_type,
                    {"period": period, "count": len(klines), "is_full": is_full},
                    symbol=symbol,
                    message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                )

            if result['status'] == 'ok':
                # 更新转折点
                all_klines = kline_service.get_all_kline_objects(symbol, period)
                if all_klines:
                    pivot_service.update_pivots(symbol, period, all_klines)
                result["structure_plan_count"] = engine.refresh_structure_plans(
                    symbol, period
                )

            cursor_row = get_storage().fetchone(
                """
                SELECT MAX(timestamp) AS last_bar_time
                FROM historical_klines
                WHERE user_id = ? AND account_id = ? AND symbol = ? AND period = ?
                """,
                (identity.user_id, 0, symbol, period),
            )
            result["server_last_bar_time"] = (
                int(cursor_row["last_bar_time"])
                if cursor_row and cursor_row["last_bar_time"] else 0
            )
            return result

        except Exception as e:
            print(f"[MarketAPI] 接收K线数据异常: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )

    @router.post("/ea/kline_batch")
    async def receive_kline_batch(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """EA批量推送多个周期的K线数据"""
        try:
            engine = engine_manager.get_engine_for_ea(identity)
            kline_service = engine.kline_service
            pivot_service = engine.pivot_service
            data = await request.json()
            symbol = data.get('symbol', 'GOLD')
            is_full = data.get('is_full', False)
            kline_data = data.get('data', {})

            results = {}
            system_log = engine.system_log

            for period, klines in kline_data.items():
                period = period.upper()
                if period not in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    continue

                result = kline_service.process_kline_data(symbol, period, klines, is_full)
                _persist_historical_klines(
                    identity, symbol, period, klines,
                    data.get("broker_utc_offset_seconds", 0),
                )
                results[period] = result

                if is_full:
                    event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                    system_log.add_log(
                        event_type,
                        {"period": period, "count": len(klines), "is_full": is_full},
                        symbol=symbol,
                        message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                    )

                if result['status'] == 'ok':
                    all_klines = kline_service.get_all_kline_objects(symbol, period)
                    if all_klines:
                        pivot_service.update_pivots(symbol, period, all_klines)
                    result["structure_plan_count"] = engine.refresh_structure_plans(
                        symbol, period
                    )

            return {
                "status": "ok",
                "symbol": symbol,
                "results": results
            }

        except Exception as e:
            print(f"[MarketAPI] 批量接收K线数据异常: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )

    # ==================== 查询接口 ====================

    @protected_router.get("/market/kline/{symbol}")
    async def get_kline(
        symbol: str,
        period: str = Query("M5", description="周期: H4/H1/M15/M5/M1"),
        count: int = Query(100, description="返回条数"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取K线数据"""
        engine = engine_manager.get_market_engine(user.user_id)
        period = period.upper()
        klines = engine.kline_service.get_klines(symbol, period, count)
        # MT5 broker suffixes (for example BTCUSD/BTCUSDm) can differ from
        # the strategy symbol while representing the same reported stream.
        # Fall back to a normalized in-memory symbol so execution charts do
        # not disappear merely because of the broker suffix.
        if not klines:
            store = getattr(engine.kline_service, "store", None)
            stored = getattr(store, "_klines", {})
            requested = str(symbol).rstrip("#").lower()
            requested_base = requested[:-1] if requested.endswith("m") else requested
            for actual_symbol in stored.keys():
                normalized = str(actual_symbol).rstrip("#").lower()
                normalized_base = normalized[:-1] if normalized.endswith("m") else normalized
                if normalized == requested or normalized_base == requested_base:
                    klines = engine.kline_service.get_klines(actual_symbol, period, count)
                    if klines:
                        symbol = actual_symbol
                        break

        # 结构分析不绑定某个交易账户；当主账户尚未接收该品种行情时，
        # 从用户其他在线 MT5 账户读取同名/后缀匹配的行情，避免页面误显示为空。
        if not klines:
            # 服务重启或 EA 暂停上报后，内存可能为空，但最近 7 天的行情已
            # 持久化到 MySQL。优先恢复同一用户、同一品种/周期的历史收盘K线。
            historical = get_storage().fetchall(
                """
                SELECT timestamp, timestamp_utc, broker_utc_offset_seconds,
                       open_price AS open, high_price AS high,
                       low_price AS low, close_price AS close, volume
                FROM historical_klines
                WHERE user_id = ? AND account_id = 0 AND symbol = ? AND period = ?
                  AND (timestamp_utc >= ? OR (timestamp_utc = 0 AND timestamp >= ?))
                ORDER BY COALESCE(NULLIF(timestamp_utc, 0), timestamp) DESC
                LIMIT ?
                """,
                (user.user_id, symbol, period, int(time.time()) - 7 * 86400,
                 int(time.time()) - 7 * 86400, min(1000, max(1, count))),
            )
            if historical:
                klines = [dict(row) for row in reversed(historical)]

        if not klines:
            for account in account_repo.list_for_user(user.user_id):
                if account.account_type != "mt5":
                    continue
                try:
                    candidate = engine_manager.get_engine(
                        user.user_id, account.account_id
                    ).kline_service.get_klines(symbol, period, count)
                    if candidate:
                        klines = candidate
                        break
                except Exception:
                    continue

        print(f"[MarketAPI] K线查询 symbol={symbol} period={period} count={len(klines)}")
        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "data": klines
        }

    @protected_router.get("/market/structure/{symbol}")
    async def get_market_structure(symbol: str, period: str = Query("M5"), count: int = Query(600), user: AuthUser = Depends(require_auth)):
        engine = engine_manager.get_market_engine(user.user_id)
        rows = engine.kline_service.get_klines(symbol, period.upper(), min(1000, max(50, count)))
        if not rows:
            # 结构分析必须严格匹配品种名称；GOLD、GOLD_、GOLDm 不互相替代。
            store = getattr(engine.kline_service, "store", None)
            stored = getattr(store, "_klines", {})
            for actual_symbol in stored.keys():
                if str(actual_symbol) == str(symbol):
                    rows = engine.kline_service.get_klines(actual_symbol, period.upper(), min(1000, max(50, count)))
                    if rows:
                        break
        if not rows:
            historical = get_storage().fetchall(
                """
                SELECT timestamp, timestamp_utc, broker_utc_offset_seconds,
                       open_price AS open, high_price AS high,
                       low_price AS low, close_price AS close, volume
                FROM historical_klines
                WHERE user_id = ? AND account_id = 0 AND symbol = ? AND period = ?
                  AND (timestamp_utc >= ? OR (timestamp_utc = 0 AND timestamp >= ?))
                ORDER BY COALESCE(NULLIF(timestamp_utc, 0), timestamp) DESC
                LIMIT ?
                """,
                (user.user_id, symbol, period.upper(), int(time.time()) - 7 * 86400,
                 int(time.time()) - 7 * 86400, min(1000, max(50, count))),
            )
            if historical:
                rows = [dict(row) for row in reversed(historical)]

        if not rows:
            for account in account_repo.list_for_user(user.user_id):
                # 行情是否可用取决于该账户最近是否上报，而不是账户管理页的
                # active 标记。模拟账户可能被标记为 inactive，但仍有 EA 行情流。
                if account.account_type != "mt5":
                    continue
                try:
                    candidate_engine = engine_manager.get_engine(user.user_id, account.account_id)
                    rows = candidate_engine.kline_service.get_klines(symbol, period.upper(), min(1000, max(50, count)))
                    if rows:
                        engine = candidate_engine
                        break
                except Exception:
                    continue
        print(f"[MarketAPI] 结构查询 symbol={symbol} period={period.upper()} count={len(rows)}")
        cfg_entity = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        cfg = dict(MARKET_STRUCTURE_DEFAULT_CONFIG)
        stored = cfg_entity[-1] if cfg_entity else {}
        cfg.update({k: v for k, v in stored.items() if k in MARKET_STRUCTURE_DEFAULT_CONFIG})
        for profile in stored.get("profiles", []) if isinstance(stored, dict) else []:
            if str(profile.get("symbol", "")).upper() == str(symbol).upper() and str(profile.get("period", "")).upper() == period.upper():
                cfg.update({k: v for k, v in profile.items() if k in MARKET_STRUCTURE_DEFAULT_CONFIG})
                break
        account_id = int(getattr(engine, "account_id", 0) or 0)
        previous = load_market_structure_snapshot(user.user_id, account_id, symbol, period.upper())
        # Older compact snapshots did not contain chart overlays. Restoring
        # those snapshots makes the next refresh render candles without Pivot,
        # BOS/CHoCH, sweep, or trendline markers; force a full recalculation.
        if previous and all(key in previous for key in ("swings", "events", "trendlines", "local_patterns")):
            restore_market_structure_snapshot(previous)
        result = analyze_market_structure_v2(symbol, period.upper(), rows, cfg)
        try:
            save_market_structure_checkpoint(result, user.user_id, account_id)
        except Exception as snapshot_error:
            print(f"[MarketAPI] 本地结构快照保存失败（不影响接口返回）: {snapshot_error}")
        RuntimeStateRepository(user.user_id, account_id).upsert_entity(
            "market_structure", f"{symbol}::{period.upper()}", {
                "symbol": symbol, "period": period.upper(),
                "engine_version": result.get("engine_version"),
                "snapshot_path": str(market_structure_snapshot_path(user.user_id, account_id, symbol, period.upper())),
                "last_bar_time": result.get("last_bar_time"),
                "config_signature": result.get("config_signature"),
                "updated_at": result.get("analyzed_at"),
            }, symbol=symbol, status=result.get("current_state", "undetermined"),
        )
        return {"status":"ok", "data": result}

    @protected_router.get("/market/structure/{symbol}/trade-plans")
    async def get_structure_trade_plans(
        symbol: str,
        period: str = Query("M5"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """行情层结构计划；不绑定页面当前查看账户。"""
        strategy_rows = strategy_repo.get_all_strategies(user.user_id)
        repo = StructureTradePlanRepository(get_storage())
        items = []
        source_ids = {
            str(source.get("signal_source_id") or "")
            for strategy in strategy_rows
            for source in strategy.get_signal_sources(
                "structure_plan", enabled_only=True
            )
            if str(source.get("period") or "").upper() == period.upper()
        }
        if not source_ids:
            source_ids.add("market-structure")
        for source_id in source_ids:
            items.extend(repo.list_current(
                user.user_id, 0, "", source_id, symbol, period.upper(),
            ))
        items = list({str(item.get("plan_id")): item for item in items}.values())
        # 结构分析页是行情层视图，即使当前没有绑定策略，也要展示
        # 基础结构计划；策略执行中心只在自己的部署作用域内筛选这些计划。
        if not items:
            # 行情层统一取该用户主实盘引擎；非实盘账户不再维护副本。
            engine = engine_manager.get_market_engine(user.user_id)
            rows = engine.kline_store.get_all_klines(symbol, period.upper())
            if rows:
                structure = analyze_market_structure_v2(
                    symbol, period.upper(), rows[-600:], MARKET_STRUCTURE_DEFAULT_CONFIG
                )
                items = StructurePlanBuilder(
                    resolve_structure_plan_config(symbol, period.upper())
                ).build(
                    "market-structure", symbol, period.upper(), rows[-600:], structure,
                )
                bar_time = int(
                    float(rows[-1].get("timestamp") or rows[-1].get("time") or 0)
                )
                if bar_time > 10_000_000_000:
                    bar_time //= 1000
                repo.replace_scope(
                    user.user_id, 0, "", "market-structure",
                    symbol, period.upper(), items, bar_time,
                )
                items = repo.list_current(
                    user.user_id, 0, "", "market-structure",
                    symbol, period.upper(),
                )
        return {"status": "ok", "symbol": symbol, "period": period.upper(), "plans": items}

    @protected_router.get("/admin/market-structure/config")
    async def get_market_structure_config(user: AuthUser = Depends(require_admin)):
        items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored = items[-1] if items else {}
        defaults = {**MARKET_STRUCTURE_DEFAULT_CONFIG, **STRUCTURE_PLAN_DEFAULT_CONFIG}
        return {"status": "ok", "config": {**defaults, **{k:v for k,v in stored.items() if k in defaults}}, "profiles": stored.get("profiles", [])}

    @protected_router.put("/admin/market-structure/config")
    async def put_market_structure_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        allowed = {**MARKET_STRUCTURE_DEFAULT_CONFIG, **STRUCTURE_PLAN_DEFAULT_CONFIG}
        cfg = dict(allowed)
        integer_keys = {
            "pivot_legs", "medium_pivot_legs", "large_pivot_legs",
            "break_confirm_bars", "retest_bars", "range_min_touches",
            "range_min_bars", "min_segment_bars", "trendline_min_touches",
            "trendline_min_bars",
        }
        for key in cfg:
            if key in payload:
                try:
                    value = float(payload[key])
                    cfg[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                except (TypeError, ValueError): pass
        profiles = payload.get("profiles", [])
        normalized_profiles = []
        for profile in profiles if isinstance(profiles, list) else []:
            if not profile.get("symbol") or not profile.get("period"): continue
            item = {"symbol": str(profile["symbol"]).strip(), "period": str(profile["period"]).upper()}
            for key in allowed:
                if key in profile:
                    try:
                        value = float(profile[key])
                        item[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                    except (TypeError, ValueError): pass
            normalized_profiles.append(item)
        cfg["profiles"] = normalized_profiles
        RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
        return {"status": "ok", "config": {k:v for k,v in cfg.items() if k in allowed}, "profiles": normalized_profiles}

    @protected_router.get("/market/pivots/{symbol}")
    async def get_pivots(
        symbol: str,
        period: str = Query(None, description="周期，不指定则返回全部"),
        direction: str = Query(None, description="方向: high/low"),
        count: int = Query(10, ge=1, le=10, description="返回条数（最多10条）"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取转折点数据"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        pivot_service = engine.pivot_service
        if period:
            period = period.upper()
            pivots = pivot_service.get_pivots(symbol, period, direction, count)
            return {
                "status": "ok",
                "symbol": symbol,
                "period": period,
                "count": len(pivots),
                "data": pivots
            }
        else:
            result = {}
            for p in ['H4', 'H1', 'M15', 'M5', 'M1']:
                pivots = pivot_service.get_pivots(symbol, p, direction, count)
                if pivots:
                    result[p] = pivots

            return {
                "status": "ok",
                "symbol": symbol,
                "data": result
            }

    @protected_router.get("/market/symbols")
    async def get_symbols(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return symbols that can be selected when creating a strategy."""
        account_repo = TradingAccountRepository()
        symbols = collect_ai_signal_symbols(
            user.user_id,
            engine_manager,
            trade_config_repo,
            strategy_repo,
            ai_signal_source_repo,
            account_repo,
        )
        return {
            "status": "ok",
            "symbols": symbols,
            "count": len(symbols),
            # Kept for older clients. The full list intentionally includes
            # durable configuration as well as currently observed symbols.
            "market_symbols": symbols,
        }

    @protected_router.get("/market/configured_symbols")
    async def get_configured_symbols(user: AuthUser = Depends(require_auth)) -> Dict:
        """获取配置的品种列表及其数据状态"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        kline_store = engine.kline_store
        kline_service = engine.kline_service
        config_data = trade_config_repo.get_config(user.user_id)
        configured_symbols = list(config_data.get("symbol_config", {}).keys())

        symbols_status = []
        for symbol in configured_symbols:
            m1_status = kline_service.check_m1_updated_within(symbol, 180)
            latest_m1_time = kline_store.get_latest_kline_time(symbol, 'M1')

            period_counts = {}
            with kline_store._lock:
                for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    period_counts[period] = len(kline_store._klines[symbol][period])

            symbols_status.append({
                "symbol": symbol,
                "has_data": m1_status["has_data"],
                "m1_count": period_counts.get('M1', 0),
                "latest_m1_time": latest_m1_time.isoformat() if latest_m1_time else None,
                "m1_update_time": m1_status.get("update_time").isoformat() if m1_status.get("update_time") else None,
                "seconds_ago": m1_status.get("seconds_ago"),
                "market_status": m1_status.get("market_status", "closed"),
                "period_counts": period_counts,
                "config": config_data.get("symbol_config", {}).get(symbol, {})
            })

        return {
            "status": "ok",
            "symbols": symbols_status,
            "count": len(symbols_status)
        }

    @protected_router.get("/market/status")
    async def get_market_status(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取行情存储状态"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        kline_service = engine.kline_service
        pivot_service = engine.pivot_service
        store_status = kline_service.get_status()
        pivot_status = pivot_service.get_status()

        # 使用 trading_server 获取状态
        server_status = engine.get_status()

        return {
            "status": "ok",
            "store": store_status,
            "pivots": pivot_status,
            "server": server_status
        }

    @protected_router.get("/market/thresholds")
    async def get_thresholds() -> Dict:
        """获取各周期的接近阈值"""
        thresholds = PivotService.THRESHOLDS

        return {
            "status": "ok",
            "thresholds": {
                period: {
                    "value": threshold,
                    "percent": f"{threshold * 100:.4f}%",
                    "description": f"千分之{threshold * 1000}"
                }
                for period, threshold in thresholds.items()
            }
        }

    # ==================== 趋势分析接口 ====================

    @protected_router.get("/trend/{symbol}")
    async def get_trend(
        symbol: str,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取单个品种的趋势分析"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        tech_service = engine.tech_service
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            tech_service.analyze_trend(symbol, period)

        resonance = tech_service.analyze_resonance(symbol)
        changes = tech_service.get_trend_changes(symbol, 10)

        return {
            "status": "ok",
            "symbol": symbol,
            "resonance": resonance,
            "trend_changes": changes
        }

    @protected_router.post("/trend/generate_order/{symbol}")
    async def generate_trade_order(
        symbol: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """基于趋势分析生成交易建议"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        tech_service = engine.tech_service
        kline_service = engine.kline_service
        pending_order_service = engine.pending_order_service
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            tech_service.analyze_trend(symbol, period)

        current_price = kline_service.get_latest_price(symbol)
        if not current_price:
            return {"status": "error", "message": "无法获取当前价格"}

        suggestion = tech_service.generate_trade_suggestion(symbol, current_price)

        if not suggestion:
            return {
                "status": "ok",
                "message": "当前无交易建议",
                "resonance": tech_service.analyze_resonance(symbol)
            }

        order_id = pending_order_service.create_order_from_dict(suggestion)

        return {
            "status": "ok",
            "message": "交易建议已生成",
            "order_id": order_id,
            "suggestion": suggestion
        }

    # ==================== 待确认订单接口 ====================

    @protected_router.get("/pending_orders")
    async def get_pending_orders(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取待确认订单列表"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        pending_order_service = engine.pending_order_service
        orders = pending_order_service.get_orders_dict(symbol)
        return {
            "status": "ok",
            "count": len(orders),
            "orders": orders
        }

    @protected_router.post("/pending_orders/{order_id}/confirm")
    async def confirm_pending_order(
        order_id: str,
        account_id: Optional[int] = Query(None),
        request: Request = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """确认待确认订单"""
        account, engine = resolve_web_engine(engine_manager, user, account_id)
        if account is not None and (
            account.status != "active" or not account.trading_enabled
        ):
            raise HTTPException(status_code=409, detail="当前账户交易已暂停")
        try:
            memberships.assert_live_trading(user.user_id, account.account_id)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        pending_order_service = engine.pending_order_service
        update_data = {}
        if request:
            try:
                update_data = await request.json()
            except:
                pass

        # 获取订单并确认
        order = pending_order_service.confirm_order(order_id, update_data)
        if not order:
            return {"status": "error", "message": "订单不存在"}
        engine.update_decision_status(order_id, "confirmed")

        system_log = engine.system_log
        action_text = '买入' if order.action == 'b' else '卖出'
        symbol = order.symbol
        mount = order.mount
        price = order.price
        sl = order.sl
        tp = order.tp

        system_log.add_log(
            "order_confirmed",
            {
                "order_id": order_id,
                "action": order.action,
                "price": price,
                "mount": mount,
                "sl": sl,
                "tp": tp
            },
            symbol=symbol,
            message=f"{action_text} @ {price}, 手数={mount}, SL={sl}, TP={tp}"
        )

        print(f"[订单确认] {symbol} | {action_text} | 价格={price} | 手数={mount} | SL={sl} | TP={tp}")

        return {
            "status": "ok",
            "message": "订单已确认",
            "order": order.to_dict()
        }

    @protected_router.post("/pending_orders/{order_id}/reject")
    async def reject_pending_order(
        order_id: str,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """拒绝待确认订单"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        pending_order_service = engine.pending_order_service
        order = pending_order_service.reject_order(order_id)
        if not order:
            return {"status": "error", "message": "订单不存在"}
        engine.update_decision_status(order_id, "rejected")

        system_log = engine.system_log
        system_log.add_log(
            "order_rejected",
            {"order_id": order_id, "action": order.action, "price": order.price},
            symbol=order.symbol,
            message=f"订单已拒绝"
        )

        return {
            "status": "ok",
            "message": "订单已拒绝"
        }

    # ==================== 交易配置接口 ====================

    @protected_router.get("/trade_config")
    async def get_trade_config(user: AuthUser = Depends(require_auth)) -> Dict:
        """获取交易配置"""
        return {
            "status": "ok",
            "config": trade_config_repo.get_config(user.user_id)
        }

    @protected_router.post("/trade_config")
    async def update_trade_config(request: Request, user: AuthUser = Depends(require_auth)) -> Dict:
        """更新交易配置"""
        try:
            data = await request.json()
            current_config = trade_config_repo.get_config(user.user_id)
            current_config.update(data)
            saved_config = trade_config_repo.save_config(user.user_id, current_config)

            engine = engine_manager.get_engine_for_user(user.user_id)
            engine.trade_config.update(saved_config)

            return {
                "status": "ok",
                "message": "配置已更新",
                "config": saved_config
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ==================== 策略决策接口 ====================

    @protected_router.get("/position-management-policies")
    async def list_position_management_policies(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        policies = position_policy_repo.list(user.user_id)
        return {
            "status": "ok", "count": len(policies),
            "policies": [policy.to_dict() for policy in policies],
        }

    @protected_router.get("/position-management-policies/shared")
    async def list_shared_position_management_policies(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        policies = position_policy_repo.list_shared(user.user_id)
        return {"status": "ok", "count": len(policies), "policies": policies}

    @protected_router.post("/position-management-policies/shared/{owner_user_id}/{policy_id}/use")
    async def use_shared_position_management_policy(
        owner_user_id: int, policy_id: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        policy = position_policy_repo.use_shared_policy(
            user.user_id, owner_user_id, policy_id
        )
        if policy is None:
            raise HTTPException(status_code=404, detail="共享持仓管理方案不存在或未开放")
        return {
            "status": "ok",
            "message": "已添加共享持仓管理方案引用；源方案被应用后会冻结，后续演进请复制新版本",
            "policy": policy.to_dict(),
        }

    @protected_router.post("/position-management-policies/{policy_id}/copy")
    async def copy_position_management_policy(
        policy_id: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        policy = position_policy_repo.copy_policy(user.user_id, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="持仓管理方案不存在")
        return {
            "status": "ok",
            "message": "已复制为新的私有持仓管理方案",
            "policy": policy.to_dict(),
        }

    @protected_router.post("/position-management-policies")
    async def create_position_management_policy(
        request: Request, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        from market.models import PositionManagementPolicy

        try:
            data = await request.json()
            policy = PositionManagementPolicy(
                user_id=user.user_id, name=data.get("name", ""),
                enabled=bool(data.get("enabled", True)),
                visibility=str(data.get("visibility", "private")),
                config=data.get("config") or None,
            )
            position_policy_repo.save(policy)
            return {"status": "ok", "policy": policy.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @protected_router.put("/position-management-policies/{policy_id}")
    async def update_position_management_policy(
        policy_id: str, request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        from market.models import normalize_position_management_config

        policy = position_policy_repo.get(user.user_id, policy_id)
        if policy is None:
            raise HTTPException(status_code=404, detail="持仓管理方案不存在")
        if policy.source_owner_user_id:
            raise HTTPException(
                status_code=400,
                detail="共享持仓管理方案为只读引用，不能修改；源方案变更会自动同步",
            )
        try:
            data = await request.json()
            confirmed = bool(data.pop("_confirm_hot_reload", False))
            impact = impact_service.analyze(user.user_id, "position_management", policy.policy_id)
            if not impact["allowed"]:
                raise HTTPException(status_code=409, detail={"message": impact["blocked_reason"], "impact": impact})
            if impact["requires_confirmation"] and not confirmed:
                raise HTTPException(status_code=409, detail={"message": "该修改会热更新已部署策略，请确认影响范围", "impact": impact})
            was_shared = policy.visibility == "shared"
            policy.name = str(data.get("name", policy.name)).strip()
            if not policy.name:
                raise ValueError("持仓管理方案名称不能为空")
            policy.enabled = bool(data.get("enabled", policy.enabled))
            if "visibility" in data or "is_shared" in data:
                policy.visibility = (
                    "shared"
                    if data.get("visibility") == "shared" or data.get("is_shared")
                    else "private"
                )
            if "config" in data:
                policy.config = normalize_position_management_config(data["config"])
            policy.version += 1
            position_policy_repo.save(policy)
            position_policy_repo.invalidate_linked_strategies(
                user.user_id, policy.policy_id
            )
            engine_manager.refresh_user_strategies(user.user_id)
            return {"status": "ok", "policy": policy.to_dict()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @protected_router.delete("/position-management-policies/{policy_id}")
    async def delete_position_management_policy(
        policy_id: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            policy = position_policy_repo.get(user.user_id, policy_id)
            if (
                policy and policy.visibility == "shared"
                and position_policy_repo.policy_application_count(user.user_id, policy_id)
            ):
                raise ValueError("该共享持仓管理方案已被应用，不能删除；请保留原方案并复制新版本")
            if policy and position_policy_repo.active_deployment_count(
                user.user_id, policy.policy_id
            ):
                raise ValueError(
                    "该持仓管理方案正被已部署的策略使用（模拟盘或实盘），"
                    "不能删除；请先解除相关策略部署后再删除"
                )
            if not position_policy_repo.delete(user.user_id, policy_id):
                raise HTTPException(status_code=404, detail="持仓管理方案不存在")
            return {"status": "ok", "message": "持仓管理方案已删除"}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @protected_router.get("/strategy")
    async def get_all_strategies(user: AuthUser = Depends(require_auth)) -> Dict:
        """获取所有策略配置"""
        strategies = strategy_repo.get_all_strategies(user.user_id)
        return {
            "status": "ok",
            "count": len(strategies),
            "strategies": [strategy_payload(s, user.user_id) for s in strategies],
            "quota": quota_service.get_summary(user.user_id, user.role),
        }

    @protected_router.post("/strategy")
    async def create_strategy(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """创建策略；同一品种可创建多条。"""
        try:
            data = await request.json()
            symbol = str(data.get("symbol", "")).strip()
            if not symbol:
                return {"status": "error", "message": "请选择交易品种"}
            bind_alpha_signal_snapshots(user, data)
            validate_ai_signal_configuration(user, data)
            policy_id = str(data.get("position_management_policy_id", "")).strip()
            if not policy_id or not position_policy_repo.get(user.user_id, policy_id):
                return {"status": "error", "message": "请选择有效的持仓管理方案"}
            engine = engine_manager.get_engine_for_user(user.user_id)
            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "strategies")
                quota_service.assert_strategy_sources(
                    user.user_id, user.role, "", data.get("signal_sources") or [],
                )
                strategy = engine.strategy_service.strategy_store.create_strategy(
                    symbol, data
                )
            shared_ai_runtime_repo.sync_strategy_visibility(
                user.user_id, strategy.to_dict()
            )
            engine_manager.refresh_user_strategies(user.user_id)
            add_audit_event(
                user, "strategy_created", "创建策略",
                f"创建策略 {strategy.strategy_name}",
                {"symbol": strategy.symbol}, "strategy", strategy.strategy_id,
            )
            return {
                "status": "ok",
                "message": "策略已创建",
                "strategy": strategy_payload(strategy, user.user_id),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # 一键创建AI策略已下线；保留旧函数体仅用于平滑升级，未注册任何HTTP路由。
    async def quick_create_ai_strategy(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Create a draft AI strategy with a reusable source and 1R partial exit."""
        from market.models import PositionManagementPolicy

        try:
            data = await request.json()
            symbol = str(data.get("symbol") or "").strip()
            period = str(data.get("period") or "M5").strip().upper()
            account_id = data.get("account_id")
            if not symbol:
                raise ValueError("请选择交易品种")
            if period not in {"M1", "M5", "M15", "H1", "H4"}:
                raise ValueError("不支持的 AI 分析周期")

            account = None
            if account_id not in (None, ""):
                account = TradingAccountRepository().get_by_id(
                    user.user_id, int(account_id)
                )
                if account is None:
                    raise ValueError("交易账户不存在或不属于当前用户")
            if account is None:
                account = TradingAccountRepository().get_primary_mt5(user.user_id)
            broker_server = str(getattr(account, "mt5_server", "") or "")

            requested_source_id = str(data.get("ai_signal_source_id") or "").strip()
            requested_source_owner_id = data.get("ai_signal_source_owner_id")
            managed_source = None
            created_source = False
            if requested_source_id:
                owner_user_id = int(requested_source_owner_id or user.user_id)
                managed_source = ai_signal_source_repo.get_visible(
                    user.user_id, requested_source_id, owner_user_id
                )
                if managed_source is None:
                    raise ValueError("所选 AI 信号源不存在、已停用或无权使用")
                if str(managed_source.get("period") or "").upper() != period:
                    raise ValueError("所选 AI 信号源的分析周期与当前策略不一致")
                source_symbol = str(managed_source.get("symbol") or "").strip()
                if owner_user_id == user.user_id and source_symbol != symbol:
                    raise ValueError("自己的 AI 信号源必须与策略品种一致")
                if owner_user_id != user.user_id and source_symbol != symbol:
                    if not instrument_mapping_repo.user_can_use_symbol(
                        owner_user_id, source_symbol, user.user_id, symbol
                    ):
                        raise ValueError("所选共享 AI 信号源不适配当前品种")
            else:
                # Reuse a published source first. The repository ranks
                # same-broker matches ahead of mapped symbols.
                managed_source = ai_signal_source_repo.find_shared_for_symbol_period(
                    user.user_id, symbol, period, broker_server
                )
            if managed_source is None:
                access = llm_access_repo.get_status(user.user_id, user.role)
                if not access["access_granted"]:
                    raise ValueError("没有可复用的共享 AI 信号源，当前用户尚未开通大模型功能")
                scene = llm_governance.scene_options(
                    user.user_id, AI_SIGNAL_ANALYSIS
                )
                models = scene.get("models") or []
                if not models:
                    raise ValueError("管理员尚未为 AI 行情与交易信号配置可用模型")
                requested_model = str(data.get("model") or "").strip()
                if requested_model and requested_model not in models:
                    raise ValueError("所选 AI 模型未被管理员为 AI 行情与交易信号场景开放")
                source_config = {
                    "analysis_mode": "self_analysis",
                    "analysis_interval_minutes": max(
                        {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}[period],
                        int(data.get("analysis_interval_minutes") or 5),
                    ),
                    "kline_count": max(
                        AI_SIGNAL_KLINE_MIN_COUNT,
                        min(AI_SIGNAL_KLINE_MAX_COUNT, int(data.get("kline_count") or 288)),
                    ),
                    "entry_threshold": float(data.get("entry_threshold") or 0.0008),
                    "model": requested_model or str(models[0]),
                    "reference_runtime_ids": [],
                    "reference_market_data": [],
                }
                generator_scene = llm_governance.scene_options(
                    user.user_id, AI_SIGNAL_PROMPT_GENERATION
                )
                generator_prompt = str(
                    generator_scene.get("user_prompt_template") or ""
                ).replace(
                    "{{signal_source_config}}",
                    json.dumps({
                        "name": f"{symbol} AI {period}", "symbol": symbol,
                        "period": period,
                        "analysis_interval_minutes": source_config["analysis_interval_minutes"],
                        "kline_count": source_config["kline_count"],
                        "reference_market_data": [],
                    }, ensure_ascii=False),
                ).replace(
                    "{{user_intent}}",
                    str(data.get("prompt_intent") or "识别主周期趋势、关键位置和满足条件的入场机会；数据不足时不要给出交易建议。"),
                )
                _, prompt_engine = resolve_web_engine(engine_manager, user, None)
                prompt_result = await asyncio.to_thread(
                    prompt_engine.llm_service.call_llm,
                    generator_prompt, None, None, AI_SIGNAL_PROMPT_GENERATION,
                    "ai_signal_prompt_candidate", "quick-create", 8000,
                )
                if not isinstance(prompt_result, dict):
                    raise ValueError(
                        "自动生成 AI 信号源提示词失败，请先在 AI 信号源页面生成后再创建策略"
                    )
                source_config.update({
                    "prompt_mode": "custom",
                    "system_prompt": str(prompt_result.get("system_prompt") or "").strip(),
                    "analysis_prompt_template": str(
                        prompt_result.get("analysis_prompt_template") or ""
                    ).strip(),
                })
                normalize_ai_signal_prompt_config(source_config)
                managed_source = ai_signal_source_repo.create(user.user_id, {
                    "name": f"{symbol} AI {period}",
                    "symbol": symbol,
                    "period": period,
                    "enabled": True,
                    "share_runtime_data": True,
                    "config": source_config,
                })
                created_source = True

            owner_user_id = int(managed_source["user_id"])
            managed_source_id = str(managed_source["signal_source_id"])
            source_instance_id = uuid.uuid4().hex[:12]
            source_config = managed_source.get("config") or {}
            signal_params = {
                "analysis_mode": (
                    "shared_reference" if owner_user_id != user.user_id
                    else "self_analysis"
                ),
                "ai_signal_source_id": managed_source_id,
                "ai_signal_source_owner_id": owner_user_id,
                "shared_runtime_id": (
                    f"{owner_user_id}:ai:{managed_source_id}"
                    if owner_user_id != user.user_id else ""
                ),
                "min_confidence": int(data.get("min_confidence") or 70),
                "entry_threshold": float(data.get("entry_threshold") or source_config.get("entry_threshold") or 0.0008),
                "reference_runtime_ids": [],
            }

            def is_suitable_ai_policy(candidate) -> bool:
                """Only reuse policies that implement the standard AI exit flow."""
                config = (
                    candidate.get("config") if isinstance(candidate, dict)
                    else getattr(candidate, "config", None)
                ) or {}

                def has_rule(rule_group, rule_type):
                    return any(
                        isinstance(rule, dict) and rule.get("type") == rule_type
                        for rule in (config.get(rule_group) or [])
                    )

                management_types = {
                    rule.get("type")
                    for rule in (config.get("management_rules") or [])
                    if isinstance(rule, dict)
                }
                return (
                    has_rule("initial_stop_rules", "signal")
                    and has_rule("initial_take_profit_rules", "signal")
                    and "partial_take_profit" in management_types
                    and "trailing_stop" in management_types
                )

            requested_policy_id = str(
                data.get("position_management_policy_id") or ""
            ).strip()
            requested_policy_owner_id = data.get("position_management_policy_owner_id")
            created_policy = False
            reused_shared_policy = False
            if requested_policy_id:
                policy_owner_id = int(requested_policy_owner_id or user.user_id)
                if policy_owner_id == user.user_id:
                    policy = position_policy_repo.get(user.user_id, requested_policy_id)
                else:
                    # Keep the existing dynamic-reference model: the recipient
                    # uses the publisher's live scheme without copying its rules.
                    policy = position_policy_repo.use_shared_policy(
                        user.user_id, policy_owner_id, requested_policy_id
                    )
                    reused_shared_policy = policy is not None
                if policy is None or not policy.enabled or not is_suitable_ai_policy(policy):
                    raise ValueError("所选持仓管理方案不存在、已停用或不适用于 AI 策略")
            else:
                policy = next(
                    (
                        item for item in position_policy_repo.list(
                            user.user_id, enabled_only=True
                        )
                        if is_suitable_ai_policy(item)
                    ),
                    None,
                )
                if policy is None:
                    shared_policy = next(
                        (
                            item for item in position_policy_repo.list_shared(user.user_id)
                            if is_suitable_ai_policy(item)
                        ),
                        None,
                    )
                    if shared_policy is not None:
                        policy = position_policy_repo.use_shared_policy(
                            user.user_id,
                            int(shared_policy["owner_user_id"]),
                            str(shared_policy["policy_id"]),
                        )
                        reused_shared_policy = policy is not None
                if policy is None:
                    policy = PositionManagementPolicy(
                        user_id=user.user_id,
                        name=f"{symbol} AI 信号止损止盈 · 1R分批",
                        visibility="shared",
                        config={
                            "initial_stop_rules": [{"type": "signal"}],
                            "initial_take_profit_rules": [{"type": "signal"}],
                            "management_rules": [{
                                "type": "partial_take_profit",
                                "levels": [{
                                    "level_id": "tp1_1r",
                                    "trigger_r": 1.0,
                                    "close_percent": 33.0,
                                    "move_sl": "break_even",
                                }],
                            }, {
                                "type": "trailing_stop",
                                "activation_r": 1.0,
                                "distance_r": 0.8,
                            }],
                            "signal_take_profit_close_percent": 50.0,
                            "min_risk_reward": 1.0,
                            "min_stop_distance": 0.0,
                            "max_stop_distance": 0.0,
                        },
                    )
                    created_policy = True

            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "strategies")
                if created_source:
                    quota_service.assert_can_create(
                        user.user_id, user.role, "signal_sources"
                    )
                quota_service.assert_strategy_sources(
                    user.user_id, user.role, "", [{
                        "source": "ai_entry", "period": period,
                        "params": signal_params,
                    }],
                )
                if created_policy:
                    position_policy_repo.save(policy)
                engine = engine_manager.get_engine_for_user(user.user_id)
                strategy = engine.strategy_service.strategy_store.create_strategy(
                    symbol,
                    {
                        "strategy_name": str(
                            data.get("strategy_name") or f"{symbol} AI 策略"
                        ).strip(),
                        "enabled": True,
                        "lifecycle_status": StrategyLifecycle.DRAFT,
                        "signal_sources": [{
                            "signal_source_id": source_instance_id,
                            "source": "ai_entry",
                            "enabled": True,
                            "period": period,
                            "weight": 100,
                            "params": signal_params,
                        }],
                        "position_management_policy_id": policy.policy_id,
                        "fixed_volume": float(data.get("fixed_volume") or 0.01),
                        "risk_percent": float(data.get("risk_percent") or 1.0),
                        "max_positions": int(data.get("max_positions") or 3),
                        "max_same_direction": int(data.get("max_same_direction") or 2),
                        "min_confidence": signal_params["min_confidence"],
                        "consistency_requirement": "any",
                        "visibility": "private",
                    },
                )

            shared_ai_runtime_repo.sync_strategy_visibility(
                user.user_id, strategy.to_dict()
            )
            engine_manager.refresh_user_strategies(user.user_id)
            add_audit_event(
                user, "strategy_quick_created", "一键创建 AI 策略",
                f"为 {symbol} 创建 AI 策略 {strategy.strategy_name}",
                {
                    "symbol": symbol, "period": period,
                    "signal_source_id": managed_source_id,
                    "source_reused": not created_source,
                    "position_policy_reused": not created_policy,
                    "position_policy_shared": reused_shared_policy,
                    "position_policy_id": policy.policy_id,
                }, "strategy", strategy.strategy_id,
            )
            return {
                "status": "ok",
                "message": "AI 策略已创建为草稿",
                "source_reused": not created_source,
                "signal_source": ai_signal_source_payload(managed_source),
                "position_policy_reused": not created_policy,
                "position_policy_shared": reused_shared_policy,
                "position_policy": policy.to_dict(),
                "strategy": strategy_payload(strategy, user.user_id),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @protected_router.get("/strategy/decisions")
    async def get_decisions(
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        count: int = Query(50, ge=1, le=1000),
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取当前账户的策略决策审计记录。"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        try:
            decisions = engine.get_decision_history(
                symbol=symbol,
                count=count,
                strategy_id=strategy_id,
                status=status,
                date_from=date_from,
                date_to=date_to,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="时间筛选格式无效") from exc
        return {
            "status": "ok",
            "count": len(decisions),
            "decisions": decisions
        }

    @protected_router.get("/strategy/{strategy_id}/execution-overview")
    async def get_strategy_execution_overview(
        strategy_id: str,
        include_chart: bool = Query(False),
        include_inactive: bool = Query(False),
        start_ts: Optional[int] = Query(None, ge=0),
        end_ts: Optional[int] = Query(None, ge=0),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return deployment-scoped decision history for a single strategy.

        This deliberately does not use the web page's selected market account:
        one strategy can run in several paper and live accounts concurrently.
        """
        strategy = strategy_repo.get_strategy_by_id(user.user_id, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在或不属于当前用户")

        deployments = strategy_deployment_repo.list_for_strategy(
            user.user_id, strategy_id
        )
        trade_config_enabled = bool(
            trade_config_repo.get_config(user.user_id).get("enabled", True)
        )
        account_views = []
        for deployment in deployments:
            account_id = int(deployment["account_id"])
            account = account_repo.get_by_id(user.user_id, account_id)
            runtime_active = _deployment_is_running(
                deployment, account, trade_config_enabled,
            )
            if not runtime_active and not include_inactive:
                continue
            engine = engine_manager.get_engine(user.user_id, account_id)
            # 执行中心首屏只展示最近 10 条决策；历史回放按时间范围另行加载，
            # 避免把账户全部运行态、上千根 K 线和成交事件一次性拼进响应。
            decisions = engine.get_decision_history(
                strategy_id=strategy_id, count=10,
            )
            if not include_chart:
                # 策略执行中心只展示部署和最近决策；K 线交易回放页显式请求
                # include_chart=true 后再加载 K 线及成交轨迹，避免首屏超时。
                account_views.append({
                    **deployment,
                    "runtime_active": runtime_active,
                    "symbol": str(deployment.get("symbol") or strategy.symbol or ""),
                    "chart": None,
                    "decisions": decisions,
                })
                continue
            symbol = str(deployment.get("symbol") or strategy.symbol or "")
            configured_symbol = symbol
            strategy_config = getattr(strategy, "config", None) or {}
            source_periods = [
                str(item.get("period") or "").upper()
                for item in (getattr(strategy, "signal_sources", None) or [])
                if isinstance(item, dict)
            ]
            period = str(strategy_config.get("primary_period") or next(
                (value for value in source_periods if value), "M5"
            )).upper()
            if period not in {"M1", "M5", "M15", "H1", "H4"}:
                period = "M5"
            def load_chart_bars(
                source_engine, requested_symbol, source_account_id=None,
            ):
                """在当前账户优先读取，必要时兼容 MT5 经纪商后缀。"""
                chart_account_id = 0
                historical = get_storage().fetchall(
                    """
                    SELECT timestamp, timestamp_utc, broker_utc_offset_seconds,
                           open_price AS open, high_price AS high,
                           low_price AS low, close_price AS close, volume
                    FROM historical_klines
                    WHERE user_id = ? AND account_id = ? AND symbol = ? AND period = ?
                      AND timestamp_utc > 0
                      AND timestamp_utc >= ?
                      AND (? IS NULL OR timestamp_utc >= ?)
                      AND (? IS NULL OR timestamp_utc <= ?)
                    ORDER BY timestamp_utc
                    """,
                    (user.user_id, chart_account_id, requested_symbol, period, int(time.time()) - 7 * 86400,
                     start_ts, start_ts, end_ts, end_ts),
                )
                if historical:
                    return [dict(item) for item in historical], requested_symbol
                direct = source_engine.kline_service.get_all_klines(
                    requested_symbol, period
                )
                if start_ts is not None or end_ts is not None:
                    direct = [
                        item for item in direct
                        if (start_ts is None or (_historical_kline_timestamp(item.get("timestamp") or item.get("time")) or 0) >= start_ts)
                        and (end_ts is None or (_historical_kline_timestamp(item.get("timestamp") or item.get("time")) or 0) <= end_ts)
                    ]
                if direct:
                    return direct, requested_symbol
                store = getattr(source_engine.kline_service, "store", None)
                stored = getattr(store, "_klines", {})
                requested = str(requested_symbol).rstrip("#").lower()
                requested_base = requested[:-1] if requested.endswith("m") else requested
                for actual_symbol in stored.keys():
                    normalized = str(actual_symbol).rstrip("#").lower()
                    normalized_base = normalized[:-1] if normalized.endswith("m") else normalized
                    if normalized == requested or normalized_base == requested_base:
                        candidate = source_engine.kline_service.get_all_klines(
                            actual_symbol, period
                        )
                        if candidate:
                            return candidate, actual_symbol
                return [], requested_symbol

            def parse_mt5_wall_time(value):
                """Parse a broker wall-clock K-line time without using server TZ."""
                if value is None or value == "":
                    return None
                if isinstance(value, (int, float)):
                    numeric = int(value)
                    return numeric if numeric >= 10**9 else None
                text = str(value).strip()
                for fmt in (
                    "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M",
                ):
                    try:
                        # timegm treats this as a wall-clock value, rather than
                        # silently applying the Linux server's timezone.
                        return timegm(datetime.strptime(text, fmt).timetuple())
                    except ValueError:
                        continue
                return None

            def normalize_chart_bar_times(chart_bars):
                """Align MT5 K-line timestamps to UTC for comparison with fills.

                EA K-lines are sent as broker-local wall-clock strings, whereas
                paper/live fill records are UTC epoch values. Infer the active
                broker offset from the latest bar (which is continually pushed),
                then return UTC epochs. This prevents valid fills being filtered
                out solely because the broker is not on Beijing time.
                """
                normalized = []
                needs_inference = []
                for item in chart_bars:
                    if not isinstance(item, dict):
                        continue
                    bar = dict(item)
                    explicit_utc = normalize_epoch(
                        bar.get("timestamp_utc")
                    )
                    if explicit_utc is not None:
                        bar["mt5_timestamp"] = bar.get("timestamp") or bar.get("time")
                        bar["timestamp"] = explicit_utc
                        normalized.append(bar)
                    else:
                        needs_inference.append(bar)
                if not needs_inference:
                    offsets = {
                        int(item.get("broker_utc_offset_seconds") or 0)
                        for item in normalized
                    }
                    offset = next(iter(offsets)) if len(offsets) == 1 else 0
                    normalized.sort(key=lambda item: int(item.get("timestamp") or 0))
                    return normalized, offset / 3600

                parsed = [
                    parse_mt5_wall_time(item.get("timestamp") or item.get("time"))
                    for item in needs_inference
                ]
                parsed = [value for value in parsed if value is not None]
                if not parsed:
                    return normalized or chart_bars, 0
                offset_hours = int(round((max(parsed) - time.time()) / 3600))
                offset_hours = max(-12, min(14, offset_hours))
                for item in needs_inference:
                    bar = dict(item)
                    raw_timestamp = bar.get("timestamp") or bar.get("time")
                    epoch = parse_mt5_wall_time(raw_timestamp)
                    if epoch is not None:
                        bar["mt5_timestamp"] = raw_timestamp
                        bar["timestamp"] = epoch - offset_hours * 3600
                    normalized.append(bar)
                normalized.sort(key=lambda item: int(item.get("timestamp") or 0))
                return normalized, offset_hours

            bars, chart_symbol = load_chart_bars(
                engine, symbol, getattr(engine, "account_id", account_id),
            )
            # 模拟盘不接收 EA 行情。若部署账户没有 K 线，使用同一用户
            # 当前 MT5 行情账户的 K 线作为图表背景；订单事件仍来自部署账户。
            if not bars:
                market_engine = engine_manager.get_engine_for_user(user.user_id)
                if market_engine is not engine:
                    bars, chart_symbol = load_chart_bars(
                        market_engine,
                        symbol,
                        getattr(market_engine, "account_id", None),
                    )
            bars, mt5_timezone_offset_hours = normalize_chart_bar_times(bars)
            # 订单/成交记录可能保存标准名，而 K 线来自带后缀的经纪商名；
            # 两者只要去掉 # 和末尾 m 后一致，就视为同一品种。
            def same_symbol(left, right):
                left_norm = str(left or "").rstrip("#").lower()
                right_norm = str(right or "").rstrip("#").lower()
                left_base = left_norm[:-1] if left_norm.endswith("m") else left_norm
                right_base = right_norm[:-1] if right_norm.endswith("m") else right_norm
                return left_norm == right_norm or left_base == right_base
            symbol = chart_symbol
            events = []
            if deployment.get("execution_mode") == "paper":
                # 执行中心只需要当前部署的成交轨迹。不要调用完整账户详情，
                # 后者还会读取权益曲线、运行日志、全部持仓和全部成交，
                # 在远程 MySQL 上会把首屏请求拖到 10 秒以上。
                paper_storage = engine_manager.paper_trading.storage
                deployment_id = str(deployment.get("deployment_id") or "")
                paper_orders = [dict(row) for row in paper_storage.fetchall(
                    """
                    SELECT o.*, p.position_id AS linked_position_id
                    FROM paper_orders o
                    LEFT JOIN paper_positions p ON p.order_id = o.order_id
                    WHERE o.user_id = ? AND o.account_id = ?
                      AND o.deployment_id = ? AND o.status = 'filled'
                    ORDER BY o.filled_at DESC, o.order_id DESC
                    LIMIT 100
                    """,
                    (user.user_id, account_id, deployment_id),
                )]
                paper_trades = [dict(row) for row in paper_storage.fetchall(
                    """
                    SELECT t.*, o.decision_id AS open_decision_id
                    FROM paper_trades t
                    LEFT JOIN paper_orders o ON o.order_id = t.order_id
                    WHERE t.user_id = ? AND t.account_id = ?
                      AND t.deployment_id = ?
                    ORDER BY t.closed_at DESC, t.trade_id DESC
                    LIMIT 100
                    """,
                    (user.user_id, account_id, deployment_id),
                )]
                decision_by_id = {
                    str(item.get("decision_id")): item
                    for item in decisions
                    if item.get("decision_id")
                }
                # 同一仓位的开仓订单、分批止盈与最终平仓通过 position_id
                # 串联，供 K 线回放页绘制完整持仓轨迹。
                position_by_order = {
                    str(order.get("order_id")): order.get("linked_position_id")
                    for order in paper_orders
                    if order.get("order_id") and order.get("linked_position_id")
                }
                for trade in paper_trades:
                    if trade.get("order_id") and trade.get("position_id"):
                        position_by_order[str(trade["order_id"])] = trade["position_id"]
                for order in paper_orders:
                    if not same_symbol(order.get("symbol"), configured_symbol):
                        continue
                    events.append({
                        "type": "buy" if str(order.get("direction")).lower() == "buy" else "sell",
                        "timestamp": order.get("filled_at") or order.get("requested_at"),
                        "price": order.get("filled_price") or order.get("requested_price"),
                        "reason": "订单成交",
                        "order_id": order.get("order_id"),
                        "position_id": position_by_order.get(str(order.get("order_id"))),
                        "volume": order.get("filled_volume") or order.get("requested_volume"),
                        "decision_id": order.get("decision_id"),
                        "decision": decision_by_id.get(str(order.get("decision_id"))) if order.get("decision_id") else None,
                    })
                for trade in paper_trades:
                    if not same_symbol(trade.get("symbol"), configured_symbol):
                        continue
                    reason = str(trade.get("exit_reason") or "平仓")
                    lowered = reason.lower()
                    event_type = "take_profit" if "profit" in lowered or "tp" in lowered else (
                        "stop_loss" if "stop" in lowered or "sl" in lowered else "close"
                    )
                    order_decision_id = next((
                        item.get("decision_id") for item in paper_orders
                        if str(item.get("order_id")) == str(trade.get("order_id"))
                    ), None)
                    events.append({
                        "type": event_type,
                        "timestamp": trade.get("closed_at"),
                        "price": trade.get("exit_price"),
                        "reason": reason,
                        "trade_id": trade.get("trade_id"),
                        "order_id": trade.get("order_id"),
                        "position_id": trade.get("position_id"),
                        "volume": trade.get("volume"),
                        "decision_id": order_decision_id,
                        "decision": decision_by_id.get(str(order_decision_id)) if order_decision_id else None,
                    })
            else:
                for report in TradeExecutionRepository().list_for_account(
                    user.user_id, account_id, 100,
                ):
                    if not same_symbol(report.get("symbol"), configured_symbol) or not report.get("success"):
                        continue
                    action = str(report.get("action") or "").lower()
                    if action in {"buy", "b"}:
                        marker = "buy"
                    elif action in {"sell", "s"}:
                        marker = "sell"
                    elif action == "partial_close":
                        marker = "take_profit"
                    else:
                        marker = "close"
                    events.append({
                        "type": marker,
                        "timestamp": report.get("reported_at"),
                        "price": report.get("executed_price") or report.get("requested_price"),
                        "reason": "实盘分批平仓" if action == "partial_close" else "实盘成交回报",
                        "order_id": report.get("order_id") or report.get("instruction_id"),
                        "position_id": str(report.get("mt5_position_id") or ""),
                        "volume": report.get("executed_volume") or report.get("requested_volume"),
                    })
                for event in PositionManagementEventRepository().list_for_account(
                    user.user_id, account_id, configured_symbol, 100,
                ):
                    event_type = str(event.get("event_type") or "").lower()
                    if "take" in event_type or "profit" in event_type:
                        marker = "take_profit"
                    elif "stop" in event_type:
                        marker = "stop_loss"
                    elif "close" in event_type:
                        marker = "close"
                    else:
                        continue
                    events.append({
                        "type": marker,
                        "timestamp": event.get("event_time"),
                        "price": event.get("price"),
                        "reason": event.get("message") or event.get("event_type"),
                        "event_id": event.get("event_id"),
                        "position_id": str(event.get("position_id") or event.get("ticket") or ""),
                        "volume": event.get("volume"),
                    })
            if start_ts is not None or end_ts is not None:
                events = [
                    item for item in events
                    if (start_ts is None or (_historical_kline_timestamp(item.get("timestamp")) or 0) >= start_ts)
                    and (end_ts is None or (_historical_kline_timestamp(item.get("timestamp")) or 0) <= end_ts)
                ]
            print(
                "[StrategyExecutionChart]",
                f"strategy={strategy_id} deployment={deployment.get('deployment_id')} ",
                f"account={account_id} mode={deployment.get('execution_mode')} ",
                f"symbol={symbol} period={period} bars={len(bars)} events={len(events)}",
            )
            account_views.append({
                **deployment,
                "runtime_active": runtime_active,
                "symbol": symbol,
                "chart": {
                    "symbol": symbol,
                    "period": period,
                    "bars": bars,
                    "events": events,
                    "display_timezone": "Asia/Shanghai",
                    "mt5_timezone_offset_hours": mt5_timezone_offset_hours,
                },
                "decisions": decisions,
            })
        return {
            "status": "ok",
            "strategy": {
                "strategy_id": strategy.strategy_id,
                "strategy_name": strategy.strategy_name,
                "symbol": strategy.symbol,
            },
            "deployments": account_views,
        }

    @protected_router.get("/strategy/{strategy_id}/audit-chain")
    async def get_strategy_audit_chain(
        strategy_id: str,
        deployment_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return the complete AI -> decision -> order -> fill audit chain."""
        strategy = strategy_repo.get_strategy_by_id(user.user_id, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在或不属于当前用户")
        deployments = strategy_deployment_repo.list_for_strategy(user.user_id, strategy_id)
        deployment = next((item for item in deployments if not deployment_id or str(item.get("deployment_id")) == str(deployment_id)), None)
        if deployment is None:
            return {"status": "ok", "items": []}
        account_id = int(deployment["account_id"])
        engine = engine_manager.get_engine(user.user_id, account_id)
        decisions = engine.get_decision_history(strategy_id=strategy_id, count=10)
        if decision_id:
            decisions = [item for item in decisions if str(item.get("decision_id")) == str(decision_id)]
        storage = engine_manager.paper_trading.storage
        items = []
        for decision in decisions:
            did = str(decision.get("decision_id") or "")
            orders = [dict(row) for row in storage.fetchall(
                "SELECT * FROM paper_orders WHERE user_id = ? AND account_id = ? AND deployment_id = ? AND decision_id = ? ORDER BY requested_at DESC",
                (user.user_id, account_id, deployment.get("deployment_id"), did),
            )]
            trades = [dict(row) for row in storage.fetchall(
                "SELECT t.*, o.decision_id FROM paper_trades t LEFT JOIN paper_orders o ON o.order_id = t.order_id WHERE t.user_id = ? AND t.account_id = ? AND t.deployment_id = ? AND o.decision_id = ? ORDER BY t.closed_at DESC",
                (user.user_id, account_id, deployment.get("deployment_id"), did),
            )]
            items.append({"decision": decision, "signals": decision.get("signals") or [], "orders": orders, "trades": trades})
        return {"status": "ok", "strategy": {"strategy_id": strategy_id, "strategy_name": strategy.strategy_name}, "deployment": deployment, "items": items}

    @protected_router.post("/strategy/{strategy_id}/ai-review")
    async def review_strategy_execution(
        strategy_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Use the LLM to review a deployment's execution evidence.

        The model only returns an evidence-based review and configuration
        suggestions. No strategy, source, or position policy is changed here.
        """
        strategy = strategy_repo.get_strategy_by_id(user.user_id, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在或不属于当前用户")
        data = await request.json()
        if not getattr(request, "_strategy_review_background", False):
            job_id = uuid.uuid4().hex
            with _STRATEGY_REVIEW_JOBS_LOCK:
                _STRATEGY_REVIEW_JOBS[job_id] = {
                    "job_id": job_id, "status": "queued", "created_at": int(time.time()),
                    "user_id": int(user.user_id), "strategy_id": str(strategy_id),
                    "deployment_id": str(data.get("deployment_id") or ""),
                }
                cutoff = int(time.time()) - _STRATEGY_REVIEW_JOB_TTL
                for old_id, old_job in list(_STRATEGY_REVIEW_JOBS.items()):
                    if int(old_job.get("created_at") or 0) < cutoff:
                        _STRATEGY_REVIEW_JOBS.pop(old_id, None)

            async def run_review_job():
                with _STRATEGY_REVIEW_JOBS_LOCK:
                    if job_id in _STRATEGY_REVIEW_JOBS:
                        _STRATEGY_REVIEW_JOBS[job_id]["status"] = "running"
                try:
                    result = await asyncio.to_thread(
                        lambda: asyncio.run(
                            review_strategy_execution(
                                strategy_id, _StrategyReviewRequest(data), user,
                            )
                        )
                    )
                    with _STRATEGY_REVIEW_JOBS_LOCK:
                        if job_id in _STRATEGY_REVIEW_JOBS:
                            _STRATEGY_REVIEW_JOBS[job_id].update(
                                status="completed", result=result,
                                completed_at=int(time.time()),
                            )
                except Exception as exc:
                    with _STRATEGY_REVIEW_JOBS_LOCK:
                        if job_id in _STRATEGY_REVIEW_JOBS:
                            _STRATEGY_REVIEW_JOBS[job_id].update(
                                status="failed", error=str(exc),
                                completed_at=int(time.time()),
                            )

            asyncio.create_task(run_review_job())
            return {
                "status": "accepted", "job_id": job_id,
                "message": "策略复盘已提交，后台生成中",
            }
        deployment_id = str(data.get("deployment_id") or "")
        hours = max(1, min(24 * 30, int(data.get("hours") or 24)))
        deployments = strategy_deployment_repo.list_for_strategy(
            user.user_id, strategy_id
        )
        deployment = next(
            (item for item in deployments
             if str(item.get("deployment_id")) == deployment_id),
            None,
        ) if deployment_id else next(
            (item for item in deployments if item.get("status") == "active"),
            deployments[0] if deployments else None,
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="该策略没有可复盘的部署")

        account_id = int(deployment["account_id"])
        paper = deployment.get("execution_mode") == "paper"
        if paper:
            engine_manager.paper_trading.reconcile_decision_statuses(
                user.user_id, account_id,
            )
        engine = engine_manager.get_engine(user.user_id, account_id)
        now = int(time.time())
        start_at = now - hours * 3600
        report = engine_manager.paper_trading.build_report(
            user.user_id, account_id, strategy_id, start_at,
        ) if paper else None
        detail = (
            engine_manager.paper_trading.get_account_detail(user.user_id, account_id)
            if paper else {}
        )
        trades = [
            item for item in detail.get("trades", [])
            if str(item.get("deployment_id")) == str(deployment.get("deployment_id"))
            and int(item.get("closed_at") or item.get("opened_at") or 0) >= start_at
        ][-100:]
        orders = [
            item for item in detail.get("orders", [])
            if str(item.get("deployment_id")) == str(deployment.get("deployment_id"))
            and int(item.get("requested_at") or 0) >= start_at
        ][-100:]
        decisions = engine.get_decision_history(
            symbol=str(strategy.symbol), strategy_id=strategy_id, count=1000,
        )
        def event_epoch(value):
            if value in (None, ""):
                return 0
            if isinstance(value, (int, float)):
                return int(value)
            try:
                return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError, OverflowError):
                return 0
        decisions = [
            item for item in decisions
            if event_epoch(item.get("created_at") or item.get("timestamp")) >= start_at
        ][-100:]

        def compact_text(value, limit=600):
            text = str(value or "")
            return text if len(text) <= limit else text[:limit] + "…"

        def compact_record(item, fields):
            return {
                key: item.get(key)
                for key in fields
                if item.get(key) not in (None, "", [], {})
            }

        # 运行态记录包含持仓方案快照、AI完整分析快照和归因 JSON；直接把
        # 100 条原始对象重复交给模型会产生数十万 token。复盘只保留能够
        # 支撑结论的成交、风控和信号字段，汇总统计仍来自完整数据库记录。
        compact_trades = [compact_record(item, (
            "trade_id", "position_id", "order_id", "direction", "volume",
            "entry_price", "exit_price", "net_profit", "commission",
            "exit_reason", "opened_at", "closed_at", "signal_source_id",
            "setup_type", "setup_family", "setup_profile_id",
        )) for item in trades[-50:]]
        compact_orders = []
        for item in orders[-50:]:
            record = compact_record(item, (
                "order_id", "decision_id", "direction", "status",
                "requested_volume", "filled_volume", "requested_price",
                "filled_price", "stop_loss", "take_profit", "confidence",
                "requested_at", "filled_at", "canceled_at",
                "signal_source_id", "exit_mode",
            ))
            if item.get("rejection_reason"):
                record["rejection_reason"] = compact_text(
                    item.get("rejection_reason"), 300
                )
            compact_orders.append(record)
        compact_decisions = []
        for item in decisions[-50:]:
            record = compact_record(item, (
                "decision_id", "created_at", "timestamp", "action", "status",
                "confidence", "decision_type", "auto_executed", "order_id",
                "observation_count",
            ))
            record["reason"] = compact_text(item.get("reason"), 500)
            record["signals"] = []
            for signal in (item.get("signals") or [])[:5]:
                if not isinstance(signal, dict):
                    continue
                signal_record = compact_record(signal, (
                    "signal_id", "signal_source_id", "source", "source_period",
                    "action", "direction", "confidence", "trigger_price",
                    "suggested_entry", "suggested_sl", "suggested_tp",
                    "setup_type", "setup_family", "ai_setup_type",
                    "ai_entry_mode", "pivot_price", "pivot_score",
                ))
                if signal.get("trigger_reason"):
                    signal_record["trigger_reason"] = compact_text(
                        signal.get("trigger_reason"), 400
                    )
                record["signals"].append(signal_record)
            compact_decisions.append(record)
        ai_context = []
        for source in strategy.get_signal_sources("ai_entry", enabled_only=True):
            params = source.get("params") or {}
            source_id = str(params.get("ai_signal_source_id") or source.get("signal_source_id") or "")
            result = engine.llm_store.get_analysis_result_for_source(
                str(strategy.symbol), source_id,
            ) if source_id and hasattr(engine.llm_store, "get_analysis_result_for_source") else None
            if result:
                ai_context.append({
                    "signal_source_id": source_id,
                    "analyzed_at": result.analyzed_at,
                    "overall_trend": result.overall_trend,
                    "trend_analysis": result.trend_analysis,
                    "trade_suggestions": result.trade_suggestions[:10],
                })
        policy = None
        try:
            policy = engine._position_policy_repository.get_for_strategy(
                user.user_id, strategy,
            )
        except Exception:
            policy = None
        def self_group_trade_stats(items, field):
            grouped = {}
            for item in items:
                grouped.setdefault(str(item.get(field) or "unknown"), []).append(
                    float(item.get("net_profit") or 0)
                )
            return [
                {
                    "name": name,
                    "trade_count": len(values),
                    "win_rate": round(
                        sum(value > 0 for value in values) / len(values) * 100, 2
                    ) if values else 0,
                    "net_profit": round(sum(values), 2),
                }
                for name, values in grouped.items()
            ]
        review_input = {
            "scope": {
                "strategy_id": strategy_id,
                "strategy_name": strategy.strategy_name,
                "symbol": strategy.symbol,
                "deployment_id": deployment.get("deployment_id"),
                "account_name": deployment.get("account_name"),
                "execution_mode": deployment.get("execution_mode"),
                "hours": hours,
            },
            "performance": report.get("summary") if report else {
                "trade_count": len(trades), "order_count": len(orders),
            },
            "direction_stats": self_group_trade_stats(trades, "direction"),
            "exit_stats": report.get("by_exit_reason") if report else [],
            "setup_performance": {
                "coverage": {
                    "closed_positions": report.get("summary", {}).get(
                        "closed_position_count", 0
                    ),
                    "attributed_positions": report.get("summary", {}).get(
                        "setup_attributed_position_count", 0
                    ),
                    "unattributed_positions": report.get("summary", {}).get(
                        "setup_unattributed_position_count", 0
                    ),
                },
                "by_setup": report.get("by_setup", []),
                "by_setup_family": report.get("by_setup_family", []),
                "by_setup_profile": report.get("by_setup_profile", []),
                "by_setup_direction": report.get("by_setup_direction", []),
            } if report else {},
            "ai_context": ai_context,
            "strategy_config": {
                "min_confidence": strategy.min_confidence,
                "consistency_requirement": strategy.consistency_requirement,
                "conflict_resolution": strategy.conflict_resolution,
                "max_positions": strategy.max_positions,
                "max_same_direction": strategy.max_same_direction,
                "signal_sources": strategy.get_signal_sources(enabled_only=True),
            },
            "position_policy": policy.to_dict() if policy else None,
            "evidence_limits": {
                "trades_in_scope": len(trades),
                "orders_in_scope": len(orders),
                "decisions_in_scope": len(decisions),
                "detail_limit_per_type": 50,
            },
            "trades": compact_trades,
            "orders": compact_orders,
            "decisions": compact_decisions,
        }
        system_prompt = (
            "你是量化策略复盘分析器。只能依据输入的成交、订单、决策、AI行情和持仓管理数据分析，"
            "不要臆测未提供的市场事实。必须只返回严格 JSON，不要 Markdown、解释或额外字段。"
        )
        prompt = (
            "请复盘以下策略运行数据，找出亏损主要来源，并分别给出信号源、策略、持仓管理三类可执行改进建议。"
            "必须引用具体统计证据；优先比较不同 Setup、方向和所用持仓场景方案的胜率、平均R、收益因子和连续亏损。"
            "少于10个已平仓持仓的 Setup 只能标记为样本不足，不能据此给出停用结论；10到29个只能给出观察性建议。"
            "不要直接修改配置。建议要说明目标字段、修改方向、原因、风险和验证方式。"
            "输出格式：{\"summary\":\"\",\"root_causes\":[{\"category\":\"signal_source|strategy|position_management|execution\",\"severity\":\"high|medium|low\",\"evidence\":\"\",\"explanation\":\"\"}],\"suggestions\":[{\"target\":\"signal_source|strategy|position_management\",\"field\":\"\",\"change\":\"\",\"patch\":{},\"reason\":\"\",\"risk\":\"\",\"validation\":\"\"}],\"risk_notes\":[\"\"],\"confidence\":0}. patch只能包含明确可写入的配置字段，无法确定时必须返回空对象。\n\n"
            + json.dumps(review_input, ensure_ascii=False, default=str)
        )
        try:
            review = engine.llm_service.call_llm(
                prompt,
                system_prompt=system_prompt,
                scene_code=AI_SIGNAL_ANALYSIS,
                object_type="strategy_review",
                object_id=f"{strategy_id}:{deployment.get('deployment_id')}",
                max_tokens=5000,
            )
        except (LLMRequestError, LLMGovernanceError, ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "ok",
            "review": review or {},
            "evidence": {
                "summary": review_input["performance"],
                "trade_count": len(trades),
                "order_count": len(orders),
                "decision_count": len(decisions),
                "generated_at": now,
            },
        }

    @protected_router.get("/strategy/{strategy_id}/ai-review/{job_id}")
    async def get_strategy_review_status(
        strategy_id: str,
        job_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Poll an asynchronous strategy review without blocking the page."""
        with _STRATEGY_REVIEW_JOBS_LOCK:
            job = dict(_STRATEGY_REVIEW_JOBS.get(str(job_id)) or {})
        if not job or int(job.get("user_id") or 0) != int(user.user_id) or str(job.get("strategy_id")) != str(strategy_id):
            raise HTTPException(status_code=404, detail="复盘任务不存在或已过期")
        response = {
            "status": job.get("status"), "job_id": str(job_id),
            "message": "策略复盘已提交，后台生成中" if job.get("status") in {"queued", "running"} else "",
        }
        if job.get("status") == "completed":
            response.update(job.get("result") or {})
        elif job.get("status") == "failed":
            response["error"] = str(job.get("error") or "AI 策略复盘失败")
        return response

    @protected_router.post("/strategy/{strategy_id}/ai-review/apply")
    async def apply_strategy_review_changes(
        strategy_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Apply an explicitly reviewed AI patch after optional deployment stop."""
        strategy = strategy_repo.get_strategy_by_id(user.user_id, strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在或不属于当前用户")
        data = await request.json()
        stop_deployments = bool(data.get("stop_deployments"))
        storage = engine_manager.paper_trading.storage
        deployments = [dict(row) for row in storage.fetchall(
            """SELECT deployment_id, account_id, execution_mode, status, symbol
               FROM strategy_deployments WHERE user_id = ? AND strategy_id = ?
                 AND status = 'active'""", (user.user_id, strategy_id)
        )]
        if deployments and not stop_deployments:
            return {"status": "blocked", "message": "该策略仍有运行部署，请先停止后再应用修改",
                    "deployments": deployments}
        if deployments:
            storage.execute(
                "UPDATE strategy_deployments SET status = 'paused', updated_at = ? WHERE user_id = ? AND strategy_id = ? AND status = 'active'",
                (int(time.time()), user.user_id, strategy_id),
            )
        changed = []
        strategy_patch = data.get("strategy") or {}
        allowed_strategy = {"min_confidence", "consistency_requirement", "conflict_resolution", "max_positions", "max_same_direction", "min_risk_reward", "max_risk_reward", "position_management_policy_id", "signal_sources"}
        strategy_patch = {key: value for key, value in strategy_patch.items() if key in allowed_strategy}
        if strategy_patch:
            engine = engine_manager.get_engine_for_user(user.user_id)
            engine.strategy_service.update_strategy(strategy.symbol, strategy_patch, strategy_id)
            changed.append("strategy")
        for item in data.get("signal_sources") or []:
            source_id = str(item.get("signal_source_id") or item.get("id") or "")
            patch = item.get("patch") or {}
            if source_id and isinstance(patch, dict):
                source = ai_signal_source_repo.get(user.user_id, source_id)
                if source:
                    ai_signal_source_repo.update(user.user_id, source_id, patch)
                    changed.append(f"signal_source:{source_id}")
        policy_patch = data.get("position_policy") or {}
        policy_id = str(policy_patch.get("policy_id") or "")
        if policy_id and isinstance(policy_patch.get("patch"), dict):
            policy = position_policy_repo.get(user.user_id, policy_id)
            if policy:
                payload = policy.to_dict()
                payload.update(policy_patch["patch"])
                position_policy_repo.save(payload)
                changed.append(f"position_policy:{policy_id}")
        engine_manager.refresh_user_strategies(user.user_id)
        add_audit_event(user, "strategy_review_applied", "应用 AI 复盘修改", f"应用策略 {strategy_id} 的复盘修改", {"changed": changed, "stopped_deployments": [item["deployment_id"] for item in deployments]}, "strategy", strategy_id)
        return {"status": "ok", "changed": changed, "stopped_deployments": deployments, "message": "复盘修改已应用，运行部署已暂停"}

    @protected_router.get("/strategy/shared")
    async def get_shared_strategies(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取平台共享策略库；共享内容只允许引用，不暴露机密配置。"""
        strategies = [
            shared_strategy_payload(item, user.user_id)
            for item in strategy_repo.list_shared_strategies(user.user_id)
        ]
        return {
            "status": "ok",
            "count": len(strategies),
            "strategies": strategies,
        }

    @protected_router.post("/strategy/shared/{owner_user_id}/{strategy_id}/use")
    async def use_shared_strategy(
        owner_user_id: int,
        strategy_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """为当前用户创建不可编辑的平台策略引用。"""
        try:
            data = await request.json()
        except Exception:
            data = {}

        target_symbol = str(data.get("target_symbol", "")).strip()
        source = strategy_repo.get_strategy_by_id(owner_user_id, strategy_id)
        if source is None or source.visibility != "shared":
            return {"status": "error", "message": "共享策略不存在或未开放使用"}
        target_symbol = target_symbol or source.symbol
        if not instrument_mapping_repo.user_can_use_symbol(
            owner_user_id, source.symbol, user.user_id, target_symbol,
        ):
            return {
                "status": "error",
                "message": "目标品种未与共享策略品种建立平台关联映射",
            }
        try:
            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "strategies")
                quota_service.assert_strategy_sources(
                    user.user_id, user.role, "", source.signal_sources or [],
                )
                strategy = strategy_repo.use_shared_strategy(
                    user.user_id, owner_user_id, strategy_id, target_symbol,
                )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if strategy is None:
            return {"status": "error", "message": "共享策略不存在或未开放使用"}

        engine_manager.refresh_user_strategies(user.user_id)
        return {
            "status": "ok",
            "message": "已添加平台策略引用；共享策略被应用后会冻结，后续演进请复制新版本",
            "strategy": strategy_payload(strategy, user.user_id),
        }

    @protected_router.post("/strategy/{strategy_ref}/copy")
    async def copy_strategy(
        strategy_ref: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        strategy = (
            strategy_repo.get_strategy_by_id(user.user_id, strategy_ref)
            or strategy_repo.get_strategy(user.user_id, strategy_ref)
        )
        if strategy is None:
            return {"status": "error", "message": "策略配置不存在"}
        try:
            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "strategies")
                quota_service.assert_strategy_sources(
                    user.user_id, user.role, "", strategy.signal_sources or [],
                )
                copied = strategy_repo.copy_strategy(user.user_id, strategy)
            engine_manager.refresh_user_strategies(user.user_id)
            return {
                "status": "ok",
                "message": "已复制为新的私有草稿策略",
                "strategy": strategy_payload(copied, user.user_id),
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @protected_router.get("/strategy/{strategy_ref}")
    async def get_strategy(strategy_ref: str, user: AuthUser = Depends(require_auth)) -> Dict:
        """按策略 ID 获取；兼容按品种获取第一条策略。"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        strategy = (
            strategy_repo.get_strategy_by_id(user.user_id, strategy_ref)
            or strategy_repo.get_strategy(user.user_id, strategy_ref)
        )
        if strategy is None:
            return {"status": "error", "message": "策略配置不存在"}
        return {
            "status": "ok",
            "strategy": strategy_payload(strategy, user.user_id)
        }

    @protected_router.post("/strategy/{strategy_ref}")
    async def update_strategy(strategy_ref: str, request: Request, user: AuthUser = Depends(require_auth)) -> Dict:
        """按策略 ID 更新；兼容按品种更新第一条策略。"""
        try:
            engine = engine_manager.get_engine_for_user(user.user_id)
            data = await request.json()
            confirmed = bool(data.pop("_confirm_hot_reload", False))
            if "position_management_policy_id" in data:
                policy_id = str(data.get("position_management_policy_id", "")).strip()
                if not policy_id or not position_policy_repo.get(user.user_id, policy_id):
                    return {"status": "error", "message": "请选择有效的持仓管理方案"}
            strategy = (
                engine.strategy_service.strategy_store.get_strategy_by_id(strategy_ref)
                or engine.strategy_service.strategy_store.get_strategy(strategy_ref)
            )
            if strategy is None:
                return {"status": "error", "message": "策略配置不存在"}
            if strategy.source_owner_user_id:
                return {
                    "status": "error",
                    "message": "平台共享策略为只读引用，不能修改；如不再使用可以删除引用",
                }
            assert_strategy_not_locked(user, strategy, allow_hot_reload=True)
            impact = impact_service.analyze(user.user_id, "strategy", strategy.strategy_id)
            if not impact["allowed"]:
                return {"status": "error", "code": "external_deployment", "message": impact["blocked_reason"], "impact": impact}
            if impact["requires_confirmation"] and not confirmed:
                return {"status": "error", "code": "hot_reload_confirmation_required", "message": "该修改会热更新已部署策略，请确认影响范围", "impact": impact}
            assert_strategy_material_edit_allowed(strategy, data, allow_hot_reload=confirmed or not impact["requires_confirmation"])
            was_shared = strategy.visibility == "shared"
            if "signal_sources" in data:
                bind_alpha_signal_snapshots(user, data)
                validate_ai_signal_configuration(user, data)
                with quota_service.guarded():
                    quota_service.assert_strategy_sources(
                        user.user_id, user.role, strategy.strategy_id,
                        data.get("signal_sources") or [],
                    )
                    strategy = engine.strategy_service.update_strategy(
                        strategy.symbol,
                        data,
                        strategy.strategy_id,
                    )
            else:
                strategy = engine.strategy_service.update_strategy(
                    strategy.symbol,
                    data,
                    strategy.strategy_id,
                )
            shared_ai_runtime_repo.sync_strategy_visibility(
                user.user_id, strategy.to_dict()
            )
            engine_manager.refresh_user_strategies(user.user_id)
            if was_shared or strategy.visibility == "shared":
                action = "取消共享" if was_shared and strategy.visibility != "shared" else "修改"
                notify_strategy_references(user, strategy, action)
            add_audit_event(
                user, "strategy_updated", "修改策略",
                f"修改策略 {strategy.strategy_name}",
                {"changed_fields": sorted(data.keys())},
                "strategy", strategy.strategy_id,
            )

            return {
                "status": "ok",
                "message": "策略配置已更新",
                "strategy": strategy_payload(strategy, user.user_id)
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @protected_router.post("/strategy/{strategy_ref}/lifecycle")
    async def transition_strategy_lifecycle(
        strategy_ref: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """按状态机转换策略生命周期。"""
        try:
            data = await request.json()
            target_status = str(data.get("target_status", "")).strip()
            reason = str(data.get("reason", "")).strip()
            if not target_status:
                return {"status": "error", "message": "请选择目标状态"}

            engine = engine_manager.get_engine_for_user(user.user_id)
            store = engine.strategy_service.strategy_store
            current = store.get_strategy_by_id(strategy_ref)
            if current is None:
                return {"status": "error", "message": "策略配置不存在"}
            if current.source_owner_user_id:
                return {
                    "status": "error",
                    "message": "平台共享策略为只读引用，不能修改生命周期",
                }
            assert_strategy_not_locked(user, current)
            if (
                current.lifecycle_status == StrategyLifecycle.PRODUCTION
                and target_status == StrategyLifecycle.PAPER_TRADING
            ):
                active_live = engine_manager.paper_trading.storage.fetchone(
                    "SELECT COUNT(*) AS count FROM strategy_deployments "
                    "WHERE user_id = ? AND strategy_id = ? AND execution_mode = 'live' "
                    "AND status IN ('active', 'paused')",
                    (user.user_id, current.strategy_id),
                )
                if active_live and int(active_live["count"]):
                    return {
                        "status": "error",
                        "message": "请先结束全部实盘部署，再回退到模拟盘验证",
                    }
            if target_status == "production":
                memberships.assert_live_trading(user.user_id)
            admission_service.validate_transition(
                user.user_id, current, target_status
            )
            strategy = store.transition_lifecycle(
                strategy_ref, target_status, reason
            )
            if strategy is None:
                return {"status": "error", "message": "策略配置不存在"}
            shared_ai_runtime_repo.sync_strategy_visibility(
                user.user_id, strategy.to_dict()
            )
            engine_manager.refresh_user_strategies(user.user_id)
            if strategy.visibility == "shared":
                notify_strategy_references(user, strategy, "变更生命周期")
            add_audit_event(
                user, "strategy_lifecycle_changed", "策略生命周期变更",
                f"策略进入 {strategy.lifecycle_status}",
                {"target_status": target_status, "reason": reason},
                "strategy", strategy.strategy_id,
            )
            return {
                "status": "ok",
                "message": f"策略已进入“{strategy.to_dict()['lifecycle_label']}”状态",
                "strategy": strategy_payload(strategy, user.user_id),
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @protected_router.get("/admin/strategies")
    async def admin_list_strategies(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """管理员查看所有用户策略，便于人工推进验证状态。"""
        items = strategy_repo.list_admin_strategies()
        return {
            "status": "ok",
            "count": len(items),
            "strategies": items,
            "lifecycle_options": [
                {"value": value, "label": label}
                for value, label in StrategyLifecycle.LABELS.items()
            ],
        }

    @protected_router.post("/admin/strategies/{target_user_id}/{strategy_id}/lifecycle")
    async def admin_transition_strategy_lifecycle(
        target_user_id: int,
        strategy_id: str,
        request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """管理员直接推进用户策略状态；共享引用允许推进到实盘。"""
        try:
            data = await request.json()
            target_status = str(data.get("target_status", "")).strip()
            reason = str(data.get("reason", "")).strip()
            if not StrategyLifecycle.is_valid(target_status):
                return {"status": "error", "message": "请选择有效的目标状态"}

            target_user = UserRepository().get_by_id(int(target_user_id))
            if target_user is None:
                return {"status": "error", "message": "目标用户不存在"}
            strategy = strategy_repo.get_strategy_by_id(target_user.user_id, strategy_id)
            if strategy is None:
                return {"status": "error", "message": "策略配置不存在"}

            if target_status == StrategyLifecycle.PRODUCTION:
                memberships.assert_live_trading(target_user.user_id)
                if not strategy.position_management_policy_id:
                    return {"status": "error", "message": "策略缺少持仓管理方案"}
                if not position_policy_repo.get_for_strategy(
                    target_user.user_id, strategy
                ):
                    return {"status": "error", "message": "策略绑定的持仓管理方案不存在"}
                if not strategy.get_signal_sources(enabled_only=True):
                    return {"status": "error", "message": "策略至少需要一个已启用信号源"}

            now = datetime.now()
            previous_status = strategy.lifecycle_status
            strategy.lifecycle_status = target_status
            strategy.lifecycle_updated_at = now
            strategy.updated_at = now
            strategy.lifecycle_history.append({
                "from_status": previous_status,
                "to_status": target_status,
                "changed_at": now.isoformat(),
                "reason": reason or "管理员推进策略状态",
                "actor": "admin",
                "actor_user_id": user.user_id,
            })

            saved = strategy_repo.save_strategy(target_user.user_id, strategy)
            shared_ai_runtime_repo.sync_strategy_visibility(
                target_user.user_id, saved.to_dict()
            )
            engine_manager.refresh_user_strategies(target_user.user_id)
            if saved.visibility == "shared":
                notify_strategy_references(
                    AuthUser(
                        user_id=target_user.user_id,
                        username=target_user.username,
                        email=target_user.email,
                        role=target_user.role,
                        membership_level=target_user.membership_level,
                        live_trading_enabled=target_user.live_trading_enabled,
                    ),
                    saved,
                    "变更生命周期",
                )
            add_audit_event(
                user,
                "admin_strategy_lifecycle_changed",
                "管理员推进策略状态",
                f"管理员将用户 {target_user.email or target_user.username} 的策略推进到 {target_status}",
                {
                    "target_user_id": target_user.user_id,
                    "target_email": target_user.email,
                    "strategy_id": saved.strategy_id,
                    "strategy_name": saved.strategy_name,
                    "from_status": previous_status,
                    "target_status": target_status,
                    "reason": reason,
                },
                "strategy",
                saved.strategy_id,
            )
            return {
                "status": "ok",
                "message": f"策略已推进到“{saved.to_dict()['lifecycle_label']}”",
                "strategy": strategy_payload(saved, target_user.user_id),
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @protected_router.get("/admin/strategies/{target_user_id}/{strategy_id}/deployments")
    async def admin_strategy_deployments(
        target_user_id: int,
        strategy_id: str,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """管理员查看策略挂载的账户（模拟盘/实盘）及账户盈亏快照。"""
        try:
            target_user = UserRepository().get_by_id(int(target_user_id))
            if target_user is None:
                return {"status": "error", "message": "目标用户不存在"}
            strategy = strategy_repo.get_strategy_by_id(target_user.user_id, strategy_id)
            if strategy is None:
                return {"status": "error", "message": "策略配置不存在"}

            rows = engine_manager.paper_trading.storage.fetchall(
                """
                SELECT d.deployment_id, d.account_id, d.execution_mode, d.status,
                       d.symbol, d.created_at, d.scheduled_end_at,
                       a.account_name, a.account_type, a.environment,
                       a.balance, a.equity, a.free_margin, a.initial_balance,
                       a.currency, a.status AS account_status,
                       a.trading_enabled, a.auto_trading_enabled,
                       a.last_seen_at, a.financial_updated_at
                FROM strategy_deployments d
                JOIN trading_accounts a ON a.id = d.account_id
                WHERE d.user_id = ? AND d.strategy_id = ?
                ORDER BY d.created_at DESC
                """,
                (target_user.user_id, strategy.strategy_id),
            )
            now = int(time.time())
            deployments = []
            for row in rows:
                deployment = dict(row)
                connected = bool(
                    deployment["account_type"] == "mt5"
                    and deployment["last_seen_at"]
                    and now - int(deployment["last_seen_at"] or 0) <= 120
                )
                deployment["account_active"] = bool(
                    deployment["account_status"] == "active"
                )
                deployment["connected"] = connected
                deployment["balance"] = float(deployment["balance"] or 0)
                deployment["equity"] = float(deployment["equity"] or 0)
                deployment["free_margin"] = float(deployment["free_margin"] or 0)
                deployment["initial_balance"] = float(
                    deployment["initial_balance"] or 0
                )
                deployment["unrealized_pnl"] = (
                    deployment["equity"] - deployment["balance"]
                )
                total_pnl = deployment["equity"] - deployment["initial_balance"]
                deployment["total_pnl"] = total_pnl
                deployment["total_pnl_pct"] = (
                    round(total_pnl / deployment["initial_balance"] * 100, 2)
                    if deployment["initial_balance"] > 0 else 0.0
                )
                deployments.append(deployment)

            return {
                "status": "ok",
                "strategy": {
                    "strategy_id": strategy.strategy_id,
                    "strategy_name": strategy.strategy_name,
                    "symbol": strategy.symbol,
                    "lifecycle_status": strategy.lifecycle_status,
                },
                "deployments": deployments,
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @protected_router.get("/strategy-admission")
    async def get_strategy_admission(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        engine = engine_manager.get_engine_for_user(user.user_id)
        strategies = engine.strategy_service.strategy_store.get_all_strategies()
        return {
            "status": "ok",
            "items": [
                admission_service.evaluate(user.user_id, strategy)
                for strategy in strategies
            ],
        }

    @protected_router.delete("/strategy/{strategy_ref}")
    async def delete_strategy(strategy_ref: str, user: AuthUser = Depends(require_auth)) -> Dict:
        """按策略 ID 删除；兼容按品种删除全部策略。"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        store = engine.strategy_service.strategy_store
        strategy = store.get_strategy_by_id(strategy_ref)
        if strategy:
            if strategy.source_owner_user_id:
                active = engine_manager.paper_trading.storage.fetchone(
                    "SELECT COUNT(*) AS count FROM strategy_deployments "
                    "WHERE user_id = ? AND strategy_id = ? "
                    "AND status IN ('active', 'paused')",
                    (user.user_id, strategy.strategy_id),
                )
                if active and int(active["count"]):
                    return {
                        "status": "error",
                        "message": "该共享策略仍有运行中的部署，请先结束全部部署后再移除引用",
                    }
            try:
                assert_strategy_not_locked(user, strategy)
            except ValueError as exc:
                return {"status": "error", "message": str(exc)}
            success = store.delete_strategy(strategy.symbol, strategy.strategy_id)
            if success:
                shared_ai_runtime_repo.remove_for_strategy(
                    user.user_id, strategy.strategy_id
                )
                if strategy.visibility == "shared":
                    notify_strategy_references(user, strategy, "删除")
        else:
            strategy_ids = [
                item.strategy_id for item in store.get_strategies(strategy_ref)
            ]
            success = store.delete_strategy(strategy_ref)
            if success:
                for strategy_id in strategy_ids:
                    shared_ai_runtime_repo.remove_for_strategy(
                        user.user_id, strategy_id
                    )
        if success:
            engine_manager.refresh_user_strategies(user.user_id)
            add_audit_event(
                user, "strategy_deleted", "删除策略",
                f"删除策略 {strategy_ref}", entity_type="strategy",
                entity_id=strategy_ref,
            )
            return {"status": "ok", "message": "策略配置已删除"}
        return {"status": "error", "message": "策略配置不存在"}

    @protected_router.post("/strategy/trigger/{symbol}")
    async def trigger_strategy_decision(
        symbol: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """手动触发策略决策"""
        engine = engine_manager.get_engine_for_user(user.user_id)
        current_price = engine.kline_service.get_latest_price(symbol)
        if not current_price:
            return {"status": "error", "message": "无法获取当前价格"}

        result = engine.process_price(symbol, current_price)
        return {
            "status": "ok",
            "result": result
        }

    # ==================== 系统日志接口 ====================

    def build_log_filters(user: AuthUser, values: Dict) -> Dict:
        filters = {
            key: values.get(key) for key in (
                "account_id", "user_id", "symbol", "search", "start_at", "end_at",
                "page", "page_size", "correlation_id",
            ) if values.get(key) not in (None, "")
        }
        for source, target in (
            ("level", "levels"), ("category", "categories"),
            ("event_type", "event_types"),
        ):
            if values.get(source):
                filters[target] = [
                    item.strip() for item in str(values[source]).split(",")
                    if item.strip()
                ]
        if user.role != "admin":
            filters["user_id"] = user.user_id
            account_id = filters.get("account_id")
            if account_id and TradingAccountRepository().get_by_id(
                user.user_id, int(account_id)
            ) is None:
                raise HTTPException(status_code=404, detail="交易账户不存在")
        return filters

    @protected_router.get("/system/logs")
    async def get_system_logs(
        page: int = 1, page_size: int = 50,
        level: Optional[str] = None, category: Optional[str] = None,
        event_type: Optional[str] = None, symbol: Optional[str] = None,
        search: Optional[str] = None, account_id: Optional[int] = None,
        user_id: Optional[int] = None, start_at: Optional[int] = None,
        end_at: Optional[int] = None, correlation_id: Optional[str] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Tenant-scoped event search; administrators may search the platform."""
        filters = build_log_filters(user, locals())
        result = event_logs.list(filters)
        return {
            "status": "ok", "logs": result["items"],
            "total": result["total"], "page": result["page"],
            "page_size": result["page_size"],
            "facets": event_logs.facets(None if user.role == "admin" else user.user_id),
        }

    @protected_router.get("/system/logs/summary")
    async def get_system_log_summary(
        level: Optional[str] = None, category: Optional[str] = None,
        account_id: Optional[int] = None, user_id: Optional[int] = None,
        start_at: Optional[int] = None, end_at: Optional[int] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        filters = build_log_filters(user, locals())
        return {"status": "ok", "summary": event_logs.summary(filters)}

    @protected_router.post("/admin/system/logs/purge")
    async def purge_system_logs(
        request: Request, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        data = await request.json()
        before = int(data.get("before") or 0)
        if before <= 0:
            raise HTTPException(status_code=400, detail="必须指定日志保留截止时间")
        deleted = event_logs.purge_operational(before)
        event_logs.add({
            "level": "warning", "category": "audit",
            "event_type": "system_log_purged", "event_name": "清理运行日志",
            "user_id": user.user_id, "actor_type": "user",
            "actor_id": str(user.user_id), "status": "completed",
            "message": f"管理员清理了 {deleted} 条过期运行日志",
            "detail": {"before": before, "deleted": deleted},
        })
        return {
            "status": "ok", "deleted": deleted,
            "message": f"已清理 {deleted} 条过期运行日志，审计日志未删除",
        }

    # ==================== WebSocket接口 ====================

    @router.websocket("/ws/market")
    async def websocket_market(websocket: WebSocket):
        """登录后绑定到当前用户的账户级 WebSocket。"""
        await websocket.accept()
        engine = None

        try:
            auth_text = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=10,
            )
            auth_message = json.loads(auth_text)
            if auth_message.get("type") != "auth" or not auth_message.get("token"):
                await websocket.close(code=1008, reason="请先登录")
                return

            user = get_auth_manager().verify_token(auth_message["token"])
            _, engine = resolve_web_engine(
                engine_manager, user, auth_message.get("account_id")
            )
            engine.add_ws_client(websocket)
            engine.system_log.add_log(
                "websocket_connect",
                message="行情 WebSocket 已连接",
            )

            await websocket.send_text(json.dumps({
                "type": "connected",
                "message": "已连接到账户行情监控服务",
                "user_id": user.user_id,
                "account_id": engine.account_id,
            }))

            while True:
                try:
                    data = await websocket.receive_text()
                    msg = json.loads(data)

                    if msg.get('type') == 'ping':
                        await websocket.send_text(json.dumps({"type": "pong"}))

                except WebSocketDisconnect:
                    break

        except asyncio.TimeoutError:
            await websocket.close(code=1008, reason="登录超时")
        except (HTTPException, json.JSONDecodeError):
            await websocket.close(code=1008, reason="登录凭证无效")
        except Exception as e:
            print(f"[WebSocket] 连接异常: {e}")

        finally:
            if engine is not None:
                engine.system_log.add_log(
                    "websocket_disconnect",
                    message="行情 WebSocket 已断开",
                )
                engine.remove_ws_client(websocket)

    @router.websocket("/ws/system-logs")
    async def websocket_system_logs(websocket: WebSocket):
        """Authenticated live event stream with tenant filtering."""
        await websocket.accept()
        broadcaster = get_system_log_broadcaster()
        subscribed = False
        try:
            auth_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            auth_message = json.loads(auth_text)
            if auth_message.get("type") != "auth" or not auth_message.get("token"):
                await websocket.close(code=1008, reason="请先登录")
                return
            user = get_auth_manager().verify_token(auth_message["token"])
            account_id = auth_message.get("account_id")
            if account_id is not None:
                account_id = int(account_id)
            if user.role != "admin" and account_id and TradingAccountRepository().get_by_id(
                user.user_id, account_id
            ) is None:
                await websocket.close(code=1008, reason="交易账户不存在")
                return
            broadcaster.add(
                websocket, user.user_id, account_id, user.role == "admin"
            )
            subscribed = True
            await websocket.send_text(json.dumps({
                "type": "connected", "message": "日志实时流已连接",
            }))
            while True:
                payload = json.loads(await websocket.receive_text())
                if payload.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            if subscribed:
                broadcaster.remove(websocket)

    # ==================== 大模型分析接口 ====================

    def require_llm_access(user: AuthUser) -> Dict:
        access = llm_access_repo.get_status(user.user_id, user.role)
        if not access["access_granted"]:
            raise HTTPException(status_code=403, detail="大模型行情分析功能尚未开通")
        return access

    @protected_router.get("/llm/access")
    async def get_llm_access(user: AuthUser = Depends(require_auth)) -> Dict:
        access = llm_access_repo.get_status(user.user_id, user.role)
        effective_config = llm_config_repo.get_effective_config(user.user_id)
        return {
            "status": "ok",
            "access": {
                **access,
                "service_configured": effective_config.enabled,
                "feature_enabled": (
                    access["access_granted"] and effective_config.enabled
                ),
            },
        }

    @protected_router.get("/admin/instrument-mappings")
    async def list_instrument_mappings(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        return {"status": "ok", "mappings": instrument_mapping_repo.list()}

    @protected_router.get("/admin/instrument-observations")
    async def list_instrument_observations(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """汇总 EA 实际上报过行情的交易商与品种，供管理员建立映射。"""
        rows = llm_governance.storage.fetchall(
            """
            SELECT COALESCE(c.mt5_server, a.mt5_server, '') AS broker_server,
                   e.symbol, COUNT(*) AS report_count,
                   MAX(e.created_at) AS last_reported_at,
                   COUNT(DISTINCT e.account_id) AS account_count
            FROM system_event_logs e
            LEFT JOIN trading_accounts a ON a.id = e.account_id
            LEFT JOIN mt5_account_connections c ON c.account_id = a.id
            WHERE e.event_type IN ('ea_kline_full', 'ea_kline_incremental')
              AND COALESCE(e.symbol, '') != ''
            GROUP BY COALESCE(c.mt5_server, a.mt5_server, ''), e.symbol
            ORDER BY last_reported_at DESC, report_count DESC
            """
        )
        # Full K-line uploads are infrequent.  Prefer the short-lived quote
        # cache populated by EA statistics/TICK reports so this admin view
        # reflects the real latest market upload without high-frequency DB logs.
        recent_quotes = {
            (
                str(item.get("broker_name") or "").strip().upper(),
                str(item.get("symbol") or "").strip().upper(),
            ): int((item.get("latest") or {}).get("timestamp") or 0)
            for item in get_instrument_price_store().list()
        }
        items = []
        for row in rows:
            item = dict(row)
            item["broker_name"] = instrument_mapping_repo.broker_name_from_server(
                item.get("broker_server", "")
            )
            quote_time = recent_quotes.get((
                str(item["broker_name"] or "").strip().upper(),
                str(item.get("symbol") or "").strip().upper(),
            ), 0)
            logged_time = int(item.get("last_reported_at") or 0)
            if quote_time >= logged_time:
                item["last_reported_at"] = quote_time
                item["last_reported_source"] = "quote"
            else:
                item["last_reported_source"] = "kline_full"
            items.append(item)
        return {"status": "ok", "items": items}

    @protected_router.get("/admin/instrument-price-observations")
    async def list_instrument_price_observations(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """展示最近报价，关联判断由管理员手工完成。"""
        return {"status": "ok", "items": get_instrument_price_store().list()}

    @protected_router.put("/admin/instrument-mappings")
    async def save_instrument_mapping(
        request: Request, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        try:
            mapping = instrument_mapping_repo.save(await request.json())
            add_audit_event(
                user, "instrument_mapping_saved", "保存品种关联映射",
                f"保存 {mapping['effective_broker_name']} / {mapping['native_symbol']} -> {mapping['mapping_group']}",
                mapping, "instrument_mapping", mapping["mapping_id"],
            )
            return {"status": "ok", "mapping": mapping}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.delete("/admin/instrument-mappings/{mapping_id}")
    async def delete_instrument_mapping(
        mapping_id: str, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        if not instrument_mapping_repo.delete(mapping_id):
            raise HTTPException(status_code=404, detail="品种关联映射不存在")
        add_audit_event(
            user, "instrument_mapping_deleted", "删除品种关联映射",
            f"删除品种关联映射 {mapping_id}", {}, "instrument_mapping", mapping_id,
        )
        return {"status": "ok"}

    @protected_router.get("/llm/signal-options")
    async def get_llm_signal_options(
        symbol: Optional[str] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return AI signal configuration capabilities and shared references."""
        access = llm_access_repo.get_status(user.user_id, user.role)
        scene_options = llm_governance.scene_options(
            user.user_id, AI_SIGNAL_ANALYSIS
        )
        prompt_generation_options = llm_governance.scene_options(
            user.user_id, AI_SIGNAL_PROMPT_GENERATION
        )
        shared = [
            item for item in enrich_shared_ai_items(
                shared_ai_runtime_repo.list_shared(user.user_id),
                user.user_id, [symbol] if symbol else [],
            ) if not item["is_owner"]
        ]
        # Include live EA-reported symbols so a new signal source can be the
        # first configuration that references a newly reported instrument.
        try:
            available_symbols = collect_ai_signal_symbols(
                user.user_id,
                engine_manager,
                trade_config_repo,
                strategy_repo,
                ai_signal_source_repo,
            )
        except Exception:
            available_symbols = []
        market_data_accounts = [{"value": 0, "title": "用户共享行情"}]
        return {
            "status": "ok",
            "access_granted": access["access_granted"],
            "models": scene_options["models"],
            "prompt_generation_models": prompt_generation_options["models"],
            "symbols": available_symbols,
            "market_data_accounts": market_data_accounts,
            "default_system_prompt": (
                scene_options.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
            ),
            "default_analysis_prompt_template": (
                scene_options.get("user_prompt_template")
                or DEFAULT_ANALYSIS_PROMPT_TEMPLATE
            ),
            "runtime_contract": AI_SIGNAL_SOURCE_RUNTIME_CONTRACT,
            "shared_runtime_data": shared,
        }

    def ai_signal_source_payload(source: Dict) -> Dict:
        payload = dict(source)
        payload["locked"] = ai_signal_source_repo.is_locked(
            int(source["user_id"]), source["signal_source_id"]
        )
        return payload

    def validate_independent_ai_signal_source(user: AuthUser, data: Dict) -> None:
        if not str(data.get("name") or "").strip():
            raise ValueError("请填写 AI 信号源名称")
        symbol = str(data.get("symbol") or "").strip()
        period = str(data.get("period") or "").upper()
        config = data.get("config") or {}
        if not symbol or period not in {"M1", "M5", "M15", "H1", "H4"}:
            raise ValueError("请填写交易品种并选择有效的分析周期")
        data["market_data_account_id"] = 0
        mode = str(config.get("analysis_mode") or "self_analysis")
        if mode != "self_analysis":
            raise ValueError("AI 信号源仅支持自主 AI 分析")
        access = llm_access_repo.get_status(user.user_id, user.role)
        if not access["access_granted"]:
            raise ValueError("自主 AI 分析仅对已开通大模型功能的用户开放")
        models = llm_governance.scene_options(
            user.user_id, AI_SIGNAL_ANALYSIS
        )["models"]
        if str(config.get("model") or "") not in models:
            raise ValueError("请选择管理员为 AI 行情场景开放的模型")
        interval = int(config.get("analysis_interval_minutes") or 0)
        minimum = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}[period]
        if interval < minimum:
            raise ValueError(f"{period} 周期的调用间隔不能低于 {minimum} 分钟")
        kline_count = int(config.get("kline_count") or 0)
        if not AI_SIGNAL_KLINE_MIN_COUNT <= kline_count <= AI_SIGNAL_KLINE_MAX_COUNT:
            raise ValueError(
                f"AI 信号源分析 K 线数量必须在 {AI_SIGNAL_KLINE_MIN_COUNT}-"
                f"{AI_SIGNAL_KLINE_MAX_COUNT} 根之间"
            )
        references = config.get("reference_market_data") or []
        if not isinstance(references, list):
            raise ValueError("参考行情配置格式无效")
        if len(references) > 5:
            raise ValueError("每个 AI 信号源最多配置 5 条参考行情")
        occupied = set()
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("参考行情配置项格式无效")
            ref_symbol = str(reference.get("symbol") or "").strip()
            ref_period = str(reference.get("period") or "").strip().upper()
            if not ref_symbol or ref_period not in {"M1", "M5", "M15", "H1", "H4"}:
                raise ValueError("参考行情必须填写有效品种和周期")
            if ref_symbol == symbol and ref_period == period:
                raise ValueError("参考行情不能与主行情使用相同的品种和周期")
            key = (ref_symbol.upper(), ref_period)
            if key in occupied:
                raise ValueError("同一个品种和周期不能重复添加参考行情")
            occupied.add(key)
            if str(reference.get("role") or "market_context") not in {
                "higher_timeframe", "lower_timeframe", "related_symbol", "market_context"
            }:
                raise ValueError("参考行情类型无效")

    def normalize_reference_market_data(config: Dict) -> None:
        normalized = []
        for reference in config.get("reference_market_data") or []:
            normalized.append({
                "symbol": str(reference.get("symbol") or "").strip(),
                "period": str(reference.get("period") or "").strip().upper(),
                "kline_count": max(
                    AI_SIGNAL_KLINE_MIN_COUNT,
                    min(AI_SIGNAL_KLINE_MAX_COUNT, int(reference.get("kline_count", 100) or 100)),
                ),
                "role": str(reference.get("role") or "market_context").strip(),
            })
        config["reference_market_data"] = normalized

    def normalize_ai_signal_kline_count(config: Dict) -> None:
        """Keep source requirements within the EA full-initialization window."""
        config["kline_count"] = max(
            AI_SIGNAL_KLINE_MIN_COUNT,
            min(AI_SIGNAL_KLINE_MAX_COUNT, int(config.get("kline_count", 100) or 100)),
        )
        horizon = config.get("forecast_horizon_bars", 0)
        if str(horizon).strip().lower() in {"", "auto", "0", "none"}:
            config["forecast_horizon_bars"] = 0
        else:
            config["forecast_horizon_bars"] = max(3, min(48, int(horizon)))
        if str(config.get("signal_source_version") or "1.0") == "2.0":
            config["adaptive_enabled"] = config.get("adaptive_enabled", True) is not False
            config["adaptive_sample_size"] = max(
                5, min(50, int(config.get("adaptive_sample_size", 7) or 7))
            )

    def prompt_candidate_context(data: Dict) -> Dict:
        config = dict(data.get("config") or {})
        normalize_ai_signal_kline_count(config)
        normalize_reference_market_data(config)
        return {
            "name": str(data.get("name") or "").strip(),
            "symbol": str(data.get("symbol") or "").strip(),
            "period": str(data.get("period") or "").strip().upper(),
            "analysis_interval_minutes": int(
                config.get("analysis_interval_minutes") or 0
            ),
            "kline_count": int(config.get("kline_count") or 0),
            "reference_market_data": config.get("reference_market_data") or [],
            "runtime_contract": AI_SIGNAL_SOURCE_RUNTIME_CONTRACT,
        }

    def normalize_ai_signal_prompt_config(config: Dict) -> None:
        """Normalize the legacy 1.0 prompt or the structured 2.0 template."""
        version = str(config.get("signal_source_version") or "1.0").strip()
        if version not in {"1.0", "2.0"}:
            raise ValueError("AI 信号源配置版本只能是 1.0 或 2.0")
        config["signal_source_version"] = version
        if version == "2.0":
            config["analysis_template"] = str(
                config.get("analysis_template") or "auto_structure"
            )
            config["prompt_mode"] = "structured"
            config["system_prompt"] = str(
                config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
            )[:10000]
            config["analysis_prompt_template"] = STRUCTURE_ANALYSIS_PROMPT_TEMPLATE
            return
        # 1.0 keeps the legacy editable prompt format, but no longer requires
        # users to generate a candidate first. Missing fields are initialized
        # from the platform defaults and can then be edited directly.
        config["prompt_mode"] = "custom"
        system_prompt = str(config.get("system_prompt") or DEFAULT_SYSTEM_PROMPT).strip()
        template = str(
            config.get("analysis_prompt_template") or DEFAULT_ANALYSIS_PROMPT_TEMPLATE
        ).strip()
        if "{{market_data}}" not in template:
            raise ValueError("专属提示词缺少 {{market_data}}")
        if "{{strategy_context}}" in template:
            raise ValueError("专属提示词不能包含已废弃的策略上下文")
        references = config.get("reference_market_data") or []
        if references and "{{reference_market_data}}" not in template:
            raise ValueError("配置了参考行情时，专属提示词必须包含 {{reference_market_data}}")
        config["system_prompt"] = system_prompt[:10000]
        config["analysis_prompt_template"] = template[:50000]

    @protected_router.post("/ai-signal-sources/generate-prompt")
    async def generate_ai_signal_prompt(
        request: Request, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Generate a source-specific prompt candidate from unsaved form data."""
        try:
            data = await request.json()
            data["market_data_account_id"] = 0
            context = prompt_candidate_context(data)
            if not context["symbol"] or context["period"] not in {
                "M1", "M5", "M15", "H1", "H4",
            }:
                raise ValueError("请先选择主品种和主分析周期")
            intent = str(data.get("intent") or "").strip()
            if not intent:
                raise ValueError("请描述希望 AI 重点分析什么")
            if len(intent) > 2000:
                raise ValueError("分析想法不能超过 2000 个字符")
            scene = llm_governance.scene_options(
                user.user_id, AI_SIGNAL_PROMPT_GENERATION,
            )
            prompt = str(scene.get("user_prompt_template") or "")
            prompt = prompt.replace(
                "{{signal_source_config}}",
                json.dumps(context, ensure_ascii=False),
            ).replace("{{user_intent}}", intent)
            requested_models = data.get("model_ids") or []
            if isinstance(requested_models, str):
                requested_models = [requested_models]
            if not isinstance(requested_models, list):
                raise ValueError("提示词生成模型格式无效")
            requested_models = list(dict.fromkeys(
                str(model or "").strip() for model in requested_models if str(model or "").strip()
            ))
            if len(requested_models) > 3:
                raise ValueError("一次最多选择 3 个模型生成候选")
            models = requested_models or [str(scene.get("default_model_id") or "")]
            available_models = set(scene.get("models") or [])
            if any(model not in available_models for model in models):
                raise ValueError("所选模型不在提示词生成场景的可用模型列表中")
            _, engine = resolve_web_engine(engine_manager, user, None)

            async def generate_for_model(model: str) -> Dict:
                result = await asyncio.to_thread(
                    engine.llm_service.call_llm,
                    prompt, model, None, AI_SIGNAL_PROMPT_GENERATION,
                    "ai_signal_prompt_candidate", str(data.get("signal_source_id") or "draft"),
                    8000,
                )
                if not isinstance(result, dict):
                    raise ValueError("大模型未返回有效提示词候选")
                system_prompt = str(result.get("system_prompt") or "").strip()
                template = str(result.get("analysis_prompt_template") or "").strip()
                if not system_prompt or not template:
                    raise ValueError("提示词候选缺少 System Prompt 或分析模板")
                if "{{market_data}}" not in template:
                    raise ValueError("提示词候选缺少 {{market_data}}，请重新生成")
                if "{{current_price}}" not in template:
                    raise ValueError("提示词候选缺少 {{current_price}}，请重新生成")
                if "{{strategy_context}}" in template:
                    raise ValueError("提示词候选包含已废弃的策略上下文，请重新生成")
                if context["reference_market_data"] and "{{reference_market_data}}" not in template:
                    raise ValueError("提示词候选未包含参考行情变量，请重新生成")
                return {
                    "model": model,
                    "system_prompt": system_prompt[:10000],
                    "analysis_prompt_template": template[:50000],
                    "summary": str(result.get("summary") or "").strip()[:1000],
                    "assumptions": result.get("assumptions") or [],
                    "context": context,
                }

            generated = await asyncio.gather(
                *(generate_for_model(model) for model in models), return_exceptions=True,
            )
            candidates = [item for item in generated if isinstance(item, dict)]
            if not candidates:
                errors = [str(item) for item in generated if isinstance(item, Exception)]
                raise ValueError(errors[0] if errors else "未能生成有效提示词候选")
            return {
                "status": "ok",
                "candidates": candidates,
                "errors": [str(item) for item in generated if isinstance(item, Exception)],
            }
        except (ValueError, LLMGovernanceError, LLMRequestError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.get("/ai-signal-sources")
    async def get_ai_signal_sources(
        symbol: Optional[str] = None,
        include_shared: bool = False,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        items = (
            ai_signal_source_repo.list_visible(user.user_id)
            if include_shared else ai_signal_source_repo.list(user.user_id)
        )
        target_symbol = str(symbol or "").strip()
        visible_items = []
        for item in items:
            is_owner = int(item["user_id"]) == user.user_id
            if target_symbol and not is_owner and not instrument_mapping_repo.user_can_use_symbol(
                int(item["user_id"]), item.get("symbol", ""),
                user.user_id, target_symbol,
            ):
                continue
            if target_symbol and is_owner and item.get("symbol") != target_symbol:
                continue
            item["is_owner"] = is_owner
            visible_items.append(item)
        locked_ids = ai_signal_source_repo.locked_ids(
            user.user_id,
            [item["signal_source_id"] for item in visible_items],
        )
        for item in visible_items:
            item["locked"] = item["signal_source_id"] in locked_ids
        return {
            "status": "ok",
            "items": visible_items,
        }

    @protected_router.post("/ai-signal-sources")
    async def create_ai_signal_source(request: Request, user: AuthUser = Depends(require_auth)) -> Dict:
        try:
            data = await request.json()
            data["market_data_account_id"] = 0
            config = dict(data.get("config") or {})
            normalize_ai_signal_kline_count(config)
            normalize_reference_market_data(config)
            normalize_ai_signal_prompt_config(config)
            data["config"] = config
            validate_independent_ai_signal_source(user, data)
            with quota_service.guarded():
                quota_service.assert_can_create(
                    user.user_id, user.role, "signal_sources"
                )
                source = ai_signal_source_repo.create(user.user_id, data)
            return {"status": "ok", "source": ai_signal_source_payload(source)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @protected_router.put("/ai-signal-sources/{signal_source_id}")
    async def update_ai_signal_source(signal_source_id: str, request: Request, user: AuthUser = Depends(require_auth)) -> Dict:
        try:
            data = await request.json()
            confirmed = bool(data.pop("_confirm_hot_reload", False))
            current = ai_signal_source_repo.get(user.user_id, signal_source_id)
            if current is None:
                raise HTTPException(status_code=404, detail="AI 信号源不存在")
            candidate = {**current, **data}
            candidate_config = dict(candidate.get("config") or {})
            normalize_ai_signal_kline_count(candidate_config)
            normalize_reference_market_data(candidate_config)
            normalize_ai_signal_prompt_config(candidate_config)
            candidate["config"] = candidate_config
            validate_independent_ai_signal_source(user, candidate)
            data["market_data_account_id"] = 0
            data["config"] = candidate_config
            impact = impact_service.analyze(user.user_id, "ai_signal_source", signal_source_id)
            if not impact["allowed"]:
                raise HTTPException(status_code=409, detail={"message": impact["blocked_reason"], "impact": impact})
            if impact["requires_confirmation"] and not confirmed:
                raise HTTPException(status_code=409, detail={"message": "该修改会热更新已使用的信号源，请确认影响范围", "impact": impact})
            source = ai_signal_source_repo.update(
                user.user_id, signal_source_id, data, allow_hot_reload=True,
            )
            engine_manager.refresh_user_strategies(user.user_id)
            return {"status": "ok", "source": ai_signal_source_payload(source)}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @protected_router.post("/ai-signal-sources/{signal_source_id}/copy")
    async def copy_ai_signal_source(signal_source_id: str, user: AuthUser = Depends(require_auth)) -> Dict:
        try:
            source = ai_signal_source_repo.copy(user.user_id, signal_source_id)
            return {"status": "ok", "source": ai_signal_source_payload(source)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @protected_router.get("/ai-signal-sources/{signal_source_id}/impact")
    async def get_ai_signal_source_impact(
        signal_source_id: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        source = ai_signal_source_repo.get(user.user_id, signal_source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="AI 信号源不存在")
        impact = impact_service.analyze(user.user_id, "ai_signal_source", signal_source_id)
        return {"status": "ok", "source": ai_signal_source_payload(source), **impact, "items": impact["own_deployments"] + impact["external_deployments"]}

    @protected_router.get("/configuration-impact/{entity_type}/{entity_id}")
    async def get_configuration_impact(
        entity_type: str, entity_id: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Preview cross-account/user impact before a hot-reloadable edit."""
        aliases = {"policy": "position_management", "position_policy": "position_management", "strategy": "strategy", "ai": "ai_signal_source", "signal_source": "ai_signal_source"}
        try:
            impact = impact_service.analyze(
                user.user_id, aliases.get(entity_type, entity_type), entity_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "impact": impact}

    @protected_router.post("/ai-signal-sources/{signal_source_id}/pause")
    async def pause_ai_signal_source(
        signal_source_id: str, request: Request, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        source = ai_signal_source_repo.get(user.user_id, signal_source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="AI 信号源不存在")
        data = await request.json()
        paused = bool(data.get("paused", True))
        source = ai_signal_source_repo.set_analysis_paused(
            user.user_id, signal_source_id, paused,
        )
        items = ai_signal_source_repo.deployment_impact(user.user_id, signal_source_id)
        return {
            "status": "ok", "paused": bool(source.get("config", {}).get("analysis_paused")),
            "source": ai_signal_source_payload(source), "items": items,
        }

    @protected_router.post("/ai-signal-sources/{signal_source_id}/adaptive")
    async def configure_ai_signal_source_adaptive(
        signal_source_id: str, request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        source = ai_signal_source_repo.get(user.user_id, signal_source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="AI 信号源不存在")
        config = dict(source.get("config") or {})
        if str(config.get("signal_source_version") or "1.0") != "2.0":
            raise HTTPException(status_code=400, detail="只有2.0信号源支持自适应调参")
        data = await request.json()
        config["adaptive_enabled"] = bool(data.get("enabled", True))
        config["adaptive_sample_size"] = max(
            5, min(50, int(data.get("sample_size", config.get("adaptive_sample_size", 7)) or 7))
        )
        source = ai_signal_source_repo.update_adaptive_config(
            user.user_id, signal_source_id, config,
        )
        engine_manager.refresh_user_strategies(user.user_id)
        add_audit_event(
            user, "ai_signal_adaptive_configured", "调整 AI 信号源自适应设置",
            f"信号源 {signal_source_id} 自适应调参{'开启' if config['adaptive_enabled'] else '关闭'}",
            {"enabled": config["adaptive_enabled"], "sample_size": config["adaptive_sample_size"]},
            "ai_signal_source", signal_source_id,
        )
        return {"status": "ok", "source": ai_signal_source_payload(source), "hot_reloaded": True}

    @protected_router.delete("/ai-signal-sources/{signal_source_id}")
    async def delete_ai_signal_source(signal_source_id: str, user: AuthUser = Depends(require_auth)) -> Dict:
        try:
            ai_signal_source_repo.delete(user.user_id, signal_source_id)
            return {"status": "ok"}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @protected_router.get("/llm/runtime-shares")
    async def get_shared_ai_runtime_data(
        symbol: Optional[str] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        items = enrich_shared_ai_items(
            shared_ai_runtime_repo.list_shared(user.user_id),
            user.user_id, [symbol] if symbol else [],
        )
        return {"status": "ok", "count": len(items), "items": items}

    @protected_router.get("/llm/market-view")
    async def get_ai_market_view(
        symbol: Optional[str] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Aggregate user-scoped AI market sources."""
        access = llm_access_repo.get_status(user.user_id, user.role)
        effective_config = llm_config_repo.get_effective_config(user.user_id)
        access = {
            **access,
            "service_configured": effective_config.enabled,
            "feature_enabled": (
                access["access_granted"] and effective_config.enabled
            ),
        }
        own_cards = []
        reported_symbols = set()
        engine = engine_manager.get_market_engine(user.user_id)
        for source in ai_signal_source_repo.list(user.user_id):
            if symbol and source.get("symbol") != symbol:
                continue
            reported_symbols.update(engine.kline_service.get_symbols())
            analysis = engine.get_llm_analysis(source.get("symbol")) or {}
            card = engine._independent_ai_market_card(source, analysis)
            card["linked_strategies"] = engine._linked_ai_strategies(
                str(source.get("signal_source_id") or "")
            )
            card["market_data_account"] = {
                "account_id": 0,
                "account_name": "用户共享行情",
                "mt5_server": "",
            }
            own_cards.append(card)
        shared_cards = []
        for item in enrich_shared_ai_items(
            shared_ai_runtime_repo.list_shared(user.user_id),
            user.user_id, [], "",
        ):
            if item["is_owner"]:
                continue
            result = item.get("result") or {}
            trend = (result.get("trend_analysis") or {}).get(item["period"]) or {}
            suggestion = max(
                result.get("trade_suggestions") or [],
                key=lambda value: int(value.get("confidence", 0) or 0),
                default=None,
            )
            direction_value = str(
                (suggestion or {}).get("direction") or trend.get("trend") or ""
            ).lower()
            direction = (
                "up" if direction_value in {"up", "buy", "bullish", "上涨", "看涨"}
                else "down" if direction_value in {"down", "sell", "bearish", "下跌", "看跌"}
                else "sideways"
            )
            confidence = int(
                (suggestion or {}).get("confidence")
                or trend.get("confidence")
                or 0
            )
            if item["recommended_symbols"]:
                applicability = "推荐适用于 " + "、".join(item["recommended_symbols"])
            elif item["similar_symbols"]:
                applicability = "相似候选 " + "、".join(item["similar_symbols"])
            else:
                applicability = "可作为共享行情参考"
            shared_cards.append({
                **item,
                "card_id": item["share_id"],
                "direction": direction,
                "confidence": confidence,
                "status": "shared_reference",
                "status_reason": f"由平台用户主动共享；{applicability}",
                "trend": trend,
                "overall_trend": result.get("overall_trend"),
                "key_levels": result.get("key_levels"),
                "suggestion": suggestion,
                "analyzed_at": result.get("analyzed_at") or item["last_run_at"],
                "entry_price": float((suggestion or {}).get("entry_price", 0) or 0),
                "stop_loss": float((suggestion or {}).get("stop_loss", 0) or 0),
                "take_profit": float((suggestion or {}).get("take_profit", 0) or 0),
            })
        return {
            "status": "ok",
            "account": {
                "reported_symbols": sorted(reported_symbols),
            },
            "access": access,
            "own": own_cards,
            "shared": shared_cards,
            "summary": {
                "own_count": len(own_cards),
                "actionable_count": sum(
                    card["status"] == "analysis_ready"
                    for card in own_cards
                ),
                "shared_count": len(shared_cards),
                "last_analysis_time": max(
                    (
                        str(card.get("analyzed_at") or "")
                        for card in own_cards
                    ),
                    default="",
                ),
            },
        }

    @protected_router.get("/llm/market-history/{signal_source_id}")
    async def get_ai_market_history(
        signal_source_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """返回当前用户某个 AI 信号源最近的模型调用记录。"""
        source = ai_signal_source_repo.get(user.user_id, signal_source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="AI 信号源不存在")
        rows = llm_governance.storage.fetchall(
            """
            SELECT call_id, model_id, status, duration_ms, error_message,
                   result_summary, created_at, completed_at
            FROM llm_call_logs
            WHERE user_id = ? AND scene_code = ?
              AND object_type = 'ai_market_analysis'
              AND FIND_IN_SET(?, object_id) > 0
            ORDER BY created_at DESC LIMIT 5
            """,
            (user.user_id, AI_SIGNAL_ANALYSIS, signal_source_id),
        )
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["result"] = json.loads(item.pop("result_summary") or "")
            except (TypeError, json.JSONDecodeError):
                item["result"] = None
                item.pop("result_summary", None)
            items.append(item)
        return {"status": "ok", "items": items}

    @protected_router.get("/llm/market-suggestions/{signal_source_id}")
    async def get_ai_market_suggestions(
        signal_source_id: str,
        limit: int = Query(10, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return private, durable AI trade plans for one owned signal source."""
        source = ai_signal_source_repo.get(user.user_id, signal_source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="AI 信号源不存在")
        items = AITradeSuggestionRepository().list_recent(
            user.user_id, signal_source_id, limit,
        )
        return {"status": "ok", "items": items}

    @protected_router.post("/llm/access/request")
    async def request_llm_access(user: AuthUser = Depends(require_auth)) -> Dict:
        access = llm_access_repo.request_access(user.user_id, user.role)
        return {
            "status": "ok",
            "message": (
                "申请已提交，请等待管理员审批"
                if access["status"] == "pending"
                else "大模型行情分析功能已开通"
            ),
            "access": access,
        }

    @protected_router.get("/admin/llm/access-requests")
    async def get_llm_access_requests(
        status: Optional[str] = None,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        requests = llm_access_repo.list_requests(status)
        return {
            "status": "ok",
            "count": len(requests),
            "requests": requests,
        }

    @protected_router.post("/admin/llm/access-requests/{request_id}/review")
    async def review_llm_access_request(
        request_id: int,
        request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        data = await request.json()
        try:
            reviewed = llm_access_repo.review(
                request_id,
                user.user_id,
                str(data.get("decision", "")),
                str(data.get("note", "")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if reviewed is None:
            raise HTTPException(status_code=404, detail="开通申请不存在")
        return {
            "status": "ok",
            "message": "审批已完成",
            "access": reviewed,
        }

    @protected_router.get("/llm/analysis")
    async def get_llm_analysis(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取大模型分析结果"""
        require_llm_access(user)
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        result = engine.get_llm_analysis(symbol)
        return {
            "status": "ok",
            "data": result
        }

    @protected_router.get("/llm/status")
    async def get_llm_status(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取大模型分析器状态"""
        access = llm_access_repo.get_status(user.user_id, user.role)
        if not access["access_granted"]:
            return {
                "status": "ok",
                "data": {
                    "enabled": False,
                    "access_status": access["status"],
                    "analysis_status": "disabled",
                    "analysis_message": "大模型行情分析功能尚未开通",
                },
            }
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        status_data = engine.get_llm_status()
        status_data["access_status"] = access["status"]
        return {
            "status": "ok",
            "data": status_data,
        }

    @protected_router.get("/llm/config")
    async def get_llm_config(user: AuthUser = Depends(require_admin)) -> Dict:
        """管理员获取共享大模型配置。"""
        return {
            "status": "ok",
            "config": llm_config_repo.get_config(user.user_id).to_dict(),
            "governance": llm_governance.overview(),
        }

    @protected_router.get("/llm/scenes/{scene_code}")
    async def get_llm_scene_options(
        scene_code: str, user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            return {
                "status": "ok",
                "scene": llm_governance.scene_options(user.user_id, scene_code),
            }
        except LLMGovernanceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.post("/admin/llm/models/sync")
    async def sync_llm_models(user: AuthUser = Depends(require_admin)) -> Dict:
        try:
            models = llm_governance.sync_models()
            add_audit_event(
                user, "llm_models_synced", "同步大模型列表",
                f"同步到 {len(models)} 个模型",
            )
            return {
                "status": "ok",
                "message": f"已同步 {len(models)} 个模型",
                "governance": llm_governance.overview(),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.put("/admin/llm/models/{model_id:path}")
    async def update_llm_model(
        model_id: str, request: Request, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        try:
            data = await request.json()
            llm_governance.set_model_enabled(model_id, bool(data.get("enabled")))
            add_audit_event(
                user, "llm_model_status_changed", "修改大模型状态",
                f"模型 {model_id} 已{'启用' if data.get('enabled') else '停用'}",
                {"enabled": bool(data.get("enabled"))}, "llm_model", model_id,
            )
            return {
                "status": "ok",
                "governance": llm_governance.overview(),
            }
        except LLMGovernanceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.put("/admin/llm/providers")
    async def save_llm_provider(
        request: Request, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        try:
            data = await request.json()
            provider = llm_config_repo.save_provider_config(
                user.user_id,
                provider_id=data.get("provider_id"),
                provider_name=data.get("provider_name"),
                api_key=data.get("api_key") if data.get("api_key") else None,
                api_base=data.get("api_base"),
                model=data.get("model"),
                active=bool(data.get("active", False)),
            )
            config = llm_config_repo.get_config(user.user_id)
            if provider["active"]:
                # Provider changes can invalidate the old catalog. Repair all
                # current model references when the new catalog is already
                # known; otherwise sync_models will complete the migration.
                llm_governance.last_reconciliation = llm_governance.reconcile_model_references(preferred_model=config.model)
                engine = engine_manager.get_engine_for_user(user.user_id)
                engine.configure_llm(
                    api_key=config.api_key,
                    api_base=config.api_base,
                    model=config.model,
                )
            add_audit_event(
                user, "llm_provider_saved", "保存大模型供应商",
                f"保存供应商 {provider['provider_name']}",
                {
                    "provider_id": provider["provider_id"],
                    "active": provider["active"],
                    "api_base": provider["api_base"],
                    "model": provider["model"],
                },
                "llm_provider", provider["provider_id"],
            )
            return {
                "status": "ok",
                "provider": provider,
                "governance": llm_governance.overview(),
                "config": config.to_dict(),
            }
        except (ValueError, LLMGovernanceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.post("/admin/llm/providers/{provider_id}/activate")
    async def activate_llm_provider(
        provider_id: str, user: AuthUser = Depends(require_admin),
    ) -> Dict:
        try:
            provider = llm_config_repo.set_active_provider(
                user.user_id, provider_id
            )
            config = llm_config_repo.get_config(user.user_id)
            engine = engine_manager.get_engine_for_user(user.user_id)
            llm_governance.last_reconciliation = llm_governance.reconcile_model_references(preferred_model=config.model)
            engine.configure_llm(
                api_key=config.api_key,
                api_base=config.api_base,
                model=config.model,
            )
            add_audit_event(
                user, "llm_provider_activated", "切换大模型供应商",
                f"切换有效供应商为 {provider['provider_name']}",
                {
                    "provider_id": provider["provider_id"],
                    "api_base": provider["api_base"],
                    "model": provider["model"],
                },
                "llm_provider", provider["provider_id"],
            )
            return {
                "status": "ok",
                "provider": provider,
                "governance": llm_governance.overview(),
                "config": config.to_dict(),
            }
        except (ValueError, LLMGovernanceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.put("/admin/llm/scenes/{scene_code}")
    async def update_llm_scene(
        scene_code: str, request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        try:
            scene = llm_governance.save_scene(
                scene_code, await request.json(), user.user_id
            )
            add_audit_event(
                user, "llm_scene_updated", "修改大模型场景",
                f"修改场景 {scene['display_name']}",
                {
                    "models": scene["model_ids"],
                    "enabled": scene["enabled"],
                    "prompt_configured": bool(
                        scene.get("system_prompt")
                        and scene.get("user_prompt_template")
                    ),
                },
                "llm_scene", scene_code,
            )
            return {"status": "ok", "scene": scene}
        except LLMGovernanceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @protected_router.post("/llm/trigger")
    async def trigger_llm_analysis(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """手动触发大模型分析"""
        require_llm_access(user)
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        return engine.trigger_llm_analysis()

    @protected_router.post("/llm/configure")
    async def configure_llm(request: Request, user: AuthUser = Depends(require_admin)) -> Dict:
        """管理员配置共享大模型参数。"""
        try:
            data = await request.json()
            provider = llm_config_repo.save_provider_config(
                user.user_id,
                provider_id=data.get("provider_id"),
                provider_name=data.get("provider_name") or "默认供应商",
                api_key=data.get("api_key") if data.get("api_key") else None,
                api_base=data.get("api_base"),
                model=data.get("model"),
                active=bool(data.get("active", True)),
            )
            config = llm_config_repo.save_config(
                user.user_id,
                system_prompt=data.get("system_prompt"),
                analysis_prompt_template=data.get("analysis_prompt_template"),
            )
            result = {
                "status": "ok",
                "enabled": config.enabled,
                "model": config.model,
                "api_base": config.api_base,
            }

            if provider["active"]:
                llm_governance.last_reconciliation = llm_governance.reconcile_model_references(preferred_model=data.get("model") or config.model)
                engine = engine_manager.get_engine_for_user(user.user_id)
                result = engine.configure_llm(
                    api_key=data.get("api_key"),
                    api_base=data.get("api_base"),
                    model=data.get("model"),
                    system_prompt=data.get("system_prompt"),
                    analysis_prompt_template=data.get("analysis_prompt_template"),
                )
            add_audit_event(
                user, "llm_provider_configured", "修改大模型服务配置",
                "管理员更新了大模型服务配置",
                {"changed_fields": sorted(data.keys()), "api_base": config.api_base,
                 "model": config.model, "provider_id": provider["provider_id"]},
                "llm_provider", provider["provider_id"],
            )
            return {
                "status": "ok",
                "data": result,
                "provider": provider,
                "governance": llm_governance.overview(),
                "config": config.to_dict(),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @protected_router.post("/llm/config/reset-prompts")
    async def reset_llm_prompts(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """管理员恢复系统内置提示词。"""
        config = llm_config_repo.reset_prompts(user.user_id)
        engine = engine_manager.get_engine_for_user(user.user_id)
        engine.configure_llm(
            system_prompt=config.system_prompt,
            analysis_prompt_template=config.analysis_prompt_template,
        )
        return {
            "status": "ok",
            "message": "提示词已恢复为系统默认值",
            "config": config.to_dict(),
        }

    router.include_router(protected_router)
    return router
