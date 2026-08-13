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
import asyncio
import json

from auth import AuthUser, get_auth_manager, require_admin, require_auth
from ea_auth import EAIdentity, require_ea_auth
from market.services import PivotService
from market.models.llm_config import (
    DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
    DEFAULT_SYSTEM_PROMPT,
)
from llm_governance import AI_SIGNAL_ANALYSIS, LLMGovernanceError, LLMGovernanceService
from membership import MembershipService
from sqlite_storage import (
    LLMAccessRepository,
    LLMConfigRepository,
    SharedAIRuntimeRepository,
    PositionManagementPolicyRepository,
    StrategyConfigRepository,
    TradeConfigRepository,
    TradingAccountRepository,
)
from trading_engine_manager import TradingEngineManager
from strategy_admission import StrategyAdmissionService
from web_account_context import resolve_web_engine
from user_quotas import UserQuotaService
from alpha_research import AlphaLibraryRepository
from system_event_log import SystemEventLogRepository
from market.system_log import get_system_log_broadcaster
from data_retention import DataRetentionService


def create_market_routes(
    engine_manager: TradingEngineManager,
) -> APIRouter:
    """创建按当前用户或 EA 动态解析引擎的行情路由。"""
    router = APIRouter()
    protected_router = APIRouter(dependencies=[Depends(require_auth)])

    trade_config_repo = TradeConfigRepository()
    strategy_repo = StrategyConfigRepository()
    position_policy_repo = PositionManagementPolicyRepository()
    llm_config_repo = LLMConfigRepository()
    llm_access_repo = LLMAccessRepository()
    shared_ai_runtime_repo = SharedAIRuntimeRepository()
    quota_service = UserQuotaService()
    admission_service = StrategyAdmissionService(engine_manager.paper_trading)
    alpha_library = AlphaLibraryRepository()
    llm_governance = LLMGovernanceService()
    event_logs = SystemEventLogRepository()
    data_retention = DataRetentionService()
    memberships = MembershipService()

    def strategy_payload(strategy) -> Dict:
        payload = strategy.to_dict()
        payload["readonly_reference"] = bool(strategy.source_owner_user_id)
        if payload["readonly_reference"]:
            for source in payload.get("signal_sources") or []:
                source["params"] = {}
        return payload

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

    def bind_alpha_signal_snapshots(user: AuthUser, data: Dict) -> None:
        """Resolve validated Alpha definitions server-side and pin their version."""
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
                "alpha_version": int(alpha.get("version") or 1),
                "alpha_name": alpha.get("name") or "Validated Alpha",
                "alpha_snapshot": definition,
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
            mode = params.get("analysis_mode", "self_analysis")
            if mode == "shared_reference":
                share_id = str(params.get("shared_runtime_id") or "").strip()
                shared = shared_ai_runtime_repo.get_shared(share_id)
                if shared is None or shared["owner_user_id"] == user.user_id:
                    raise ValueError("选择的共享AI运行数据不存在或已取消共享")
                if str(source.get("period", "")).upper() != shared["period"]:
                    raise ValueError("共享引用信号的周期必须与共享数据周期一致")
                continue
            if not access["access_granted"]:
                raise ValueError("自主AI分析仅对已开通大模型分析的付费用户开放")
            options = llm_governance.scene_options(
                user.user_id, AI_SIGNAL_ANALYSIS
            )
            if str(params.get("model") or "") not in options["models"]:
                raise ValueError("AI入场信号选择的模型不在管理员开放列表中")
            for share_id in params.get("reference_runtime_ids") or []:
                shared = shared_ai_runtime_repo.get_shared(str(share_id))
                if shared is None:
                    raise ValueError("选择的共享AI运行数据不存在或已取消共享")

    # ==================== EA端接口 ====================

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
                    print(f"[MarketAPI] {symbol} {period} 数据不连续，缺失 {continuity['gap_count']} 个周期")
                    return JSONResponse(
                        status_code=400,
                        content={
                            "status": "error",
                            "code": 8888,
                            "message": f"数据不连续，缺失 {continuity['gap_count']} 个周期，需要全量数据"
                        }
                    )

            # 保存K线数据
            result = kline_service.process_kline_data(symbol, period, klines, is_full)
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
        engine = engine_manager.get_engine_for_user(user.user_id)
        period = period.upper()
        klines = engine.kline_service.get_klines(symbol, period, count)

        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "data": klines
        }

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
        """获取用户可配置的品种，不依赖行情是否仍在内存中。"""
        _, engine = resolve_web_engine(engine_manager, user, account_id)
        market_symbols = engine.kline_service.get_symbols()
        config = trade_config_repo.get_config(user.user_id)
        configured_symbols = list(config.get("symbol_config", {}).keys())
        strategy_symbols = [
            strategy.symbol
            for strategy in strategy_repo.get_all_strategies(user.user_id)
        ]
        symbols = sorted(set(
            market_symbols + configured_symbols + strategy_symbols
        ))
        return {
            "status": "ok",
            "symbols": symbols,
            "count": len(symbols),
            "market_symbols": market_symbols,
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
        try:
            data = await request.json()
            policy.name = str(data.get("name", policy.name)).strip()
            if not policy.name:
                raise ValueError("持仓管理方案名称不能为空")
            policy.enabled = bool(data.get("enabled", policy.enabled))
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
            "strategies": [strategy_payload(s) for s in strategies],
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
                "strategy": strategy_payload(strategy),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @protected_router.get("/strategy/decisions")
    async def get_decisions(
        symbol: Optional[str] = None,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        count: int = Query(50, ge=1, le=200),
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

    @protected_router.get("/strategy/shared")
    async def get_shared_strategies(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """获取平台共享策略库；共享内容只允许引用，不暴露机密配置。"""
        strategies = strategy_repo.list_shared_strategies(user.user_id)
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

        policy_id = str(data.get("position_management_policy_id", "")).strip()
        if policy_id and not position_policy_repo.get(user.user_id, policy_id):
            return {"status": "error", "message": "请选择有效的持仓管理方案"}
        if not policy_id:
            policy = next(
                iter(position_policy_repo.list(user.user_id, enabled_only=True)),
                None,
            )
            if policy is None:
                return {
                    "status": "error",
                    "message": "请先创建并启用一个持仓管理方案",
                }
            policy_id = policy.policy_id

        source = strategy_repo.get_strategy_by_id(owner_user_id, strategy_id)
        if source is None or source.visibility != "shared":
            return {"status": "error", "message": "共享策略不存在或未开放使用"}
        try:
            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "strategies")
                quota_service.assert_strategy_sources(
                    user.user_id, user.role, "", source.signal_sources or [],
                )
                strategy = strategy_repo.use_shared_strategy(
                    user.user_id, owner_user_id, strategy_id, policy_id
                )
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if strategy is None:
            return {"status": "error", "message": "共享策略不存在或未开放使用"}

        engine_manager.refresh_user_strategies(user.user_id)
        return {
            "status": "ok",
            "message": "已添加平台策略引用，原作者配置受保护且不可修改",
            "strategy": strategy_payload(strategy),
        }

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
            "strategy": strategy_payload(strategy)
        }

    @protected_router.post("/strategy/{strategy_ref}")
    async def update_strategy(strategy_ref: str, request: Request, user: AuthUser = Depends(require_auth)) -> Dict:
        """按策略 ID 更新；兼容按品种更新第一条策略。"""
        try:
            engine = engine_manager.get_engine_for_user(user.user_id)
            data = await request.json()
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
            add_audit_event(
                user, "strategy_updated", "修改策略",
                f"修改策略 {strategy.strategy_name}",
                {"changed_fields": sorted(data.keys())},
                "strategy", strategy.strategy_id,
            )

            return {
                "status": "ok",
                "message": "策略配置已更新",
                "strategy": strategy_payload(strategy)
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
            add_audit_event(
                user, "strategy_lifecycle_changed", "策略生命周期变更",
                f"策略进入 {strategy.lifecycle_status}",
                {"target_status": target_status, "reason": reason},
                "strategy", strategy.strategy_id,
            )
            return {
                "status": "ok",
                "message": f"策略已进入“{strategy.to_dict()['lifecycle_label']}”状态",
                "strategy": strategy_payload(strategy),
            }
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
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
            success = store.delete_strategy(strategy.symbol, strategy.strategy_id)
            if success:
                shared_ai_runtime_repo.remove_for_strategy(
                    user.user_id, strategy.strategy_id
                )
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

    @protected_router.get("/admin/system/data-maintenance")
    async def get_data_maintenance(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        return {"status": "ok", **data_retention.get_status()}

    @protected_router.post("/admin/system/data-maintenance/run")
    async def run_data_maintenance(
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        result = await asyncio.to_thread(
            data_retention.run_maintenance, "manual"
        )
        event_logs.add({
            "level": "info" if result.get("status") == "completed" else "error",
            "category": "audit", "event_type": "data_maintenance_run",
            "event_name": "执行数据维护", "user_id": user.user_id,
            "actor_type": "user", "actor_id": str(user.user_id),
            "status": result.get("status", "unknown"),
            "message": "管理员手动执行数据维护",
            "detail": {"run_id": result.get("run_id")},
        })
        return {
            "status": result.get("status", "failed"),
            "message": "数据维护执行完成" if result.get("status") == "completed"
            else result.get("error_message") or "数据维护执行失败",
            "run": result,
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
        shared = [
            item for item in shared_ai_runtime_repo.list_shared(
                user.user_id, symbol
            )
            if not item["is_owner"]
        ]
        return {
            "status": "ok",
            "access_granted": access["access_granted"],
            "models": scene_options["models"],
            "default_system_prompt": (
                scene_options.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
            ),
            "default_analysis_prompt_template": (
                scene_options.get("user_prompt_template")
                or DEFAULT_ANALYSIS_PROMPT_TEMPLATE
            ),
            "shared_runtime_data": shared,
        }

    @protected_router.get("/llm/runtime-shares")
    async def get_shared_ai_runtime_data(
        symbol: Optional[str] = None,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        items = shared_ai_runtime_repo.list_shared(user.user_id, symbol)
        return {"status": "ok", "count": len(items), "items": items}

    @protected_router.get("/llm/market-view")
    async def get_ai_market_view(
        account_id: Optional[int] = Query(None),
        symbol: Optional[str] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """聚合当前账户 AI 分析状态和平台共享分析。"""
        account, engine = resolve_web_engine(engine_manager, user, account_id)
        access = llm_access_repo.get_status(user.user_id, user.role)
        effective_config = llm_config_repo.get_effective_config(user.user_id)
        access = {
            **access,
            "service_configured": effective_config.enabled,
            "feature_enabled": (
                access["access_granted"] and effective_config.enabled
            ),
        }
        own_cards = engine.get_ai_market_cards(symbol)
        shared_cards = []
        for item in shared_ai_runtime_repo.list_shared(user.user_id, symbol):
            if item["is_owner"]:
                continue
            result = item.get("result") or {}
            trend = (result.get("trend_analysis") or {}).get(item["period"]) or {}
            suggestion = max(
                result.get("trade_suggestions") or [],
                key=lambda value: int(value.get("confidence", 0) or 0),
                default=None,
            )
            direction = engine._ai_direction(
                (suggestion or {}).get("direction") or trend.get("trend")
            )
            confidence = int(
                (suggestion or {}).get("confidence")
                or trend.get("confidence")
                or 0
            )
            shared_cards.append({
                **item,
                "card_id": item["share_id"],
                "direction": direction,
                "confidence": confidence,
                "status": "shared_reference",
                "status_reason": "由平台用户主动共享，仅作为行情分析参考",
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
                "account_id": account.account_id if account else 0,
                "account_name": account.account_name if account else "",
            },
            "access": access,
            "own": own_cards,
            "shared": shared_cards,
            "summary": {
                "own_count": len(own_cards),
                "actionable_count": sum(
                    card["status"] in {
                        "ready_to_signal", "signal_formed", "decision_created"
                    }
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
