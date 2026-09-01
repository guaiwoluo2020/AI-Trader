"""Platform-level instrument mapping repository."""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage


class PlatformInstrumentMappingRepository:
    """Manage explicit broker-native symbol compatibility mappings."""

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()

    @staticmethod
    def _normalize(value: str) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def broker_name_from_server(server: str) -> str:
        value = str(server or "").strip()
        return value.split("-", 1)[0].strip() if value else ""

    def list(self, enabled_only: bool = False) -> List[Dict]:
        sql = "SELECT *, COALESCE(NULLIF(broker_name, ''), broker_server) AS effective_broker_name FROM platform_instrument_mappings"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY mapping_group, broker_server, native_symbol"
        return [dict(row) for row in self.storage.fetchall(sql)]

    def save(self, data: Dict) -> Dict:
        broker_name = str(data.get("broker_name") or data.get("broker_server") or "").strip()
        native_symbol = self._normalize(data.get("native_symbol"))
        mapping_group = self._normalize(data.get("mapping_group"))
        display_name = str(data.get("display_name") or "").strip()
        if not broker_name or not native_symbol or not mapping_group:
            raise ValueError("交易商、品种和关联组均不能为空")
        mapping_id = str(data.get("mapping_id") or uuid.uuid4().hex[:16])
        now = int(time.time())
        self.storage.execute(
            """INSERT INTO platform_instrument_mappings
               (mapping_id, broker_name, broker_server, native_symbol, mapping_group,
                display_name, enabled, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE broker_name=VALUES(broker_name),
                mapping_group=VALUES(mapping_group), display_name=VALUES(display_name),
                enabled=VALUES(enabled), updated_at=VALUES(updated_at)""",
            (mapping_id, broker_name, broker_name, native_symbol, mapping_group,
             display_name, int(bool(data.get("enabled", True))), now, now),
        )
        row = self.storage.fetchone(
            "SELECT *, COALESCE(NULLIF(broker_name, ''), broker_server) AS effective_broker_name FROM platform_instrument_mappings WHERE broker_server=? AND native_symbol=?",
            (broker_name, native_symbol),
        )
        return dict(row) if row else {}

    def delete(self, mapping_id: str) -> bool:
        row = self.storage.fetchone("SELECT mapping_id FROM platform_instrument_mappings WHERE mapping_id=?", (str(mapping_id),))
        if not row:
            return False
        self.storage.execute("DELETE FROM platform_instrument_mappings WHERE mapping_id=?", (str(mapping_id),))
        return True

    def source_server(self, user_id: int, symbol: str) -> str:
        row = self.storage.fetchone(
            """SELECT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
               FROM trading_accounts a LEFT JOIN mt5_account_connections c ON c.account_id=a.id
               WHERE a.user_id=? AND a.account_type='mt5'
                 AND COALESCE(c.mt5_server, a.mt5_server, '') != ''
               ORDER BY COALESCE(c.last_seen_at, a.last_seen_at, 0) DESC, a.id DESC LIMIT 1""",
            (int(user_id),),
        )
        return str(row["mt5_server"] or "") if row else ""

    def compatible(self, source_server: str, source_symbol: str, target_server: str, target_symbol: str) -> bool:
        source_symbol, target_symbol = self._normalize(source_symbol), self._normalize(target_symbol)
        if not source_symbol or not target_symbol:
            return False
        if source_symbol == target_symbol:
            return True
        rows = self.storage.fetchall(
            """SELECT mapping_group FROM platform_instrument_mappings WHERE enabled=1
               AND ((COALESCE(NULLIF(broker_name,''),broker_server)=? AND native_symbol=?)
                 OR (COALESCE(NULLIF(broker_name,''),broker_server)=? AND native_symbol=?))""",
            (self.broker_name_from_server(source_server), source_symbol,
             self.broker_name_from_server(target_server), target_symbol),
        )
        groups = {str(row["mapping_group"]) for row in rows}
        return len(rows) == 2 and len(groups) == 1

    def target_options(self, source_owner_user_id: int, source_symbol: str,
                       target_user_id: int) -> List[Dict]:
        source_server = self.source_server(source_owner_user_id, source_symbol)
        accounts = self.storage.fetchall(
            """SELECT DISTINCT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
               FROM trading_accounts a LEFT JOIN mt5_account_connections c ON c.account_id=a.id
               WHERE a.user_id=? AND a.account_type='mt5' AND a.status='active'
                 AND a.enabled=1 AND COALESCE(c.mt5_server, a.mt5_server, '') != ''""",
            (int(target_user_id),),
        )
        normalized = self._normalize(source_symbol)
        options = [{"symbol": normalized, "broker_server": "", "label": f"{normalized}（同名品种）"}]
        for account in accounts:
            target_server = str(account["mt5_server"] or "")
            mappings = self.storage.fetchall(
                """SELECT native_symbol FROM platform_instrument_mappings
                   WHERE COALESCE(NULLIF(broker_name,''),broker_server)=? AND enabled=1
                   ORDER BY native_symbol""",
                (self.broker_name_from_server(target_server),),
            )
            for mapping in mappings:
                target_symbol = str(mapping["native_symbol"])
                if self.compatible(source_server, source_symbol, target_server, target_symbol):
                    option = {"symbol": target_symbol, "broker_server": target_server,
                              "label": f"{target_symbol} · {self.broker_name_from_server(target_server)}"}
                    if option not in options:
                        options.append(option)
        return options

    def user_can_use_symbol(self, source_owner_user_id: int, source_symbol: str,
                            target_user_id: int, target_symbol: str) -> bool:
        if self._normalize(source_symbol) == self._normalize(target_symbol):
            return True
        source_server = self.source_server(source_owner_user_id, source_symbol)
        accounts = self.storage.fetchall(
            """SELECT DISTINCT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
               FROM trading_accounts a LEFT JOIN mt5_account_connections c ON c.account_id=a.id
               WHERE a.user_id=? AND a.account_type='mt5' AND a.status='active' AND a.enabled=1""",
            (int(target_user_id),),
        )
        return any(self.compatible(source_server, source_symbol, row["mt5_server"], target_symbol)
                   for row in accounts)
