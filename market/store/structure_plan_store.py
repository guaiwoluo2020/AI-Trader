"""MySQL-backed structure trade plans and per-deployment executions."""
from __future__ import annotations

import json
import time
import uuid
import re
from typing import Dict, List, Optional

from mysql_repositories import get_storage


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
        # Expiry is authoritative.  An actionable retest/reclaim plan may be
        # valid for several bars and must not disappear merely because the
        # next closed bar produces an observation/no_trade snapshot.
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='invalidated', updated_at=? "
            "WHERE user_id=? AND account_id=? AND strategy_id=? "
            "AND signal_source_id=? AND symbol=? AND period=? "
            "AND expires_at>0 AND expires_at<=? "
            "AND status IN ('active','watching')",
            (now, user_id, account_id, strategy_id, signal_source_id,
             symbol, period, now),
        )
        current = self.storage.fetchall(
            "SELECT plan_id,status,direction,payload_json FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching')",
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

    def invalidate_plan(self, plan_id: str, reason: str) -> None:
        """Persist an event-driven invalidation for a public structure plan."""
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
        payload["status"] = "invalidated"
        payload["invalidated_reason"] = str(reason or "structure_event")
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='invalidated', payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )

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
    ) -> bool:
        return self.storage.fetchone(
            "SELECT execution_id FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND status<>'released' LIMIT 1",
            (user_id, account_id, deployment_id, plan_id),
        ) is not None

    @staticmethod
    def _execution_id(
        user_id: int, account_id: int, deployment_id: str,
        plan_id: str, plan_group_id: str = "",
    ) -> str:
        # Plans in one group are mutually exclusive alternatives.  Using the
        # group as the deterministic primary-key scope makes concurrent Tick
        # workers race on one database key, so only one direction can win for
        # a deployment even when both become triggerable at nearly the same
        # instant.
        claim_scope = str(plan_group_id or plan_id)
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{user_id}:{account_id}:{deployment_id}:{claim_scope}",
        ).hex[:32]

    def claim_execution(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str = "",
        reason: str = "", payload: Optional[Dict] = None,
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
        execution_id = self._execution_id(
            user_id, account_id, deployment_id, plan_id, plan_group_id,
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
                plan_id,plan_group_id,status,order_id,reason,payload_json,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
            """,
            (
                execution_id, user_id, account_id, deployment_id, strategy_id,
                plan_id, plan_group_id, "claimed", "", reason,
                json.dumps(claim_payload, ensure_ascii=False), now, now,
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
        reason: str = "技术失败，允许重新领取",
    ) -> None:
        # Only a claim that has not produced an order may be released. Delete
        # it so the same unique key can be claimed again on the next Tick.
        self.storage.execute(
            "DELETE FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=? "
            "AND status='claimed'",
            (user_id, account_id, deployment_id, plan_id),
        )

    def record_execution(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str, status: str,
        order_id: str = "", reason: str = "", payload: Optional[Dict] = None,
    ) -> None:
        now = int(time.time())
        execution_id = self._execution_id(
            user_id, account_id, deployment_id, plan_id, plan_group_id,
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

    def update_execution_status(
        self, user_id: int, account_id: int, deployment_id: str,
        plan_id: str, status: str, *, order_id: str = "", reason: str = "",
        payload: Optional[Dict] = None,
    ) -> bool:
        allowed = {"claimed", "ordered", "accepted", "pending", "filled",
                   "partially_filled", "rejected", "failed", "timeout", "released"}
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
        params.extend([int(user_id), int(account_id), str(deployment_id), str(plan_id)])
        self.storage.execute(
            "UPDATE structure_plan_executions SET " + ",".join(changes) +
            " WHERE user_id=? AND account_id=? AND deployment_id=? AND plan_id=?",
            tuple(params),
        )
        return self.storage.fetchone(
            "SELECT execution_id FROM structure_plan_executions WHERE user_id=? AND account_id=? "
            "AND deployment_id=? AND plan_id=? LIMIT 1",
            (int(user_id), int(account_id), str(deployment_id), str(plan_id)),
        ) is not None

    def list_executions(self, user_id: int, plan_ids: List[str]) -> List[Dict]:
        ids = [str(item) for item in plan_ids if str(item)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.storage.fetchall(
            "SELECT execution_id,user_id,account_id,deployment_id,strategy_id,"
            "plan_id,plan_group_id,status,order_id,reason,created_at,updated_at "
            f"FROM structure_plan_executions WHERE user_id=? AND plan_id IN ({placeholders}) "
            "ORDER BY updated_at DESC",
            (int(user_id), *ids),
        )
        return [dict(row) for row in rows]
