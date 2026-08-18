"""MySQL runtime storage adapter for the existing repository layer."""

from __future__ import annotations

import os
import queue
import re
import threading
from typing import Any, Dict, List, Optional


class MySQLConnection:
    """Expose the small sqlite3 connection surface used by repositories."""

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
    """Thread-safe MySQL storage. SQLite is intentionally not a runtime option."""

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
            self._initialized = True

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.initialize()
        with self._connect() as conn:
            conn.execute(sql, params)

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
        """Translate the repository's common SQLite syntax to MySQL 8 syntax."""
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
        # issue this SQLite pragma before transactions, so make it a harmless no-op.
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
