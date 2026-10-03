"""MySQL runtime storage adapter for the existing repository layer."""

from __future__ import annotations

import os
import queue
import re
import threading
import hashlib
import time
from typing import Any, Dict, List, Optional
from runtime_cache import (
    cache_domain_for_sql,
    domains_for_sql,
    invalidate,
    sql_read_cache,
)


class MySQLConnection:
    """Expose the small DB-API connection surface used by repositories."""

    def __init__(self, connection, release, discard, storage=None):
        self._connection = connection
        self._release = release
        self._discard = discard
        self._storage = storage

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
        started = time.perf_counter()
        try:
            cursor.execute(MySQLStorage.translate_sql(sql), tuple(params or ()))
        except Exception:
            if self._storage is not None:
                self._storage.record_sql_timing(sql, time.perf_counter() - started)
            raise
        if self._storage is not None:
            self._storage.record_sql_timing(sql, time.perf_counter() - started)
        return cursor

    def executemany(self, sql: str, params: List[tuple]):
        cursor = self._connection.cursor()
        started = time.perf_counter()
        try:
            cursor.executemany(MySQLStorage.translate_sql(sql), params)
        except Exception:
            if self._storage is not None:
                self._storage.record_sql_timing(sql, time.perf_counter() - started)
            raise
        if self._storage is not None:
            self._storage.record_sql_timing(sql, time.perf_counter() - started)
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
        # Runtime ticks can fan out across Paper, Live and maintenance work.
        # Keep the pool configurable, but make the safe default large enough
        # that a slow receipt/analytics request cannot starve matching.
        self.pool_size = max(
            4, int(os.getenv("AI_TRADER_MYSQL_POOL_SIZE", "32"))
        )
        self.pool_wait_seconds = max(
            5, int(os.getenv("AI_TRADER_MYSQL_POOL_WAIT_SECONDS", "60"))
        )
        self._lock = threading.RLock()
        self._initialize_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=self.pool_size)
        self._pool_created = 0
        self._initialized = False
        self.sql_slow_threshold_ms = max(
            1.0, float(os.getenv("AI_TRADER_SQL_SLOW_THRESHOLD_MS", "500"))
        )
        self._sql_stats_lock = threading.RLock()
        self._sql_stats_pending: Dict[tuple, Dict[str, Any]] = {}
        self._sql_stats_flush_running = False

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
                    connection = self._pool.get(timeout=self.pool_wait_seconds)
                except queue.Empty as exc:
                    raise RuntimeError(
                        f"MySQL 连接池已耗尽（{self.pool_size} 个连接均繁忙），请稍后重试"
                    ) from exc

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
            self,
        )

    @staticmethod
    def _sql_shape(sql: str) -> str:
        """Normalize SQL without persisting bound parameter values."""
        text = re.sub(r"\s+", " ", str(sql or "").strip())
        text = re.sub(r"'([^']|'')*'", "?", text)
        text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
        return text[:4000]

    def record_sql_timing(self, sql: str, elapsed_seconds: float) -> None:
        """Aggregate SQL latency in memory and flush in batches.

        This method is called from every DB-API operation, including failed
        statements.  Aggregation is intentionally lock-light; only batches
        are persisted so observability cannot become a per-query write.
        """
        shape = self._sql_shape(sql)
        digest = hashlib.sha256(shape.encode("utf-8")).hexdigest()
        elapsed_ms = max(0.0, float(elapsed_seconds) * 1000.0)
        operation = (shape.split(" ", 1)[0] if shape else "UNKNOWN").upper()[:16]
        now = int(time.time())
        hour_bucket = now - (now % 3600)
        pending_key = (digest, hour_bucket)
        with self._sql_stats_lock:
            item = self._sql_stats_pending.setdefault(pending_key, {
                "sql_hash": digest, "hour_bucket": hour_bucket,
                "operation": operation,
                "sql_shape": shape, "count": 0, "total_ms": 0.0,
                "max_ms": 0.0, "slow_count": 0,
            })
            item["count"] += 1
            item["total_ms"] += elapsed_ms
            item["max_ms"] = max(item["max_ms"], elapsed_ms)
            if elapsed_ms >= self.sql_slow_threshold_ms:
                item["slow_count"] += 1
            item["last_ms"] = elapsed_ms
            item["last_at"] = now
            should_flush = len(self._sql_stats_pending) >= 50
            if should_flush and not self._sql_stats_flush_running:
                self._sql_stats_flush_running = True
                threading.Thread(
                    target=self._flush_sql_stats,
                    name="sql-latency-flush",
                    daemon=True,
                ).start()

    def _flush_sql_stats(self) -> None:
        batch = []
        connection = None
        try:
            with self._sql_stats_lock:
                batch = list(self._sql_stats_pending.values())
                self._sql_stats_pending.clear()
            if not batch:
                return
            self.initialize()
            connection = self._borrow_connection()
            cursor = connection.cursor()
            cursor.executemany(
                self.translate_sql(
                    """
                    INSERT INTO sql_execution_stats(
                        sql_hash, hour_bucket, operation, sql_shape, execution_count,
                        total_duration_ms, max_duration_ms, slow_count,
                        last_duration_ms, last_executed_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                        operation=VALUES(operation), sql_shape=VALUES(sql_shape),
                        execution_count=execution_count + VALUES(execution_count),
                        total_duration_ms=total_duration_ms + VALUES(total_duration_ms),
                        max_duration_ms=GREATEST(max_duration_ms, VALUES(max_duration_ms)),
                        slow_count=slow_count + VALUES(slow_count),
                        last_duration_ms=VALUES(last_duration_ms),
                        last_executed_at=VALUES(last_executed_at), updated_at=VALUES(updated_at)
                    """
                ),
                [
                    (
                        item["sql_hash"], item["hour_bucket"], item["operation"], item["sql_shape"],
                        item["count"], item["total_ms"], item["max_ms"],
                        item["slow_count"], item.get("last_ms", 0.0),
                        item.get("last_at", int(time.time())), int(time.time()),
                    )
                    for item in batch
                ],
            )
            connection.commit()
            self._release_connection(connection)
            connection = None
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    self._discard_connection(connection)
            if batch:
                with self._sql_stats_lock:
                    for item in batch:
                        current = self._sql_stats_pending.get((item["sql_hash"], item["hour_bucket"]))
                        if current is None:
                            self._sql_stats_pending[(item["sql_hash"], item["hour_bucket"])] = item
                        else:
                            current["count"] += item["count"]
                            current["total_ms"] += item["total_ms"]
                            current["max_ms"] = max(current["max_ms"], item["max_ms"])
                            current["slow_count"] += item["slow_count"]
                            current["last_ms"] = item.get("last_ms", current.get("last_ms", 0.0))
                            current["last_at"] = max(current.get("last_at", 0), item.get("last_at", 0))
            print(f"[MySQLStorage] SQL 延时统计写入失败: {exc}")
        finally:
            with self._sql_stats_lock:
                self._sql_stats_flush_running = False

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
                conn.execute("""
                CREATE TABLE IF NOT EXISTS background_tasks (
                    task_id VARCHAR(64) NOT NULL,
                    task_key VARCHAR(255) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'queued',
                    attempts INT NOT NULL DEFAULT 0,
                    max_retries INT NOT NULL DEFAULT 0,
                    submitted_at BIGINT NOT NULL,
                    started_at BIGINT NULL,
                    finished_at BIGINT NULL,
                    lease_until BIGINT NULL,
                    error_message VARCHAR(1000) NOT NULL DEFAULT '',
                    result_json JSON NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (task_id),
                    UNIQUE KEY uq_background_task_key (task_key),
                    KEY idx_background_task_status (status, updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS sql_execution_stats (
                    sql_hash VARCHAR(64) NOT NULL,
                    hour_bucket BIGINT NOT NULL DEFAULT 0,
                    operation VARCHAR(16) NOT NULL,
                    sql_shape TEXT NOT NULL,
                    execution_count BIGINT NOT NULL DEFAULT 0,
                    total_duration_ms DOUBLE NOT NULL DEFAULT 0,
                    max_duration_ms DOUBLE NOT NULL DEFAULT 0,
                    slow_count BIGINT NOT NULL DEFAULT 0,
                    last_duration_ms DOUBLE NOT NULL DEFAULT 0,
                    last_executed_at BIGINT NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (sql_hash, hour_bucket),
                    KEY idx_sql_stats_slow (slow_count, max_duration_ms),
                    KEY idx_sql_stats_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl_statistics (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    user_id BIGINT NOT NULL, account_id BIGINT NOT NULL,
                    execution_mode VARCHAR(16) NOT NULL, business_date DATE NOT NULL,
                    symbol VARCHAR(64) NOT NULL, period VARCHAR(16) NOT NULL,
                    setup_type VARCHAR(64) NOT NULL, strategy_id VARCHAR(64) NOT NULL DEFAULT '',
                    strategy_name VARCHAR(160) NOT NULL DEFAULT '', strategy_status VARCHAR(24) NOT NULL DEFAULT '',
                    trade_count INT NOT NULL DEFAULT 0, win_count INT NOT NULL DEFAULT 0,
                    loss_count INT NOT NULL DEFAULT 0,
                    gross_profit DOUBLE NOT NULL DEFAULT 0, gross_loss DOUBLE NOT NULL DEFAULT 0,
                    max_profit DOUBLE NULL, min_profit DOUBLE NULL,
                    max_loss DOUBLE NULL, min_loss DOUBLE NULL, net_profit DOUBLE NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_daily_pnl_bucket (user_id,account_id,business_date,symbol,period,setup_type,strategy_id),
                    KEY idx_daily_pnl_account_date (user_id,account_id,business_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS market_execution_quality_samples (
                    account_id BIGINT NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    window_start BIGINT NOT NULL,
                    sample_count INT NOT NULL DEFAULT 0,
                    spread_median_points DOUBLE NOT NULL DEFAULT 0,
                    spread_p95_points DOUBLE NOT NULL DEFAULT 0,
                    spread_max_points DOUBLE NOT NULL DEFAULT 0,
                    buy_slippage_p95_points DOUBLE NOT NULL DEFAULT 0,
                    sell_slippage_p95_points DOUBLE NOT NULL DEFAULT 0,
                    created_at BIGINT NOT NULL,
                    PRIMARY KEY (account_id, symbol, window_start),
                    KEY idx_quality_samples_window (window_start)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS market_execution_quality_events (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    account_id BIGINT NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    reason VARCHAR(80) NOT NULL,
                    details_json JSON NOT NULL,
                    created_at BIGINT NOT NULL,
                    PRIMARY KEY (id),
                    KEY idx_quality_events_lookup (account_id, symbol, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                # Migrate installations created before hourly buckets existed.
                # Existing cumulative rows are assigned to the hour containing
                # their last update; new rows are isolated by (hash, hour).
                try:
                    conn.execute("ALTER TABLE sql_execution_stats ADD COLUMN hour_bucket BIGINT NOT NULL DEFAULT 0 AFTER sql_hash")
                except Exception:
                    pass
                try:
                    conn.execute("UPDATE sql_execution_stats SET hour_bucket = updated_at - MOD(updated_at, 3600) WHERE hour_bucket = 0")
                    conn.execute("ALTER TABLE sql_execution_stats DROP PRIMARY KEY, ADD PRIMARY KEY (sql_hash, hour_bucket)")
                except Exception:
                    # Fresh tables already have the composite key, so this is
                    # expected to fail harmlessly on subsequent startups.
                    pass
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
                conn.execute("""
                CREATE TABLE IF NOT EXISTS structure_default_configs (
                    user_id BIGINT NOT NULL, version BIGINT NOT NULL DEFAULT 1,
                    config_json JSON NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'active',
                    updated_by BIGINT NOT NULL DEFAULT 0, updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id), KEY idx_structure_default_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS structure_symbol_period_configs (
                    user_id BIGINT NOT NULL, symbol VARCHAR(80) NOT NULL, period VARCHAR(16) NOT NULL,
                    version BIGINT NOT NULL DEFAULT 1, config_json JSON NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active', updated_by BIGINT NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL, PRIMARY KEY (user_id,symbol,period)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS structure_setup_configs (
                    user_id BIGINT NOT NULL, symbol VARCHAR(80) NOT NULL, period VARCHAR(16) NOT NULL,
                    setup_type VARCHAR(80) NOT NULL, version BIGINT NOT NULL DEFAULT 1, config_json JSON NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'active', updated_by BIGINT NOT NULL DEFAULT 0,
                    updated_at BIGINT NOT NULL, PRIMARY KEY (user_id,symbol,period,setup_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute("""
                CREATE TABLE IF NOT EXISTS structure_config_change_logs (
                    id BIGINT NOT NULL AUTO_INCREMENT, user_id BIGINT NOT NULL, scope VARCHAR(30) NOT NULL,
                    symbol VARCHAR(80) NOT NULL DEFAULT '', period VARCHAR(16) NOT NULL DEFAULT '',
                    setup_type VARCHAR(80) NOT NULL DEFAULT '', before_json JSON NULL, after_json JSON NOT NULL,
                    source VARCHAR(30) NOT NULL DEFAULT 'manual', reason VARCHAR(500) NOT NULL DEFAULT '',
                    changed_by BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL,
                    PRIMARY KEY (id), KEY idx_structure_change_user (user_id,created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS account_instrument_specs (
                    account_id BIGINT NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    min_volume DECIMAL(20,8) NOT NULL DEFAULT 0.01,
                    volume_step DECIMAL(20,8) NOT NULL DEFAULT 0.01,
                    max_volume DECIMAL(20,8) NOT NULL DEFAULT 100.0,
                    volume_digits INT NOT NULL DEFAULT 2,
                    contract_size DECIMAL(24,8) NOT NULL DEFAULT 1.0,
                    price_digits INT NOT NULL DEFAULT 0,
                    tick_size DECIMAL(24,10) NOT NULL DEFAULT 0,
                    point_size DECIMAL(24,10) NOT NULL DEFAULT 0,
                    tick_value DECIMAL(24,10) NOT NULL DEFAULT 0,
                    swap_long DECIMAL(24,10) NOT NULL DEFAULT 0,
                    swap_short DECIMAL(24,10) NOT NULL DEFAULT 0,
                    swap_mode INT NOT NULL DEFAULT 0,
                    swap_rollover3days INT NOT NULL DEFAULT 0,
                    stops_level INT NOT NULL DEFAULT 0,
                    freeze_level INT NOT NULL DEFAULT 0,
                    filling_mode INT NOT NULL DEFAULT 0,
                    trade_calc_mode INT NOT NULL DEFAULT 0,
                    currency_base VARCHAR(16) NOT NULL DEFAULT '',
                    currency_profit VARCHAR(16) NOT NULL DEFAULT '',
                    currency_margin VARCHAR(16) NOT NULL DEFAULT '',
                    source VARCHAR(32) NOT NULL DEFAULT 'default',
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (account_id, symbol),
                    KEY idx_account_instrument_specs_updated (updated_at)
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
                CREATE TABLE IF NOT EXISTS market_data_symbol_policies (
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    canonical_symbol VARCHAR(64) NOT NULL,
                    broker_name VARCHAR(120) NOT NULL,
                    mode VARCHAR(24) NOT NULL,
                    primary_account_id BIGINT NOT NULL DEFAULT 0,
                    conflict_symbols_json LONGTEXT NOT NULL,
                    message VARCHAR(512) NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (user_id, account_id, canonical_symbol),
                    KEY idx_market_symbol_policy_mode (user_id, canonical_symbol, mode),
                    KEY idx_market_symbol_policy_account (user_id, account_id)
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
                    plan_stage VARCHAR(32) NOT NULL DEFAULT 'default',
                    direction VARCHAR(16) NOT NULL DEFAULT 'none',
                    tick_id VARCHAR(64) NOT NULL DEFAULT '',
                    execution_mode VARCHAR(16) NOT NULL DEFAULT '',
                    status VARCHAR(24) NOT NULL,
                    order_id VARCHAR(64) NOT NULL DEFAULT '',
                    reason_code VARCHAR(64) NOT NULL DEFAULT '',
                    reason VARCHAR(512) NOT NULL DEFAULT '',
                    payload_json LONGTEXT NOT NULL,
                    gate_trace_json LONGTEXT NULL,
                    account_snapshot_json LONGTEXT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (execution_id),
                    UNIQUE KEY uq_structure_plan_deployment_stage_direction (
                        user_id, account_id, deployment_id, plan_id,
                        plan_stage, direction
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
                CREATE TABLE IF NOT EXISTS strategy_decision_cooldowns (
                    cooldown_key VARCHAR(512) NOT NULL,
                    user_id BIGINT NOT NULL DEFAULT 0,
                    account_id BIGINT NOT NULL DEFAULT 0,
                    deployment_id VARCHAR(64) NOT NULL DEFAULT '',
                    strategy_id VARCHAR(64) NOT NULL DEFAULT '',
                    plan_id VARCHAR(64) NOT NULL DEFAULT '',
                    plan_stage VARCHAR(32) NOT NULL DEFAULT '',
                    direction VARCHAR(16) NOT NULL DEFAULT '',
                    cooldown_until BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (cooldown_key),
                    KEY idx_strategy_cooldown_account (
                        user_id,account_id,cooldown_until
                    )
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS execution_gate_audits (
                    audit_id VARCHAR(64) NOT NULL,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    deployment_id VARCHAR(64) NOT NULL,
                    strategy_id VARCHAR(64) NOT NULL,
                    tick_id VARCHAR(64) NOT NULL,
                    execution_mode VARCHAR(16) NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    plan_id VARCHAR(64) NOT NULL DEFAULT '',
                    plan_stage VARCHAR(32) NOT NULL DEFAULT 'default',
                    direction VARCHAR(16) NOT NULL DEFAULT 'none',
                    status VARCHAR(24) NOT NULL,
                    reason_code VARCHAR(64) NOT NULL,
                    message VARCHAR(512) NOT NULL DEFAULT '',
                    gate_trace_json LONGTEXT NOT NULL,
                    account_snapshot_json LONGTEXT NOT NULL,
                    first_seen_at BIGINT NOT NULL,
                    last_seen_at BIGINT NOT NULL,
                    occurrence_count BIGINT NOT NULL DEFAULT 1,
                    last_tick_id VARCHAR(64) NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (audit_id),
                    KEY idx_execution_gate_plan (user_id,plan_id,updated_at),
                    KEY idx_execution_gate_tick (user_id,tick_id,updated_at),
                    KEY idx_execution_gate_deployment (
                        user_id,account_id,deployment_id,updated_at
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
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS key_level_signal_cooldowns (
                    cooldown_id VARCHAR(512) NOT NULL,
                    user_id BIGINT NOT NULL DEFAULT 0,
                    account_id BIGINT NOT NULL DEFAULT 0,
                    cooldown_until BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (cooldown_id),
                    KEY idx_key_level_cooldowns_lookup (
                        user_id, account_id, cooldown_until
                    )
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS account_flatten_runs (
                    run_id VARCHAR(64) NOT NULL,
                    account_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    business_date DATE NOT NULL,
                    scheduled_time VARCHAR(5) NOT NULL,
                    window_started_at BIGINT NOT NULL,
                    window_ended_at BIGINT NOT NULL,
                    status VARCHAR(24) NOT NULL,
                    position_count INT NOT NULL DEFAULT 0,
                    closed_count INT NOT NULL DEFAULT 0,
                    failed_count INT NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (run_id),
                    UNIQUE KEY uq_account_flatten_day (account_id, business_date),
                    KEY idx_account_flatten_status (status, window_started_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                  COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS account_flatten_items (
                    instruction_id VARCHAR(128) NOT NULL,
                    run_id VARCHAR(64) NOT NULL,
                    account_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    symbol VARCHAR(64) NOT NULL,
                    ticket BIGINT NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    requested_at BIGINT NOT NULL,
                    delivered_at BIGINT NULL,
                    reported_at BIGINT NULL,
                    mt5_order BIGINT NOT NULL DEFAULT 0,
                    mt5_deal BIGINT NOT NULL DEFAULT 0,
                    retcode BIGINT NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (instruction_id),
                    UNIQUE KEY uq_flatten_item_ticket (run_id, ticket),
                    KEY idx_flatten_item_delivery (account_id, status, symbol),
                    CONSTRAINT fk_flatten_item_run FOREIGN KEY (run_id)
                        REFERENCES account_flatten_runs(run_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                conn.execute(
                    """
                CREATE TABLE IF NOT EXISTS account_notification_deliveries (
                    id BIGINT NOT NULL AUTO_INCREMENT,
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    business_date DATE NOT NULL,
                    notification_type VARCHAR(32) NOT NULL,
                    reason_key VARCHAR(64) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    subject VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    error_message TEXT NULL,
                    sent_at BIGINT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_account_notification (account_id, business_date, notification_type, reason_key),
                    KEY idx_account_notification_user (user_id, business_date, notification_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
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
                        ("strategy_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                    ),
                    "live_trade_deals": (
                        ("position_attribution_json", "JSON NULL"),
                        ("deal_timestamp", "BIGINT NOT NULL DEFAULT 0"),
                        ("broker_utc_offset_seconds", "INT NOT NULL DEFAULT 0"),
                        ("strategy_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
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
                    "daily_pnl_statistics": (
                        ("win_count", "INT NOT NULL DEFAULT 0"),
                        ("loss_count", "INT NOT NULL DEFAULT 0"),
                        ("strategy_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                        ("strategy_name", "VARCHAR(160) NOT NULL DEFAULT ''"),
                        ("strategy_status", "VARCHAR(24) NOT NULL DEFAULT ''"),
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
                    "trading_accounts": (
                        ("auto_flatten_enabled", "TINYINT NOT NULL DEFAULT 0"),
                        ("auto_flatten_time", "VARCHAR(5) NULL"),
                        ("daily_risk_limit", "DOUBLE NOT NULL DEFAULT 0"),
                        ("single_position_loss_limit_enabled", "TINYINT NOT NULL DEFAULT 1"),
                        ("single_position_loss_limit_amount", "DOUBLE NOT NULL DEFAULT 30.0"),
                        ("manual_order_daily_limit_enabled", "TINYINT NOT NULL DEFAULT 1"),
                        ("manual_order_daily_limit", "INT NOT NULL DEFAULT 10"),
                        ("manual_losing_order_daily_limit", "INT NOT NULL DEFAULT 3"),
                        ("single_order_risk_limit", "DOUBLE NOT NULL DEFAULT 15"),
                        ("broker_trailing_stop_enabled", "TINYINT NOT NULL DEFAULT 0"),
                    ),
                    "account_instrument_specs": (
                        ("trade_sessions_json", "TEXT NULL"),
                        ("price_digits", "INT NOT NULL DEFAULT 0"),
                        ("tick_size", "DECIMAL(24,10) NOT NULL DEFAULT 0"),
                        ("point_size", "DECIMAL(24,10) NOT NULL DEFAULT 0"),
                        ("tick_value", "DECIMAL(24,10) NOT NULL DEFAULT 0"),
                        ("swap_long", "DECIMAL(24,10) NOT NULL DEFAULT 0"),
                        ("swap_short", "DECIMAL(24,10) NOT NULL DEFAULT 0"),
                        ("swap_mode", "INT NOT NULL DEFAULT 0"),
                        ("swap_rollover3days", "INT NOT NULL DEFAULT 0"),
                        ("stops_level", "INT NOT NULL DEFAULT 0"),
                        ("freeze_level", "INT NOT NULL DEFAULT 0"),
                        ("filling_mode", "INT NOT NULL DEFAULT 0"),
                        ("trade_calc_mode", "INT NOT NULL DEFAULT 0"),
                        ("currency_base", "VARCHAR(16) NOT NULL DEFAULT ''"),
                        ("currency_profit", "VARCHAR(16) NOT NULL DEFAULT ''"),
                        ("currency_margin", "VARCHAR(16) NOT NULL DEFAULT ''"),
                    ),
                    "structure_plan_executions": (
                        ("plan_stage", "VARCHAR(32) NOT NULL DEFAULT 'default'"),
                        ("direction", "VARCHAR(16) NOT NULL DEFAULT 'none'"),
                        ("tick_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                        ("execution_mode", "VARCHAR(16) NOT NULL DEFAULT ''"),
                        ("reason_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
                        ("gate_trace_json", "LONGTEXT NULL"),
                        ("account_snapshot_json", "LONGTEXT NULL"),
                    ),
                    "execution_gate_audits": (
                        ("first_seen_at", "BIGINT NOT NULL DEFAULT 0"),
                        ("last_seen_at", "BIGINT NOT NULL DEFAULT 0"),
                        ("occurrence_count", "BIGINT NOT NULL DEFAULT 1"),
                        ("last_tick_id", "VARCHAR(64) NOT NULL DEFAULT ''"),
                    ),
                    "users": (
                        ("is_frozen", "TINYINT NOT NULL DEFAULT 0"),
                        ("frozen_at", "BIGINT NULL"),
                        ("freeze_reason", "VARCHAR(255) NULL"),
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
                for table in ("live_trade_deals", "trade_execution_reports"):
                    conn.execute(
                        f"""
                        UPDATE {table}
                        SET strategy_id = JSON_UNQUOTE(JSON_EXTRACT(position_attribution_json, '$.strategy_id'))
                        WHERE (strategy_id IS NULL OR strategy_id = '')
                          AND JSON_EXTRACT(position_attribution_json, '$.strategy_id') IS NOT NULL
                          AND JSON_UNQUOTE(JSON_EXTRACT(position_attribution_json, '$.strategy_id')) <> ''
                        """
                    )
                try:
                    conn.execute("ALTER TABLE daily_pnl_statistics DROP INDEX uq_daily_pnl_bucket")
                    conn.execute("ALTER TABLE daily_pnl_statistics ADD UNIQUE KEY uq_daily_pnl_bucket (user_id,account_id,business_date,symbol,period,setup_type,strategy_id)")
                except Exception:
                    pass
                for index_sql in (
                    "ALTER TABLE live_trade_deals ADD KEY idx_live_trade_deals_strategy "
                    "(user_id, account_id, strategy_id, deal_timestamp)",
                    "ALTER TABLE trade_execution_reports ADD KEY idx_trade_execution_reports_strategy "
                    "(user_id, account_id, strategy_id, reported_at)",
                ):
                    try:
                        conn.execute(index_sql)
                    except Exception as exc:
                        if getattr(exc, "args", (None,))[0] != 1061:
                            raise
                conn.execute(
                    """
                    UPDATE execution_gate_audits
                    SET first_seen_at = CASE WHEN first_seen_at = 0 THEN created_at ELSE first_seen_at END,
                        last_seen_at = CASE WHEN last_seen_at = 0 THEN updated_at ELSE last_seen_at END,
                        last_tick_id = CASE WHEN last_tick_id = '' THEN tick_id ELSE last_tick_id END
                    WHERE first_seen_at = 0 OR last_seen_at = 0 OR last_tick_id = ''
                    """
                )
                try:
                    conn.execute(
                        "ALTER TABLE key_level_signal_cooldowns "
                        "MODIFY COLUMN cooldown_id VARCHAR(512) NOT NULL"
                    )
                except Exception as exc:
                    if getattr(exc, "args", (None,))[0] not in (1051, 1146):
                        raise
                # The original key consumed the whole plan after the first
                # stage. Replace it with the true execution identity so an
                # initial entry and its later breakout add-on can each execute
                # exactly once without allowing duplicate Tick consumption.
                try:
                    conn.execute(
                        "ALTER TABLE structure_plan_executions "
                        "DROP INDEX uq_structure_plan_deployment"
                    )
                except Exception as exc:
                    if getattr(exc, "args", (None,))[0] not in (1091, 1146):
                        raise
                try:
                    conn.execute(
                        "ALTER TABLE structure_plan_executions ADD UNIQUE INDEX "
                        "uq_structure_plan_deployment_stage_direction "
                        "(user_id,account_id,deployment_id,plan_id,plan_stage,direction)"
                    )
                except Exception as exc:
                    if getattr(exc, "args", (None,))[0] != 1061:
                        raise
                compatibility_indexes = (
                    (
                        "strategy_deployments",
                        "idx_strategy_deployments_expiry",
                        "user_id, status, scheduled_end_at",
                    ),
                    (
                        "strategy_deployments",
                        "idx_strategy_deployments_account_created",
                        "user_id, account_id, created_at",
                    ),
                    (
                        "strategy_deployments",
                        "idx_strategy_deployments_funnel_match",
                        # These columns are VARCHAR(255) on older production
                        # schemas.  A full utf8mb4 composite index exceeds
                        # MySQL's 3072-byte InnoDB key limit, so use bounded
                        # prefixes while retaining the lookup selectivity.
                        "user_id, account_id, symbol(64), strategy_id(64), status(32)",
                    ),
                    (
                        "structure_trade_plans",
                        "idx_structure_plans_funnel_day",
                        "user_id, account_id, created_at, plan_id, direction, symbol, strategy_id",
                    ),
                    (
                        "historical_klines",
                        "idx_historical_klines_utc",
                        "user_id, account_id, symbol, period, timestamp_utc",
                    ),
                    (
                        "historical_klines",
                        "idx_historical_klines_user_symbol_updated",
                        "user_id, symbol, updated_at",
                    ),
                    (
                        "live_trade_deals",
                        "idx_live_trade_deals_account_timestamp",
                        "account_id, deal_timestamp, received_at",
                    ),
                    # Paper runtime is opened by account and then narrowed by
                    # status/time.  These indexes keep the first screen and
                    # paginated refreshes bounded as history grows.
                    (
                        "paper_positions",
                        "idx_paper_positions_account_status_opened",
                        "account_id, status, opened_at",
                    ),
                    (
                        "paper_positions",
                        "idx_paper_positions_user_account_position_status",
                        # position_id is TEXT on older installations; use a
                        # bounded prefix so MySQL can create the index on
                        # both legacy and fresh schemas.
                        "user_id, account_id, position_id(128), status",
                    ),
                    (
                        "paper_orders",
                        "idx_paper_orders_account_requested",
                        "account_id, requested_at, order_id",
                    ),
                    (
                        "paper_orders",
                        "idx_paper_orders_user_status_requested",
                        "user_id, status, requested_at",
                    ),
                    (
                        "paper_trades",
                        "idx_paper_trades_account_closed",
                        "account_id, closed_at, trade_id",
                    ),
                    (
                        "paper_trades",
                        "idx_paper_trades_user_account_position_deployment",
                        "user_id, account_id, position_id(128), deployment_id(128), closed_at",
                    ),
                    (
                        "paper_trades",
                        "idx_paper_trades_user_closed",
                        "user_id, closed_at, trade_id",
                    ),
                    (
                        "live_trade_deals",
                        "idx_live_trade_deals_user_position",
                        "user_id, mt5_position_id, deal_timestamp",
                    ),
                    (
                        "structure_trade_plans",
                        "idx_structure_plans_status_expiry",
                        "status, expires_at, user_id, symbol, period",
                    ),
                    (
                        "platform_instrument_mappings",
                        "idx_platform_instrument_symbol_enabled",
                        "native_symbol, enabled, mapping_group",
                    ),
                    (
                        "mt5_account_connections",
                        "idx_mt5_connections_account_seen",
                        "account_id, last_seen_at",
                    ),
                    (
                        "position_management_events",
                        "idx_position_events_account_position_time",
                        "user_id, account_id, position_key, event_time, created_at",
                    ),
                    (
                        "runtime_entities",
                        "idx_runtime_entities_account_type_created",
                        "user_id, account_id, entity_type, created_at",
                    ),
                    (
                        "system_event_logs",
                        "idx_system_event_user_type_time",
                        "user_id, event_type(64), occurred_at",
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
        invalidate(domains_for_sql(sql))

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
        invalidate(domains_for_sql(sql))

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        self.initialize()
        domain = cache_domain_for_sql(sql)
        if domain:
            cached = sql_read_cache.get_sql(sql, params, domain)
            if cached is not None:
                return cached
        with self._connect() as conn:
            result = conn.execute(sql, params).fetchone()
        if domain:
            sql_read_cache.set_sql(sql, params, domain, result)
        return result

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.initialize()
        domain = cache_domain_for_sql(sql)
        if domain:
            cached = sql_read_cache.get_sql(sql, params, domain)
            if cached is not None:
                return cached
        with self._connect() as conn:
            result = list(conn.execute(sql, params).fetchall())
        if domain:
            sql_read_cache.set_sql(sql, params, domain, result)
        return result

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
