"""MySQL-backed structure trade plans and per-deployment executions."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
import re
import threading
from typing import Dict, List, Optional

from mysql_repositories import get_storage
from system_event_log import SystemEventLogRepository
from runtime_cache import TTLCache


def should_supersede_live_plan(
    *,
    current_status: str,
    plan_id: str,
    keep_ids: set[str],
    semantic_key: tuple,
    opportunity_key: tuple,
    incoming_observation_keys: set,
    incoming_active_keys: set,
) -> bool:
    """Keep untriggered live plans until a new actionable opportunity appears.

    A closed-bar refresh often emits only ``no_trade`` / watch snapshots. Those
    observations must not cancel an active retest or reclaim plan before its
    expiry, or M1 STRUCTURE PLAN never gets a Tick in the entry zone.
    """
    if str(plan_id) in keep_ids:
        return False
    live = str(current_status or "") in {"active", "event_suppressed"}
    if live:
        if opportunity_key in incoming_active_keys:
            return False
        return bool(incoming_active_keys)
    if semantic_key in incoming_observation_keys:
        return False
    if opportunity_key in incoming_active_keys:
        return False
    return True


def opportunity_status_for_execution(stage: str, execution_status: str) -> str:
    """Normalize broker/Paper receipts into a stage-scoped opportunity state."""
    stage_prefix = "initial" if str(stage or "") == "initial" else (
        "breakout" if str(stage or "") == "breakout" else "single"
    )
    normalized = {
        "filled": "filled", "partially_filled": "partially_filled",
        "accepted": "ordered", "pending": "ordered", "ordered": "ordered",
        "rejected": "failed", "failed": "failed", "timeout": "failed",
        "canceled": "failed", "released": "failed", "closed": "closed",
    }.get(str(execution_status or "").lower())
    return f"{stage_prefix}_{normalized}" if normalized else ""


class StructureTradePlanRepository:
    _current_cache = TTLCache(ttl_seconds=3, max_items=8192)
    # Multiple account engines can observe the same closed bar concurrently.
    # Structure plans are canonical per user/source/symbol/period, so those
    # refreshes must share one read-modify-write section.
    _scope_locks = {}
    _scope_locks_guard = threading.RLock()

    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    @classmethod
    def _scope_lock(cls, key):
        with cls._scope_locks_guard:
            lock = cls._scope_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._scope_locks[key] = lock
            return lock

    def replace_scope(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
        plans: List[Dict], structure_bar_time: int,
    ) -> List[Dict]:
        # account_id/strategy_id are retained in the storage API, but the
        # market structure caller intentionally uses 0/"" for the canonical
        # shared scope. Include all fields to avoid serializing unrelated
        # repository uses if this method is reused later.
        key = (
            int(user_id), int(account_id), str(strategy_id),
            str(signal_source_id), str(symbol).upper(), str(period).upper(),
        )
        with self._scope_lock(key):
            return self._replace_scope_locked(
                user_id, account_id, strategy_id, signal_source_id,
                symbol, period, plans, structure_bar_time,
            )

    def _replace_scope_locked(
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
        plans = self._bind_opportunity_cycles(user_id, symbol, period, plans)
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
            "SELECT plan_id,status,setup_type,entry_mode,direction,payload_json "
            "FROM structure_trade_plans "
            "WHERE user_id=? AND account_id=? "
            "AND strategy_id=? AND signal_source_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching','event_suppressed')",
            (user_id, account_id, strategy_id, signal_source_id, symbol, period),
        )
        current_by_semantic = {}
        current_by_opportunity = {}
        for row in current:
            semantic_key = (
                str(row.get("setup_type") or ""),
                str(row.get("direction") or "none"),
                str(row.get("entry_mode") or "watch"),
            )
            current_by_semantic.setdefault(semantic_key, row)
            try:
                current_payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                current_payload = {}
            opportunity_id = str(current_payload.get("opportunity_id") or "")
            if opportunity_id:
                current_by_opportunity.setdefault(
                    (opportunity_id, *semantic_key), row,
                )
        incoming_observation_keys = {
            (
                str(plan.get("setup_type") or ""),
                str(plan.get("direction") or "none"),
                str(plan.get("entry_mode") or "watch"),
            )
            for plan in plans
            if str(plan.get("status") or "watching") in {"watching", "event_suppressed"}
        }
        incoming_active_keys = {
            (
                str(plan.get("opportunity_id") or ""),
                str(plan.get("setup_type") or ""),
                str(plan.get("direction") or "none"),
                str(plan.get("entry_mode") or "watch"),
            )
            for plan in plans
            if (
                str(plan.get("status") or "") == "active"
                and str(plan.get("opportunity_id") or "")
            )
        }
        for row in current:
            plan_id = str(row["plan_id"])
            semantic_key = (
                str(row.get("setup_type") or ""),
                str(row.get("direction") or "none"),
                str(row.get("entry_mode") or "watch"),
            )
            try:
                current_payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                current_payload = {}
            current_opportunity_key = (
                str(current_payload.get("opportunity_id") or ""),
                *semantic_key,
            )
            if should_supersede_live_plan(
                current_status=str(row.get("status") or ""),
                plan_id=plan_id,
                keep_ids=keep,
                semantic_key=semantic_key,
                opportunity_key=current_opportunity_key,
                incoming_observation_keys=incoming_observation_keys,
                incoming_active_keys=incoming_active_keys,
            ):
                # A new actionable opportunity supersedes the previous one.
                self.supersede_plan(plan_id, "superseded_by_new_plan")
        for plan in plans:
            # Active plans retain their identity across small price/boundary
            # changes. The opportunity id is the structural identity; the
            # setup/direction/mode guards prevent reusing a changed trade.
            if str(plan.get("status") or "") == "active":
                opportunity_key = (
                    str(plan.get("opportunity_id") or ""),
                    str(plan.get("setup_type") or ""),
                    str(plan.get("direction") or "none"),
                    str(plan.get("entry_mode") or "watch"),
                )
                retained = current_by_opportunity.get(opportunity_key)
                if retained:
                    plan["plan_id"] = str(retained["plan_id"])
                    try:
                        retained_payload = json.loads(retained["payload_json"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        retained_payload = {}
                    if retained_payload.get("plan_group_id"):
                        plan["plan_group_id"] = retained_payload["plan_group_id"]
            # A repeated non-active observation is a refreshed snapshot, not a
            # new opportunity. Reuse the current row so one symbol/period does
            # not create a superseded record on every closed candle. This also
            # covers directional setup watchers such as triangle_breakout_watch.
            if (
                str(plan.get("status") or "watching") in {"watching", "event_suppressed"}
            ):
                semantic_key = (
                    str(plan.get("setup_type") or ""),
                    str(plan.get("direction") or "none"),
                    str(plan.get("entry_mode") or "watch"),
                )
                retained = current_by_semantic.get(semantic_key)
                if retained:
                    plan["plan_id"] = str(retained["plan_id"])
                    try:
                        retained_payload = json.loads(retained["payload_json"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        retained_payload = {}
                    if retained_payload.get("plan_group_id"):
                        plan["plan_group_id"] = retained_payload["plan_group_id"]
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
        # Drop the short-lived read cache so Tick evaluation sees retained
        # actionable plans immediately, not a stale no_trade snapshot.
        cache_key = (
            int(user_id), int(account_id), str(strategy_id),
            str(signal_source_id), str(symbol), str(period).upper(),
        )
        with self._current_cache._lock:
            self._current_cache._items.pop(cache_key, None)
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
        key = (int(user_id), int(account_id), str(strategy_id),
               str(signal_source_id), str(symbol), str(period).upper())
        cached = self._current_cache.get(key, "plans")
        if cached is not None:
            return cached
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
        self._current_cache.set(key, result, "plans")
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

    @staticmethod
    def _id_hash(*parts, length=32) -> str:
        raw = ":".join(str(part) for part in parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]

    @staticmethod
    def _family_key(plan: Dict) -> tuple:
        return (
            str(plan.get("opportunity_family_id") or ""),
            str(plan.get("structure_segment_id") or ""),
            str(plan.get("setup_type") or ""),
            str(plan.get("direction") or "none"),
            str(plan.get("entry_mode") or "watch"),
        )

    def _rewrite_cycle_identity(self, plan: Dict, cycle: int) -> Dict:
        cycle = max(1, int(cycle or 1))
        family = str(plan.get("opportunity_family_id") or "")
        plan["opportunity_cycle"] = cycle
        if family:
            plan["opportunity_id"] = self._id_hash(family, cycle)
            plan["plan_group_id"] = self._id_hash("group", family, cycle)
            zone_revision = str((plan.get("validation_evidence") or {}).get("zone_revision") or "")
            plan["plan_id"] = self._id_hash("plan", family, cycle, zone_revision)
        return plan

    def _round_state(self, user_id: int, plan_id: str) -> str:
        rows = self.storage.fetchall(
            "SELECT account_id,status,order_id FROM structure_plan_executions "
            "WHERE user_id=? AND plan_id=?",
            (int(user_id), str(plan_id)),
        ) or []
        if not rows:
            return "idle"
        statuses = {str(row.get("status") or "").lower() for row in rows}
        if statuses & {"claimed", "accepted", "ordered", "pending"}:
            return "in_flight"
        filled = [row for row in rows if str(row.get("status") or "").lower() in {"filled", "partially_filled"}]
        if filled:
            return "in_flight" if self._plan_has_open_position(user_id, filled) else "completed"
        if statuses <= {"closed", "rejected", "failed", "timeout", "canceled", "released"}:
            return "completed"
        return "idle"

    def _plan_has_open_position(self, user_id: int, executions: List[Dict]) -> bool:
        order_ids = [str(row.get("order_id") or "") for row in executions if str(row.get("order_id") or "")]
        if not order_ids:
            return True
        placeholders = ",".join("?" for _ in order_ids)
        if self.storage.fetchone(
            "SELECT position_id FROM paper_positions "
            f"WHERE user_id=? AND status='open' AND order_id IN ({placeholders}) LIMIT 1",
            (int(user_id), *order_ids),
        ):
            return True
        live_positions = self.storage.fetchall(
            "SELECT DISTINCT mt5_position_id FROM trade_execution_reports "
            f"WHERE user_id=? AND success=1 AND mt5_position_id>0 AND order_id IN ({placeholders})",
            (int(user_id), *order_ids),
        ) or []
        for item in live_positions:
            position_id = int(item.get("mt5_position_id") or 0)
            if position_id <= 0:
                continue
            opening = self.storage.fetchone(
                "SELECT volume FROM live_trade_deals WHERE user_id=? AND mt5_position_id=? AND entry_type=0 "
                "ORDER BY deal_timestamp, ticket LIMIT 1",
                (int(user_id), position_id),
            )
            if not opening:
                return True
            closed = self.storage.fetchone(
                "SELECT SUM(volume) AS closed_volume FROM live_trade_deals "
                "WHERE user_id=? AND mt5_position_id=? AND entry_type<>0",
                (int(user_id), position_id),
            ) or {}
            if float(closed.get("closed_volume") or 0) + 1e-9 < float(opening.get("volume") or 0):
                return True
        return False

    def _bind_opportunity_cycles(self, user_id: int, symbol: str, period: str, plans: List[Dict]) -> List[Dict]:
        """Reuse the current round until its orders are fully closed."""
        rows = self.storage.fetchall(
            "SELECT plan_id,status,setup_type,direction,entry_mode,payload_json,updated_at "
            "FROM structure_trade_plans WHERE user_id=? AND symbol=? AND period=? "
            "AND status IN ('active','watching','event_suppressed','superseded','invalidated') "
            "ORDER BY updated_at DESC",
            (int(user_id), str(symbol or ""), str(period or "")),
        ) or []
        latest = {}
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            merged = {**payload, **dict(row)}
            key = self._family_key(merged)
            if not any(key):
                continue
            latest.setdefault(key, merged)
        bound = []
        for plan in plans:
            if str(plan.get("direction") or "") not in {"buy", "sell"}:
                bound.append(plan)
                continue
            previous = latest.get(self._family_key(plan))
            if not previous:
                bound.append(self._rewrite_cycle_identity(plan, int(plan.get("opportunity_cycle") or 1)))
                continue
            previous_id = str(previous.get("plan_id") or "")
            previous_cycle = int(previous.get("opportunity_cycle") or 1)
            state = self._round_state(user_id, previous_id) if previous_id else "idle"
            if state == "completed":
                bound.append(self._rewrite_cycle_identity(plan, previous_cycle + 1))
                continue
            plan["plan_id"] = previous_id
            if previous.get("plan_group_id"):
                plan["plan_group_id"] = previous.get("plan_group_id")
            plan["opportunity_id"] = previous.get("opportunity_id") or plan.get("opportunity_id")
            plan["opportunity_family_id"] = previous.get("opportunity_family_id") or plan.get("opportunity_family_id")
            plan["opportunity_cycle"] = previous_cycle
            bound.append(plan)
        return bound

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
        if user_id is None and symbol is None:
            # A global sweep is only a safety net for disconnected symbols.
            # Bound it to rows that are already due, instead of loading every
            # live plan in the table on each minute.
            clauses.append("(expires_at>0 AND expires_at<=?)")
            params.append(now)
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
            payload["inactive_reason_code"] = "expired"
            payload["inactive_at"] = now
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
            "SELECT payload_json,status,user_id,account_id,symbol,period "
            "FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
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
        payload["inactive_reason_code"] = "invalidated"
        payload["inactive_at"] = now
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
        if previous_status in {"active", "watching"}:
            payload["status_before_event"] = previous_status
        elif not payload.get("status_before_event"):
            payload["status_before_event"] = "active"
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

    def resume_plan(self, plan_id: str) -> str:
        """Restore a paused plan after its event window has ended."""
        row = self.storage.fetchone(
            "SELECT payload_json,status FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
            (str(plan_id),),
        )
        if not row or str(row["status"] or "") != "event_suppressed":
            return str((row or {}).get("status") or "")
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        restored = str(payload.get("status_before_event") or "active")
        if restored not in {"active", "watching"}:
            restored = "active"
        payload["status"] = restored
        payload["plan_stage"] = restored
        payload.pop("event_risk", None)
        now = int(time.time())
        self.storage.execute(
            "UPDATE structure_trade_plans SET status=?, payload_json=?, updated_at=? WHERE plan_id=?",
            (restored, json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )
        return restored

    def supersede_plan(self, plan_id: str, reason: str = "superseded_by_new_plan") -> None:
        """Mark a live plan as replaced while preserving an explicit audit reason."""
        now = int(time.time())
        row = self.storage.fetchone(
            "SELECT payload_json,status,user_id,account_id,symbol,period "
            "FROM structure_trade_plans WHERE plan_id=? LIMIT 1",
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
        payload["inactive_reason_code"] = "superseded"
        payload["inactive_at"] = now
        self.storage.execute(
            "UPDATE structure_trade_plans SET status='superseded', payload_json=?, updated_at=? WHERE plan_id=?",
            (json.dumps(payload, ensure_ascii=False), now, str(plan_id)),
        )
        # Close sibling stages belonging to the same opportunity/group. Read
        # JSON in Python for compatibility with the shared MySQL/SQLite SQL
        # adapter and keep already-filled execution history untouched.
        opportunity_id = str(payload.get("opportunity_id") or "")
        plan_group_id = str(payload.get("plan_group_id") or "")
        if opportunity_id or plan_group_id:
            related = self.storage.fetchall(
                "SELECT plan_id,payload_json FROM structure_trade_plans "
                "WHERE user_id=? AND account_id=? AND symbol=? AND period=? "
                "AND status IN ('active','watching','event_suppressed')",
                (int(row.get("user_id") or 0), int(row.get("account_id") or 0),
                 str(row.get("symbol") or ""), str(row.get("period") or "")),
            )
            related_ids = [str(plan_id)]
            for related_row in related:
                if str(related_row.get("plan_id") or "") == str(plan_id):
                    continue
                try:
                    related_payload = json.loads(related_row.get("payload_json") or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    related_payload = {}
                same_opportunity = opportunity_id and str(related_payload.get("opportunity_id") or "") == opportunity_id
                same_group = plan_group_id and str(related_payload.get("plan_group_id") or "") == plan_group_id
                if not (same_opportunity or same_group):
                    continue
                related_payload["status"] = "superseded"
                related_payload["invalidated_reason"] = f"关联结构计划已失效：{reason}"
                related_payload["inactive_reason_code"] = "superseded"
                related_payload["inactive_at"] = now
                self.storage.execute(
                    "UPDATE structure_trade_plans SET status='superseded', "
                    "payload_json=?, updated_at=? WHERE plan_id=? "
                    "AND status IN ('active','watching','event_suppressed')",
                    (json.dumps(related_payload, ensure_ascii=False), now,
                     str(related_row["plan_id"])),
                )
                related_ids.append(str(related_row["plan_id"]))
            placeholders = ",".join("?" for _ in related_ids)
            self.storage.execute(
                "UPDATE structure_plan_executions SET status='released', "
                "reason=?, reason_code='plan_superseded', updated_at=? "
                f"WHERE user_id=? AND account_id=? AND status='claimed' "
                f"AND plan_id IN ({placeholders})",
                tuple([f"结构计划已失效：{reason}", now,
                       int(row.get("user_id") or 0), int(row.get("account_id") or 0)] + related_ids),
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
        return self.find_consumed(
            user_id, account_id, deployment_id, plan_id, plan_stage, direction
        ) is not None

    def find_consumed(
        self, user_id: int, account_id: int, deployment_id: str, plan_id: str,
        plan_stage: str = "", direction: str = "",
    ) -> Optional[Dict]:
        """Return the active execution receipt that blocks a new claim.

        Rejected/failed/timeout/canceled/released receipts are terminal
        failures and must not poison a later opportunity.  Only states that
        represent an accepted or in-flight/filled execution are idempotent.
        """
        row = self.storage.fetchone(
            "SELECT execution_id,user_id,account_id,deployment_id,strategy_id,"
            "plan_id,plan_group_id,plan_stage,direction,status,order_id,"
            "reason_code,reason,tick_id,execution_mode,created_at,updated_at "
            "FROM structure_plan_executions WHERE user_id=? AND account_id=? "
            "AND deployment_id=? AND plan_id=? AND plan_stage=? AND direction=? "
            "AND status IN ('claimed','accepted','ordered','pending','filled',"
            "'partially_filled') ORDER BY updated_at DESC LIMIT 1",
            (int(user_id), int(account_id), str(deployment_id or ""), str(plan_id or ""),
             str(plan_stage or "default"), str(direction or "none").lower()),
        )
        if not row:
            return None
        return dict(row)

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

    def claim_execution_result(
        self, user_id: int, account_id: int, deployment_id: str,
        strategy_id: str, plan_id: str, plan_group_id: str = "",
        plan_stage: str = "", direction: str = "", tick_id: str = "",
        execution_mode: str = "", reason_code: str = "claimed",
        reason: str = "", payload: Optional[Dict] = None,
        gate_trace: Optional[List[Dict]] = None,
        account_snapshot: Optional[Dict] = None,
        enforce_plan_status: bool = True,
    ) -> Dict:
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
        # A signal snapshot can outlive the structure plan that produced it.
        # Never claim a superseded, invalidated, or expired plan.
        live_plan = self.storage.fetchone(
            "SELECT plan_id,status,expires_at,updated_at,created_at,"
            "user_id,account_id,symbol,period,setup_type,direction,"
            "plan_group_id,payload_json FROM structure_trade_plans "
            "WHERE plan_id=? AND user_id=? LIMIT 1",
            (str(plan_id), int(user_id)),
        )
        if not live_plan and enforce_plan_status:
            return {"claimed": False, "reason_code": "plan_inactive",
                    "reason": "结构计划不存在或已被删除",
                    "details": {"inactive_type": "missing", "plan_id": str(plan_id)}}
        try:
            live_payload = json.loads((live_plan or {}).get("payload_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            live_payload = {}
        plan_status = str(live_plan.get("status") or "") if live_plan else "active"
        expires_at = int((live_plan or {}).get("expires_at") or live_payload.get("expires_at") or 0)
        inactive_reason = str(
            live_payload.get("invalidated_reason")
            or live_payload.get("inactive_reason")
            or ""
        )
        inactive_type = ""
        if plan_status == "superseded":
            inactive_type = "superseded"
        elif plan_status in {"invalidated", "expired"}:
            inactive_type = "expired" if (
                plan_status == "expired" or "过期" in inactive_reason or "有效期" in inactive_reason
                or "24小时" in inactive_reason
            ) else "invalidated"
        elif plan_status != "active":
            inactive_type = plan_status or "missing"
        elif expires_at > 0 and expires_at <= now:
            inactive_type = "expired"
            inactive_reason = inactive_reason or "计划有效期已结束"
        if inactive_type:
            detail = {
                "inactive_type": inactive_type,
                "plan_status": plan_status,
                "invalidated_reason": inactive_reason,
                "plan_id": str(plan_id),
                "plan_group_id": str((live_plan or {}).get("plan_group_id") or plan_group_id or ""),
                "plan_stage": str(plan_stage or live_payload.get("plan_stage") or "default"),
                "direction": direction,
                "symbol": str((live_plan or {}).get("symbol") or live_payload.get("symbol") or ""),
                "period": str((live_plan or {}).get("period") or live_payload.get("period") or ""),
                "setup_type": str((live_plan or {}).get("setup_type") or live_payload.get("setup_type") or ""),
                "opportunity_id": str(live_payload.get("opportunity_id") or ""),
                "structure_segment_id": str(live_payload.get("structure_segment_id") or ""),
                "structure_revision": live_payload.get("structure_revision"),
                "generated_at": int(live_payload.get("generated_at") or (live_plan or {}).get("created_at") or 0),
                "valid_from": int(live_payload.get("valid_from") or 0),
                "expires_at": expires_at,
                "updated_at": int((live_plan or {}).get("updated_at") or 0),
                "checked_at": now,
            }
            return {"claimed": False, "reason_code": "plan_inactive",
                    "reason": inactive_reason or f"结构计划已{inactive_type}",
                    "details": detail}
        execution_id = self._execution_id(
            user_id, account_id, deployment_id, plan_id, plan_group_id,
            plan_stage, direction,
        )
        claimed_sibling = self.storage.fetchone(
            "SELECT plan_id,status,order_id,reason_code,created_at,updated_at "
            "FROM structure_plan_executions "
            "WHERE execution_id=? LIMIT 1",
            (execution_id,),
        )
        if claimed_sibling and str(claimed_sibling["plan_id"] or "") != str(plan_id):
            return {"claimed": False, "reason_code": "claim_race",
                    "reason": "同一计划组已有其他结构计划被并发领取",
                    "details": {"plan_id": str(plan_id),
                                "claimed_plan_id": str(claimed_sibling["plan_id"] or ""),
                                "plan_group_id": str(plan_group_id or ""),
                                "plan_stage": plan_stage, "direction": direction,
                                "existing_execution": dict(claimed_sibling)}}
        # Re-open a prior terminal failure for the same deterministic claim
        # identity.  Keeping the row (instead of deleting audit history) lets
        # a later Tick retry rejected/timeout/canceled executions while the
        # unique execution_id still protects concurrent active claims.
        if claimed_sibling and str(claimed_sibling.get("status") or "") in {
            "released", "rejected", "failed", "timeout", "canceled",
        }:
            self.storage.execute(
                "UPDATE structure_plan_executions SET status='claimed', order_id='', "
                "reason_code=?, reason=?, tick_id=?, execution_mode=?, payload_json=?, "
                "gate_trace_json=?, account_snapshot_json=?, updated_at=? "
                "WHERE execution_id=? AND status IN ('released','rejected','failed',"
                "'timeout','canceled')",
                (str(reason_code or "claimed"), reason, str(tick_id or ""),
                 str(execution_mode or ""), json.dumps(claim_payload, ensure_ascii=False),
                 json.dumps(gate_trace or [], ensure_ascii=False),
                 json.dumps(account_snapshot or {}, ensure_ascii=False), now, execution_id),
            )
        self.storage.execute(
            """
            INSERT INTO structure_plan_executions(
                execution_id,user_id,account_id,deployment_id,strategy_id,
                plan_id,plan_group_id,plan_stage,direction,tick_id,execution_mode,
                status,order_id,reason_code,reason,payload_json,gate_trace_json,
                account_snapshot_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            "SELECT execution_id,user_id,account_id,deployment_id,strategy_id,"
            "plan_id,plan_group_id,plan_stage,direction,status,order_id,"
            "reason_code,reason,tick_id,execution_mode,payload_json,"
            "gate_trace_json,account_snapshot_json,created_at,updated_at "
            "FROM structure_plan_executions "
            "WHERE execution_id=? LIMIT 1",
            (execution_id,),
        )
        if not row or str(row["plan_id"] or "") != str(plan_id):
            return {"claimed": False, "reason_code": "claim_failed",
                    "reason": "结构计划领取写入失败", "details": {"plan_id": str(plan_id)}}
        try:
            stored_payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            stored_payload = {}
        if stored_payload.get("claim_token") != claim_token:
            return {"claimed": False, "reason_code": "already_consumed",
                    "reason": "同一部署已消费该结构计划阶段和方向",
                    "details": {"plan_id": str(plan_id), "plan_stage": plan_stage,
                                "direction": direction, "execution_id": execution_id,
                                "existing_execution": {
                                    key: value for key, value in dict(row).items()
                                    if key not in {"payload_json", "gate_trace_json", "account_snapshot_json"}
                                }}}
        return {"claimed": True, "reason_code": "claimed", "reason": "结构计划领取成功",
                "details": {"plan_id": str(plan_id), "execution_id": execution_id,
                            "plan_stage": plan_stage, "direction": direction}}

    def claim_execution(self, *args, **kwargs) -> bool:
        """Backward-compatible boolean claim API."""
        kwargs.setdefault("enforce_plan_status", False)
        return bool(self.claim_execution_result(*args, **kwargs).get("claimed"))

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
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                   "canceled", "released", "closed"}
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

    def list_filled_position_ids(
        self, user_id: int, account_id: int, order_ids: List[str],
    ) -> List[int]:
        """Resolve live broker positions created by the given plan orders.

        ``structure_plan_executions.order_id`` stores the pending-order id and
        the MT5 execution receipt echoes that same id.  Joining through the
        durable receipt prevents an unrelated same-direction position from
        satisfying a staged opportunity's initial-fill prerequisite.
        """
        ids = [str(item) for item in order_ids if str(item)]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.storage.fetchall(
            "SELECT DISTINCT mt5_position_id FROM trade_execution_reports "
            "WHERE user_id=? AND account_id=? AND success=1 "
            "AND mt5_position_id>0 "
            f"AND order_id IN ({placeholders})",
            (int(user_id), int(account_id), *ids),
        )
        return [int(row["mt5_position_id"]) for row in rows]

    def replace_events(
        self, user_id: int, symbol: str, period: str, events: List[Dict],
    ) -> List[Dict]:
        """Persist the current layer event catalog without touching plans."""
        now = int(time.time())
        symbol, period = str(symbol).upper(), str(period).upper()
        incoming = {str(item.get("event_id") or "") for item in events or [] if item.get("event_id")}
        if incoming:
            placeholders = ",".join("?" for _ in incoming)
            self.storage.execute(
                f"UPDATE structure_events SET status='superseded',updated_at=? "
                f"WHERE user_id=? AND symbol=? AND period=? "
                f"AND status IN ('active','break_confirmed') "
                f"AND event_id NOT IN ({placeholders})",
                (now, int(user_id), symbol, period, *incoming),
            )
        else:
            self.storage.execute(
                "UPDATE structure_events SET status='superseded',updated_at=? "
                "WHERE user_id=? AND symbol=? AND period=? "
                "AND status IN ('active','break_confirmed')",
                (now, int(user_id), symbol, period),
            )
        for event in events or []:
            event_id = str(event.get("event_id") or "")
            if not event_id:
                continue
            payload = dict(event)
            self.storage.execute(
                "INSERT INTO structure_events(event_id,user_id,symbol,period,layer,event_type,"
                "direction,level,protected_level,status,confirmed_at,expires_at,payload_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,"
                "level=excluded.level,direction=excluded.direction,confirmed_at=excluded.confirmed_at,"
                "expires_at=excluded.expires_at,payload_json=excluded.payload_json,updated_at=excluded.updated_at",
                (
                    event_id, int(user_id), symbol, period,
                    str(event.get("layer") or ""), str(event.get("event_type") or event.get("type") or ""),
                    str(event.get("direction") or ""), float(event.get("level") or 0),
                    float(event.get("protected_level") or 0), str(event.get("status") or "active"),
                    int(event.get("confirmed_at") or 0), int(event.get("expires_at") or 0),
                    json.dumps(payload, ensure_ascii=False), now, now,
                ),
            )
        return self.list_events(user_id, symbol, period)

    def list_events(
        self, user_id: int, symbol: str, period: str, *, active_only: bool = False,
    ) -> List[Dict]:
        conditions = " AND status='active'" if active_only else ""
        rows = self.storage.fetchall(
            "SELECT event_id,user_id,symbol,period,layer,event_type,direction,level,"
            "protected_level,status,confirmed_at,expires_at,payload_json,created_at,updated_at "
            f"FROM structure_events WHERE user_id=? AND symbol=? AND period=?{conditions} "
            "ORDER BY confirmed_at ASC, layer ASC",
            (int(user_id), str(symbol).upper(), str(period).upper()),
        )
        result = []
        for row in rows:
            item = dict(row)
            try:
                item.update(json.loads(item.get("payload_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            result.append(item)
        return result
