"""MySQL-backed structure trade plans and per-deployment executions."""
from __future__ import annotations

import json
import time
import uuid
import re
from typing import Dict, List, Optional

from sqlite_storage import get_storage


class StructureTradePlanRepository:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    def replace_scope(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
        plans: List[Dict], structure_bar_time: int,
    ) -> List[Dict]:
        now = int(time.time())
        keep = {str(plan["plan_id"]) for plan in plans}
        # A rolling structure window can move its anchor forward by one bar even
        # though the actionable opportunity has not changed.  Capture the
        # currently active semantic plan before invalidating the old bar so the
        # replacement keeps the original generation time.  A plan only receives
        # a new timestamp when its setup, direction or entry mode actually
        # changes (or after the prior opportunity has already disappeared).
        previous_active = self.storage.fetchall(
            "SELECT setup_type,direction,entry_mode,payload_json,created_at "
            "FROM structure_trade_plans WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching') ORDER BY updated_at DESC",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        previous_generated_at = {}
        for row in previous_active:
            key = (
                str(row["setup_type"] or ""),
                str(row["direction"] or "none"),
                str(row["entry_mode"] or "watch"),
            )
            try:
                previous_payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                previous_payload = {}
            previous_generated_at.setdefault(
                key,
                int(previous_payload.get("generated_at") or row["created_at"] or now),
            )
        # Invalidate every plan anchored to an older closed bar before
        # upserting the new snapshot. This is deliberately scope-local, so a
        # new plan for one strategy/symbol cannot affect another deployment.
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='invalidated', updated_at=? "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND signal_source_id=? AND symbol=? AND period=? "
            "AND structure_bar_time < ? AND status IN ('active','watching')",
            (now, user_id, account_id, strategy_id, signal_source_id,
             symbol, period, int(structure_bar_time)),
        )
        current = self.storage.fetchall(
            "SELECT plan_id FROM structure_trade_plans WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching')",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        for row in current:
            plan_id = str(row["plan_id"])
            # A newly generated snapshot supersedes older active plans. Plans
            # in the current snapshot remain active; stale plans are no longer
            # eligible for Tick evaluation.
            if plan_id not in keep:
                self.storage.execute(
                    "UPDATE structure_trade_plans SET status='invalidated', updated_at=? "
                    "WHERE plan_id=?",
                    (now, plan_id),
                )
        for plan in plans:
            payload = dict(plan)
            existing = self.storage.fetchone(
                "SELECT payload_json,created_at FROM structure_trade_plans "
                "WHERE plan_id=? LIMIT 1",
                (plan["plan_id"],),
            )
            if existing:
                try:
                    previous = json.loads(existing["payload_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    previous = {}
                payload["generated_at"] = int(
                    previous.get("generated_at") or existing["created_at"] or now
                )
            else:
                semantic_key = (
                    str(plan.get("setup_type") or ""),
                    str(plan.get("direction") or "none"),
                    str(plan.get("entry_mode") or "watch"),
                )
                if semantic_key in previous_generated_at:
                    payload["generated_at"] = previous_generated_at[semantic_key]
            payload["reason"] = re.sub(
                r"\s*·\s*计划产生于北京时间\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*$",
                "", str(payload.get("reason") or ""),
            )
            self.storage.execute(
                """
                INSERT INTO structure_trade_plans(
                    plan_id,user_id,account_id,strategy_id,signal_source_id,
                    symbol,period,plan_group_id,setup_type,direction,entry_mode,
                    status,structure_bar_time,valid_from,expires_at,fingerprint,
                    payload_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    user_id=excluded.user_id, account_id=excluded.account_id,
                    strategy_id=excluded.strategy_id,
                    signal_source_id=excluded.signal_source_id,
                    symbol=excluded.symbol, period=excluded.period,
                    status=excluded.status, structure_bar_time=excluded.structure_bar_time,
                    valid_from=excluded.valid_from, expires_at=excluded.expires_at,
                    fingerprint=excluded.fingerprint, payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    plan["plan_id"], user_id, account_id, strategy_id,
                    signal_source_id, symbol, period, plan["plan_group_id"],
                    plan["setup_type"], plan.get("direction", "none"),
                    plan.get("entry_mode", "watch"), plan.get("status", "watching"),
                    structure_bar_time, int(plan.get("valid_from") or structure_bar_time),
                    int(plan.get("expires_at") or 0), plan.get("fingerprint", ""),
                    json.dumps(payload, ensure_ascii=False), now, now,
                ),
            )
        return plans

    def list_current(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT payload_json,status FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching') ORDER BY updated_at DESC",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            payload["status"] = row["status"]
            result.append(payload)
        return result

    def is_consumed(
        self, user_id: int, account_id: int, deployment_id: str, plan_id: str,
    ) -> bool:
        return self.storage.fetchone(
            "SELECT execution_id FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND status IN ('triggered','ordered','filled') LIMIT 1",
            (user_id, account_id, deployment_id, plan_id),
        ) is not None

    def record_execution(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str, status: str,
        order_id: str = "", reason: str = "", payload: Optional[Dict] = None,
    ) -> None:
        now = int(time.time())
        execution_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{user_id}:{account_id}:{deployment_id}:{plan_id}",
        ).hex[:32]
        self.storage.execute(
            """
            INSERT INTO structure_plan_executions(
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,status,order_id,reason,payload_json,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,account_id,deployment_id,plan_id) DO UPDATE SET
                status=excluded.status,order_id=excluded.order_id,
                reason=excluded.reason,payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,status,order_id,reason,
                json.dumps(payload or {}, ensure_ascii=False),now,now,
            ),
        )
        if status in {"triggered", "ordered", "filled"} and plan_group_id:
            self.storage.execute(
                "UPDATE structure_trade_plans SET status='invalidated',updated_at=? "
                "WHERE user_id=? AND account_id=? AND strategy_id=? "
                "AND plan_group_id=? AND plan_id<>? "
                "AND status IN ('active','watching')",
                (
                    now, user_id, account_id, strategy_id,
                    plan_group_id, plan_id,
                ),
            )
