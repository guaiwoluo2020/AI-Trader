#!/usr/bin/env python3
"""统一交易账户管理接口。"""

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import AuthUser, require_auth
from membership import MembershipService
from sqlite_storage import (
    PositionManagementEventRepository,
    StrategyConfigRepository,
    TradeExecutionRepository,
    TradingAccountRecord,
    TradingAccountRepository,
)
from trading_engine_manager import TradingEngineManager


def create_account_routes(engine_manager: TradingEngineManager) -> APIRouter:
    router = APIRouter()
    repository = TradingAccountRepository()
    strategy_repository = StrategyConfigRepository()
    memberships = MembershipService()

    @router.get("/accounts")
    async def list_accounts(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        accounts = repository.list_for_user(user.user_id)
        payloads = []
        for account in accounts:
            # list_deployments also expires timed-out paper deployments before
            # we derive the account's runtime state from them.
            deployments = engine_manager.paper_trading.list_deployments(
                user.user_id, account.account_id
            )
            payloads.append({
                **_account_payload(account, deployments),
                "deployments": deployments,
            })
        return {
            "status": "ok",
            "count": len(accounts),
            "accounts": payloads,
        }

    @router.post("/accounts/paper")
    async def create_paper_account(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            paper_count = sum(
                account.account_type == "paper"
                for account in repository.list_for_user(user.user_id)
            )
            paper_limit = memberships.paper_account_limit(user.user_id)
            if paper_limit is not None and paper_count >= paper_limit:
                raise ValueError(
                    f"当前会员等级最多创建 {paper_limit} 个模拟账户"
                )
            account = repository.create_paper_account(
                user.user_id,
                account_name=payload.get("account_name", ""),
                initial_balance=payload.get("initial_balance", 100000),
                currency=payload.get("currency", "USD"),
                leverage=payload.get("leverage", 100),
                spread_points=payload.get("spread_points", 0),
                slippage_points=payload.get("slippage_points", 0),
                commission_per_lot=payload.get("commission_per_lot", 0),
            )
            return {
                "status": "ok",
                "message": "Paper 模拟账户已创建，可以部署策略开始模拟运行",
                "account": _account_payload(account),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/accounts/{account_id}")
    async def update_account(
        account_id: int,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            account = repository.update_controls(
                user.user_id,
                account_id,
                account_name=payload.get("account_name"),
                trading_enabled=payload.get("trading_enabled"),
                auto_trading_enabled=payload.get("auto_trading_enabled"),
                max_total_positions=payload.get("max_total_positions"),
                max_single_volume=payload.get("max_single_volume"),
                daily_loss_limit=payload.get("daily_loss_limit"),
                daily_order_limit=payload.get("daily_order_limit"),
            )
            return {
                "status": "ok",
                "message": "账户配置已更新",
                "account": _account_payload(account),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accounts/{account_id}/archive")
    async def archive_account(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            account = repository.set_archived(user.user_id, account_id, True)
            return {
                "status": "ok", "message": "账户已归档",
                "account": _account_payload(account),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accounts/{account_id}/restore")
    async def restore_account(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            account = repository.set_archived(user.user_id, account_id, False)
            return {
                "status": "ok", "message": "账户已恢复，请按需检查策略部署状态",
                "account": _account_payload(account),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/paper/context")
    async def get_paper_context(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        return {
            "status": "ok",
            **engine_manager.paper_trading.list_context(user.user_id),
        }

    @router.get("/accounts/{account_id}/paper")
    async def get_paper_account(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            detail = engine_manager.paper_trading.get_account_detail(
                user.user_id, account_id
            )
            return {"status": "ok", "detail": detail}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/live-monitoring")
    async def get_live_monitoring(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None or account.account_type != "mt5":
            raise HTTPException(status_code=404, detail="MT5 实盘账户不存在")

        engine = engine_manager.get_engine(user.user_id, account_id)
        positions = engine.position_service.get_positions()
        events = PositionManagementEventRepository()
        for position in positions:
            position["management_events"] = events.list_for_position(
                user.user_id, account_id, str(position.get("ticket", "")),
            )
        return {
            "status": "ok",
            "detail": {
                "account": _account_payload(account),
                "positions": positions,
                "trades": engine.trade_history_service.get_deals()[:100],
                "execution_reports": TradeExecutionRepository().list_for_account(
                    user.user_id, account_id, 100,
                ),
                "equity_curve": repository.list_live_equity_points(
                    user.user_id, account_id,
                ),
            },
        }

    @router.get("/accounts/{account_id}/paper/report")
    async def get_paper_report(
        account_id: int,
        strategy_id: str = Query(default=""),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            report = engine_manager.paper_trading.build_report(
                user.user_id, account_id, strategy_id.strip()
            )
            return {"status": "ok", "report": report}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/accounts/{account_id}/deployments")
    async def deploy_strategy(
        account_id: int,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            deployment = engine_manager.paper_trading.deploy(
                user.user_id,
                account_id,
                str(payload.get("strategy_id", "")).strip(),
            )
            return {
                "status": "ok",
                "message": "策略已绑定到交易账户",
                "deployment": deployment,
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/accounts/{account_id}/deployments/backtest")
    async def deploy_backtest_strategy(
        account_id: int,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            deployment = engine_manager.paper_trading.deploy_backtest(
                user.user_id,
                account_id,
                str(payload.get("task_id", "")).strip(),
                int(payload.get("duration_days", 30)),
            )
            return {
                "status": "ok",
                "message": "回测报告已关联到模拟账户，策略开始模拟运行",
                "deployment": deployment,
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.patch("/accounts/{account_id}/deployments/{deployment_id}")
    async def set_deployment_status(
        account_id: int,
        deployment_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            deployment = engine_manager.paper_trading.set_deployment_status(
                user.user_id,
                account_id,
                deployment_id,
                bool(payload.get("active", False)),
            )
            if deployment is None:
                raise HTTPException(status_code=404, detail="策略部署不存在")
            return {
                "status": "ok",
                "message": "策略运行状态已更新",
                "deployment": deployment,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/deployments")
    async def list_account_deployments(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            deployments = engine_manager.paper_trading.list_deployments(
                user.user_id, account_id
            )
            return {"status": "ok", "deployments": deployments}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/accounts/{account_id}/deployments/{deployment_id}/end")
    async def end_account_deployment(
        account_id: int,
        deployment_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            account = repository.get_by_id(user.user_id, account_id)
            if account is None:
                raise ValueError("交易账户不存在")
            deployment = next((item for item in engine_manager.paper_trading.list_deployments(
                user.user_id, account_id
            ) if item["deployment_id"] == deployment_id), None)
            if deployment is None:
                raise HTTPException(status_code=404, detail="策略部署不存在")
            if account.account_type == "paper":
                strategy = strategy_repository.get_strategy_by_id(
                    user.user_id, deployment["strategy_id"]
                )
                if strategy and strategy.lifecycle_status == "production":
                    raise ValueError(
                        "策略仍处于实盘阶段，请先结束实盘部署并在生命周期中回退到模拟盘验证"
                    )
            ended = engine_manager.paper_trading.end_deployment(
                user.user_id, account_id, deployment_id
            )
            return {
                "status": "ok",
                "message": "策略部署已结束，历史订单和报告已保留",
                "deployment": ended,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/accounts/{account_id}/deployments/{deployment_id}")
    async def remove_account_deployment(
        account_id: int,
        deployment_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            account = repository.get_by_id(user.user_id, account_id)
            if account is None:
                raise ValueError("交易账户不存在")
            if account.account_type == "paper":
                raise ValueError("模拟盘部署请暂停运行，以保留历史报告")
            removed = engine_manager.paper_trading.remove_deployment(
                user.user_id, account_id, deployment_id
            )
            if not removed:
                raise HTTPException(status_code=404, detail="策略绑定不存在")
            return {"status": "ok", "message": "策略已从该账户解绑"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def _account_payload(
    account: TradingAccountRecord, deployments: Optional[List[Dict]] = None,
) -> Dict:
    connected = bool(
        account.account_type == "mt5"
        and account.last_seen_at
        and int(time.time()) - account.last_seen_at <= 120
    )
    active_deployment_count = 0
    if account.account_type == "paper":
        active_deployment_count = sum(
            item.get("status") == "active"
            and item.get("execution_mode") == "paper"
            for item in deployments or []
        )
        active = bool(
            account.status == "active"
            and account.enabled
            and account.trading_enabled
            and account.auto_trading_enabled
            and active_deployment_count
        )
    else:
        active = bool(account.status == "active" and connected)
    activity_status = (
        "archived" if account.status == "archived"
        else "paused" if not account.trading_enabled
        else "active" if active
        else "inactive"
    )
    return {
        "account_id": account.account_id,
        "account_key": account.account_key,
        "account_name": account.account_name,
        "account_type": account.account_type,
        "environment": account.environment,
        "currency": account.currency,
        "initial_balance": account.initial_balance,
        "balance": account.balance,
        "equity": account.equity,
        "free_margin": account.free_margin,
        "margin": account.margin,
        "status": account.status,
        "enabled": account.enabled,
        "trading_enabled": account.trading_enabled,
        "auto_trading_enabled": account.auto_trading_enabled,
        "max_total_positions": account.max_total_positions,
        "max_single_volume": account.max_single_volume,
        "daily_loss_limit": account.daily_loss_limit,
        "daily_order_limit": account.daily_order_limit,
        "archived_at": account.archived_at,
        "is_default": (
            account.account_key == TradingAccountRepository.DEFAULT_ACCOUNT_KEY
        ),
        "connected": connected,
        "active": active,
        "active_deployment_count": active_deployment_count,
        "activity_status": activity_status,
        "last_seen_at": account.last_seen_at,
        "financial_updated_at": account.financial_updated_at,
        "mt5_login": account.mt5_login,
        "mt5_server": account.mt5_server,
        "ea_version": account.ea_version,
        "created_at": account.created_at,
        "engine_status": (
            "connected" if connected else "offline"
        ) if account.account_type == "mt5" else (
            "running" if active else "ready"
        ),
    }
