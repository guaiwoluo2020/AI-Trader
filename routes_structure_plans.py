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
from repositories.execution_gate_audits import ExecutionGateAuditRepository
from market.services.execution_divergence_monitor import ExecutionDivergenceMonitor


def assemble_structure_plan_execution(
    items, strategies, deployments, executions, gate_audits, symbol: str,
):
    """Attach subscriptions and execution outcomes without hiding gated accounts."""
    by_strategy = {str(item["strategy_id"]): item for item in strategies}
    subscribed = []
    for source in deployments or []:
        row = dict(source)
        item = by_strategy.get(str(row.get("strategy_id") or ""))
        if not item or str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        deployment_active = str(row.get("status") or "") == "active"
        account_execution_enabled = bool(
            row.get("enabled")
            and row.get("trading_enabled")
            and row.get("auto_trading_enabled")
        )
        deployment = {
            "deployment_id": str(row.get("deployment_id") or ""),
            "strategy_id": str(row.get("strategy_id") or ""),
            "strategy_name": str(item.get("strategy_name") or ""),
            "account_id": int(row.get("account_id") or 0),
            "account_name": str(row.get("account_name") or ""),
            "account_type": str(row.get("account_type") or ""),
            "execution_mode": str(row.get("execution_mode") or ""),
            "deployment_status": str(row.get("status") or ""),
            "active": deployment_active,
            "account_execution_enabled": account_execution_enabled,
        }
        item["deployments"].append(deployment)
        if deployment_active:
            subscribed.append(deployment)

    by_plan = {}
    executions_by_plan = {}
    for execution in executions or []:
        plan_id = str(execution.get("plan_id") or "")
        executions_by_plan.setdefault(plan_id, []).append(execution)
        # Repository order is newest first; do not let an older lifecycle row
        # overwrite the current status for a deployment.
        by_plan.setdefault(plan_id, {}).setdefault(
            str(execution.get("deployment_id") or ""), execution
        )
    audits_by_plan = {}
    for audit in gate_audits or []:
        audits_by_plan.setdefault(str(audit.get("plan_id") or ""), []).append(audit)

    divergence_monitor = ExecutionDivergenceMonitor()
    consumed = {
        "claimed", "triggered", "ordered", "filled", "rejected",
        "expired", "canceled",
    }
    for plan in items or []:
        plan_id = str(plan.get("plan_id") or "")
        rows_out, counts = [], {}
        for deployment in subscribed:
            execution = by_plan.get(plan_id, {}).get(deployment["deployment_id"])
            status = str(execution.get("status") or "") if execution else "unconsumed"
            counts[status] = counts.get(status, 0) + 1
            rows_out.append({
                **deployment,
                "execution_status": status,
                "order_id": str(execution.get("order_id") or "") if execution else "",
                "execution_reason": str(execution.get("reason") or "") if execution else "",
                "consumed_at": int(execution.get("updated_at") or 0) if execution else 0,
            })
        consumed_count = sum(value for key, value in counts.items() if key in consumed)
        plan["subscriptions"] = rows_out
        plan["subscription_summary"] = {
            "strategy_count": len(strategies),
            "deployment_count": len(subscribed),
            "consumed_count": consumed_count,
            "unconsumed_count": max(0, len(subscribed) - consumed_count),
            "status_counts": counts,
        }
        matrix = divergence_monitor.build_matrix(
            rows_out, audits_by_plan.get(plan_id, []),
            executions_by_plan.get(plan_id, []),
        )
        plan["execution_matrix"] = matrix["rows"]
        plan["execution_divergence"] = {
            "diverged": matrix["diverged"], "findings": matrix["findings"],
        }
    return items


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
        deployments = storage.fetchall("SELECT d.deployment_id,d.strategy_id,d.account_id,d.execution_mode,d.status,d.symbol,a.account_name,a.account_type,a.enabled,a.trading_enabled,a.auto_trading_enabled FROM strategy_deployments d JOIN trading_accounts a ON a.id=d.account_id WHERE d.user_id=?", (user.user_id,))
        executions = repo.list_executions(user.user_id, [str(x.get("plan_id") or "") for x in items])
        gate_audits = ExecutionGateAuditRepository(storage).list_for_plans(
            user.user_id, [str(x.get("plan_id") or "") for x in items]
        )
        assemble_structure_plan_execution(
            items, strategies, deployments, executions, gate_audits, symbol,
        )
        return {"status": "ok", "symbol": symbol, "period": period, "plans": items}

    @router.get("/market/structure/{symbol}/opportunities/{opportunity_id}", dependencies=[Depends(require_auth)])
    async def get_structure_opportunity(
        symbol: str, opportunity_id: str, period: str = Query("M5"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return one opportunity with its event, stages and execution receipts."""
        period = period.upper(); storage = get_storage()
        repo = StructureTradePlanRepository(storage)
        plans = repo.list_opportunity(user.user_id, opportunity_id, symbol, period)
        if not plans:
            return {"status": "ok", "symbol": symbol, "period": period,
                    "opportunity_id": opportunity_id, "found": False,
                    "plans": [], "stages": {}}
        executions = repo.list_executions(
            user.user_id, [str(item.get("plan_id") or "") for item in plans]
        )
        audits = ExecutionGateAuditRepository(storage).list_for_plans(
            user.user_id, [str(item.get("plan_id") or "") for item in plans]
        )
        execution_by_plan: Dict[str, list] = {}
        for item in executions:
            execution_by_plan.setdefault(str(item.get("plan_id") or ""), []).append(item)
        audit_by_plan: Dict[str, list] = {}
        for item in audits:
            audit_by_plan.setdefault(str(item.get("plan_id") or ""), []).append(item)
        stages = {}
        for plan in plans:
            stage = str(plan.get("opportunity_stage") or plan.get("event_stage") or "single")
            stages[stage] = {
                "plan": plan,
                "executions": execution_by_plan.get(str(plan.get("plan_id") or ""), []),
                "gate_audits": audit_by_plan.get(str(plan.get("plan_id") or ""), []),
            }
        first = plans[0]
        evidence = first.get("validation_evidence") or {}
        return {
            "status": "ok", "found": True, "symbol": symbol, "period": period,
            "opportunity_id": opportunity_id,
            "structure_segment_id": first.get("structure_segment_id"),
            "structure_revision": (first.get("structure_metadata") or {}).get("revision"),
            "zone_id": evidence.get("zone_id"),
            "zone_revision": evidence.get("zone_revision"),
            "direction": first.get("direction"),
            "setup_family": first.get("setup_family"),
            "event": {
                "event_id": evidence.get("event_id"),
                "event_type": evidence.get("type"),
                "event_status": evidence.get("event_status", "confirmed"),
                "event_stage": evidence.get("event_stage"),
                "confirmed_at": evidence.get("confirmed_at"),
                "invalidated_at": evidence.get("invalidated_at"),
                "invalidation_reason": evidence.get("invalidation_reason"),
            },
            "opportunity_status": first.get("opportunity_status"),
            "breakout_eligible": bool(first.get("breakout_eligible")),
            "initial_protection_confirmed": bool(first.get("initial_protection_confirmed")),
            "stages": stages,
        }

    return router
