"""MySQL runtime storage adapter for the existing repository layer."""

from __future__ import annotations

import os
import queue
import re
import threading
from typing import Any, Dict, List, Optional


class MySQLConnection:
    """Expose the small DB-API connection surface used by repositories."""

    def __init__(self, connection, release, discard):
        self._connection = connection
        self._release = release
        self._discard = discard

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        reusable = exc_type is None
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        except Exception:
            reusable = False
            raise
        finally:
            if reusable:
                self._release(self._connection)
            else:
                self._discard(self._connection)

    def execute(self, sql: str, params: tuple = ()):
        cursor = self._connection.cursor()
        cursor.execute(MySQLStorage.translate_sql(sql), tuple(params or ()))
        return cursor

    def executemany(self, sql: str, params: List[tuple]):
        cursor = self._connection.cursor()
        cursor.executemany(MySQLStorage.translate_sql(sql), params)
        return cursor

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()


class MySQLStorage:
    """Thread-safe MySQL storage. MySQL is intentionally not a runtime option."""

    def __init__(self):
        self.host = os.getenv("AI_TRADER_MYSQL_HOST", "").strip()
        self.port = int(os.getenv("AI_TRADER_MYSQL_PORT", "3306"))
        self.user = os.getenv("AI_TRADER_MYSQL_USER", "").strip()
        self.password = os.getenv("AI_TRADER_MYSQL_PASSWORD", "")
        self.database = os.getenv("AI_TRADER_MYSQL_DATABASE", "ai_trader").strip()
        self.pool_size = max(
            2, int(os.getenv("AI_TRADER_MYSQL_POOL_SIZE", "12"))
        )
        self._lock = threading.RLock()
        self._initialize_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=self.pool_size)
        self._pool_created = 0
        self._initialized = False

    @staticmethod
    def _driver():
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("缺少 PyMySQL，请安装 requirements.txt 后再启动服务") from exc
        return pymysql

    def _new_connection(self):
        if not self.host or not self.user or not self.database:
            raise RuntimeError(
                "MySQL 未配置：请设置 AI_TRADER_MYSQL_HOST、AI_TRADER_MYSQL_USER、"
                "AI_TRADER_MYSQL_PASSWORD、AI_TRADER_MYSQL_DATABASE"
            )
        pymysql = self._driver()
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30,
        )
    def _borrow_connection(self):
        try:
            connection = self._pool.get_nowait()
        except queue.Empty:
            with self._pool_lock:
                if self._pool_created < self.pool_size:
                    self._pool_created += 1
                    try:
                        connection = self._new_connection()
                    except Exception:
                        self._pool_created -= 1
                        raise
                else:
                    connection = None
            if connection is None:
                try:
                    connection = self._pool.get(timeout=30)
                except queue.Empty as exc:
                    raise RuntimeError("MySQL 连接池已耗尽，请稍后重试") from exc

        try:
            connection.ping(reconnect=True)
        except Exception:
            self._discard_connection(connection)
            return self._borrow_connection()
        return connection

    def _release_connection(self, connection) -> None:
        try:
            self._pool.put_nowait(connection)
        except queue.Full:
            self._discard_connection(connection)

    def _discard_connection(self, connection) -> None:
        try:
            connection.close()
        finally:
            with self._pool_lock:
                self._pool_created = max(0, self._pool_created - 1)

    def close(self) -> None:
        """Close every idle pooled connection and reset pool accounting."""
        with self._pool_lock:
            while True:
                try:
                    connection = self._pool.get_nowait()
                except queue.Empty:
                    break
                try:
                    connection.close()
                finally:
                    self._pool_created = max(0, self._pool_created - 1)

    def _connect(self) -> MySQLConnection:
        connection = self._borrow_connection()
        return MySQLConnection(
            connection,
            self._release_connection,
            self._discard_connection,
        )

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            with self._connect() as conn:
                conn.execute("SELECT 1")
                conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_id VARCHAR(64) NOT NULL,
                    event_name VARCHAR(120) NOT NULL,
                    aggregate_type VARCHAR(80) NOT NULL DEFAULT '',
                    aggregate_id VARCHAR(255) NOT NULL DEFAULT '',
                    user_id BIGINT NOT NULL DEFAULT 0,
                    account_id BIGINT NOT NULL DEFAULT 0,
                    symbol VARCHAR(64) NOT NULL DEFAULT '',
                    payload_json JSON NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    retry_count INT NOT NULL DEFAULT 0,
                    next_retry_at BIGINT NOT NULL,
                    last_error VARCHAR(500) NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL,
                    published_at BIGINT NULL,
                    claimed_at BIGINT NULL,
                    lease_until BIGINT NULL,
                    updated_at BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (event_id),
                    KEY idx_outbox_pending (status, next_retry_at, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS platform_instrument_mappings (
                    mapping_id VARCHAR(255) NOT NULL,
                    broker_name VARCHAR(120) NOT NULL DEFAULT '',
                    broker_server VARCHAR(120) NOT NULL,
                    native_symbol VARCHAR(40) NOT NULL,
                    mapping_group VARCHAR(80) NOT NULL,
                    display_name VARCHAR(255) NOT NULL DEFAULT '',
                    enabled TINYINT NOT NULL DEFAULT 1,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (mapping_id),
                    UNIQUE KEY uq_platform_instrument_broker_symbol (
                        broker_server, native_symbol
                    ),
                    KEY idx_platform_instrument_group (mapping_group, enabled)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS ai_trade_suggestions (
                    suggestion_id VARCHAR(64) NOT NULL,
                    user_id BIGINT NOT NULL,
                    signal_source_id VARCHAR(255) NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    period VARCHAR(16) NOT NULL,
                    plan_fingerprint VARCHAR(128) NOT NULL,
                    direction VARCHAR(16) NOT NULL,
                    confidence INT NOT NULL DEFAULT 0,
                    entry_price DOUBLE NOT NULL,
                    stop_loss DOUBLE NOT NULL,
                    take_profit DOUBLE NOT NULL,
                    reason TEXT NOT NULL,
                    analysis_at BIGINT NOT NULL,
                    last_seen_at BIGINT NOT NULL,
                    suggestion_count INT NOT NULL DEFAULT 1,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (suggestion_id),
                    KEY idx_ai_trade_suggestions_source_time (
                        user_id, signal_source_id, last_seen_at
                    )
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS structure_trade_plans (
                    plan_id VARCHAR(64) NOT NULL,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    strategy_id VARCHAR(64) NOT NULL,
                    signal_source_id VARCHAR(64) NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    period VARCHAR(16) NOT NULL,
                    plan_group_id VARCHAR(64) NOT NULL,
                    setup_type VARCHAR(64) NOT NULL,
                    direction VARCHAR(16) NOT NULL,
                    entry_mode VARCHAR(32) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    structure_bar_time BIGINT NOT NULL,
                    valid_from BIGINT NOT NULL,
                    expires_at BIGINT NOT NULL,
                    fingerprint VARCHAR(64) NOT NULL,
                    payload_json LONGTEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (plan_id),
                    KEY idx_structure_plans_runtime (
                        user_id, account_id, strategy_id, signal_source_id,
                        symbol, period, status, expires_at
                    ),
                    KEY idx_structure_plans_group (plan_group_id, status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS market_data_sources (
                    user_id BIGINT NOT NULL,
                    canonical_symbol VARCHAR(64) NOT NULL,
                    primary_account_id BIGINT NOT NULL,
                    broker_name VARCHAR(120) NOT NULL,
                    native_symbol VARCHAR(64) NOT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id, canonical_symbol),
                    KEY idx_market_source_account (user_id, primary_account_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS market_data_account_policies (
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    broker_name VARCHAR(120) NOT NULL,
                    mode VARCHAR(24) NOT NULL,
                    primary_account_id BIGINT NOT NULL DEFAULT 0,
                    conflict_symbols_json LONGTEXT NOT NULL,
                    message VARCHAR(512) NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id, account_id),
                    KEY idx_market_policy_mode (user_id, mode)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS structure_plan_executions (
                    execution_id VARCHAR(64) NOT NULL,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    deployment_id VARCHAR(64) NOT NULL,
                    strategy_id VARCHAR(64) NOT NULL,
                    plan_id VARCHAR(64) NOT NULL,
                    plan_group_id VARCHAR(64) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    order_id VARCHAR(64) NOT NULL DEFAULT '',
                    reason VARCHAR(512) NOT NULL DEFAULT '',
                    payload_json LONGTEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (execution_id),
                    UNIQUE KEY uq_structure_plan_deployment (
                        user_id, account_id, deployment_id, plan_id
                    ),
                    KEY idx_structure_plan_execution_group (
                        user_id, account_id, deployment_id, plan_group_id, status
                    )
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS live_trade_deals (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    ticket BIGINT NOT NULL,
                    mt5_order BIGINT NOT NULL DEFAULT 0,
                    mt5_position_id BIGINT NOT NULL DEFAULT 0,
                    symbol VARCHAR(64) NOT NULL DEFAULT '',
                    deal_type INT NOT NULL DEFAULT 0,
                    entry_type INT NOT NULL DEFAULT 0,
                    volume DOUBLE NOT NULL DEFAULT 0,
                    price DOUBLE NOT NULL DEFAULT 0,
                    profit DOUBLE NOT NULL DEFAULT 0,
                    swap DOUBLE NOT NULL DEFAULT 0,
                    commission DOUBLE NOT NULL DEFAULT 0,
                    deal_time VARCHAR(32) NOT NULL DEFAULT '',
                    deal_timestamp BIGINT NOT NULL DEFAULT 0,
                    broker_utc_offset_seconds INT NOT NULL DEFAULT 0,
                    comment VARCHAR(512) NOT NULL DEFAULT '',
                    received_at BIGINT NOT NULL,
                    payload_json JSON NOT NULL,
                    position_attribution_json JSON NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_live_trade_deals_account_ticket (account_id, ticket),
                    KEY idx_live_trade_deals_account_time (account_id, deal_timestamp, received_at),
                    CONSTRAINT fk_live_trade_deals_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CONSTRAINT fk_live_trade_deals_account FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS historical_klines (
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    period VARCHAR(16) NOT NULL,
                    timestamp BIGINT NOT NULL,
                    timestamp_utc BIGINT NOT NULL DEFAULT 0,
                    broker_utc_offset_seconds INT NOT NULL DEFAULT 0,
                    open_price DOUBLE NOT NULL,
                    high_price DOUBLE NOT NULL,
                    low_price DOUBLE NOT NULL,
                    close_price DOUBLE NOT NULL,
                    volume DOUBLE NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id, account_id, symbol, period, timestamp),
                    KEY idx_historical_klines_lookup (user_id, account_id, symbol, period, timestamp),
                    KEY idx_historical_klines_utc (user_id, account_id, symbol, period, timestamp_utc),
                    KEY idx_historical_klines_retention (timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS strategy_pivot_points (
                    pivot_id VARCHAR(64) NOT NULL,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    strategy_id VARCHAR(64) NOT NULL,
                    signal_source_id VARCHAR(64) NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    period VARCHAR(16) NOT NULL,
                    config_fingerprint VARCHAR(64) NOT NULL,
                    pivot_time BIGINT NOT NULL,
                    confirmed_at BIGINT NOT NULL,
                    valid_until BIGINT NOT NULL,
                    price DOUBLE NOT NULL,
                    direction VARCHAR(8) NOT NULL,
                    strength INT NOT NULL,
                    confirmation_count INT NOT NULL DEFAULT 1,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (pivot_id),
                    KEY idx_strategy_pivots_runtime (
                        user_id, account_id, strategy_id, signal_source_id,
                        config_fingerprint, valid_until
                    ),
                    KEY idx_strategy_pivots_expiry (valid_until)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                # Persistent resource counters are read by the strategy list
                # endpoint for quota display.  Keep this table in the MySQL
                # bootstrap path because production does not initialize the
                # MySQL schema.
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS user_resource_usage (
                    user_id BIGINT NOT NULL,
                    resource_type VARCHAR(32) NOT NULL,
                    used_count BIGINT NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id, resource_type),
                    KEY idx_user_resource_usage_type (resource_type, updated_at),
                    CONSTRAINT fk_user_resource_usage_user
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                try:
                    conn.execute(
                        "ALTER TABLE ai_signal_sources ADD COLUMN "
                        "market_data_account_id BIGINT NOT NULL DEFAULT 0"
                    )
                except Exception:
                    # The migration is idempotent; MySQL reports a duplicate
                    # column on every startup after the first successful run.
                    pass
                # Execution attribution is deliberately migrated by the MySQL
                # adapter itself. MySQLStorage.initialize() is not part of the
                # production runtime, so adding compatibility columns there is
                # insufficient for RDS deployments.
                compatibility_columns = {
                    "outbox_events": (
                        ("claimed_at", "BIGINT NULL"),
                        ("lease_until", "BIGINT NULL"),
                    ),
                    "trade_execution_reports": (
                        ("execution_status", "VARCHAR(32) NOT NULL DEFAULT 'pending'"),
                        ("mt5_position_id", "BIGINT NOT NULL DEFAULT 0"),
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "live_trade_deals": (
                        ("position_attribution_json", "JSON NULL"),
                        ("deal_timestamp", "BIGINT NOT NULL DEFAULT 0"),
                        ("broker_utc_offset_seconds", "INT NOT NULL DEFAULT 0"),
                    ),
                    "historical_klines": (
                        ("timestamp_utc", "BIGINT NOT NULL DEFAULT 0"),
                        ("broker_utc_offset_seconds", "INT NOT NULL DEFAULT 0"),
                    ),
                    "backtest_dataset_chunks": (
                        ("broker_utc_offset_seconds", "INT NOT NULL DEFAULT 0"),
                    ),
                    "paper_orders": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "paper_positions": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "paper_trades": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "backtest_orders": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "backtest_positions": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                    "backtest_trades": (
                        ("position_attribution_json", "JSON NULL"),
                    ),
                }
                for table, columns in compatibility_columns.items():
                    for column, column_type in columns:
                        try:
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN "
                                f"{column} {column_type}"
                            )
                        except Exception as exc:
                            # 1060 is MySQL's duplicate-column error. Any other
                            # failure must stop startup, otherwise the next order
                            # would fail later with a less actionable SQL error.
                            if getattr(exc, "args", (None,))[0] != 1060:
                                raise
                compatibility_indexes = (
                    (
                        "historical_klines",
                        "idx_historical_klines_utc",
                        "user_id, account_id, symbol, period, timestamp_utc",
                    ),
                    (
                        "live_trade_deals",
                        "idx_live_trade_deals_account_timestamp",
                        "account_id, deal_timestamp, received_at",
                    ),
                )
                for table, index_name, columns in compatibility_indexes:
                    try:
                        conn.execute(
                            f"ALTER TABLE {table} ADD INDEX {index_name} ({columns})"
                        )
                    except Exception as exc:
                        if getattr(exc, "args", (None,))[0] != 1061:
                            raise
            self._initialized = True

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute(sql, params)

    def executemany(self, sql: str, params: List[tuple]) -> None:
        """Execute one statement for a batch using the pooled transaction.

        Repository code uses this for persisted K-line batches. Exposing the
        same surface as the connection wrapper keeps MySQL as the only runtime
        store without falling back to MySQL-specific write paths.
        """
        if not params:
            return
        self.initialize()
        with self._connect() as conn:
            conn.executemany(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        self.initialize()
        with self._connect() as conn:
            return conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.initialize()
        with self._connect() as conn:
            return list(conn.execute(sql, params).fetchall())

    @staticmethod
    def translate_sql(sql: str) -> str:
        """Translate the repository's common MySQL syntax to MySQL 8 syntax."""
        text = str(sql)
        # `key` is reserved by MySQL, but only app_meta uses it as a column.
        # Keep DDL keywords such as PRIMARY KEY and UNIQUE KEY untouched.
        text = re.sub(
            r"(\bapp_meta\s*\(\s*)key\b",
            r"\1`key`",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"(\b(?:WHERE|AND|OR|SELECT|ORDER\s+BY|GROUP\s+BY)\s+)key\b",
            r"\1`key`",
            text,
            flags=re.I,
        )
        text = re.sub(r"\bapp_meta\.key\b", "app_meta.`key`", text, flags=re.I)
        # Foreign-key enforcement is configured by the server. Existing callers
        # issue this MySQL pragma before transactions, so make it a harmless no-op.
        if re.match(r"^\s*PRAGMA\s+foreign_keys\s*=\s*ON\s*;?\s*$", text, re.I):
            return "SELECT 1"
        text = re.sub(r"\bBEGIN\s+IMMEDIATE\b", "START TRANSACTION", text, flags=re.I)
        text = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT IGNORE", text, flags=re.I)
        text = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "REPLACE", text, flags=re.I)
        text = re.sub(r"\blast_insert_rowid\(\)", "LAST_INSERT_ID()", text, flags=re.I)

        # JSON_EXTRACT returns a JSON scalar in MySQL; repositories compare it
        # with text values, so consistently unquote it at the boundary.
        text = re.sub(
            r"\bjson_extract\(([^,()]+),\s*('(?:[^']*)')\)",
            r"JSON_UNQUOTE(JSON_EXTRACT(\1, \2))",
            text,
            flags=re.I,
        )

        if re.search(r"\bON\s+CONFLICT\s*(?:\([^)]*\))?\s*DO\s+NOTHING", text, re.I):
            text = re.sub(
                r"\s+ON\s+CONFLICT\s*(?:\([^)]*\))?\s*DO\s+NOTHING",
                "",
                text,
                flags=re.I,
            )
            text = re.sub(r"\bINSERT\s+INTO\b", "INSERT IGNORE INTO", text, count=1, flags=re.I)
        text = re.sub(
            r"\s+ON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET",
            " ON DUPLICATE KEY UPDATE",
            text,
            flags=re.I,
        )
        text = re.sub(r"\bexcluded\.([A-Za-z_][A-Za-z0-9_]*)", r"VALUES(\1)", text, flags=re.I)
        return text.replace("?", "%s")
