#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 存储层
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from market.models import LLMConfig, TradingStrategy


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DEFAULT_DB_FILE = DATA_DIR / "ai_trader.db"
DEFAULT_AUTH_FILE = ROOT_DIR / ".auth_users.json"
DEFAULT_TRADE_CONFIG_FILE = DATA_DIR / "trade_config.json"
DEFAULT_LLM_CONFIG_FILE = DATA_DIR / "llm_config.json"
DEFAULT_STRATEGY_CONFIG_FILE = DATA_DIR / "strategy_config.json"


def _now_ts() -> int:
    return int(time.time())


def _get_env_default_admin_username() -> str:
    return os.getenv("AI_TRADER_DEFAULT_ADMIN_USERNAME", "admin")


def _get_env_default_admin_password() -> str:
    return os.getenv("AI_TRADER_DEFAULT_ADMIN_PASSWORD", "admin123456")


def get_runtime_username() -> str:
    return os.getenv("AI_TRADER_RUNTIME_USERNAME", _get_env_default_admin_username())


@dataclass
class UserRecord:
    user_id: int
    username: str
    password_hash: str
    salt: str
    role: str
    token_version: int
    created_at: int
    updated_at: int


@dataclass
class TradingAccountRecord:
    account_id: int
    user_id: int
    account_key: str
    account_name: str
    enabled: bool
    last_seen_at: Optional[int]
    mt5_login: Optional[str]
    mt5_server: Optional[str]
    ea_version: Optional[str]
    activated_at: Optional[int]
    created_at: int
    updated_at: int


class SQLiteStorage:
    """SQLite 连接与建表管理"""

    def __init__(self, db_file: Optional[str] = None):
        self.db_file = Path(
            db_file
            or os.getenv("AI_TRADER_DB_FILE")
            or DEFAULT_DB_FILE
        )
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return

            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS app_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS user_trade_configs (
                        user_id INTEGER PRIMARY KEY,
                        config_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS user_llm_configs (
                        user_id INTEGER PRIMARY KEY,
                        api_key TEXT NOT NULL DEFAULT '',
                        api_base TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
                        model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS llm_access_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL UNIQUE,
                        status TEXT NOT NULL DEFAULT 'pending',
                        requested_at INTEGER NOT NULL,
                        reviewed_at INTEGER,
                        reviewed_by INTEGER,
                        review_note TEXT NOT NULL DEFAULT '',
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(reviewed_by) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_llm_access_requests_status
                    ON llm_access_requests(status, requested_at);

                    CREATE TABLE IF NOT EXISTS user_strategy_configs (
                        user_id INTEGER NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        config_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(user_id, strategy_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_user_strategy_configs_symbol
                    ON user_strategy_configs(user_id, symbol);

                    CREATE TABLE IF NOT EXISTS trading_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        account_key TEXT NOT NULL DEFAULT 'default',
                        account_name TEXT NOT NULL DEFAULT 'MT5',
                        token_hash TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        last_seen_at INTEGER,
                        mt5_login TEXT,
                        mt5_server TEXT,
                        ea_version TEXT,
                        activated_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(user_id, account_key),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_trading_accounts_user_id
                    ON trading_accounts(user_id);

                    CREATE TABLE IF NOT EXISTS ea_activation_codes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code_hash TEXT NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        used_at INTEGER,
                        created_at INTEGER NOT NULL,
                        mt5_login TEXT,
                        mt5_server TEXT,
                        ea_version TEXT,
                        program_name TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_ea_activation_codes_account
                    ON ea_activation_codes(account_id, expires_at);

                    CREATE TABLE IF NOT EXISTS runtime_entities (
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        entity_type TEXT NOT NULL,
                        entity_id TEXT NOT NULL,
                        symbol TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(user_id, account_id, entity_type, entity_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_runtime_entities_scope
                    ON runtime_entities(user_id, account_id, entity_type, status);

                    """
                )
                self._ensure_column(conn, "trading_accounts", "mt5_login", "TEXT")
                self._ensure_column(conn, "trading_accounts", "mt5_server", "TEXT")
                self._ensure_column(conn, "trading_accounts", "ea_version", "TEXT")
                self._ensure_column(conn, "trading_accounts", "activated_at", "INTEGER")
                self._ensure_column(
                    conn,
                    "users",
                    "role",
                    "TEXT NOT NULL DEFAULT 'user'",
                )
                self._ensure_column(
                    conn,
                    "users",
                    "token_version",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._migrate_strategy_configs(conn)
                admin_username = _get_env_default_admin_username().strip().lower()
                conn.execute(
                    """
                    UPDATE users
                    SET role = CASE
                        WHEN lower(username) = ? THEN 'admin'
                        ELSE 'user'
                    END
                    """,
                    (admin_username,),
                )
                conn.commit()

            self._initialized = True

    @staticmethod
    def _migrate_strategy_configs(conn: sqlite3.Connection) -> None:
        """将旧的“每品种一策略”表迁移为“每策略一行”。"""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(user_strategy_configs)")
        }
        if "strategy_id" in columns:
            return

        rows = conn.execute(
            """
            SELECT user_id, symbol, config_json, created_at, updated_at
            FROM user_strategy_configs
            """
        ).fetchall()
        conn.execute("ALTER TABLE user_strategy_configs RENAME TO user_strategy_configs_legacy")
        conn.execute("DROP INDEX IF EXISTS idx_user_strategy_configs_symbol")
        conn.executescript(
            """
            CREATE TABLE user_strategy_configs (
                user_id INTEGER NOT NULL,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, strategy_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_user_strategy_configs_symbol
            ON user_strategy_configs(user_id, symbol);
            """
        )
        for row in rows:
            try:
                payload = json.loads(row["config_json"])
            except (TypeError, json.JSONDecodeError):
                payload = {}
            strategy_id = payload.get("strategy_id") or str(uuid.uuid4())[:8]
            payload["strategy_id"] = strategy_id
            conn.execute(
                """
                INSERT INTO user_strategy_configs(
                    user_id, strategy_id, symbol, config_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    row["user_id"],
                    strategy_id,
                    row["symbol"],
                    json.dumps(payload, ensure_ascii=False),
                    row["created_at"],
                    row["updated_at"],
                ),
            )
        conn.execute("DROP TABLE user_strategy_configs_legacy")

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.initialize()
        with self._lock, self._connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        self.initialize()
        with self._lock, self._connect() as conn:
            return conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        self.initialize()
        with self._lock, self._connect() as conn:
            return conn.execute(sql, params).fetchall()


_STORAGE: Optional[SQLiteStorage] = None


def get_storage() -> SQLiteStorage:
    global _STORAGE
    if _STORAGE is None:
        _STORAGE = SQLiteStorage()
    return _STORAGE


def reset_storage() -> None:
    global _STORAGE
    _STORAGE = None


class MetaRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get(self, key: str) -> Optional[str]:
        row = self.storage.fetchone("SELECT value FROM app_meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.storage.execute(
            """
            INSERT INTO app_meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


class UserRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        row = self.storage.fetchone(
            """
            SELECT id, username, password_hash, salt, role, token_version,
                   created_at, updated_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        )
        return self._row_to_user(row)

    def get_by_id(self, user_id: int) -> Optional[UserRecord]:
        row = self.storage.fetchone(
            """
            SELECT id, username, password_hash, salt, role, token_version,
                   created_at, updated_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )
        return self._row_to_user(row)

    def create_user(
        self,
        username: str,
        password_hash: str,
        salt: str,
        role: str = "user",
    ) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO users(
                username, password_hash, salt, role, token_version,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, salt, role, now, now),
        )
        user = self.get_by_username(username)
        if user is None:
            raise RuntimeError(f"创建用户失败: {username}")
        return user

    def update_password(
        self,
        user_id: int,
        password_hash: str,
        salt: str,
    ) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """
            UPDATE users
            SET password_hash = ?, salt = ?,
                token_version = token_version + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (password_hash, salt, now, user_id),
        )
        user = self.get_by_id(user_id)
        if user is None:
            raise RuntimeError("更新密码后未找到用户")
        return user

    def count(self) -> int:
        row = self.storage.fetchone("SELECT COUNT(*) AS total FROM users")
        return int(row["total"]) if row else 0

    def ensure_runtime_user(self, password_hash_builder) -> UserRecord:
        username = get_runtime_username()
        user = self.get_by_username(username)
        if user:
            return user

        salt, password_hash = password_hash_builder(_get_env_default_admin_password())
        role = (
            "admin"
            if username.strip().lower()
            == _get_env_default_admin_username().strip().lower()
            else "user"
        )
        return self.create_user(username, password_hash, salt, role=role)

    @staticmethod
    def _row_to_user(row: Optional[sqlite3.Row]) -> Optional[UserRecord]:
        if row is None:
            return None
        return UserRecord(
            user_id=int(row["id"]),
            username=row["username"],
            password_hash=row["password_hash"],
            salt=row["salt"],
            role=row["role"],
            token_version=int(row["token_version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class TradingAccountRepository:
    """MT5 账户绑定与 EA 凭证管理。"""

    DEFAULT_ACCOUNT_KEY = "default"

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_default(self, user_id: int) -> Optional[TradingAccountRecord]:
        row = self.storage.fetchone(
            """
            SELECT id, user_id, account_key, account_name, enabled,
                   last_seen_at, mt5_login, mt5_server, ea_version,
                   activated_at, created_at, updated_at
            FROM trading_accounts
            WHERE user_id = ? AND account_key = ?
            """,
            (user_id, self.DEFAULT_ACCOUNT_KEY),
        )
        return self._row_to_account(row)

    def ensure_default(
        self,
        user_id: int,
        account_name: str = "MT5",
    ) -> TradingAccountRecord:
        """创建默认绑定但不轮换已在使用的 EA 凭证。"""
        now = _now_ts()
        placeholder_hash = self._hash_token(secrets.token_urlsafe(32))
        self.storage.execute(
            """
            INSERT INTO trading_accounts(
                user_id, account_key, account_name, token_hash, enabled,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, account_key) DO NOTHING
            """,
            (
                user_id,
                self.DEFAULT_ACCOUNT_KEY,
                account_name.strip() or "MT5",
                placeholder_hash,
                now,
                now,
            ),
        )
        account = self.get_default(user_id)
        if account is None:
            raise RuntimeError("创建 MT5 账户绑定失败")
        return account

    def create_or_rotate_default(
        self,
        user_id: int,
        account_name: str = "MT5",
    ) -> tuple[TradingAccountRecord, str]:
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO trading_accounts(
                user_id, account_key, account_name, token_hash, enabled,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, account_key) DO UPDATE SET
                account_name = excluded.account_name,
                token_hash = excluded.token_hash,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                self.DEFAULT_ACCOUNT_KEY,
                account_name.strip() or "MT5",
                token_hash,
                now,
                now,
            ),
        )
        account = self.get_default(user_id)
        if account is None:
            raise RuntimeError("创建 MT5 账户绑定失败")
        return account, token

    def authenticate(self, user_id: int, token: str) -> Optional[TradingAccountRecord]:
        token_hash = self._hash_token(token)
        row = self.storage.fetchone(
            """
            SELECT id, user_id, account_key, account_name, enabled,
                   last_seen_at, mt5_login, mt5_server, ea_version,
                   activated_at, created_at, updated_at, token_hash
            FROM trading_accounts
            WHERE user_id = ? AND token_hash = ? AND enabled = 1
            """,
            (user_id, token_hash),
        )
        if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
            return None

        now = _now_ts()
        self.storage.execute(
            "UPDATE trading_accounts SET last_seen_at = ? WHERE id = ?",
            (now, int(row["id"])),
        )
        refreshed = self.storage.fetchone(
            """
            SELECT id, user_id, account_key, account_name, enabled,
                   last_seen_at, mt5_login, mt5_server, ea_version,
                   activated_at, created_at, updated_at
            FROM trading_accounts
            WHERE id = ?
            """,
            (int(row["id"]),),
        )
        return self._row_to_account(refreshed)

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_account(row: Optional[sqlite3.Row]) -> Optional[TradingAccountRecord]:
        if row is None:
            return None
        return TradingAccountRecord(
            account_id=int(row["id"]),
            user_id=int(row["user_id"]),
            account_key=row["account_key"],
            account_name=row["account_name"],
            enabled=bool(row["enabled"]),
            last_seen_at=(
                int(row["last_seen_at"])
                if row["last_seen_at"] is not None
                else None
            ),
            mt5_login=row["mt5_login"],
            mt5_server=row["mt5_server"],
            ea_version=row["ea_version"],
            activated_at=(
                int(row["activated_at"])
                if row["activated_at"] is not None
                else None
            ),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class EAActivationRepository:
    """短期、一次性 EA 激活码管理。"""

    CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    CODE_LENGTH = 12

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.accounts = TradingAccountRepository(self.storage)

    def create(self, user_id: int, ttl_seconds: int = 10 * 60) -> tuple[str, int]:
        account = self.accounts.ensure_default(user_id)
        now = _now_ts()
        expires_at = now + max(60, int(ttl_seconds))

        for _ in range(5):
            code = "".join(
                secrets.choice(self.CODE_ALPHABET) for _ in range(self.CODE_LENGTH)
            )
            code_hash = self._hash_code(code)
            try:
                self.storage.initialize()
                with self.storage._lock, self.storage._connect() as conn:
                    conn.execute(
                        """
                        UPDATE ea_activation_codes
                        SET used_at = ?
                        WHERE account_id = ? AND used_at IS NULL
                        """,
                        (now, account.account_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO ea_activation_codes(
                            code_hash, user_id, account_id, expires_at, created_at
                        )
                        VALUES(?, ?, ?, ?, ?)
                        """,
                        (
                            code_hash,
                            user_id,
                            account.account_id,
                            expires_at,
                            now,
                        ),
                    )
                    conn.commit()
                return code, expires_at
            except sqlite3.IntegrityError:
                continue

        raise RuntimeError("生成 EA 激活码失败")

    def has_downloaded(self, user_id: int) -> bool:
        row = self.storage.fetchone(
            """
            SELECT 1 AS found
            FROM ea_activation_codes
            WHERE user_id = ?
            LIMIT 1
            """,
            (user_id,),
        )
        return row is not None

    def consume(
        self,
        code: str,
        *,
        mt5_login: str = "",
        mt5_server: str = "",
        ea_version: str = "",
        program_name: str = "",
    ) -> Optional[tuple[TradingAccountRecord, str]]:
        normalized_code = (code or "").strip().upper()
        if len(normalized_code) != self.CODE_LENGTH:
            return None

        now = _now_ts()
        token = secrets.token_urlsafe(32)
        token_hash = TradingAccountRepository._hash_token(token)
        self.storage.initialize()

        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, account_id
                FROM ea_activation_codes
                WHERE code_hash = ? AND used_at IS NULL AND expires_at >= ?
                """,
                (self._hash_code(normalized_code), now),
            ).fetchone()
            if row is None:
                return None

            cursor = conn.execute(
                """
                UPDATE ea_activation_codes
                SET used_at = ?, mt5_login = ?, mt5_server = ?,
                    ea_version = ?, program_name = ?
                WHERE id = ? AND used_at IS NULL
                """,
                (
                    now,
                    mt5_login.strip(),
                    mt5_server.strip(),
                    ea_version.strip(),
                    program_name.strip(),
                    int(row["id"]),
                ),
            )
            if cursor.rowcount != 1:
                return None

            conn.execute(
                """
                UPDATE trading_accounts
                SET token_hash = ?, enabled = 1, last_seen_at = ?,
                    mt5_login = ?, mt5_server = ?, ea_version = ?,
                    activated_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    token_hash,
                    now,
                    mt5_login.strip(),
                    mt5_server.strip(),
                    ea_version.strip(),
                    now,
                    now,
                    int(row["account_id"]),
                    int(row["user_id"]),
                ),
            )
            conn.commit()

        account = self.accounts.get_default(int(row["user_id"]))
        if account is None:
            raise RuntimeError("EA 激活后未找到绑定账户")
        return account, token

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()


class TradeConfigRepository:
    DEFAULT_CONFIG = {
        "enabled": True,
        "default_volume": 0.01,
        "default_sl_offset": 0.05,
        "mt5_timezone_offset": 0,
        "symbol_config": {
            "GOLD#": {"volume": 0.01, "sl_offset": 0.5},
            "OILCASH#": {"volume": 0.01, "sl_offset": 0.05},
        },
    }

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_config(self, user_id: int) -> Dict:
        row = self.storage.fetchone(
            "SELECT config_json FROM user_trade_configs WHERE user_id = ?",
            (user_id,),
        )
        if row:
            return json.loads(row["config_json"])

        legacy_data = self._read_legacy_config()
        config = legacy_data or self.DEFAULT_CONFIG
        self.save_config(user_id, config)
        return json.loads(json.dumps(config))

    def save_config(self, user_id: int, config: Dict) -> Dict:
        payload = json.dumps(config, ensure_ascii=False)
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO user_trade_configs(user_id, config_json, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (user_id, payload, now, now),
        )
        return json.loads(payload)

    @staticmethod
    def _read_legacy_config() -> Optional[Dict]:
        if not DEFAULT_TRADE_CONFIG_FILE.exists():
            return None
        try:
            return json.loads(DEFAULT_TRADE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None


class LLMConfigRepository:
    DEFAULT_CONFIG = {
        "api_key": "",
        "api_base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    }

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_config(self, user_id: int) -> "LLMConfig":
        from market.models.llm_config import LLMConfig

        row = self.storage.fetchone(
            """
            SELECT api_key, api_base, model
            FROM user_llm_configs
            WHERE user_id = ?
            """,
            (user_id,),
        )
        if row:
            return LLMConfig(
                api_key=row["api_key"],
                api_base=row["api_base"],
                model=row["model"],
            )

        legacy = self._read_legacy_config()
        config = legacy or self.DEFAULT_CONFIG
        return self.save_config(
            user_id,
            api_key=config.get("api_key", ""),
            api_base=config.get("api_base"),
            model=config.get("model"),
        )

    def get_effective_config(self, user_id: int) -> "LLMConfig":
        """管理员使用自己的配置；获批用户使用管理员的共享配置。"""
        from market.models.llm_config import LLMConfig

        user = self.storage.fetchone(
            "SELECT role FROM users WHERE id = ?",
            (user_id,),
        )
        if user is None:
            return LLMConfig()
        if user["role"] == "admin":
            return self.get_config(user_id)

        access = self.storage.fetchone(
            "SELECT status FROM llm_access_requests WHERE user_id = ?",
            (user_id,),
        )
        if access is None or access["status"] != "approved":
            return LLMConfig()

        admin = self.storage.fetchone(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
        )
        return self.get_config(int(admin["id"])) if admin else LLMConfig()

    def save_config(
        self,
        user_id: int,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ) -> "LLMConfig":
        from market.models.llm_config import LLMConfig

        current = self.get_config(user_id) if self._exists(user_id) else LLMConfig.from_dict(self.DEFAULT_CONFIG)
        next_config = LLMConfig(
            api_key=current.api_key if api_key is None else api_key,
            api_base=current.api_base if api_base is None else api_base,
            model=current.model if model is None else model,
        )

        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO user_llm_configs(user_id, api_key, api_base, model, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                api_key = excluded.api_key,
                api_base = excluded.api_base,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (user_id, next_config.api_key, next_config.api_base, next_config.model, now, now),
        )
        return next_config

    def _exists(self, user_id: int) -> bool:
        row = self.storage.fetchone(
            "SELECT 1 AS found FROM user_llm_configs WHERE user_id = ?",
            (user_id,),
        )
        return row is not None

    @staticmethod
    def _read_legacy_config() -> Optional[Dict]:
        if not DEFAULT_LLM_CONFIG_FILE.exists():
            return None
        try:
            return json.loads(DEFAULT_LLM_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None


class LLMAccessRepository:
    VALID_REVIEW_STATUSES = {"approved", "rejected"}

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_status(self, user_id: int, role: Optional[str] = None) -> Dict:
        if role is None:
            user = self.storage.fetchone(
                "SELECT role FROM users WHERE id = ?", (user_id,)
            )
            role = user["role"] if user else "user"
        if role == "admin":
            return {
                "request_id": None,
                "status": "approved",
                "access_granted": True,
                "requested_at": None,
                "reviewed_at": None,
                "review_note": "",
            }

        row = self.storage.fetchone(
            """
            SELECT id, status, requested_at, reviewed_at, review_note
            FROM llm_access_requests
            WHERE user_id = ?
            """,
            (user_id,),
        )
        if row is None:
            return {
                "request_id": None,
                "status": "not_requested",
                "access_granted": False,
                "requested_at": None,
                "reviewed_at": None,
                "review_note": "",
            }
        return {
            "request_id": int(row["id"]),
            "status": row["status"],
            "access_granted": row["status"] == "approved",
            "requested_at": row["requested_at"],
            "reviewed_at": row["reviewed_at"],
            "review_note": row["review_note"],
        }

    def request_access(self, user_id: int, role: str = "user") -> Dict:
        current = self.get_status(user_id, role)
        if current["access_granted"] or current["status"] == "pending":
            return current

        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO llm_access_requests(
                user_id, status, requested_at, reviewed_at, reviewed_by, review_note
            ) VALUES(?, 'pending', ?, NULL, NULL, '')
            ON CONFLICT(user_id) DO UPDATE SET
                status = 'pending',
                requested_at = excluded.requested_at,
                reviewed_at = NULL,
                reviewed_by = NULL,
                review_note = ''
            """,
            (user_id, now),
        )
        return self.get_status(user_id, role)

    def list_requests(self, status: Optional[str] = None) -> List[Dict]:
        params = ()
        where = ""
        if status:
            where = "WHERE request.status = ?"
            params = (status,)
        rows = self.storage.fetchall(
            f"""
            SELECT request.id, request.user_id, users.username,
                   request.status, request.requested_at,
                   request.reviewed_at, request.review_note,
                   reviewer.username AS reviewer_username
            FROM llm_access_requests AS request
            JOIN users ON users.id = request.user_id
            LEFT JOIN users AS reviewer ON reviewer.id = request.reviewed_by
            {where}
            ORDER BY
                CASE request.status WHEN 'pending' THEN 0 ELSE 1 END,
                request.requested_at DESC
            """,
            params,
        )
        return [dict(row) for row in rows]

    def review(
        self,
        request_id: int,
        reviewer_user_id: int,
        decision: str,
        note: str = "",
    ) -> Optional[Dict]:
        if decision not in self.VALID_REVIEW_STATUSES:
            raise ValueError("审批结果必须是 approved 或 rejected")
        now = _now_ts()
        self.storage.execute(
            """
            UPDATE llm_access_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?
            WHERE id = ?
            """,
            (decision, now, reviewer_user_id, note.strip(), request_id),
        )
        row = self.storage.fetchone(
            "SELECT user_id FROM llm_access_requests WHERE id = ?",
            (request_id,),
        )
        return self.get_status(int(row["user_id"])) if row else None


class StrategyConfigRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_all_strategies(self, user_id: int) -> List["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        rows = self.storage.fetchall(
            """
            SELECT strategy_id, symbol, config_json
            FROM user_strategy_configs
            WHERE user_id = ?
            ORDER BY symbol, created_at, strategy_id
            """,
            (user_id,),
        )
        if rows:
            return [TradingStrategy.from_dict(json.loads(row["config_json"])) for row in rows]

        legacy_strategies = self._read_legacy_strategies()
        if legacy_strategies:
            self.replace_all(user_id, legacy_strategies)
            return self.get_all_strategies(user_id)

        return []

    def get_strategy(self, user_id: int, symbol: str) -> Optional["TradingStrategy"]:
        """兼容旧调用，返回该品种创建最早的策略。"""
        strategies = self.get_strategies(user_id, symbol)
        return strategies[0] if strategies else None

    def get_strategy_by_id(
        self, user_id: int, strategy_id: str
    ) -> Optional["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        row = self.storage.fetchone(
            """
            SELECT config_json
            FROM user_strategy_configs
            WHERE user_id = ? AND strategy_id = ?
            """,
            (user_id, strategy_id),
        )
        if row:
            return TradingStrategy.from_dict(json.loads(row["config_json"]))
        return None

    def get_strategies(self, user_id: int, symbol: str) -> List["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        rows = self.storage.fetchall(
            """
            SELECT config_json
            FROM user_strategy_configs
            WHERE user_id = ? AND symbol = ?
            ORDER BY created_at, strategy_id
            """,
            (user_id, symbol),
        )
        return [TradingStrategy.from_dict(json.loads(row["config_json"])) for row in rows]

    def save_strategy(self, user_id: int, strategy: "TradingStrategy") -> "TradingStrategy":
        payload = json.dumps(strategy.to_dict(), ensure_ascii=False)
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO user_strategy_configs(
                user_id, strategy_id, symbol, config_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, strategy_id) DO UPDATE SET
                symbol = excluded.symbol,
                config_json = excluded.config_json,
                updated_at = excluded.updated_at
            """,
            (user_id, strategy.strategy_id, strategy.symbol, payload, now, now),
        )
        return strategy

    def delete_strategy(self, user_id: int, symbol: str) -> bool:
        """兼容旧调用，删除该品种的全部策略。"""
        if not self.get_strategies(user_id, symbol):
            return False
        self.storage.execute(
            "DELETE FROM user_strategy_configs WHERE user_id = ? AND symbol = ?",
            (user_id, symbol),
        )
        return True

    def delete_strategy_by_id(self, user_id: int, strategy_id: str) -> bool:
        if not self.get_strategy_by_id(user_id, strategy_id):
            return False
        self.storage.execute(
            "DELETE FROM user_strategy_configs WHERE user_id = ? AND strategy_id = ?",
            (user_id, strategy_id),
        )
        return True

    def replace_all(self, user_id: int, strategies: List["TradingStrategy"]) -> None:
        self.storage.execute(
            "DELETE FROM user_strategy_configs WHERE user_id = ?",
            (user_id,),
        )
        for strategy in strategies:
            self.save_strategy(user_id, strategy)

    @staticmethod
    def _read_legacy_strategies() -> List["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        if not DEFAULT_STRATEGY_CONFIG_FILE.exists():
            return []
        try:
            data = json.loads(DEFAULT_STRATEGY_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [
            TradingStrategy.from_dict(strategy_data)
            for strategy_data in data.get("strategies", {}).values()
        ]


class RuntimeStateRepository:
    """账户级运行数据持久化；实时行情数据不使用此仓储。"""

    def __init__(
        self,
        user_id: int,
        account_id: Optional[int],
        storage: Optional[SQLiteStorage] = None,
    ):
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)
        self.storage = storage or get_storage()

    def set_scope(self, user_id: int, account_id: Optional[int]) -> None:
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)

    def upsert_entity(
        self,
        entity_type: str,
        entity_id: str,
        payload: Dict,
        symbol: str = "",
        status: str = "",
    ) -> None:
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO runtime_entities(
                user_id, account_id, entity_type, entity_id, symbol, status,
                payload_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, account_id, entity_type, entity_id) DO UPDATE SET
                symbol = excluded.symbol,
                status = excluded.status,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                self.user_id,
                self.account_id,
                entity_type,
                str(entity_id),
                symbol,
                status,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )

    def list_entities(
        self,
        entity_type: str,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict]:
        params: List = [self.user_id, self.account_id, entity_type]
        sql = """
            SELECT payload_json
            FROM runtime_entities
            WHERE user_id = ? AND account_id = ? AND entity_type = ?
        """
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY created_at, entity_id"
        return [
            json.loads(row["payload_json"])
            for row in self.storage.fetchall(sql, tuple(params))
        ]

    def delete_entity(self, entity_type: str, entity_id: str) -> None:
        self.storage.execute(
            """
            DELETE FROM runtime_entities
            WHERE user_id = ? AND account_id = ?
              AND entity_type = ? AND entity_id = ?
            """,
            (self.user_id, self.account_id, entity_type, str(entity_id)),
        )

    def delete_entities(
        self,
        entity_type: str,
        symbol: Optional[str] = None,
    ) -> None:
        sql = """
            DELETE FROM runtime_entities
            WHERE user_id = ? AND account_id = ? AND entity_type = ?
        """
        params: tuple = (self.user_id, self.account_id, entity_type)
        if symbol is not None:
            sql += " AND symbol = ?"
            params += (symbol,)
        self.storage.execute(sql, params)

    def migrate_scope(self, account_id: int) -> None:
        target_account_id = int(account_id)
        if target_account_id == self.account_id:
            return
        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_entities(
                    user_id, account_id, entity_type, entity_id, symbol, status,
                    payload_json, created_at, updated_at
                )
                SELECT user_id, ?, entity_type, entity_id, symbol, status,
                       payload_json, created_at, ?
                FROM runtime_entities
                WHERE user_id = ? AND account_id = ?
                """,
                (target_account_id, now, self.user_id, self.account_id),
            )
            conn.execute(
                "DELETE FROM runtime_entities WHERE user_id = ? AND account_id = ?",
                (self.user_id, self.account_id),
            )
            conn.commit()
        self.account_id = target_account_id


def bootstrap_runtime_storage(password_hash_builder) -> UserRecord:
    storage = get_storage()
    storage.initialize()

    user_repo = UserRepository(storage)
    meta_repo = MetaRepository(storage)

    if not meta_repo.get("auth_secret"):
        legacy_secret = _read_legacy_auth_secret()
        meta_repo.set("auth_secret", legacy_secret or os.urandom(32).hex())

    if user_repo.count() == 0:
        legacy_users = _read_legacy_auth_users()
        if legacy_users:
            for user in legacy_users:
                try:
                    user_repo.create_user(
                        username=user["username"],
                        password_hash=user["password_hash"],
                        salt=user["salt"],
                        role=(
                            "admin"
                            if user["username"].strip().lower()
                            == _get_env_default_admin_username().strip().lower()
                            else "user"
                        ),
                    )
                except Exception:
                    continue
        else:
            user_repo.ensure_runtime_user(password_hash_builder)

    return user_repo.ensure_runtime_user(password_hash_builder)


def _read_legacy_auth_store() -> Dict:
    auth_file = Path(os.getenv("AI_TRADER_AUTH_FILE") or DEFAULT_AUTH_FILE)
    if not auth_file.exists():
        return {}
    try:
        return json.loads(auth_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_legacy_auth_secret() -> Optional[str]:
    return _read_legacy_auth_store().get("secret")


def _read_legacy_auth_users() -> List[Dict]:
    data = _read_legacy_auth_store()
    users = data.get("users", [])
    valid_users = []
    for user in users:
        if user.get("username") and user.get("password_hash") and user.get("salt"):
            valid_users.append(user)
    return valid_users
