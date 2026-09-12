"""Durable per-deployment execution gate audit records."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, List, Optional


class ExecutionGateAuditRepository:
    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def audit_id(tick_id: str, account_id: int, deployment_id: str,
                 strategy_id: str, plan_id: str = "",
                 plan_stage: str = "default", direction: str = "none") -> str:
        raw = (
            f"{tick_id}:{account_id}:{deployment_id}:{strategy_id}:"
            f"{plan_id}:{plan_stage}:{direction}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def record(
        self, *, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, tick_id: str, execution_mode: str, symbol: str,
        plan_id: str = "", plan_stage: str = "default", direction: str = "none",
        status: str, reason_code: str, message: str = "",
        gate_trace: Optional[List[Dict]] = None,
        account_snapshot: Optional[Dict] = None,
    ) -> str:
        now = int(time.time())
        audit_id = self.audit_id(
            tick_id, account_id, deployment_id, strategy_id,
            plan_id, plan_stage, str(direction or "none").lower(),
        )
        self.storage.execute(
            """
            INSERT INTO execution_gate_audits(
                audit_id,user_id,account_id,deployment_id,strategy_id,tick_id,
                execution_mode,symbol,plan_id,plan_stage,direction,status,
                reason_code,message,gate_trace_json,account_snapshot_json,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(audit_id) DO UPDATE SET
                plan_id=excluded.plan_id,plan_stage=excluded.plan_stage,
                direction=excluded.direction,status=excluded.status,
                reason_code=excluded.reason_code,message=excluded.message,
                gate_trace_json=excluded.gate_trace_json,
                account_snapshot_json=excluded.account_snapshot_json,
                updated_at=excluded.updated_at
            """,
            (
                audit_id, int(user_id), int(account_id), str(deployment_id),
                str(strategy_id), str(tick_id), str(execution_mode), str(symbol),
                str(plan_id or ""), str(plan_stage or "default"),
                str(direction or "none").lower(), str(status), str(reason_code),
                str(message or ""), json.dumps(gate_trace or [], ensure_ascii=False),
                json.dumps(account_snapshot or {}, ensure_ascii=False), now, now,
            ),
        )
        return audit_id

    def list_for_plans(self, user_id: int, plan_ids: List[str]) -> List[Dict]:
        ids = [str(item) for item in plan_ids if str(item)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.storage.fetchall(
            "SELECT audit_id,user_id,account_id,deployment_id,strategy_id,tick_id,"
            "execution_mode,symbol,plan_id,plan_stage,direction,status,reason_code,"
            "message,gate_trace_json,account_snapshot_json,created_at,updated_at "
            f"FROM execution_gate_audits WHERE user_id=? AND plan_id IN ({placeholders}) "
            "ORDER BY updated_at DESC",
            (int(user_id), *ids),
        )
        result = []
        for row in rows:
            item = dict(row)
            for source, target, fallback in (
                ("gate_trace_json", "gate_trace", []),
                ("account_snapshot_json", "account_snapshot", {}),
            ):
                try:
                    item[target] = json.loads(item.pop(source) or json.dumps(fallback))
                except (TypeError, ValueError, json.JSONDecodeError):
                    item[target] = fallback
            result.append(item)
        return result
