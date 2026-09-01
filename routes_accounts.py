#!/usr/bin/env python3
"""统一交易账户管理接口。"""

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import AuthUser, require_auth
from membership import MembershipService
from market.services.account_strategy_performance import build_live_performance
from mysql_repositories import (
    TradingAccountRecord,
)
from repositories.accounts import TradingAccountRepository
from repositories.strategy_config import StrategyConfigRepository
from repositories.trade_config import TradeConfigRepository
from repositories.trading import (
    LiveTradeDealRepository, PositionManagementEventRepository, TradeExecutionRepository,
)
from trading_engine_manager import TradingEngineManager
from market_data_source_policy import MarketDataSourcePolicy


def create_account_routes(engine_manager: TradingEngineManager) -> APIRouter:
    router = APIRouter()
    repositories = engine_manager.repositories
    repository = repositories.accounts
    strategy_repository = repositories.strategies
    trade_config_repository = repositories.trade_config
    memberships = MembershipService()
    market_source_policy = MarketDataSourcePolicy()

    def deployment_warnings(user_id: int, account_id: int, strategy_id: str) -> List[str]:
        """Non-blocking preflight for a deployment's quote binding."""
        account = repository.get_by_id(user_id, account_id)
        strategy = strategy_repository.get_strategy_by_id(user_id, strategy_id)
        if account is None or strategy is None:
            return []
        now = int(time.time())
        symbol = str(strategy.symbol or "").strip()
        rows = repository.storage.fetchall(
            "SELECT symbol, MAX(updated_at) AS updated_at FROM historical_klines "
            "WHERE user_id = ? AND updated_at >= ? GROUP BY symbol ORDER BY updated_at DESC",
            (int(user_id), now - 15 * 60),
        )
        fresh = {str(row["symbol"]): int(row["updated_at"] or 0) for row in rows}
        warnings = []
        market_status = market_source_policy.account_status(user_id, account_id)
        if market_status.get("mode") == "blocked":
            warnings.append(market_status.get("message") or "该账户存在跨交易商行情冲突")
        if symbol not in fresh:
            reported = "、".join(list(fresh)[:6]) or "暂无最近15分钟K线"
            warnings.append(
                f"策略品种「{symbol}」没有匹配的实时行情；最近上报品种：{reported}。"
                "部署仍可继续，但策略不会产生订单，直到品种一致或建立映射。"
            )
        if account.account_type == "mt5" and (not account.last_seen_at or now - int(account.last_seen_at) > 180):
            warnings.append("目标 MT5 账户超过3分钟未心跳，实盘部署后暂时不会接收交易指令。")
        return warnings

    @router.get("/accounts")
    async def list_accounts(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        accounts = repository.list_for_user(user.user_id)
        trade_config_enabled = bool(
            trade_config_repository.get_config(user.user_id).get("enabled", True)
        )
        payloads = []
        for account in accounts:
            # list_deployments also expires timed-out paper deployments before
            # we derive the account's runtime state from them.
            deployments = engine_manager.paper_trading.list_deployments(
                user.user_id, account.account_id
            )
            account_payload = _account_payload(account, deployments)
            for deployment in deployments:
                deployment["runtime_active"] = bool(
                    deployment.get("status") == "active"
                    and trade_config_enabled
                    and account.status == "active"
                    and account.enabled
                    and account.trading_enabled
                    and account.auto_trading_enabled
                    and (
                        deployment.get("execution_mode") == "paper"
                        or account_payload["connected"]
                    )
                )
            payloads.append({
                **account_payload,
                "deployments": deployments,
                "market_source": (
                    market_source_policy.account_status(
                        user.user_id, account.account_id,
                    ) if account.account_type == "mt5" else None
                ),
            })
        # 账户页以运行中的策略为首要排序依据；无运行策略的账户放在后面。
        # 同组内优先显示在线账户，再按最近更新时间倒序。
        payloads.sort(key=lambda item: (
            -int(any(str(d.get("status")) == "active" for d in (item.get("deployments") or []))),
            -int(bool(item.get("connected"))),
            -int(item.get("last_seen_at") or item.get("financial_updated_at") or item.get("created_at") or 0),
        ))
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
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        equity_from: Optional[int] = Query(None, ge=0),
        equity_to: Optional[int] = Query(None, ge=0),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            detail = engine_manager.paper_trading.get_account_detail(
                user.user_id, account_id, page=page, page_size=page_size,
                equity_from=equity_from, equity_to=equity_to,
            )
            return {"status": "ok", "detail": detail}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/live-monitoring")
    async def get_live_monitoring(
        account_id: int,
        equity_from: Optional[int] = Query(None, ge=0),
        equity_to: Optional[int] = Query(None, ge=0),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None or account.account_type != "mt5":
            raise HTTPException(status_code=404, detail="MT5 实盘账户不存在")

        engine = engine_manager.get_engine(user.user_id, account_id)
        positions = engine.position_service.get_positions()
        events = repositories.position_events
        for position in positions:
            position["management_events"] = events.list_for_position(
                user.user_id, account_id, str(position.get("ticket", "")),
            )
        execution_reports = repositories.trade_execution.list_for_account(
            user.user_id, account_id, 100,
        )
        trades = LiveTradeDealRepository(repositories.storage).list_for_account(
            user.user_id, account_id, 20,
        )
        # 成交回报通过 MT5 deal/order/position 与策略指令关联。老 EA 没有
        # position_id 时仍可用 deal/order 关联，不影响历史展示。
        by_deal = {int(r.get("mt5_deal", 0) or 0): r for r in execution_reports}
        by_order = {int(r.get("mt5_order", 0) or 0): r for r in execution_reports}
        by_position = {int(r.get("mt5_position_id", 0) or 0): r for r in execution_reports}
        for trade in trades:
            attribution = trade.get("position_attribution") or {}
            trade["time"] = trade.get("deal_time") or ""
            trade["type"] = int(trade.get("deal_type", 0) or 0)
            trade["type_text"] = (
                "买入" if int(trade.get("deal_type", 0) or 0) == 0 else "卖出"
            )
            trade["entry_text"] = {
                0: "开仓", 1: "平仓", 2: "反向成交", 3: "对锁平仓",
            }.get(int(trade.get("entry_type", 0) or 0), "成交")
            trade["order_source"] = (
                "策略指令" if attribution else (trade.get("comment") or "MT5 成交")
            )
            trade["setup_type"] = attribution.get("setup_type", "")
            trade["setup_profile_name"] = attribution.get("setup_profile_name", "")
            trade["open_reason"] = attribution.get("entry_reason", "")
            trade["close_reason"] = attribution.get("exit_reason", "")
            trade["initial_stop_loss"] = float(
                attribution.get("initial_stop_loss") or 0
            )
            trade["initial_take_profit"] = float(
                attribution.get("initial_take_profit") or 0
            )
            trade["realized_r"] = float(attribution.get("realized_r") or 0)
            report = (
                by_deal.get(int(trade.get("ticket", 0) or 0))
                or by_order.get(int(trade.get("mt5_order", 0) or 0))
                or by_position.get(int(trade.get("mt5_position_id", 0) or 0))
            )
            if report:
                trade["strategy_triggered"] = True
                trade["execution_report_id"] = report.get("id")
                trade["instruction_id"] = report.get("instruction_id", "")
                trade["execution_reason"] = (
                    attribution.get("exit_reason")
                    or attribution.get("entry_reason")
                    or "策略指令已在 MT5 成交"
                )
            else:
                trade["strategy_triggered"] = False
        for report in execution_reports:
            attribution = report.get("position_attribution") or {}
            report["setup_type"] = attribution.get("setup_type", "")
            report["setup_profile_name"] = attribution.get("setup_profile_name", "")
            report["open_reason"] = attribution.get("entry_reason", "")
            report["initial_stop_loss"] = float(
                attribution.get("initial_stop_loss") or 0
            )
            report["initial_take_profit"] = float(
                attribution.get("initial_take_profit") or 0
            )
        return {
            "status": "ok",
            "detail": {
                "account": _account_payload(account),
                "positions": positions,
                "trades": trades,
                "execution_reports": execution_reports,
                "strategy_performance": build_live_performance(
                    repository.storage, user.user_id, account_id, positions,
                ),
                "equity_curve": repository.list_live_equity_points(
                    user.user_id, account_id, from_time=equity_from, to_time=equity_to,
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
            market_status = market_source_policy.account_status(
                user.user_id, account_id,
            )
            if market_status.get("mode") == "blocked":
                raise ValueError(
                    market_status.get("message") or "该实盘账户存在行情冲突，不能部署策略"
                )
            deployment = engine_manager.paper_trading.deploy(
                user.user_id,
                account_id,
                str(payload.get("strategy_id", "")).strip(),
            )
            return {
                "status": "ok",
                "message": "策略已绑定到交易账户",
                "deployment": deployment,
                "warnings": deployment_warnings(user.user_id, account_id, str(payload.get("strategy_id", "").strip())),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/deployments/preflight")
    async def deployment_preflight(
        account_id: int,
        strategy_id: str = Query(...),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        return {
            "status": "ok",
            "warnings": deployment_warnings(user.user_id, account_id, strategy_id.strip()),
        }

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
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            all_deployments = engine_manager.paper_trading.list_deployments(
                user.user_id, account_id
            )
            start = (page - 1) * page_size
            deployments = all_deployments[start:start + page_size]
            return {"status": "ok", "deployments": deployments,
                    "total": len(all_deployments), "page": page,
                    "page_size": page_size,
                    "has_more": len(all_deployments) > start + page_size}
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
