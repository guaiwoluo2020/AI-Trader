#!/usr/bin/env python3
"""Persistent, multi-tenant operational and audit event repository."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlite_storage import SQLiteStorage, get_storage


class SystemEventLogRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def add(self, event: Dict) -> Dict:
        occurred_at = event.get("occurred_at")
        if isinstance(occurred_at, datetime):
            occurred_at = int(occurred_at.timestamp())
        occurred_at = int(occurred_at or time.time())
        event_id = str(event.get("event_id") or uuid.uuid4().hex)
        self.storage.execute(
            """
            INSERT INTO system_event_logs(
                event_id, occurred_at, level, category, event_type, event_name,
                user_id, account_id, symbol, actor_type, actor_id, entity_type,
                entity_id, correlation_id, message, status, detail_json,
                request_id, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, occurred_at, str(event.get("level") or "info").lower(),
                str(event.get("category") or "system").lower(),
                str(event.get("event_type") or "unknown"),
                str(event.get("event_name") or event.get("event_type") or "未知事件"),
                event.get("user_id"), event.get("account_id"),
                str(event.get("symbol") or ""), str(event.get("actor_type") or "system"),
                str(event.get("actor_id") or ""), str(event.get("entity_type") or ""),
                str(event.get("entity_id") or ""), str(event.get("correlation_id") or ""),
                str(event.get("message") or ""), str(event.get("status") or ""),
                json.dumps(event.get("detail") or {}, ensure_ascii=False, default=str),
                str(event.get("request_id") or ""), int(time.time()),
            ),
        )
        return self.get(event_id)

    def get(self, event_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            self._select_sql() + " WHERE logs.event_id = ?", (event_id,)
        )
        return self._row(row) if row else None

    def list(self, filters: Optional[Dict] = None) -> Dict:
        filters = filters or {}
        where, params = self._where(filters)
        page = max(1, int(filters.get("page") or 1))
        page_size = max(1, min(200, int(filters.get("page_size") or 50)))
        total = self.storage.fetchone(
            f"SELECT COUNT(*) AS total FROM system_event_logs AS logs {where}",
            tuple(params),
        )["total"]
        rows = self.storage.fetchall(
            f"{self._select_sql()} {where} ORDER BY logs.occurred_at DESC, logs.event_id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        )
        return {
            "items": [self._row(row) for row in rows],
            "total": int(total), "page": page, "page_size": page_size,
        }

    def summary(self, filters: Optional[Dict] = None) -> Dict:
        filters = dict(filters or {})
        filters.setdefault("start_at", int(time.time()) - 86400)
        where, params = self._where(filters)
        rows = self.storage.fetchall(
            f"""
            SELECT level, category, COUNT(*) AS total
            FROM system_event_logs AS logs {where}
            GROUP BY level, category
            """,
            tuple(params),
        )
        by_level: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        for row in rows:
            by_level[row["level"]] = by_level.get(row["level"], 0) + int(row["total"])
            by_category[row["category"]] = by_category.get(row["category"], 0) + int(row["total"])
        return {
            "total": sum(by_level.values()),
            "errors": by_level.get("error", 0) + by_level.get("critical", 0),
            "warnings": by_level.get("warning", 0),
            "trading": by_category.get("trading", 0) + by_category.get("risk", 0),
            "by_level": by_level, "by_category": by_category,
        }

    def facets(self, user_id: Optional[int] = None) -> Dict:
        scope = "WHERE logs.user_id = ?" if user_id is not None else ""
        params = (int(user_id),) if user_id is not None else ()
        users = self.storage.fetchall(
            f"""
            SELECT DISTINCT logs.user_id AS value, COALESCE(users.username, '平台') AS title
            FROM system_event_logs AS logs LEFT JOIN users ON users.id = logs.user_id
            {scope} ORDER BY title
            """, params,
        )
        accounts = self.storage.fetchall(
            f"""
            SELECT DISTINCT logs.account_id AS value,
                   COALESCE(accounts.account_name, '平台事件') AS title,
                   accounts.mt5_login, logs.user_id
            FROM system_event_logs AS logs
            LEFT JOIN trading_accounts AS accounts ON accounts.id = logs.account_id
            {scope} ORDER BY title
            """, params,
        )
        symbols = self.storage.fetchall(
            f"SELECT DISTINCT symbol FROM system_event_logs AS logs {scope} AND symbol != '' ORDER BY symbol"
            if scope else
            "SELECT DISTINCT symbol FROM system_event_logs AS logs WHERE symbol != '' ORDER BY symbol",
            params,
        )
        return {
            "users": [dict(row) for row in users if row["value"] is not None],
            "accounts": [dict(row) for row in accounts if row["value"] is not None],
            "symbols": [row["symbol"] for row in symbols],
        }

    def purge_operational(self, before: int) -> int:
        before = int(before)
        row = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM system_event_logs WHERE occurred_at < ? AND category != 'audit'",
            (before,),
        )
        self.storage.execute(
            "DELETE FROM system_event_logs WHERE occurred_at < ? AND category != 'audit'",
            (before,),
        )
        return int(row["total"] if row else 0)

    @staticmethod
    def _select_sql() -> str:
        return """
            SELECT logs.*, users.username,
                   accounts.account_name, accounts.mt5_login
            FROM system_event_logs AS logs
            LEFT JOIN users ON users.id = logs.user_id
            LEFT JOIN trading_accounts AS accounts ON accounts.id = logs.account_id
        """

    @staticmethod
    def _where(filters: Dict):
        clauses, params = [], []
        scalar_fields = (
            "user_id", "account_id", "level", "category", "event_type", "symbol",
            "status", "correlation_id",
        )
        for field in scalar_fields:
            value = filters.get(field)
            if value not in (None, ""):
                clauses.append(f"logs.{field} = ?")
                params.append(value)
        for field in ("levels", "categories", "event_types"):
            values = [str(value) for value in filters.get(field) or [] if str(value)]
            if values:
                column = {"levels": "level", "categories": "category", "event_types": "event_type"}[field]
                clauses.append(f"logs.{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        if filters.get("start_at"):
            clauses.append("logs.occurred_at >= ?")
            params.append(int(filters["start_at"]))
        if filters.get("end_at"):
            clauses.append("logs.occurred_at <= ?")
            params.append(int(filters["end_at"]))
        if filters.get("search"):
            term = f"%{str(filters['search']).strip()}%"
            clauses.append("(logs.message LIKE ? OR logs.event_name LIKE ? OR logs.entity_id LIKE ? OR logs.correlation_id LIKE ?)")
            params.extend((term, term, term, term))
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    @staticmethod
    def _row(row) -> Dict:
        item = dict(row)
        item["timestamp"] = datetime.fromtimestamp(item["occurred_at"]).isoformat()
        try:
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
        except (TypeError, ValueError):
            item["detail"] = {}
            item.pop("detail_json", None)
        return item
