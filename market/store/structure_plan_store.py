"""MySQL-backed structure trade plans and per-deployment executions."""
from __future__ import annotations

import json
import time
import uuid
import re
from typing import Dict, List, Optional

from mysql_repositories import get_storage
from system_event_log import SystemEventLogRepository


def opportunity_status_for_execution(stage: str, execution_status: str) -> str:
    """Normalize broker/Paper receipts into a stage-scoped opportunity state."""
    stage_prefix = "initial" if str(stage or "") == "initial" else (
        "breakout" if str(stage or "") == "breakout" else "single"
    )
    normalized = {
        "filled": "filled", "partially_filled": "partially_filled",
        "accepted": "ordered", "pending": "ordered", "ordered": "ordered",
        "rejected": "failed", "failed": "failed", "timeout": "failed",
        "canceled": "failed", "released": "failed",
    }.get(str(execution_status or "").lower())
    return f"{stage_prefix}_{normalized}" if normalized else ""


class StructureTradePlanRepository:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    def replace_scope(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
        plans: List[Dict], structure_bar_time: int,
    ) -> List[Dict]:
        now = int(time.time())
        self.expire_due_plans(
            user_id=user_id, account_id=account_id, strategy_id=strategy_id,
            signal_source_id=signal_source_id, symbol=symbol, period=period,
            now=now,
        )
        keep = {str(plan["plan_id"]) for plan in plans}
        new_actionable = {
            str(plan["plan_id"])
            for plan in plans
            if (
                str(plan.get("status") or "") == "active"
                and str(plan.get("direction") or "") in {"buy", "sell"}
                and float(plan.get("entry_price") or 0) > 0
            )
        }
        # A rolling structure window can move its anchor forward by one bar even
        # though the actionable opportunity has not changed.  Capture the
        # currently active semantic plan before invalidating the old bar so the
        # replacement keeps the original generation time.  A plan only receives
        # a new timestamp when its setup, direction or entry mode actually
        # changes (or after the prior opportunity has already disappeared).
        previous_active = self.storage.fetchall(
            "SELECT plan_id,setup_type,direction,entry_mode,status,expires_at,"
            "payload_json,created_at "
            "FROM structure_trade_plans WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching','event_suppressed') ORDER BY updated_at DESC",
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
        # Expiry is authoritative.  An actionable retest/reclaim plan may be
        # valid for several bars and must not disappear merely because the
        # next closed bar produces an observation/no_trade snapshot.
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='invalidated', updated_at=? "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND signal_source_id=? AND symbol=? AND period=? "
            "AND expires_at>0 AND expires_at<=? "
            "AND status IN ('active','watching','event_suppressed')",
            (now, user_id, account_id, strategy_id, signal_source_id,
             symbol, period, now),
        )
        current = self.storage.fetchall(
            "SELECT plan_id,status,direction,payload_json FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching','event_suppressed')",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        for row in current:
            plan_id = str(row["plan_id"])
            if plan_id not in keep:
                # A new actionable opportunity supersedes the previous one.
                self.supersede_plan(plan_id, "superseded_by_new_plan")
        for plan in plans:
            payload = dict(plan)
            existing = self.storage.fetchone(
                "SELECT payload_json,created_at,status,expires_at "
                "FROM structure_trade_plans "
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
                same_live_opportunity = (
                    str(plan.get("status") or "") == "active"
                    and str(plan.get("direction") or "") in {"buy", "sell"}
                    and str(existing["status"] or "") == "active"
                    and int(existing["expires_at"] or 0) > now
                )
                if same_live_opportunity:
                    # Keep the original boundary, validity window and payload.
                    # A repeated closed-bar calculation is the same opportunity,
                    # not permission to move the entry or extend its lifetime.
                    continue
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
        # The generator cache must include retained actionable plans as well as
        # the latest observation rows; returning only ``plans`` would keep the
        # database correct but make Tick evaluation forget the retained plan.
        return self.list_current(
            user_id, account_id, strategy_id,
            signal_source_id, symbol, period,
        )

    def list_current(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
    ) -> List[Dict]:
        self.expire_due_plans(
            user_id=user_id, account_id=account_id, strategy_id=strategy_id,
            signal_source_id=signal_source_id, symbol=symbol, period=period,
        )
        rows = self.storage.fetchall(
            "SELECT payload_json,status FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching','event_suppressed') ORDER BY updated_at DESC",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            payload["status"] = row["status"]
            result.append(payload)
        return result

    def list_opportunity(
        self, user_id: int, opportunity_id: str,
        symbol: str = "", period: str = "",
    ) -> List[Dict]:
        """Load all public plan stages belonging to one opportunity."""
        clauses = ["user_id=?"]
        params: List[object] = [int(user_id)]
        if symbol:
            clauses.append("symbol=?"); params.append(str(symbol))
        if period:
            clauses.append("period=?"); params.append(str(period).upper())
        rows = self.storage.fetchall(
            "SELECT plan_id,status,symbol,period,payload_json,created_at,updated_at "
            "FROM structure_trade_plans WHERE " + " AND ".join(clauses) +
            " ORDER BY updated_at DESC",
            tuple(params),
        )
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if str(payload.get("opportunity_id") or "") != str(opportunity_id):
                continue
            payload["status"] = row["status"]
            payload["created_at"] = int(row.get("created_at") or 0)
            payload["updated_at"] = int(row.get("updated_at") or 0)
            result.append(payload)
        return result

    def expire_due_plans(
        self, user_id: int = None, account_id: int = None,
        strategy_id: str = None, signal_source_id: str = None,
        symbol: str = None, period: str = None, now: int = None,
        max_age_seconds: int = 24 * 60 * 60,
    ) -> int:
        """Invalidate stale waiting plans even when no new Tick arrives.

        ``expires_at`` is authoritative for new plans, while the generated
        timestamp/created timestamp provides a hard 24-hour safety cap for
        rows created before that rule existed or rows whose lifetime was
        configured too broadly.  Filtering is done in Python to keep this
        compatible with the shared MySQL storage adapter without relying on
        database-specific JSON functions.
        """
        now = int(now or time.time())
        clauses = [
            "status IN ('active','watching','event_suppressed')",
        ]
        params = []
        for column, value in (
            ("user_id", user_id), ("account_id", account_id),
            ("strategy_id", strategy_id), ("signal_source_id", signal_source_id),
            ("symbol", symbol), ("period", period),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        rows = self.storage.fetchall(
            "SELECT plan_id,status,payload_json,created_at,expires_at "
            "FROM structure_trade_plans WHERE " + " AND ".join(clauses),
            tuple(params),
        )
        expired = 0
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            generated_at = int(payload.get("generated_at") or row["created_at"] or 0)
            expires_at = int(row["expires_at"] or payload.get("expires_at") or 0)
            due_by_expiry = expires_at > 0 and expires_at <= now
            due_by_safety = generated_at > 0 and generated_at + max_age_seconds <= now
            if not due_by_expiry and not due_by_safety:
                continue
            payload["status"] = "invalidated"
            payload["invalidated_reason"] = (
                "计划超过24小时未触发，自动取消"
                if due_by_safety else "计划有效期已结束"
            )
            self.storage.execute(
                "UPDATE structure_trade_plans SET status='invalidated', "
                "payload_json=?, updated_at=? WHERE plan_id=? "
                "AND status IN ('active','watching','event_suppressed')",
                (json.dumps(payload, ensure_ascii=False), now, str(row["plan_id"])),
            )
            expired += 1
        return expired

    def invalidate_plan(self, plan_id: str, reason: str) -> None:
        """Persist an event-driven invalidation for a public structure plan."""
        now = int(time.time())
        row = self.storage.fetchone(
            "SELECT payload_json,status FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row or str(row["status"] or "") not in {"active", "watching", "event_suppressed"}:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payload["status"] = "invalidated"
        payload["invalidated_reason"] = str(reason or "structure_event")
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='invalidated', payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )

    def suppress_plan(self, plan_id: str, event_risk: Dict) -> None:
        """Pause a live plan during an event window while retaining its audit trail."""
        row = self.storage.fetchone(
            "SELECT payload_json,status,user_id,account_id,symbol,period,strategy_id "
            "FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row or str(row["status"] or "") not in {"active", "watching", "event_suppressed"}:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        previous_status = str(row["status"] or "")
        previous_event = (payload.get("event_risk") or {}).get("id")
        if previous_status == "event_suppressed" and previous_event == (event_risk or {}).get("id"):
            return
        payload["status"] = "event_suppressed"
        payload["plan_stage"] = "event_suppressed"
        payload["event_risk"] = dict(event_risk or {})
        now = int(time.time())
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='event_suppressed', payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )
        # Keep a durable, searchable audit record instead of relying only on the
        # plan JSON, so the execution center can explain every risk suppression.
        try:
            SystemEventLogRepository(self.storage).add({
                "occurred_at": now, "level": "warning", "category": "risk",
                "event_type": "structure_plan_event_suppressed",
                "event_name": "结构计划被市场事件暂停",
                "user_id": row.get("user_id"), "account_id": row.get("account_id"),
                "symbol": row.get("symbol"), "actor_type": "system",
                "entity_type": "structure_trade_plan", "entity_id": str(plan_id),
                "correlation_id": str(plan_id), "status": "event_suppressed",
                "message": str((event_risk or {}).get("reason") or "市场事件风险窗口"),
                "detail": {"plan_id": str(plan_id), "strategy_id": row.get("strategy_id"),
                           "period": row.get("period"), "event_risk": dict(event_risk or {}),
                           "entry_price": payload.get("entry_price"), "stop_loss": payload.get("stop_loss"),
                           "take_profit": payload.get("take_profit")},
            })
        except Exception as exc:
            # Audit failure must not block the risk decision itself.
            print(f"[StructurePlan] 风险暂停审计写入失败: {exc}")

    def supersede_plan(self, plan_id: str, reason: str = "superseded_by_new_plan") -> None:
        """Mark a live plan as replaced while preserving an explicit audit reason."""
        now = int(time.time())
        row = self.storage.fetchone(
            "SELECT payload_json,status FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row or str(row["status"] or "") not in {"active", "watching"}:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payload["status"] = "superseded"
        payload["invalidated_reason"] = str(reason or "superseded_by_new_plan")
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='superseded', payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )

    def update_payload(self, plan_id: str, changes: Dict) -> None:
        """Persist small runtime state changes without replacing the plan."""
        row = self.storage.fetchone(
            "SELECT payload_json FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        payload.update(changes or {})
        self.storage.execute(
            "UPDATE structure_trade_plans SET payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), int(time.time()), str(plan_id)),
        )

    def is_consumed(
        self, user_id: int, account_id: int, deployment_id: str, plan_id: str,
        plan_stage: str = "", direction: str = "",
    ) -> bool:
        return self.storage.fetchone(
            "SELECT execution_id FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND plan_stage=? AND direction=? "
            "AND status<>'released' LIMIT 1",
            (user_id, account_id, deployment_id, plan_id,
             str(plan_stage or "default"), str(direction or "none")),
        ) is not None

    @staticmethod
    def _execution_id(
        user_id: int, account_id: int, deployment_id: str,
        plan_id: str, plan_group_id: str = "", plan_stage: str = "",
        direction: str = "",
    ) -> str:
        # Group alternatives intentionally share one claim id within a stage,
        # while initial/breakout remain independent opportunities.
        stage = str(plan_stage or "default")
        claim_scope = (
            f"group:{plan_group_id}:{stage}"
            if plan_group_id else
            f"plan:{plan_id}:{stage}:{str(direction or 'none')}"
        )
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{user_id}:{account_id}:{deployment_id}:{claim_scope}",
        ).hex[:32]

    def claim_execution(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str = "",
        plan_stage: str = "", direction: str = "", tick_id: str = "",
        execution_mode: str = "", reason_code: str = "claimed",
        reason: str = "", payload: Optional[Dict] = None,
        gate_trace: Optional[List[Dict]] = None,
        account_snapshot: Optional[Dict] = None,
    ) -> bool:
        """Atomically claim one public plan for one deployment.

        ``INSERT ... DO NOTHING`` is translated to ``INSERT IGNORE`` by the
        MySQL adapter.  A random token in the row lets us distinguish our own
        successful insert from a pre-existing claim without relying on a
        driver-specific rowcount.
        """
        now = int(time.time())
        claim_token = uuid.uuid4().hex
        claim_payload = dict(payload or {})
        claim_payload["claim_token"] = claim_token
        plan_stage = str(plan_stage or claim_payload.get("plan_stage")
                         or claim_payload.get("trade_opportunity_stage") or "default")
        direction = str(direction or claim_payload.get("direction") or "none").lower()
        execution_id = self._execution_id(
            user_id, account_id, deployment_id, plan_id, plan_group_id,
            plan_stage, direction,
        )
        claimed_sibling = self.storage.fetchone(
            "SELECT plan_id FROM structure_plan_executions "
            "WHERE execution_id=? LIMIT 1",
            (execution_id,),
        )
        if claimed_sibling and str(claimed_sibling["plan_id"] or "") != str(plan_id):
            return False
        self.storage.execute(
            """
            INSERT INTO structure_plan_executions(
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,plan_stage,direction,tick_id,execution_mode,
                status,order_id,reason_code,reason,payload_json,gate_trace_json,
                account_snapshot_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
            """,
            (
                execution_id, user_id, account_id, deployment_id, strategy_id,
                plan_id, plan_group_id, plan_stage, direction, str(tick_id or ""),
                str(execution_mode or ""), "claimed", "", str(reason_code or "claimed"),
                reason, json.dumps(claim_payload, ensure_ascii=False),
                json.dumps(gate_trace or [], ensure_ascii=False),
                json.dumps(account_snapshot or {}, ensure_ascii=False), now, now,
            ),
        )
        row = self.storage.fetchone(
            "SELECT plan_id,payload_json FROM structure_plan_executions "
            "WHERE execution_id=? LIMIT 1",
            (execution_id,),
        )
        if not row or str(row["plan_id"] or "") != str(plan_id):
            return False
        try:
            stored_payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            stored_payload = {}
        return stored_payload.get("claim_token") == claim_token

    def release_claim(
        self, user_id: int, account_id: int, deployment_id: str, plan_id: str,
        plan_stage: str = "", direction: str = "",
        reason: str = "技术失败，允许重新领取",
    ) -> None:
        # Only a claim that has not produced an order may be released. Delete
        # it so the same unique key can be claimed again on the next Tick.
        self.storage.execute(
            "DELETE FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND plan_stage=? AND direction=? "
            "AND status='claimed'",
            (user_id, account_id, deployment_id, plan_id,
             str(plan_stage or "default"), str(direction or "none").lower()),
        )

    def record_execution(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str, status: str,
        order_id: str = "", reason: str = "", payload: Optional[Dict] = None,
        plan_stage: str = "", direction: str = "", tick_id: str = "",
        execution_mode: str = "", reason_code: str = "",
        gate_trace: Optional[List[Dict]] = None,
        account_snapshot: Optional[Dict] = None,
    ) -> None:
        now = int(time.time())
        execution_payload = dict(payload or {})
        plan_stage = str(plan_stage or execution_payload.get("plan_stage")
                         or execution_payload.get("trade_opportunity_stage") or "default")
        direction = str(direction or execution_payload.get("direction") or "none").lower()
        execution_id = self._execution_id(
            user_id, account_id, deployment_id, plan_id, plan_group_id,
            plan_stage, direction,
        )
        claimed_sibling = self.storage.fetchone(
            "SELECT plan_id FROM structure_plan_executions "
            "WHERE execution_id=? LIMIT 1",
            (execution_id,),
        )
        if claimed_sibling and str(claimed_sibling["plan_id"] or "") != str(plan_id):
            # The opposite alternative in this group already owns the claim.
            # Never let a late/legacy callback overwrite its execution row.
            return
        self.storage.execute(
            """
            INSERT INTO structure_plan_executions(
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,plan_stage,direction,tick_id,execution_mode,
                status,order_id,reason_code,reason,payload_json,gate_trace_json,
                account_snapshot_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,account_id,deployment_id,plan_id,plan_stage,direction) DO UPDATE SET
                status=excluded.status,order_id=excluded.order_id,
                reason_code=excluded.reason_code,reason=excluded.reason,
                payload_json=excluded.payload_json,
                gate_trace_json=excluded.gate_trace_json,
                account_snapshot_json=excluded.account_snapshot_json,
                updated_at=excluded.updated_at
            """,
            (
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,plan_stage,direction,str(tick_id or ""),
                str(execution_mode or ""),status,order_id,
                str(reason_code or status),reason,
                json.dumps(execution_payload, ensure_ascii=False),
                json.dumps(gate_trace or [], ensure_ascii=False),
                json.dumps(account_snapshot or {}, ensure_ascii=False),now,now,
            ),
        )
        self._update_opportunity_state(plan_id, status, execution_payload)

    def _update_opportunity_state(self, plan_id: str, execution_status: str,
                                  execution_payload: Optional[Dict] = None) -> None:
        """Mirror execution receipt status into the public plan payload."""
        row = self.storage.fetchone(
            "SELECT user_id,account_id,symbol,period,payload_json "
            "FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row:
            return
        try:
            plan = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            plan = {}
        payload = execution_payload or {}
        stage = str(plan.get("opportunity_stage") or payload.get("trade_opportunity_stage") or "")
        opportunity_id = str(plan.get("opportunity_id") or payload.get("trade_opportunity_id") or "")
        if not opportunity_id or not stage:
            return
        status = str(execution_status or "").lower()
        opportunity_status = opportunity_status_for_execution(stage, status)
        if not opportunity_status:
            return
        prefix = opportunity_status.split("_", 1)[0]
        normalized = opportunity_status.split("_", 1)[1]
        plan[f"{prefix}_execution_status"] = normalized
        plan[f"{prefix}_execution_status_updated_at"] = int(time.time())
        if execution_payload and execution_payload.get("order_id"):
            plan[f"{prefix}_order_id"] = str(execution_payload["order_id"])
        plan["opportunity_status"] = opportunity_status
        plan["opportunity_execution_status"] = status
        plan["opportunity_status_updated_at"] = int(time.time())
        if prefix == "initial" and normalized == "filled":
            plan["breakout_eligible"] = False
        if prefix == "breakout" and normalized == "filled":
            plan["breakout_eligible"] = False
        plan.update({
            "last_execution_status": status,
        })
        self.storage.execute(
            "UPDATE structure_trade_plans SET payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(plan, ensure_ascii=False), int(time.time()), str(plan_id)),
        )
        self._sync_related_opportunity_plans(
            row, opportunity_id, plan,
            exclude_plan_id=str(plan_id),
        )

    @staticmethod
    def _aggregate_opportunity_status(payload: Dict) -> str:
        """Return the public status shared by all plans in one opportunity."""
        breakout = str(payload.get("breakout_execution_status") or "").lower()
        initial = str(payload.get("initial_execution_status") or "").lower()
        protected = bool(payload.get("initial_protection_confirmed"))
        if breakout == "filled":
            return "breakout_filled"
        if breakout == "partially_filled":
            return "breakout_partially_filled"
        if breakout == "ordered":
            return "breakout_ordered"
        if breakout == "failed":
            return "breakout_failed"
        if protected:
            return "breakout_eligible"
        if initial == "filled":
            return "protection_pending"
        if initial == "partially_filled":
            return "initial_partially_filled"
        if initial == "ordered":
            return "initial_ordered"
        if initial == "failed":
            return "initial_failed"
        return str(payload.get("opportunity_status") or "pending")

    def _sync_related_opportunity_plans(
        self, source_row: Dict, opportunity_id: str, source_plan: Dict,
        *, exclude_plan_id: str = "",
    ) -> int:
        """Propagate stage state to the initial/breakout sibling plans."""
        if not opportunity_id:
            return 0
        rows = self.storage.fetchall(
            "SELECT plan_id,payload_json FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? AND symbol=? AND period=?",
            (int(source_row.get("user_id") or 0), int(source_row.get("account_id") or 0),
             str(source_row.get("symbol") or ""), str(source_row.get("period") or "")),
        )
        aggregate = self._aggregate_opportunity_status(source_plan)
        changed = 0
        for row in rows:
            sibling_id = str(row.get("plan_id") or "")
            if sibling_id == str(exclude_plan_id):
                continue
            try:
                sibling = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                sibling = {}
            if str(sibling.get("opportunity_id") or "") != str(opportunity_id):
                continue
            sibling.update({
                "opportunity_status": aggregate,
                "opportunity_status_updated_at": int(time.time()),
                "breakout_eligible": aggregate == "breakout_eligible",
            })
            if source_plan.get("initial_protection_confirmed"):
                sibling["initial_protection_confirmed"] = True
                sibling["initial_protection_status"] = "confirmed"
                sibling["protection_confirmed_at"] = source_plan.get("protection_confirmed_at")
            self.storage.execute(
                "UPDATE structure_trade_plans SET payload_json=?, updated_at=? WHERE plan_id=?",
                (json.dumps(sibling, ensure_ascii=False), int(time.time()), sibling_id),
            )
            changed += 1
        return changed

    def confirm_protection_for_account(self, user_id: int, account_id: int,
                                       symbol: str, positions: List[Dict]) -> int:
        """Mark filled initial opportunities protected by broker SL data."""
        protected = []
        for item in positions or []:
            direction = str(item.get("direction") or "").lower()
            if not direction:
                direction = "buy" if str(item.get("type") or "").upper() == "BUY" else "sell"
            try:
                stop = float(item.get("sl") or item.get("stop_loss") or 0)
            except (TypeError, ValueError):
                stop = 0.0
            if direction in {"buy", "sell"} and stop > 0:
                protected.append(direction)
        if not protected:
            return 0
        rows = self.storage.fetchall(
            "SELECT plan_id,payload_json FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND status IN ('filled','partially_filled')",
            (int(user_id), int(account_id)),
        )
        changed = 0
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            if str(payload.get("trade_opportunity_stage") or "") != "initial":
                continue
            if str(payload.get("direction") or "").lower() not in protected:
                continue
            plan_row = self.storage.fetchone(
                "SELECT user_id,account_id,symbol,period,payload_json "
                "FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
                (str(row["plan_id"]),),
            )
            if not plan_row:
                continue
            try:
                plan = json.loads(plan_row["payload_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                plan = {}
            if plan.get("opportunity_status") == "protection_confirmed":
                continue
            plan.update({
                "opportunity_status": "protection_confirmed",
                "initial_protection_status": "confirmed",
                "initial_protection_confirmed": True,
                "breakout_eligible": True,
                "protection_confirmed_at": int(time.time()),
                "protection_confirmation_source": "account_position_snapshot",
            })
            self.storage.execute(
                "UPDATE structure_trade_plans SET payload_json=?, updated_at=? WHERE plan_id=?",
                (json.dumps(plan, ensure_ascii=False), int(time.time()), str(row["plan_id"])),
            )
            self._sync_related_opportunity_plans(
                plan_row, str(plan.get("opportunity_id") or ""), plan,
                exclude_plan_id=str(row["plan_id"]),
            )
            changed += 1
        return changed

    def update_execution_status(
        self, user_id: int, account_id: int, deployment_id: str,
        plan_id: str, status: str, *, order_id: str = "", reason: str = "",
        payload: Optional[Dict] = None, plan_stage: str = "",
        direction: str = "", reason_code: str = "",
    ) -> bool:
        allowed = {"claimed", "ordered", "accepted", "pending", "filled",
                   "partially_filled", "rejected", "failed", "timeout",
                   "canceled", "released"}
        status = str(status or "").lower()
        if status not in allowed:
            raise ValueError(f"不支持的计划执行状态: {status}")
        now = int(time.time())
        changes = ["status=?", "reason=?", "updated_at=?"]
        params = [status, str(reason or ""), now]
        if order_id:
            changes.append("order_id=?"); params.append(str(order_id))
        if payload is not None:
            changes.append("payload_json=?"); params.append(json.dumps(payload, ensure_ascii=False))
        plan_stage = str(plan_stage or (payload or {}).get("trade_opportunity_stage") or "default")
        direction = str(direction or (payload or {}).get("direction") or "none").lower()
        if reason_code:
            changes.append("reason_code=?"); params.append(str(reason_code))
        params.extend([int(user_id), int(account_id), str(deployment_id), str(plan_id),
                       plan_stage, direction])
        self.storage.execute(
            "UPDATE structure_plan_executions SET " + ",".join(changes) +
            " WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND plan_stage=? AND direction=?",
            tuple(params),
        )
        status_payload = dict(payload or {})
        if order_id:
            status_payload.setdefault("order_id", str(order_id))
        self._update_opportunity_state(plan_id, status, status_payload)
        return self.storage.fetchone(
            "SELECT execution_id FROM structure_plan_executions WHERE user_id=? AND account_id=? "
            "AND deployment_id=? AND plan_id=? AND plan_stage=? AND direction=? LIMIT 1",
            (int(user_id), int(account_id), str(deployment_id), str(plan_id),
             plan_stage, direction),
        ) is not None

    def list_executions(self, user_id: int, plan_ids: List[str]) -> List[Dict]:
        ids = [str(item) for item in plan_ids if str(item)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.storage.fetchall(
            "SELECT execution_id,user_id,account_id,deployment_id,strategy_id,"
            "plan_id,plan_group_id,plan_stage,direction,tick_id,execution_mode,"
            "status,order_id,reason_code,reason,gate_trace_json,"
            "account_snapshot_json,created_at,updated_at "
            f"FROM structure_plan_executions WHERE user_id=? AND plan_id IN ({placeholders}) "
            "ORDER BY updated_at DESC",
            (int(user_id), *ids),
        )
        return [dict(row) for row in rows]
