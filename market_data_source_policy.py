"""User-level live market-source arbitration.

One broker publishes a user's canonical symbol once. Additional accounts from
the same broker reuse that feed; a different broker may publish only symbols
that are not already owned inside the user market domain.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Dict

from mysql_repositories import (
    TradingAccountRepository,
    get_storage,
)
from repositories.platform import PlatformInstrumentMappingRepository


class MarketDataSourcePolicy:
    CACHE_SECONDS = 30

    def __init__(self):
        self.storage = get_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.mappings = PlatformInstrumentMappingRepository(self.storage)
        self._cache: Dict[tuple, tuple] = {}
        self._lock = threading.RLock()

    def canonical_symbol(self, broker_name: str, symbol: str) -> str:
        native = self.mappings._normalize(symbol)
        row = self.storage.fetchone(
            """
            SELECT mapping_group FROM platform_instrument_mappings
            WHERE enabled = 1 AND native_symbol = ?
              AND COALESCE(NULLIF(broker_name, ''), broker_server) = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (native, broker_name),
        )
        return str(row["mapping_group"] or native).upper() if row else native

    def resolve(self, user_id: int, account_id: int, symbol: str) -> Dict:
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        if account is None or account.account_type != "mt5":
            return {"mode": "blocked", "message": "MT5 实盘账户不存在"}
        broker_name = self.mappings.broker_name_from_server(
            account.mt5_server or ""
        ).casefold()
        canonical = self.canonical_symbol(broker_name, symbol)
        cache_key = (int(user_id), int(account_id), canonical)
        now = time.time()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.CACHE_SECONDS:
                return dict(cached[1])
        result = self._resolve_uncached(account, broker_name, symbol, canonical)
        with self._lock:
            self._cache[cache_key] = (now, dict(result))
        return result

    def _resolve_uncached(
        self, account, broker_name: str, native_symbol: str, canonical: str,
    ) -> Dict:
        blocked = self.storage.fetchone(
            "SELECT * FROM market_data_account_policies "
            "WHERE user_id = ? AND account_id = ? AND mode = 'blocked'",
            (account.user_id, account.account_id),
        )
        if blocked:
            return self._policy_payload(blocked, canonical)

        broker_accounts = [
            item for item in self.accounts.list_for_user(account.user_id)
            if item.account_type == "mt5" and item.status == "active"
            and self.mappings.broker_name_from_server(
                item.mt5_server or ""
            ).casefold() == broker_name
        ]
        broker_accounts.sort(key=lambda item: (
            int(item.activated_at or item.created_at or 0), item.account_id,
        ))
        broker_primary = broker_accounts[0] if broker_accounts else account
        if broker_primary.account_id != account.account_id:
            source = self._claim_source(
                account.user_id, canonical, broker_primary.account_id,
                broker_name, native_symbol,
            )
            if str(source.get("broker_name") or "").casefold() != broker_name:
                return self._block(
                    account, broker_name, canonical,
                    int(source.get("primary_account_id") or 0),
                )
            return self._save_policy(
                account, broker_name, "reuse", broker_primary.account_id, [],
                f"复用同交易商账户「{broker_primary.account_name}」的行情和策略触发 Tick",
                canonical,
            )

        source = self._claim_source(
            account.user_id, canonical, account.account_id,
            broker_name, native_symbol,
        )
        if (
            int(source.get("primary_account_id") or 0) != account.account_id
            and str(source.get("broker_name") or "").casefold() != broker_name
        ):
            return self._block(
                account, broker_name, canonical,
                int(source.get("primary_account_id") or 0),
            )
        return self._save_policy(
            account, broker_name, "primary", account.account_id, [],
            "该账户负责此品种的用户共享行情和策略触发 Tick", canonical,
        )

    def _claim_source(
        self, user_id: int, canonical: str, primary_account_id: int,
        broker_name: str, native_symbol: str,
    ) -> Dict:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO market_data_sources(
                user_id, canonical_symbol, primary_account_id, broker_name,
                native_symbol, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, canonical_symbol) DO NOTHING
            """,
            (user_id, canonical, primary_account_id, broker_name,
             str(native_symbol).upper(), now, now),
        )
        row = self.storage.fetchone(
            "SELECT * FROM market_data_sources "
            "WHERE user_id = ? AND canonical_symbol = ?",
            (user_id, canonical),
        )
        return dict(row) if row else {}

    def _block(
        self, account, broker_name: str, canonical: str,
        primary_account_id: int,
    ) -> Dict:
        primary = self.accounts.get_by_id(account.user_id, primary_account_id)
        primary_name = primary.account_name if primary else str(primary_account_id)
        message = (
            f"标准品种 {canonical} 已由不同交易商账户「{primary_name}」提供行情，"
            "该实盘账户已禁止策略开仓"
        )
        return self._save_policy(
            account, broker_name, "blocked", primary_account_id,
            [canonical], message, canonical,
        )

    def _save_policy(
        self, account, broker_name: str, mode: str, primary_account_id: int,
        conflicts, message: str, canonical: str,
    ) -> Dict:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO market_data_account_policies(
                user_id, account_id, broker_name, mode, primary_account_id,
                conflict_symbols_json, message, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, account_id) DO UPDATE SET
                broker_name = excluded.broker_name, mode = excluded.mode,
                primary_account_id = excluded.primary_account_id,
                conflict_symbols_json = excluded.conflict_symbols_json,
                message = excluded.message, updated_at = excluded.updated_at
            """,
            (account.user_id, account.account_id, broker_name, mode,
             primary_account_id, json.dumps(conflicts, ensure_ascii=False),
             message, now, now),
        )
        return {
            "mode": mode, "broker_name": broker_name,
            "canonical_symbol": canonical,
            "primary_account_id": primary_account_id,
            "conflict_symbols": list(conflicts), "message": message,
            "is_market_primary": mode == "primary",
            "can_open_trade": mode != "blocked",
        }

    @staticmethod
    def _policy_payload(row, canonical: str = "") -> Dict:
        item = dict(row)
        try:
            conflicts = json.loads(item.get("conflict_symbols_json") or "[]")
        except (TypeError, ValueError):
            conflicts = []
        return {
            "mode": item.get("mode") or "unknown",
            "broker_name": item.get("broker_name") or "",
            "canonical_symbol": canonical,
            "primary_account_id": int(item.get("primary_account_id") or 0),
            "conflict_symbols": conflicts,
            "message": item.get("message") or "",
            "is_market_primary": item.get("mode") == "primary",
            "can_open_trade": item.get("mode") != "blocked",
        }

    def account_status(self, user_id: int, account_id: int) -> Dict:
        row = self.storage.fetchone(
            "SELECT * FROM market_data_account_policies "
            "WHERE user_id = ? AND account_id = ?",
            (int(user_id), int(account_id)),
        )
        return self._policy_payload(row) if row else {
            "mode": "pending", "message": "等待 EA 上报品种后确认行情来源",
            "primary_account_id": 0, "conflict_symbols": [],
            "is_market_primary": False, "can_open_trade": True,
        }

    def execution_account_ids(
        self, user_id: int, broker_name: str,
    ) -> list[int]:
        """All active same-broker accounts driven by the primary market Tick."""
        result = []
        for account in self.accounts.list_for_user(int(user_id)):
            if (
                account.account_type != "mt5" or account.status != "active"
                or not account.enabled
            ):
                continue
            account_broker = self.mappings.broker_name_from_server(
                account.mt5_server or ""
            ).casefold()
            if account_broker != str(broker_name or "").casefold():
                continue
            status = self.account_status(user_id, account.account_id)
            if status.get("mode") != "blocked":
                result.append(account.account_id)
        return result

    def activation_notice(self, user_id: int, account_id: int) -> Dict:
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        if account is None:
            return {"mode": "pending", "message": "等待账户识别"}
        broker_name = self.mappings.broker_name_from_server(
            account.mt5_server or ""
        ).casefold()
        peers = [
            item for item in self.accounts.list_for_user(int(user_id))
            if item.account_type == "mt5" and item.status == "active"
            and item.account_id != account.account_id
            and self.mappings.broker_name_from_server(
                item.mt5_server or ""
            ).casefold() == broker_name
        ]
        peers.sort(key=lambda item: (
            int(item.activated_at or item.created_at or 0), item.account_id,
        ))
        if peers:
            primary = peers[0]
            return {
                "mode": "reuse", "primary_account_id": primary.account_id,
                "message": (
                    f"检测到同交易商实盘账户；将复用「{primary.account_name}」"
                    "的K线和策略触发Tick，本账户仍独立执行订单和持仓管理"
                ),
            }
        return {
            "mode": "pending", "primary_account_id": account.account_id,
            "message": "账户已激活；首次上报品种时将检查跨交易商行情冲突",
        }
