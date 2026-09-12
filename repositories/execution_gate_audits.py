"""Durable, meaningful execution gate audit records.

Tick evaluation is intentionally much more frequent than durable auditing.  The
repository is the common Live/Paper boundary that drops inactive Tick noise and
aggregates repeated blocks into one observable audit episode.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict, List, Optional


class ExecutionGateAuditRepository:
    INACTIVE_REASON_CODES = frozenset({"no_direction", "no_new_trigger"})

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def audit_id(
        tick_id: str, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str = "", plan_stage: str = "default",
        direction: str = "none", *, execution_mode: str = "",
        symbol: str = "", status: str = "", reason_code: str = "",
        aggregate: bool = False,
    ) -> str:
        identity = (
            f"{account_id}:{deployment_id}:{strategy_id}:{execution_mode}:"
            f"{symbol}:{plan_id}:{plan_stage}:{direction}:{status}:{reason_code}"
        )
        raw = f"aggregate:{identity}" if aggregate else f"event:{tick_id}:{identity}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def should_persist(cls, status: str, reason_code: str) -> bool:
        return not (
            str(status or "").lower() == "no_action"
            and str(reason_code or "").lower() in cls.INACTIVE_REASON_CODES
        )

    def record(
        self, *, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, tick_id: str, execution_mode: str, symbol: str,
        plan_id: str = "", plan_stage: str = "default", direction: str = "none",
        status: str, reason_code: str, message: str = "",
        gate_trace: Optional[List[Dict]] = None,
        account_snapshot: Optional[Dict] = None,
    ) -> Optional[str]:
        normalized_status = str(status or "").lower()
        normalized_reason = str(reason_code or "").lower()
        if not self.should_persist(normalized_status, normalized_reason):
            return None

        now = int(time.time())
        normalized_direction = str(direction or "none").lower()
        aggregate = normalized_status in {"blocked", "no_action"}
        audit_id = self.audit_id(
            tick_id, account_id, deployment_id, strategy_id,
            plan_id, plan_stage, normalized_direction,
            execution_mode=str(execution_mode), symbol=str(symbol),
            status=normalized_status, reason_code=normalized_reason,
            aggregate=aggregate,
        )
        occurrence_update = (
            "occurrence_count=execution_gate_audits.occurrence_count + 1,"
            if aggregate else
            "occurrence_count=excluded.occurrence_count,"
        )
        self.storage.execute(
            f"""
            INSERT INTO execution_gate_audits(
                audit_id,user_id,account_id,deployment_id,strategy_id,tick_id,
                execution_mode,symbol,plan_id,plan_stage,direction,status,
                reason_code,message,gate_trace_json,account_snapshot_json,
                first_seen_at,last_seen_at,occurrence_count,last_tick_id,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(audit_id) DO UPDATE SET
                plan_id=excluded.plan_id,plan_stage=excluded.plan_stage,
                direction=excluded.direction,status=excluded.status,
                reason_code=excluded.reason_code,message=excluded.message,
                gate_trace_json=excluded.gate_trace_json,
                account_snapshot_json=excluded.account_snapshot_json,
                last_seen_at=excluded.last_seen_at,
                {occurrence_update}
                last_tick_id=excluded.last_tick_id,
                updated_at=excluded.updated_at
            """,
            (
                audit_id, int(user_id), int(account_id), str(deployment_id),
                str(strategy_id), str(tick_id), str(execution_mode), str(symbol),
                str(plan_id or ""), str(plan_stage or "default"),
                normalized_direction, normalized_status, normalized_reason,
                str(message or ""), json.dumps(gate_trace or [], ensure_ascii=False),
                json.dumps(account_snapshot or {}, ensure_ascii=False),
                now, now, 1, str(tick_id), now, now,
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
            "message,gate_trace_json,account_snapshot_json,first_seen_at,last_seen_at,"
            "occurrence_count,last_tick_id,created_at,updated_at "
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
