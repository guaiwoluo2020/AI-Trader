#!/usr/bin/env python3
"""统一交易账户管理接口。"""

import json
import time
from datetime import datetime, time as datetime_time
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth import AuthUser, require_auth
from membership import MembershipService
from market.services.decision_brief import build_decision_brief
from market.services.account_strategy_performance import build_live_performance, build_paper_performance
from market.services.today_trade_stats import today_trade_stats
from market.services.pnl_statistics import build_daily_pnl_statistics, query_daily_pnl_statistics
from market.models.trading_strategy import StrategyLifecycle
from market.services.live_strategy_promotion import promotion_candidate_accounts
from mysql_repositories import (
    RuntimeStateRepository,
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
from system_event_log import SystemEventLogRepository
from strategy_admission import StrategyAdmissionService


def _execution_funnel(storage, user_id: int, account_id: int) -> Dict:
    """Return a compact, account-level execution funnel.

    This intentionally aggregates by plan/execution rather than strategy or Tick;
    inactive ``no_direction``/``no_new_trigger`` audits are not persisted and do
    not inflate the result.
    """
    # 漏斗按北京时间自然日统计，而不是滚动的最近 24 小时。
    # 数据库存储的是 Unix 时间戳，因此将北京时间当天 00:00 转成 epoch
    # 后可直接用于 MySQL 查询；夏令时/冬令时由 ZoneInfo 自动处理。
    beijing = ZoneInfo("Asia/Shanghai")
    today_beijing = datetime.now(beijing).date()
    since = int(datetime.combine(today_beijing, datetime_time.min, tzinfo=beijing).timestamp())
    user_id = int(user_id)
    account_id = int(account_id)
    # 结构计划由行情/结构层按用户公共作用域生成（account_id=0）。
    # 先取出本账户部署品种，再统计公共计划，避免对大表逐行 EXISTS。
    deployment_rows = storage.fetchall(
        """
        SELECT DISTINCT symbol, strategy_id
        FROM strategy_deployments
        WHERE user_id=? AND account_id=?
          AND status IN ('active','paused','pending')
        """,
        (user_id, account_id),
    ) or []
    symbols = sorted({
        str(row.get("symbol") or "").strip().upper()
        for row in deployment_rows
        if str(row.get("symbol") or "").strip()
    })
    strategy_ids = sorted({
        str(row.get("strategy_id") or "").strip()
        for row in deployment_rows
        if str(row.get("strategy_id") or "").strip()
    })
    funnel_row = {"plans": 0, "directions": 0}
    if symbols:
        symbol_placeholders = ", ".join("?" for _ in symbols)
        # Public market-structure plans use empty strategy_id. Strategy-scoped
        # rows, if any, still match only when this account deploys that strategy.
        strategy_clause = "p.strategy_id = ''"
        plan_params: list = [user_id, *symbols, since]
        if strategy_ids:
            strategy_placeholders = ", ".join("?" for _ in strategy_ids)
            strategy_clause = (
                f"(p.strategy_id = '' OR p.strategy_id IN ({strategy_placeholders}))"
            )
            plan_params = [user_id, *symbols, *strategy_ids, since]
        funnel_row = storage.fetchone(
            f"""
            SELECT
              COUNT(DISTINCT CASE
                WHEN p.setup_type<>'no_trade' THEN p.plan_id END) AS plans,
              COUNT(DISTINCT CASE
                WHEN p.direction IN ('buy','sell') THEN p.plan_id END) AS directions
            FROM structure_trade_plans p
            WHERE p.user_id=?
              AND p.account_id=0
              AND p.symbol IN ({symbol_placeholders})
              AND {strategy_clause}
              AND (p.created_at>=? OR p.updated_at>=?)
            """,
            tuple(plan_params + [since]),
        ) or funnel_row
    params = (int(user_id), int(account_id), since)
    # Trigger/order counts come from execution_gate_audits, not
    # structure_plan_executions.  Live/Paper persist every actionable Tick
    # outcome there, including ordered, blocked, and no_action entry-guard
    # cases.  The execution table is only a claim/order receipt and can lag
    # or stay empty after a claim-path change.
    audit_rows = storage.fetchall(
        "SELECT status, reason_code, COUNT(*) AS n, "
        "COALESCE(SUM(occurrence_count), 0) AS occurrences "
        "FROM execution_gate_audits "
        "WHERE user_id=? AND account_id=? AND last_seen_at>=? "
        "GROUP BY status, reason_code",
        params,
    )
    triggered = 0
    risk_passed = 0
    ordered = 0
    blocks = []
    for row in audit_rows:
        status = str(row.get('status') or '').lower()
        reason = str(row.get('reason_code') or '').lower()
        count = int(row.get('n') or 0)
        occurrences = int(row.get('occurrences') or count or 0)
        if status in {'ordered', 'blocked'} or (
            status == 'no_action' and reason not in {'no_direction', 'no_new_trigger'}
        ):
            triggered += count
        if status == 'ordered':
            risk_passed += count
        if status == 'blocked' or (
            status == 'no_action' and reason not in {'no_direction', 'no_new_trigger'}
        ):
            blocks.append({
                'reason_code': reason,
                'n': occurrences,
            })
    account_row = storage.fetchone(
        "SELECT account_type FROM trading_accounts WHERE id=? AND user_id=?",
        (int(account_id), int(user_id)),
    ) or {}
    account_type = str(account_row.get("account_type") or "").lower()
    filled_row = storage.fetchone(
        """
        SELECT COUNT(*) AS n FROM paper_orders
        WHERE user_id=? AND account_id=? AND status='filled'
          AND COALESCE(filled_at, requested_at)>=?
        """,
        params,
    ) or {}
    live_filled_row = storage.fetchone(
        """
        SELECT COUNT(*) AS n FROM trade_execution_reports
        WHERE user_id=? AND account_id=? AND success=1
          AND LOWER(action) IN ('b','s','buy','sell')
          AND reported_at>=?
        """,
        params,
    ) or {}
    # Paper matching also writes trade_execution_reports. Counting both
    # tables on a Paper account doubles 下单数 against a single 风控通过.
    if account_type == "paper":
        ordered = int(filled_row.get("n") or 0)
    else:
        ordered = int(live_filled_row.get("n") or 0)
    timeout_row = storage.fetchone(
        """
        SELECT COUNT(*) AS n FROM paper_orders
        WHERE user_id=? AND account_id=?
          AND status IN ('canceled','rejected')
          AND requested_at>=?
          AND rejection_reason LIKE ?
        """,
        (*params, "%撮合超时%"),
    ) or {}
    timeout_count = int(timeout_row.get("n") or 0)
    if timeout_count:
        blocks.append({"reason_code": "timeout", "n": timeout_count})
    merged = {}
    for item in blocks:
        reason = str(item.get('reason_code') or '')
        merged[reason] = merged.get(reason, 0) + int(item.get('n') or 0)
    blocks = [
        {'reason_code': reason, 'n': count}
        for reason, count in merged.items()
    ]
    blocks.sort(key=lambda item: int(item.get('n') or 0), reverse=True)
    blocks = blocks[:8]
    reason_labels = {
        "risk_limit": "账户风控",
        "position_limit": "持仓数量限制",
        "position_policy": "持仓策略限制",
        "claim_conflict": "结构计划重复消费（幂等保护）",
        "already_consumed": "结构计划重复消费（幂等保护）",
        "invalid_volume": "手数无效",
        "technical_failure": "技术错误",
        "entry_guard": "入场门禁拦截",
        "trading_disabled": "账户交易开关关闭",
        "timeout": "模拟撮合超时",
    }
    return {
        "window_start": f"{today_beijing.isoformat()} 00:00",
        "window_timezone": "Asia/Shanghai",
        "labels": ["计划数", "方向形成", "触发数", "风控通过", "下单数"],
        "plans": int(funnel_row.get('plans', 0) or 0),
        "directions": int(funnel_row.get('directions', 0) or 0),
        "triggered": triggered,
        "risk_passed": risk_passed,
        "ordered": ordered,
        "blocked_reasons": [
            {"reason_code": str(row['reason_code'] or ''),
             "label": reason_labels.get(str(row['reason_code'] or ''), str(row['reason_code'] or '')),
             "count": int(row['n'] or 0)}
            for row in blocks
        ],
    }


def _runtime_stats_payload(storage, user_id: int, account) -> Dict:
    account_id = int(account.account_id)
    if account.account_type == "paper":
        performance = build_paper_performance(storage, user_id, account_id)
    else:
        positions = []
        for payload in RuntimeStateRepository(user_id, account_id, storage).list_entities(
            "position", statuses=["open"],
        ):
            if isinstance(payload, dict):
                positions.append(payload)
        performance = build_live_performance(storage, user_id, account_id, positions)
    return {
        "today_trade_stats": today_trade_stats(
            storage, user_id, account_id, account.account_type,
        ),
        "execution_funnel": _execution_funnel(storage, user_id, account_id),
        "strategy_performance": performance,
    }


def create_account_routes(engine_manager: TradingEngineManager) -> APIRouter:
    router = APIRouter()
    repositories = engine_manager.repositories
    repository = repositories.accounts
    strategy_repository = repositories.strategies
    trade_config_repository = repositories.trade_config
    memberships = MembershipService()
    market_source_policy = MarketDataSourcePolicy()
    admission_service = StrategyAdmissionService(engine_manager.paper_trading)

    def promotion_candidates(user_id: int, strategy):
        """Build the selectable live-account set for a paper strategy.

        The strategy keeps its original id.  A policy row proves that the
        account has reported/claimed the same canonical instrument; existing
        deployments are removed before the response reaches the UI.
        """
        storage = repository.storage
        deployed_rows = storage.fetchall(
            "SELECT DISTINCT account_id FROM strategy_deployments "
            "WHERE user_id = ? AND strategy_id = ? AND execution_mode = 'live' "
            "AND status IN ('active', 'paused', 'pending')",
            (int(user_id), str(strategy.strategy_id)),
        )
        deployed_ids = {int(row["account_id"]) for row in deployed_rows}
        symbol = str(strategy.symbol or "").strip().upper()
        symbol_keys = {symbol}
        mapping_rows = storage.fetchall(
            "SELECT mapping_group FROM platform_instrument_mappings "
            "WHERE enabled = 1 AND UPPER(native_symbol) = ?",
            (symbol,),
        )
        symbol_keys.update(
            str(row["mapping_group"] or "").strip().upper()
            for row in mapping_rows
            if str(row["mapping_group"] or "").strip()
        )
        placeholders = ", ".join("?" for _ in symbol_keys)
        policy_rows = storage.fetchall(
            "SELECT account_id, canonical_symbol, mode, broker_name, "
            "primary_account_id, message "
            "FROM market_data_symbol_policies "
            f"WHERE user_id = ? AND UPPER(canonical_symbol) IN ({placeholders}) "
            "AND mode <> 'blocked'",
            (int(user_id), *sorted(symbol_keys)),
        )
        policy_by_account = {int(row["account_id"]): dict(row) for row in policy_rows}
        snapshots = []
        for account in repository.list_for_user(user_id):
            policy = policy_by_account.get(int(account.account_id))
            if policy is None or account.account_type not in {"mt5", "ibkr"}:
                continue
            snapshot = _account_payload(account)
            snapshot.update({
                "symbol": symbol,
                "market_source": MarketDataSourcePolicy._policy_payload(policy),
            })
            snapshots.append(snapshot)
        return promotion_candidate_accounts(snapshots, symbol, deployed_ids), deployed_ids

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
        if account.account_type in {"mt5", "ibkr"} and (not account.last_seen_at or now - int(account.last_seen_at) > 180):
            label = "MT5 终端" if account.account_type == "mt5" else "IBKR Gateway"
            warnings.append(f"目标 {label} 超过3分钟未心跳，实盘部署后暂时不会接收交易指令。")
        return warnings

    @router.get("/accounts")
    def list_accounts(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        accounts = repository.list_for_user(user.user_id)
        trade_config_enabled = bool(
            trade_config_repository.get_config(user.user_id).get("enabled", True)
        )
        account_ids = [int(account.account_id) for account in accounts]
        account_types = {
            int(account.account_id): account.account_type for account in accounts
        }
        # Load all bindings in one query.  The previous implementation called
        # list_deployments() once per account, including a per-account expiry
        # check and per-deployment strategy lookup.
        deployments_by_account = (
            engine_manager.paper_trading.list_deployment_summaries_for_accounts(
                user.user_id, account_ids, account_types,
            )
        )
        market_sources_by_account = {}
        mt5_ids = [
            int(account.account_id) for account in accounts
            if account.account_type == "mt5"
        ]
        # Account cards must summarize symbol-level primary/reuse roles.
        # Taking only the newest symbol row can keep showing a stale reuse
        # label after failover has already moved market_data_sources.
        for account_id in mt5_ids:
            market_sources_by_account[account_id] = (
                market_source_policy.summarize_account(user.user_id, account_id)
            )
        payloads = []
        for account in accounts:
            deployments = deployments_by_account.get(int(account.account_id), [])
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
                    market_sources_by_account.get(int(account.account_id), {
                        "mode": "pending",
                        "message": "等待 EA 上报品种后确认行情来源",
                        "primary_account_id": 0,
                        "conflict_symbols": [],
                        "is_market_primary": False,
                        "can_open_trade": True,
                    }) if account.account_type == "mt5" else None
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
                reference_account_id=payload.get("reference_account_id"),
            )
            return {
                "status": "ok",
                "message": "Paper 模拟账户已创建，可以部署策略开始模拟运行",
                "account": _account_payload(account),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/paper/reference-preview")
    async def preview_paper_reference(
        reference_account_id: int = Query(...),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            settings = repository.resolve_paper_reference(
                user.user_id, int(reference_account_id),
            )
            return {"status": "ok", "settings": settings}
        except ValueError as exc:
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
                daily_risk_limit=payload.get("daily_risk_limit"),
                daily_order_limit=payload.get("daily_order_limit"),
                auto_flatten_enabled=payload.get("auto_flatten_enabled"),
                auto_flatten_time=payload.get("auto_flatten_time"),
                single_position_loss_limit_enabled=payload.get(
                    "single_position_loss_limit_enabled"
                ),
                single_position_loss_limit_amount=payload.get(
                    "single_position_loss_limit_amount"
                ),
                manual_order_daily_limit_enabled=payload.get(
                    "manual_order_daily_limit_enabled"
                ),
                manual_order_daily_limit=payload.get(
                    "manual_order_daily_limit"
                ),
                manual_losing_order_daily_limit=payload.get(
                    "manual_losing_order_daily_limit"
                ),
                single_order_risk_limit=payload.get(
                    "single_order_risk_limit"
                ),
                broker_trailing_stop_enabled=payload.get(
                    "broker_trailing_stop_enabled"
                ),
            )
            return {
                "status": "ok",
                "message": "账户配置已更新",
                "account": _account_payload(account),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/close-check")
    async def close_paper_account_check(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None or account.account_type != "paper":
            raise HTTPException(status_code=404, detail="Paper 模拟账户不存在")
        storage = repository.storage
        positions = storage.fetchall(
            "SELECT position_id, symbol, direction, volume FROM paper_positions "
            "WHERE account_id=? AND status='open' ORDER BY opened_at DESC",
            (int(account_id),),
        )
        pending = storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id=? AND status='pending'",
            (int(account_id),),
        )
        deployments = storage.fetchall(
            "SELECT d.deployment_id,d.strategy_id,d.status,COALESCE(s.config_json,'{}') AS config_json "
            "FROM strategy_deployments d LEFT JOIN user_strategy_configs s "
            "ON s.user_id=d.user_id AND s.strategy_id=d.strategy_id "
            "WHERE d.user_id=? AND d.account_id=? AND d.status IN ('active','paused','pending')",
            (int(user.user_id), int(account_id)),
        )
        strategy_ids = list(dict.fromkeys(str(row["strategy_id"]) for row in deployments))
        strategy_items = []
        for strategy_id in strategy_ids:
            other_row = storage.fetchone(
                "SELECT COUNT(DISTINCT account_id) AS count FROM strategy_deployments "
                "WHERE user_id=? AND strategy_id=? AND account_id<>? "
                "AND status IN ('active','paused','pending')",
                (int(user.user_id), strategy_id, int(account_id)),
            )
            other = int(other_row["count"] if other_row else 0)
            # Count other accounts, not rows: duplicate deployments on this
            # account must still allow the strategy to retire after closing.
            strategy = strategy_repository.get_strategy_by_id(user.user_id, strategy_id)
            strategy_items.append({
                "strategy_id": strategy_id,
                "strategy_name": strategy.strategy_name if strategy else strategy_id,
                "other_active_deployments": other,
                "will_retire": other == 0,
            })
        missing_quotes = []
        for row in positions:
            symbol = str(row["symbol"] or "")
            if engine_manager.paper_trading.last_quote(user.user_id, symbol) is None:
                historical = storage.fetchone(
                    "SELECT close_price FROM historical_klines WHERE user_id=? AND account_id=0 AND symbol=? "
                    "ORDER BY COALESCE(timestamp_utc,timestamp) DESC LIMIT 1",
                    (int(user.user_id), symbol),
                )
                if not historical or float(historical["close_price"] or 0) <= 0:
                    missing_quotes.append(symbol)
        return {
            "status": "ok", "account_id": int(account_id),
            "can_close": not missing_quotes,
            "positions": [dict(row) for row in positions],
            "position_count": len(positions),
            "pending_order_count": int(pending["count"] if pending else 0),
            "deployments": [dict(row) for row in deployments],
            "strategies": strategy_items,
            "missing_quotes": sorted(set(missing_quotes)),
        }

    @router.post("/accounts/{account_id}/close")
    async def close_paper_account(
        account_id: int,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None or account.account_type != "paper":
            raise HTTPException(status_code=404, detail="Paper 模拟账户不存在")
        try:
            payload = await request.json()
            reason = str(payload.get("reason") or "用户关闭模拟账户").strip()
            check = await close_paper_account_check(account_id, user)
            if not check["can_close"]:
                raise ValueError(
                    "以下持仓没有可用最后报价，不能关闭：" + "、".join(check["missing_quotes"])
                )
            result = engine_manager.paper_trading.close_paper_account(
                user.user_id, account_id, reason=reason,
            )
            retired = []
            for strategy_id in result.get("strategy_ids") or []:
                if strategy_repository.strategy_deployment_count(user.user_id, strategy_id) != 0:
                    continue
                strategy = strategy_repository.get_strategy_by_id(user.user_id, strategy_id)
                if strategy is None or strategy.lifecycle_status == StrategyLifecycle.RETIRED:
                    continue
                previous = strategy.lifecycle_status
                now = datetime.now()
                strategy.lifecycle_status = StrategyLifecycle.RETIRED
                strategy.lifecycle_updated_at = now
                strategy.updated_at = now
                strategy.lifecycle_history.append({
                    "from_status": previous, "to_status": StrategyLifecycle.RETIRED,
                    "changed_at": now.isoformat(),
                    "reason": f"Paper 账户 {account_id} 关闭后无其他活跃部署",
                })
                strategy_repository.save_strategy(user.user_id, strategy)
                retired.append({"strategy_id": strategy_id, "strategy_name": strategy.strategy_name})
                SystemEventLogRepository(repository.storage).add({
                    "level": "warning", "category": "trading",
                    "event_type": "strategy_retired_by_account_closed",
                    "event_name": "策略因模拟账户关闭而下线",
                    "user_id": user.user_id, "account_id": account_id,
                    "symbol": strategy.symbol, "actor_type": "user",
                    "entity_type": "strategy", "entity_id": strategy_id,
                    "message": f"策略 {strategy.strategy_name} 无其他活跃部署，已自动下线",
                    "detail": {"strategy_id": strategy_id, "account_id": account_id,
                               "previous_status": previous, "reason": reason},
                })
            SystemEventLogRepository(repository.storage).add({
                "level": "warning", "category": "trading",
                "event_type": "paper_account_closed",
                "event_name": "Paper 模拟账户已关闭",
                "user_id": user.user_id, "account_id": account_id,
                "actor_type": "user", "entity_type": "trading_account",
                "entity_id": str(account_id), "status": "closed",
                "message": reason,
                "detail": {"settled_positions": result.get("settled_positions", 0),
                           "strategy_ids": result.get("strategy_ids", []),
                           "retired_strategies": retired},
            })
            return {"status": "ok", "message": "Paper 模拟账户已关闭", "account": _account_payload(result["account"]),
                    "retired_strategies": retired}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
            account = repository.get_by_id(user.user_id, account_id)
            if account is not None:
                detail.update(_runtime_stats_payload(
                    repository.storage, user.user_id, account,
                ))
            return {"status": "ok", "detail": detail}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


    @router.get("/accounts/{account_id}/positions/{position_key}/decision-brief")
    async def get_position_decision_brief(
        account_id: int,
        position_key: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在")
        storage = repository.storage
        attribution = {}
        position = {}
        if account.account_type == "paper":
            row = storage.fetchone(
                "SELECT * FROM paper_positions WHERE user_id=? AND account_id=? AND position_id=?",
                (int(user.user_id), int(account_id), str(position_key)),
            )
            if row is None:
                raise HTTPException(status_code=404, detail="持仓不存在")
            position = dict(row)
            try:
                attribution = json.loads(position.get("position_attribution_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
        else:
            try:
                ticket = int(str(position_key).strip() or 0)
            except (TypeError, ValueError):
                raise HTTPException(status_code=404, detail="持仓不存在")
            report = repositories.trade_execution.find_for_position(
                int(user.user_id), int(account_id), ticket,
            ) or {}
            attribution = report.get("position_attribution") or {}
            position = {
                "symbol": report.get("symbol") or "",
                "direction": attribution.get("direction") or "",
                "entry_price": report.get("executed_price") or report.get("requested_price"),
                "volume": report.get("executed_volume") or report.get("requested_volume"),
                "opened_at": report.get("reported_at"),
                "strategy_id": report.get("strategy_id") or attribution.get("strategy_id"),
            }
            if not attribution:
                raise HTTPException(status_code=404, detail="这笔持仓没有策略决策记录")
        plan_id = str(attribution.get("trade_plan_id") or "")
        plan = {}
        if plan_id:
            plan_row = storage.fetchone(
                "SELECT payload_json FROM structure_trade_plans WHERE user_id=? AND plan_id=? LIMIT 1",
                (int(user.user_id), plan_id),
            )
            if plan_row:
                try:
                    plan = json.loads(plan_row.get("payload_json") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    plan = {}
        saved = attribution.get("decision_brief")
        if isinstance(saved, dict) and saved.get("available"):
            return {"status": "ok", "brief": saved}
        frozen_plan = attribution.get("decision_plan") if isinstance(attribution.get("decision_plan"), dict) else {}
        return {"status": "ok", "brief": build_decision_brief(attribution, frozen_plan or plan, position)}

    @router.get("/accounts/{account_id}/paper/runtime-logs")
    async def get_paper_runtime_logs(
        account_id: int,
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            return {
                "status": "ok",
                **engine_manager.paper_trading.list_runtime_logs(
                    user.user_id, account_id, page=page, page_size=page_size,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/paper/equity-curve")
    async def get_paper_equity_curve(
        account_id: int,
        equity_from: Optional[int] = Query(None, ge=0),
        equity_to: Optional[int] = Query(None, ge=0),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            curve = engine_manager.paper_trading.get_equity_curve(
                user.user_id, account_id, equity_from, equity_to,
            )
            return {"status": "ok", "equity_curve": curve}
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
        if account is None or account.account_type not in {"mt5", "ibkr"}:
            raise HTTPException(status_code=404, detail="实盘账户不存在")

        # 运行台只读已持久化的持仓快照，不唤醒账户交易引擎。
        # get_engine() 会加载策略缓存并和 Tick 抢同一进程资源。
        positions = []
        for payload in RuntimeStateRepository(
            user.user_id, account_id, repositories.storage,
        ).list_entities("position", statuses=["open"]):
            if not isinstance(payload, dict):
                continue
            ticket = payload.get("ticket")
            if ticket is not None and "ticket" not in payload:
                payload["ticket"] = ticket
            positions.append(payload)
        events_by_position = repositories.position_events.list_for_positions(
            user.user_id,
            account_id,
            [str(position.get("ticket", "")) for position in positions],
            limit=100,
        )
        for position in positions:
            position["management_events"] = events_by_position.get(
                str(position.get("ticket", "")), []
            )
        # 运行台只展示最近 30 条策略执行回报，避免历史回报拖慢首屏和刷新。
        execution_reports = repositories.trade_execution.list_for_account(
            user.user_id, account_id, 30,
        )
        strategy_names = {}
        for report in execution_reports:
            if str(report.get("action") or "").strip().lower() == "position_modify_sl":
                report["strategy_name"] = "持仓管理"
                continue
            attribution = report.get("position_attribution") or {}
            strategy_id = str(
                report.get("strategy_id") or attribution.get("strategy_id") or ""
            ).strip()
            if not strategy_id:
                report["strategy_name"] = "未归属策略"
                continue
            strategy = strategy_names.get(strategy_id)
            if strategy is None:
                strategy = strategy_repository.get_strategy_by_id(user.user_id, strategy_id)
                strategy_names[strategy_id] = strategy or False
            report["strategy_id"] = strategy_id
            report["strategy_name"] = (
                strategy.strategy_name if strategy else str(
                    attribution.get("strategy_name") or strategy_id
                )
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
            trade["plan_type"] = attribution.get("plan_type") or attribution.get("setup_type", "")
            trade["setup_type"] = trade["plan_type"]
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
            report["plan_type"] = attribution.get("plan_type") or attribution.get("setup_type", "")
            report["setup_type"] = report["plan_type"]
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
                "equity_curve": [],
                **_runtime_stats_payload(
                    repositories.storage, user.user_id, account,
                ),
            },
        }

    @router.get("/accounts/{account_id}/runtime-stats")
    async def get_account_runtime_stats(
        account_id: int,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="交易账户不存在")
        if account.account_type not in {"paper", "mt5", "ibkr"}:
            raise HTTPException(status_code=404, detail="该账户没有运行台统计")
        return {"status": "ok", **_runtime_stats_payload(
            repository.storage, user.user_id, account,
        )}

    @router.get("/accounts/{account_id}/pnl-statistics")
    async def get_account_pnl_statistics(
        account_id: int, business_date: Optional[str] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="交易账户不存在")
        try:
            day = datetime.strptime(business_date, "%Y-%m-%d").date() if business_date else (datetime.now(ZoneInfo("Asia/Shanghai")).date())
            if not business_date:
                from datetime import timedelta
                day -= timedelta(days=1)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc
        rows = query_daily_pnl_statistics(repository.storage, user.user_id, account_id, day)
        stale = any(
            str(row.get("period") or "") == "未知"
            and str(row.get("plan_type") or row.get("setup_type") or "") == "未分类"
            and not str(row.get("strategy_id") or "").strip()
            for row in rows or []
        )
        # 任务刚部署、服务在统计时刻未运行、或快照仍含手工单时，补建该日快照。
        if day < datetime.now(ZoneInfo("Asia/Shanghai")).date() and (not rows or stale):
            build_daily_pnl_statistics(repository.storage, user.user_id, account_id, day)
            rows = query_daily_pnl_statistics(repository.storage, user.user_id, account_id, day)
        return {"status": "ok", "business_date": day.isoformat(), "rows": rows}

    @router.get("/accounts/{account_id}/live-monitoring/equity-curve")
    async def get_live_equity_curve(
        account_id: int,
        equity_from: Optional[int] = Query(None, ge=0),
        equity_to: Optional[int] = Query(None, ge=0),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account = repository.get_by_id(user.user_id, account_id)
        if account is None or account.account_type not in {"mt5", "ibkr"}:
            raise HTTPException(status_code=404, detail="实盘账户不存在")
        return {"status": "ok", "equity_curve": repository.list_live_equity_points(
            user.user_id, account_id, count=5000,
            from_time=equity_from, to_time=equity_to,
        )}

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
            engine_manager.refresh_user_strategies(user.user_id, account_id=account_id)
            return {
                "status": "ok",
                "message": "策略已绑定到交易账户",
                "deployment": deployment,
                "warnings": deployment_warnings(user.user_id, account_id, str(payload.get("strategy_id", "").strip())),
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/promotion-candidates")
    async def list_promotion_candidates(
        account_id: int,
        strategy_id: str = Query(...),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """List same-symbol live accounts not already bound to the strategy."""
        source = repository.get_by_id(user.user_id, account_id)
        if source is None or source.account_type != "paper":
            raise HTTPException(status_code=404, detail="Paper 模拟账户不存在")
        strategy = strategy_repository.get_strategy_by_id(user.user_id, strategy_id.strip())
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        if strategy.source_owner_user_id:
            raise HTTPException(status_code=400, detail="共享策略不能从当前账户直接推送实盘")
        source_deployment = repository.storage.fetchone(
            "SELECT deployment_id FROM strategy_deployments "
            "WHERE user_id = ? AND account_id = ? AND strategy_id = ? "
            "AND execution_mode = 'paper' AND status IN ('active', 'paused') LIMIT 1",
            (int(user.user_id), int(account_id), strategy.strategy_id),
        )
        if source_deployment is None:
            raise HTTPException(status_code=400, detail="该策略尚未部署到当前模拟账户")
        candidates, deployed_ids = promotion_candidates(user.user_id, strategy)
        return {
            "status": "ok",
            "strategy": strategy.to_dict(),
            "source_account_id": int(account_id),
            "accounts": candidates,
            "excluded_deployment_account_ids": sorted(deployed_ids),
        }

    @router.post("/accounts/{account_id}/promote-and-deploy")
    async def promote_and_deploy(
        account_id: int,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Promote the existing strategy, then bind that same id to live accounts."""
        source = repository.get_by_id(user.user_id, account_id)
        if source is None or source.account_type != "paper":
            raise HTTPException(status_code=404, detail="Paper 模拟账户不存在")
        try:
            payload = await request.json()
            strategy_id = str(payload.get("strategy_id", "")).strip()
            selected_ids = sorted({int(value) for value in (payload.get("account_ids") or [])})
            if not strategy_id or not selected_ids:
                raise ValueError("请选择至少一个实盘账户")
            if not bool(payload.get("confirm_production")):
                raise ValueError("请确认将策略批准为可用于实盘")
            strategy = strategy_repository.get_strategy_by_id(user.user_id, strategy_id)
            if strategy is None:
                raise ValueError("策略不存在")
            if strategy.source_owner_user_id:
                raise ValueError("共享策略不能从当前账户直接推送实盘")
            candidates, _ = promotion_candidates(user.user_id, strategy)
            candidate_ids = {int(item["account_id"]) for item in candidates}
            invalid_ids = [item for item in selected_ids if item not in candidate_ids]
            if invalid_ids:
                raise ValueError(
                    "以下账户已部署、品种不匹配或当前不可交易：" + ", ".join(map(str, invalid_ids))
                )
            source_deployment = repository.storage.fetchone(
                "SELECT deployment_id FROM strategy_deployments "
                "WHERE user_id = ? AND account_id = ? AND strategy_id = ? "
                "AND execution_mode = 'paper' AND status IN ('active', 'paused') LIMIT 1",
                (int(user.user_id), int(account_id), strategy_id),
            )
            if source_deployment is None:
                raise ValueError("该策略尚未部署到当前模拟账户")
            if strategy.lifecycle_status == StrategyLifecycle.PAPER_TRADING:
                admission_service.validate_transition(
                    user.user_id, strategy, StrategyLifecycle.PRODUCTION,
                )
                strategy.transition_lifecycle(
                    StrategyLifecycle.PRODUCTION, "模拟运行台一键推送实盘",
                )
                strategy_repository.save_strategy(user.user_id, strategy)
                engine_manager.refresh_user_strategies(user.user_id)
            elif strategy.lifecycle_status != StrategyLifecycle.PRODUCTION:
                raise ValueError(
                    f"策略当前处于“{StrategyLifecycle.LABELS.get(strategy.lifecycle_status, strategy.lifecycle_status)}”，"
                    "只能从模拟盘验证状态推送实盘"
                )
            account_by_id = {int(item["account_id"]): item for item in candidates}
            results = []
            for target_id in selected_ids:
                target = account_by_id[target_id]
                try:
                    deployment = engine_manager.paper_trading.deploy(
                        user.user_id, target_id, strategy_id,
                    )
                    engine_manager.refresh_user_strategies(
                        user.user_id, account_id=target_id
                    )
                    results.append({
                        "account_id": target_id,
                        "account_name": target.get("account_name") or str(target_id),
                        "status": "deployed",
                        "deployment": deployment,
                        "warnings": deployment_warnings(user.user_id, target_id, strategy_id),
                    })
                except (TypeError, ValueError) as exc:
                    results.append({
                        "account_id": target_id,
                        "account_name": target.get("account_name") or str(target_id),
                        "status": "failed",
                        "error": str(exc),
                    })
            deployed_count = sum(item["status"] == "deployed" for item in results)
            failed_count = len(results) - deployed_count
            return {
                "status": "ok" if deployed_count else "error",
                "message": f"已绑定 {deployed_count} 个实盘账户" + (f"，{failed_count} 个失败" if failed_count else ""),
                "strategy": strategy.to_dict(),
                "results": results,
                "deployed_count": deployed_count,
                "failed_count": failed_count,
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
            engine_manager.refresh_user_strategies(user.user_id, account_id=account_id)
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
            engine_manager.refresh_user_strategies(user.user_id, account_id=account_id)
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
            engine_manager.refresh_user_strategies(user.user_id, account_id=account_id)
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
            engine_manager.refresh_user_strategies(user.user_id, account_id=account_id)
            return {"status": "ok", "message": "策略已从该账户解绑"}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def _account_payload(
    account: TradingAccountRecord, deployments: Optional[List[Dict]] = None,
) -> Dict:
    connected = bool(
        account.account_type in {"mt5", "ibkr"}
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
        "daily_risk_limit": account.daily_risk_limit,
        "daily_order_limit": account.daily_order_limit,
        "auto_flatten_enabled": account.auto_flatten_enabled,
        "auto_flatten_time": account.auto_flatten_time,
        "single_position_loss_limit_enabled": account.single_position_loss_limit_enabled,
        "single_position_loss_limit_amount": account.single_position_loss_limit_amount,
        "manual_order_daily_limit_enabled": account.manual_order_daily_limit_enabled,
        "manual_order_daily_limit": account.manual_order_daily_limit,
        "manual_losing_order_daily_limit": account.manual_losing_order_daily_limit,
        "single_order_risk_limit": account.single_order_risk_limit,
        "broker_trailing_stop_enabled": account.broker_trailing_stop_enabled,
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
        ) if account.account_type in {"mt5", "ibkr"} else (
            "running" if active else "ready"
        ),
    }
