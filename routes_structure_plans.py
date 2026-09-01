"""Structure trade-plan query routes."""
from __future__ import annotations

import time
from typing import Dict
from fastapi import APIRouter, Depends, Query
from auth import AuthUser, require_auth
from mysql_repositories import get_storage
from market.store.structure_plan_store import StructureTradePlanRepository
from market.services.signal.structure_plan_signal import StructurePlanBuilder, MARKET_STRUCTURE_PLAN_SOURCE_ID, resolve_structure_plan_config
from market.services.market_structure_engine_v2 import analyze_incremental


def create_structure_plan_routes(engine_manager, strategy_repo, structure_defaults: Dict) -> APIRouter:
    router = APIRouter()

    @router.get("/market/structure/{symbol}/trade-plans", dependencies=[Depends(require_auth)])
    async def get_structure_trade_plans(symbol: str, period: str = Query("M5"), user: AuthUser = Depends(require_auth)) -> Dict:
        period = period.upper(); storage = get_storage()
        repo = StructureTradePlanRepository(storage)
        items = repo.list_current(user.user_id, 0, "", MARKET_STRUCTURE_PLAN_SOURCE_ID, symbol, period)
        if not items:
            engine = engine_manager.get_market_engine(user.user_id)
            rows = engine.kline_store.get_all_klines(symbol, period)
            if rows:
                structure = analyze_incremental(symbol, period, rows[-600:], structure_defaults)
                items = StructurePlanBuilder(resolve_structure_plan_config(symbol, period)).build(
                    MARKET_STRUCTURE_PLAN_SOURCE_ID, symbol, period, rows[-600:], structure,
                )
                bar_time = int(float(rows[-1].get("timestamp") or rows[-1].get("time") or 0))
                repo.replace_scope(user.user_id, 0, "", MARKET_STRUCTURE_PLAN_SOURCE_ID, symbol, period, items, bar_time)
                items = repo.list_current(user.user_id, 0, "", MARKET_STRUCTURE_PLAN_SOURCE_ID, symbol, period)
        strategies = []
        for strategy in strategy_repo.get_all_strategies(user.user_id):
            if str(strategy.symbol).upper() != symbol.upper(): continue
            if not any(str(s.get("period") or "M5").upper() == period for s in strategy.get_signal_sources("structure_plan", enabled_only=True)): continue
            strategies.append({"strategy_id": strategy.strategy_id, "strategy_name": strategy.strategy_name, "period": period, "deployments": []})
        by_strategy = {x["strategy_id"]: x for x in strategies}
        deployments = storage.fetchall("SELECT d.deployment_id,d.strategy_id,d.account_id,d.execution_mode,d.status,d.symbol,a.account_name,a.account_type,a.enabled,a.trading_enabled,a.auto_trading_enabled FROM strategy_deployments d JOIN trading_accounts a ON a.id=d.account_id WHERE d.user_id=?", (user.user_id,))
        active = []
        for row in deployments:
            item = by_strategy.get(str(row["strategy_id"]))
            if not item or str(row["symbol"]).upper() != symbol.upper(): continue
            deployment = {"deployment_id": str(row["deployment_id"]), "strategy_id": str(row["strategy_id"]), "strategy_name": item["strategy_name"], "account_id": int(row["account_id"]), "account_name": str(row["account_name"] or ""), "account_type": str(row["account_type"] or ""), "execution_mode": str(row["execution_mode"] or ""), "deployment_status": str(row["status"] or ""), "active": bool(row["status"] == "active" and row["enabled"] and row["trading_enabled"] and row["auto_trading_enabled"])}
            item["deployments"].append(deployment)
            if deployment["active"]: active.append(deployment)
        executions = repo.list_executions(user.user_id, [str(x.get("plan_id") or "") for x in items])
        by_plan = {}
        for execution in executions: by_plan.setdefault(str(execution["plan_id"]), {})[str(execution["deployment_id"])] = execution
        consumed = {"claimed", "triggered", "ordered", "filled", "rejected", "expired", "canceled"}
        for plan in items:
            rows_out=[]; counts={}
            for deployment in active:
                execution = by_plan.get(str(plan.get("plan_id") or ""), {}).get(deployment["deployment_id"])
                status = str(execution.get("status") or "") if execution else "unconsumed"; counts[status] = counts.get(status, 0)+1
                rows_out.append({**deployment, "execution_status": status, "order_id": str(execution.get("order_id") or "") if execution else "", "execution_reason": str(execution.get("reason") or "") if execution else "", "consumed_at": int(execution.get("updated_at") or 0) if execution else 0})
            plan["subscriptions"] = rows_out
            plan["subscription_summary"] = {"strategy_count": len(strategies), "deployment_count": len(active), "consumed_count": sum(v for k,v in counts.items() if k in consumed), "unconsumed_count": max(0, len(active)-sum(v for k,v in counts.items() if k in consumed)), "status_counts": counts}
        return {"status": "ok", "symbol": symbol, "period": period, "plans": items}

    return router
