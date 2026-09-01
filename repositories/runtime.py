"""Runtime state, position policy and audit repository boundary."""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage

from mysql_repositories import (
    PositionManagementEventRepository,
    PositionManagementPolicyRepository,
    RuntimeStateRepository,
)
from system_event_log import SystemEventLogRepository


def _now_ts() -> int:
    return int(time.time())


class RuntimeStateRepository:
    """Persist account-scoped runtime entities without scanning full history."""

    def __init__(self, user_id: int, account_id: Optional[int], storage: Optional[MySQLStorage] = None):
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)
        self.storage = storage or get_mysql_storage()

    def set_scope(self, user_id: int, account_id: Optional[int]) -> None:
        self.user_id, self.account_id = int(user_id or 0), int(account_id or 0)

    def upsert_entity(self, entity_type: str, entity_id: str, payload: Dict,
                      symbol: str = "", status: str = "") -> None:
        now = _now_ts()
        self.storage.execute(
            """INSERT INTO runtime_entities(user_id, account_id, entity_type,
               entity_id, symbol, status, payload_json, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE symbol=VALUES(symbol), status=VALUES(status),
               payload_json=VALUES(payload_json), updated_at=VALUES(updated_at)""",
            (self.user_id, self.account_id, entity_type, str(entity_id), symbol,
             status, json.dumps(payload, ensure_ascii=False), now, now),
        )

    def list_entities(self, entity_type: str, statuses: Optional[List[str]] = None,
                      limit: Optional[int] = None) -> List[Dict]:
        params: List = [self.user_id, self.account_id, entity_type]
        sql = "SELECT payload_json FROM runtime_entities WHERE user_id=? AND account_id=? AND entity_type=?"
        if statuses:
            sql += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        if limit is not None:
            sql += " ORDER BY created_at DESC, entity_id DESC LIMIT ?"
            params.append(max(1, int(limit)))
        else:
            sql += " ORDER BY created_at, entity_id"
        return [json.loads(row["payload_json"]) for row in self.storage.fetchall(sql, tuple(params))]

    def get_entity(self, entity_type: str, entity_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT payload_json FROM runtime_entities WHERE user_id=? AND account_id=? AND entity_type=? AND entity_id=? LIMIT 1",
            (self.user_id, self.account_id, entity_type, str(entity_id)),
        )
        if not row:
            return None
        try:
            return json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def delete_entity(self, entity_type: str, entity_id: str) -> None:
        self.storage.execute(
            "DELETE FROM runtime_entities WHERE user_id=? AND account_id=? AND entity_type=? AND entity_id=?",
            (self.user_id, self.account_id, entity_type, str(entity_id)),
        )

    def delete_entities(self, entity_type: str, symbol: Optional[str] = None) -> None:
        sql = "DELETE FROM runtime_entities WHERE user_id=? AND account_id=? AND entity_type=?"
        params = (self.user_id, self.account_id, entity_type)
        if symbol is not None:
            sql += " AND symbol=?"
            params += (symbol,)
        self.storage.execute(sql, params)

    def trim_entities(self, entity_type: str, max_count: int) -> None:
        self.storage.execute(
            """DELETE FROM runtime_entities WHERE user_id=? AND account_id=? AND entity_type=?
               AND entity_id NOT IN (SELECT entity_id FROM
                 (SELECT entity_id FROM runtime_entities WHERE user_id=? AND account_id=?
                  AND entity_type=? ORDER BY updated_at DESC LIMIT ?) keep_rows)""",
            (self.user_id, self.account_id, entity_type, self.user_id, self.account_id,
             entity_type, max(0, int(max_count))),
        )

    def migrate_scope(self, account_id: int) -> None:
        target = int(account_id)
        if target == self.account_id:
            return
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """INSERT IGNORE INTO runtime_entities(user_id, account_id, entity_type,
                   entity_id, symbol, status, payload_json, created_at, updated_at)
                   SELECT user_id, ?, entity_type, entity_id, symbol, status, payload_json,
                          created_at, ? FROM runtime_entities WHERE user_id=? AND account_id=?""",
                (target, _now_ts(), self.user_id, self.account_id),
            )
            conn.execute("DELETE FROM runtime_entities WHERE user_id=? AND account_id=?",
                         (self.user_id, self.account_id))
            conn.commit()
        self.account_id = target

__all__ = [
    "PositionManagementEventRepository", "PositionManagementPolicyRepository",
    "RuntimeStateRepository", "SystemEventLogRepository",
]
