#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 存储层
"""

from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime
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
    email: Optional[str]
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
    account_type: str
    environment: str
    currency: str
    initial_balance: float
    balance: float
    equity: float
    free_margin: float
    margin: float
    status: str
    financial_updated_at: Optional[int]
    enabled: bool
    trading_enabled: bool
    auto_trading_enabled: bool
    max_total_positions: int
    max_single_volume: float
    daily_loss_limit: float
    daily_order_limit: int
    archived_at: Optional[int]
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
                        email TEXT UNIQUE,
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
                        system_prompt TEXT NOT NULL DEFAULT '',
                        analysis_prompt_template TEXT NOT NULL DEFAULT '',
                        prompt_version INTEGER NOT NULL DEFAULT 1,
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

                    CREATE TABLE IF NOT EXISTS user_quota_overrides (
                        user_id INTEGER PRIMARY KEY,
                        max_datasets INTEGER,
                        max_strategies INTEGER,
                        max_signal_sources INTEGER,
                        updated_by INTEGER,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE TABLE IF NOT EXISTS system_email_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        smtp_host TEXT NOT NULL DEFAULT 'smtp.qiye.aliyun.com',
                        smtp_port INTEGER NOT NULL DEFAULT 465,
                        use_ssl INTEGER NOT NULL DEFAULT 1,
                        sender_email TEXT NOT NULL DEFAULT '',
                        sender_name TEXT NOT NULL DEFAULT 'AI Trader',
                        encrypted_password TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 0,
                        updated_by INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE TABLE IF NOT EXISTS email_verification_codes (
                        email TEXT PRIMARY KEY,
                        code_hash TEXT NOT NULL,
                        code_salt TEXT NOT NULL,
                        expires_at INTEGER NOT NULL,
                        sent_at INTEGER NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_email_verification_expiry
                    ON email_verification_codes(expires_at);

                    CREATE TABLE IF NOT EXISTS email_verification_send_events (
                        requester_hash TEXT NOT NULL,
                        sent_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_email_send_events_requester
                    ON email_verification_send_events(requester_hash, sent_at);

                    CREATE TABLE IF NOT EXISTS shared_ai_runtime_data (
                        share_id TEXT PRIMARY KEY,
                        owner_user_id INTEGER NOT NULL,
                        strategy_id TEXT NOT NULL,
                        signal_source_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        period TEXT NOT NULL,
                        model TEXT NOT NULL,
                        signal_params_json TEXT NOT NULL DEFAULT '{}',
                        system_prompt TEXT NOT NULL DEFAULT '',
                        analysis_prompt_template TEXT NOT NULL DEFAULT '',
                        strategy_name TEXT NOT NULL DEFAULT '',
                        strategy_lifecycle TEXT NOT NULL DEFAULT 'draft',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        last_run_at INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
                        UNIQUE(owner_user_id, strategy_id, signal_source_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_shared_ai_runtime_lookup
                    ON shared_ai_runtime_data(symbol, period, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS position_management_policies (
                        policy_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        config_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_position_management_policies_user
                    ON position_management_policies(user_id, created_at, policy_id);

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
                        account_type TEXT NOT NULL DEFAULT 'mt5',
                        environment TEXT NOT NULL DEFAULT 'unknown',
                        currency TEXT NOT NULL DEFAULT 'USD',
                        initial_balance REAL NOT NULL DEFAULT 0,
                        balance REAL NOT NULL DEFAULT 0,
                        equity REAL NOT NULL DEFAULT 0,
                        free_margin REAL NOT NULL DEFAULT 0,
                        margin REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'active',
                        financial_updated_at INTEGER,
                        token_hash TEXT NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        trading_enabled INTEGER NOT NULL DEFAULT 1,
                        auto_trading_enabled INTEGER NOT NULL DEFAULT 1,
                        max_total_positions INTEGER NOT NULL DEFAULT 10,
                        max_single_volume REAL NOT NULL DEFAULT 10,
                        daily_loss_limit REAL NOT NULL DEFAULT 5,
                        daily_order_limit INTEGER NOT NULL DEFAULT 20,
                        archived_at INTEGER,
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

                    CREATE TABLE IF NOT EXISTS mt5_account_connections (
                        account_id INTEGER PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        last_seen_at INTEGER,
                        mt5_login TEXT,
                        mt5_server TEXT,
                        ea_version TEXT,
                        program_name TEXT NOT NULL DEFAULT '',
                        activated_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_mt5_connections_seen
                    ON mt5_account_connections(last_seen_at);

                    CREATE TABLE IF NOT EXISTS ea_activation_codes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        code_hash TEXT NOT NULL UNIQUE,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER,
                        expires_at INTEGER NOT NULL,
                        used_at INTEGER,
                        created_at INTEGER NOT NULL,
                        mt5_login TEXT,
                        mt5_server TEXT,
                        ea_version TEXT,
                        program_name TEXT,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE SET NULL
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

                    CREATE TABLE IF NOT EXISTS trade_execution_reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        instruction_id TEXT NOT NULL,
                        order_id TEXT NOT NULL DEFAULT '',
                        symbol TEXT NOT NULL,
                        action TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        requested_price REAL NOT NULL DEFAULT 0,
                        executed_price REAL NOT NULL DEFAULT 0,
                        requested_volume REAL NOT NULL DEFAULT 0,
                        executed_volume REAL NOT NULL DEFAULT 0,
                        slippage REAL NOT NULL DEFAULT 0,
                        mt5_order INTEGER NOT NULL DEFAULT 0,
                        mt5_deal INTEGER NOT NULL DEFAULT 0,
                        retcode INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT NOT NULL DEFAULT '',
                        reported_at INTEGER NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        UNIQUE(account_id, instruction_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_trade_execution_reports_account
                    ON trade_execution_reports(account_id, reported_at DESC);

                    CREATE TABLE IF NOT EXISTS backtest_datasets (
                        dataset_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        dataset_name TEXT NOT NULL,
                        visibility TEXT NOT NULL DEFAULT 'shared',
                        symbol TEXT NOT NULL,
                        timeframe TEXT NOT NULL DEFAULT 'M1',
                        requested_start INTEGER NOT NULL,
                        requested_end INTEGER NOT NULL,
                        warmup_start INTEGER NOT NULL,
                        cursor_time INTEGER NOT NULL,
                        next_chunk_index INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        received_bars INTEGER NOT NULL DEFAULT 0,
                        duplicate_count INTEGER NOT NULL DEFAULT 0,
                        gap_count INTEGER NOT NULL DEFAULT 0,
                        invalid_count INTEGER NOT NULL DEFAULT 0,
                        quality_score REAL NOT NULL DEFAULT 0,
                        broker_server TEXT NOT NULL DEFAULT '',
                        ea_version TEXT NOT NULL DEFAULT '',
                        data_format TEXT NOT NULL DEFAULT 'csv.gz',
                        file_path TEXT NOT NULL DEFAULT '',
                        data_hash TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        claimed_at INTEGER,
                        completed_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_datasets_owner
                    ON backtest_datasets(user_id, created_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_backtest_datasets_ea_task
                    ON backtest_datasets(account_id, symbol, status, created_at);

                    CREATE TABLE IF NOT EXISTS backtest_dataset_chunks (
                        dataset_id TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        range_start INTEGER NOT NULL,
                        range_end INTEGER NOT NULL,
                        first_bar_time INTEGER,
                        last_bar_time INTEGER,
                        bar_count INTEGER NOT NULL DEFAULT 0,
                        invalid_count INTEGER NOT NULL DEFAULT 0,
                        checksum TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY(dataset_id, chunk_index),
                        FOREIGN KEY(dataset_id) REFERENCES backtest_datasets(dataset_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS backtest_templates (
                        template_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        template_name TEXT NOT NULL,
                        visibility TEXT NOT NULL DEFAULT 'shared',
                        strategy_id TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        initial_capital REAL NOT NULL DEFAULT 100000,
                        position_sizing_mode TEXT NOT NULL DEFAULT 'strategy',
                        fixed_volume REAL NOT NULL DEFAULT 0.01,
                        risk_percent REAL NOT NULL DEFAULT 1,
                        spread_points REAL NOT NULL DEFAULT 0,
                        slippage_points REAL NOT NULL DEFAULT 0,
                        commission_per_lot REAL NOT NULL DEFAULT 0,
                        max_positions INTEGER NOT NULL DEFAULT 1,
                        max_same_direction INTEGER NOT NULL DEFAULT 1,
                        use_strategy_exits INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_templates_owner
                    ON backtest_templates(user_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS backtest_template_datasets (
                        template_id TEXT NOT NULL,
                        dataset_id TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY(template_id, dataset_id),
                        FOREIGN KEY(template_id) REFERENCES backtest_templates(template_id) ON DELETE CASCADE,
                        FOREIGN KEY(dataset_id) REFERENCES backtest_datasets(dataset_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS backtest_batches (
                        batch_id TEXT PRIMARY KEY,
                        template_id TEXT,
                        user_id INTEGER NOT NULL,
                        batch_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        task_count INTEGER NOT NULL DEFAULT 0,
                        completed_tasks INTEGER NOT NULL DEFAULT 0,
                        failed_tasks INTEGER NOT NULL DEFAULT 0,
                        canceled_tasks INTEGER NOT NULL DEFAULT 0,
                        strategy_id TEXT NOT NULL,
                        strategy_name TEXT NOT NULL,
                        strategy_snapshot_json TEXT NOT NULL,
                        strategy_snapshot_hash TEXT NOT NULL,
                        template_snapshot_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        started_at INTEGER,
                        completed_at INTEGER,
                        FOREIGN KEY(template_id) REFERENCES backtest_templates(template_id) ON DELETE SET NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_batches_owner
                    ON backtest_batches(user_id, created_at DESC);

                    CREATE TABLE IF NOT EXISTS backtest_tasks (
                        task_id TEXT PRIMARY KEY,
                        batch_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        dataset_id TEXT,
                        status TEXT NOT NULL DEFAULT 'queued',
                        progress REAL NOT NULL DEFAULT 0,
                        llm_analysis_count INTEGER NOT NULL DEFAULT 0,
                        llm_call_count INTEGER NOT NULL DEFAULT 0,
                        llm_cache_hits INTEGER NOT NULL DEFAULT 0,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        dataset_file_path TEXT NOT NULL,
                        dataset_snapshot_json TEXT NOT NULL,
                        result_json TEXT NOT NULL DEFAULT '{}',
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        started_at INTEGER,
                        completed_at INTEGER,
                        FOREIGN KEY(batch_id) REFERENCES backtest_batches(batch_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(dataset_id) REFERENCES backtest_datasets(dataset_id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_tasks_queue
                    ON backtest_tasks(status, created_at);

                    CREATE INDEX IF NOT EXISTS idx_backtest_tasks_batch
                    ON backtest_tasks(batch_id, created_at);

                    CREATE TABLE IF NOT EXISTS backtest_llm_cache (
                        cache_key TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        dataset_hash TEXT NOT NULL,
                        strategy_hash TEXT NOT NULL,
                        analysis_time INTEGER NOT NULL,
                        model TEXT NOT NULL,
                        prompt_hash TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_llm_cache_lookup
                    ON backtest_llm_cache(
                        user_id, dataset_hash, strategy_hash, analysis_time
                    );

                    CREATE TABLE IF NOT EXISTS backtest_ai_analyses (
                        task_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        model TEXT NOT NULL DEFAULT '',
                        prompt_hash TEXT NOT NULL DEFAULT '',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        completed_at INTEGER,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_ai_analyses_owner
                    ON backtest_ai_analyses(user_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS backtest_accounts (
                        task_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        initial_balance REAL NOT NULL,
                        balance REAL NOT NULL,
                        equity REAL NOT NULL,
                        free_margin REAL NOT NULL,
                        margin REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS backtest_orders (
                        order_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_volume REAL NOT NULL DEFAULT 0,
                        filled_volume REAL NOT NULL DEFAULT 0,
                        requested_price REAL NOT NULL DEFAULT 0,
                        filled_price REAL,
                        stop_loss REAL NOT NULL DEFAULT 0,
                        take_profit REAL NOT NULL DEFAULT 0,
                        signal_source TEXT NOT NULL DEFAULT '',
                        contributing_sources_json TEXT NOT NULL DEFAULT '[]',
                        confidence INTEGER NOT NULL DEFAULT 0,
                        rejection_reason TEXT NOT NULL DEFAULT '',
                        requested_at INTEGER NOT NULL,
                        filled_at INTEGER,
                        canceled_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_orders_task
                    ON backtest_orders(task_id, requested_at, order_id);

                    CREATE TABLE IF NOT EXISTS backtest_positions (
                        position_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        order_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        status TEXT NOT NULL,
                        volume REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        opened_at INTEGER NOT NULL,
                        closed_at INTEGER,
                        close_price REAL,
                        close_reason TEXT NOT NULL DEFAULT '',
                        net_profit REAL NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(order_id) REFERENCES backtest_orders(order_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_positions_task
                    ON backtest_positions(task_id, status, opened_at);

                    CREATE TABLE IF NOT EXISTS backtest_trades (
                        trade_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        order_id TEXT NOT NULL,
                        position_id TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        volume REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        gross_profit REAL NOT NULL,
                        commission REAL NOT NULL,
                        net_profit REAL NOT NULL,
                        exit_reason TEXT NOT NULL,
                        opened_at INTEGER NOT NULL,
                        closed_at INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(order_id) REFERENCES backtest_orders(order_id) ON DELETE CASCADE,
                        FOREIGN KEY(position_id) REFERENCES backtest_positions(position_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_trades_task
                    ON backtest_trades(task_id, closed_at, trade_id);

                    CREATE TABLE IF NOT EXISTS backtest_equity_points (
                        task_id TEXT NOT NULL,
                        point_time INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        balance REAL NOT NULL,
                        equity REAL NOT NULL,
                        open_positions INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(task_id, point_time),
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS backtest_replay_bars (
                        task_id TEXT NOT NULL,
                        bar_time INTEGER NOT NULL,
                        end_time INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        open REAL NOT NULL,
                        high REAL NOT NULL,
                        low REAL NOT NULL,
                        close REAL NOT NULL,
                        tick_volume INTEGER NOT NULL DEFAULT 0,
                        bar_count INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY(task_id, bar_time),
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS alpha_research_runs (
                        run_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        dataset_id TEXT,
                        research_name TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        progress REAL NOT NULL DEFAULT 0,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        config_json TEXT NOT NULL,
                        best_params_json TEXT NOT NULL DEFAULT '{}',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        started_at INTEGER,
                        completed_at INTEGER,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(dataset_id) REFERENCES backtest_datasets(dataset_id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_alpha_research_runs_owner
                    ON alpha_research_runs(user_id, created_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_alpha_research_runs_queue
                    ON alpha_research_runs(status, created_at);

                    CREATE TABLE IF NOT EXISTS alpha_research_trials (
                        run_id TEXT NOT NULL,
                        trial_number INTEGER NOT NULL,
                        iteration_number INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL,
                        score REAL,
                        params_json TEXT NOT NULL DEFAULT '{}',
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        duration_ms INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY(run_id, trial_number),
                        FOREIGN KEY(run_id) REFERENCES alpha_research_runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS alpha_research_iterations (
                        run_id TEXT NOT NULL,
                        iteration_number INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'running',
                        candidate_json TEXT NOT NULL DEFAULT '{}',
                        expression_text TEXT NOT NULL DEFAULT '',
                        best_params_json TEXT NOT NULL DEFAULT '{}',
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        feedback_prompt TEXT NOT NULL DEFAULT '',
                        feedback_response_json TEXT NOT NULL DEFAULT '{}',
                        llm_model TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        started_at INTEGER NOT NULL,
                        completed_at INTEGER,
                        PRIMARY KEY(run_id, iteration_number),
                        FOREIGN KEY(run_id) REFERENCES alpha_research_runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS alpha_library (
                        alpha_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        source_run_id TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'validated',
                        visibility TEXT NOT NULL DEFAULT 'private',
                        timeframe TEXT NOT NULL,
                        definition_json TEXT NOT NULL,
                        metrics_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(source_run_id) REFERENCES alpha_research_runs(run_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_alpha_library_visible
                    ON alpha_library(status, visibility, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS alpha_research_signals (
                        run_id TEXT NOT NULL,
                        bar_time INTEGER NOT NULL,
                        direction INTEGER NOT NULL,
                        alpha_value REAL NOT NULL,
                        close_price REAL NOT NULL,
                        PRIMARY KEY(run_id, bar_time),
                        FOREIGN KEY(run_id) REFERENCES alpha_research_runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS alpha_research_trades (
                        trade_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        entry_time INTEGER NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_time INTEGER NOT NULL,
                        exit_price REAL NOT NULL,
                        exit_reason TEXT NOT NULL,
                        gross_return REAL NOT NULL,
                        holding_bars INTEGER NOT NULL,
                        FOREIGN KEY(run_id) REFERENCES alpha_research_runs(run_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_alpha_research_trades_run
                    ON alpha_research_trades(run_id, entry_time);

                    CREATE TABLE IF NOT EXISTS paper_account_settings (
                        account_id INTEGER PRIMARY KEY,
                        leverage REAL NOT NULL DEFAULT 100,
                        spread_points REAL NOT NULL DEFAULT 0,
                        slippage_points REAL NOT NULL DEFAULT 0,
                        commission_per_lot REAL NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS strategy_deployments (
                        deployment_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        strategy_snapshot_hash TEXT NOT NULL DEFAULT '',
                        strategy_snapshot_json TEXT NOT NULL DEFAULT '{}',
                        source_backtest_task_id TEXT NOT NULL DEFAULT '',
                        strategy_version_at INTEGER NOT NULL DEFAULT 0,
                        scheduled_end_at INTEGER,
                        execution_mode TEXT NOT NULL DEFAULT 'paper',
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(account_id, strategy_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_strategy_deployments_lookup
                    ON strategy_deployments(user_id, symbol, status);

                    CREATE TABLE IF NOT EXISTS paper_orders (
                        order_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        deployment_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        decision_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        status TEXT NOT NULL,
                        requested_volume REAL NOT NULL,
                        filled_volume REAL NOT NULL DEFAULT 0,
                        requested_price REAL NOT NULL,
                        filled_price REAL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        confidence REAL NOT NULL DEFAULT 0,
                        rejection_reason TEXT NOT NULL DEFAULT '',
                        requested_at INTEGER NOT NULL,
                        filled_at INTEGER,
                        canceled_at INTEGER,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(account_id, decision_id),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                        FOREIGN KEY(deployment_id) REFERENCES strategy_deployments(deployment_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_paper_orders_account
                    ON paper_orders(account_id, status, requested_at DESC);

                    CREATE TABLE IF NOT EXISTS paper_positions (
                        position_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        order_id TEXT NOT NULL,
                        deployment_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open',
                        volume REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        open_commission REAL NOT NULL DEFAULT 0,
                        current_price REAL NOT NULL,
                        unrealized_profit REAL NOT NULL DEFAULT 0,
                        net_profit REAL NOT NULL DEFAULT 0,
                        opened_at INTEGER NOT NULL,
                        closed_at INTEGER,
                        close_price REAL,
                        close_reason TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                        FOREIGN KEY(order_id) REFERENCES paper_orders(order_id) ON DELETE CASCADE,
                        FOREIGN KEY(deployment_id) REFERENCES strategy_deployments(deployment_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_paper_positions_account
                    ON paper_positions(account_id, status, symbol);

                    CREATE TABLE IF NOT EXISTS paper_trades (
                        trade_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        order_id TEXT NOT NULL,
                        position_id TEXT NOT NULL,
                        deployment_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        volume REAL NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        gross_profit REAL NOT NULL,
                        commission REAL NOT NULL,
                        net_profit REAL NOT NULL,
                        exit_reason TEXT NOT NULL,
                        opened_at INTEGER NOT NULL,
                        closed_at INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                        FOREIGN KEY(order_id) REFERENCES paper_orders(order_id) ON DELETE CASCADE,
                        FOREIGN KEY(position_id) REFERENCES paper_positions(position_id) ON DELETE CASCADE,
                        FOREIGN KEY(deployment_id) REFERENCES strategy_deployments(deployment_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_paper_trades_account
                    ON paper_trades(account_id, closed_at DESC);

                    CREATE TABLE IF NOT EXISTS paper_equity_points (
                        account_id INTEGER NOT NULL,
                        point_time INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        balance REAL NOT NULL,
                        equity REAL NOT NULL,
                        free_margin REAL NOT NULL,
                        margin REAL NOT NULL,
                        open_positions INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(account_id, point_time),
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS paper_runtime_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        message TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_paper_runtime_logs_account
                    ON paper_runtime_logs(account_id, created_at DESC);

                    CREATE TABLE IF NOT EXISTS market_calendar_events (
                        event_date TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        event_time TEXT NOT NULL DEFAULT '',
                        importance INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(event_date, event_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_market_calendar_time
                    ON market_calendar_events(event_date, event_time);

                    CREATE TABLE IF NOT EXISTS market_key_events (
                        event_date TEXT NOT NULL,
                        event_id TEXT NOT NULL,
                        event_time TEXT NOT NULL DEFAULT '',
                        importance INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY(event_date, event_id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_market_key_event_time
                    ON market_key_events(event_date, event_time);

                    CREATE TABLE IF NOT EXISTS market_flash_news (
                        news_id TEXT PRIMARY KEY,
                        published_at TEXT NOT NULL DEFAULT '',
                        importance INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT '',
                        payload_json TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_market_flash_published
                    ON market_flash_news(published_at DESC, updated_at DESC);

                    """
                )
                self._ensure_column(
                    conn, "backtest_tasks", "engine_version", "TEXT NOT NULL DEFAULT ''"
                )
                self._ensure_column(
                    conn, "position_management_policies", "version",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "strategy_snapshot_hash",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "strategy_version_at",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "strategy_snapshot_json",
                    "TEXT NOT NULL DEFAULT '{}'",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "source_backtest_task_id",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "scheduled_end_at", "INTEGER",
                )
                self._ensure_column(
                    conn, "user_llm_configs", "system_prompt", "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "user_llm_configs", "analysis_prompt_template",
                    "TEXT NOT NULL DEFAULT ''",
                )
                for table in ("paper_orders", "paper_positions"):
                    self._ensure_column(
                        conn, table, "signal_source_id", "TEXT NOT NULL DEFAULT ''",
                    )
                    self._ensure_column(
                        conn, table, "exit_mode", "TEXT NOT NULL DEFAULT 'fixed_rr'",
                    )
                    self._ensure_column(
                        conn, table, "trailing_activation_r", "REAL NOT NULL DEFAULT 1",
                    )
                    self._ensure_column(
                        conn, table, "trailing_distance_r", "REAL NOT NULL DEFAULT 1",
                    )
                    self._ensure_column(
                        conn, table, "position_policy_snapshot_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    )
                self._ensure_column(
                    conn, "paper_positions", "initial_risk", "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "paper_positions", "favorable_price", "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "paper_positions", "holding_bars", "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "user_llm_configs", "prompt_version",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._ensure_column(
                    conn, "backtest_tasks", "worker_id", "TEXT NOT NULL DEFAULT ''"
                )
                self._ensure_column(conn, "backtest_tasks", "heartbeat_at", "INTEGER")
                self._ensure_column(
                    conn, "backtest_tasks", "llm_analysis_count",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "backtest_tasks", "llm_call_count",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "backtest_tasks", "llm_cache_hits",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "backtest_tasks", "cancel_requested",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "backtest_batches", "canceled_tasks",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(conn, "trading_accounts", "mt5_login", "TEXT")
                self._ensure_column(conn, "trading_accounts", "mt5_server", "TEXT")
                self._ensure_column(conn, "trading_accounts", "ea_version", "TEXT")
                self._ensure_column(conn, "trading_accounts", "activated_at", "INTEGER")
                self._ensure_column(
                    conn, "trading_accounts", "account_type",
                    "TEXT NOT NULL DEFAULT 'mt5'",
                )
                self._ensure_column(
                    conn, "trading_accounts", "environment",
                    "TEXT NOT NULL DEFAULT 'unknown'",
                )
                self._ensure_column(
                    conn, "trading_accounts", "currency",
                    "TEXT NOT NULL DEFAULT 'USD'",
                )
                self._ensure_column(
                    conn, "trading_accounts", "initial_balance",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "trading_accounts", "balance",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "trading_accounts", "equity",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "trading_accounts", "free_margin",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "trading_accounts", "margin",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "trading_accounts", "status",
                    "TEXT NOT NULL DEFAULT 'active'",
                )
                self._ensure_column(
                    conn, "trading_accounts", "financial_updated_at", "INTEGER"
                )
                self._ensure_column(
                    conn, "trading_accounts", "trading_enabled",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._ensure_column(
                    conn, "trading_accounts", "auto_trading_enabled",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._ensure_column(
                    conn, "trading_accounts", "max_total_positions",
                    "INTEGER NOT NULL DEFAULT 10",
                )
                self._ensure_column(
                    conn, "trading_accounts", "max_single_volume",
                    "REAL NOT NULL DEFAULT 10",
                )
                self._ensure_column(
                    conn, "trading_accounts", "daily_loss_limit",
                    "REAL NOT NULL DEFAULT 5",
                )
                self._ensure_column(
                    conn, "trading_accounts", "daily_order_limit",
                    "INTEGER NOT NULL DEFAULT 20",
                )
                self._ensure_column(
                    conn, "trading_accounts", "archived_at", "INTEGER"
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_trading_accounts_type
                    ON trading_accounts(user_id, account_type, status)
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO mt5_account_connections(
                        account_id, token_hash, last_seen_at, mt5_login,
                        mt5_server, ea_version, activated_at, created_at, updated_at
                    )
                    SELECT id, token_hash, last_seen_at, mt5_login,
                           mt5_server, ea_version, activated_at, created_at, updated_at
                    FROM trading_accounts
                    WHERE account_type = 'mt5'
                    """
                )
                conn.execute(
                    """
                    UPDATE trading_accounts
                    SET environment = 'demo'
                    WHERE account_type = 'mt5'
                      AND environment = 'unknown'
                      AND id IN (
                          SELECT account_id
                          FROM mt5_account_connections
                          WHERE LOWER(COALESCE(mt5_server, '')) LIKE '%demo%'
                      )
                    """
                )
                self._ensure_column(
                    conn,
                    "backtest_datasets",
                    "visibility",
                    "TEXT NOT NULL DEFAULT 'shared'",
                )
                self._ensure_column(
                    conn,
                    "alpha_research_trials",
                    "iteration_number",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_backtest_datasets_visibility
                    ON backtest_datasets(visibility, status, created_at DESC)
                    """
                )
                self._ensure_column(
                    conn,
                    "backtest_templates",
                    "visibility",
                    "TEXT NOT NULL DEFAULT 'shared'",
                )
                self._ensure_column(
                    conn,
                    "backtest_templates",
                    "max_same_direction",
                    "INTEGER",
                )
                conn.execute(
                    """
                    UPDATE backtest_templates
                    SET max_same_direction = max_positions
                    WHERE max_same_direction IS NULL
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_backtest_templates_visibility
                    ON backtest_templates(visibility, updated_at DESC)
                    """
                )
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
                self._ensure_column(conn, "users", "email", "TEXT")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
                    "ON users(email) WHERE email IS NOT NULL"
                )
                self._migrate_ea_activation_codes(conn)
                self._migrate_strategy_configs(conn)
                self._remove_pivot_signal_strategies(conn)
                migration_key = "account_strategy_bindings_v1"
                migration = conn.execute(
                    "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
                ).fetchone()
                if migration is None:
                    # 升级前 MT5 默认执行用户的全部生产策略；仅首次迁移建立显式绑定。
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO strategy_deployments(
                            deployment_id, user_id, account_id, strategy_id, symbol,
                            execution_mode, status, created_at, updated_at
                        )
                        SELECT 'legacy-live-' || a.id || '-' || s.strategy_id,
                               a.user_id, a.id, s.strategy_id, s.symbol,
                               'live', 'active', CAST(strftime('%s','now') AS INTEGER),
                               CAST(strftime('%s','now') AS INTEGER)
                        FROM trading_accounts a
                        JOIN user_strategy_configs s ON s.user_id = a.user_id
                        WHERE a.account_type = 'mt5'
                          AND COALESCE(json_extract(s.config_json, '$.lifecycle_status'), 'production') = 'production'
                          AND COALESCE(json_extract(s.config_json, '$.enabled'), 1) = 1
                        """
                    )
                    conn.execute(
                        "INSERT INTO app_meta(key, value) VALUES(?, ?)",
                        (migration_key, str(_now_ts())),
                    )
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
    def _remove_pivot_signal_strategies(conn: sqlite3.Connection) -> None:
        """删除以转折点为信号源的历史策略及其回测数据。"""
        migration_key = "remove_pivot_signal_strategies_v1"
        if conn.execute(
            "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
        ).fetchone() is not None:
            return

        conn.execute(
            """
            CREATE TEMP TABLE pivot_strategy_removal (
                user_id INTEGER NOT NULL,
                strategy_id TEXT NOT NULL,
                PRIMARY KEY(user_id, strategy_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO pivot_strategy_removal(user_id, strategy_id)
            SELECT user_id, strategy_id
            FROM user_strategy_configs
            WHERE json_valid(config_json)
              AND EXISTS (
                  SELECT 1
                  FROM json_each(config_json, '$.signal_sources')
                  WHERE json_extract(value, '$.source') = 'pivot'
              )
            """
        )
        strategy_count = conn.execute(
            "SELECT COUNT(*) FROM pivot_strategy_removal"
        ).fetchone()[0]
        template_count = conn.execute(
            """
            SELECT COUNT(*) FROM backtest_templates t
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = t.user_id AND p.strategy_id = t.strategy_id
            )
            """
        ).fetchone()[0]
        batch_count = conn.execute(
            """
            SELECT COUNT(*) FROM backtest_batches b
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = b.user_id AND p.strategy_id = b.strategy_id
            )
            """
        ).fetchone()[0]
        task_count = conn.execute(
            """
            SELECT COUNT(*) FROM backtest_tasks t
            JOIN backtest_batches b ON b.batch_id = t.batch_id
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = b.user_id AND p.strategy_id = b.strategy_id
            )
            """
        ).fetchone()[0]

        conn.execute(
            """
            DELETE FROM backtest_batches
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = backtest_batches.user_id
                  AND p.strategy_id = backtest_batches.strategy_id
            )
            """
        )
        conn.execute(
            """
            DELETE FROM backtest_templates
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = backtest_templates.user_id
                  AND p.strategy_id = backtest_templates.strategy_id
            )
            """
        )
        conn.execute(
            """
            DELETE FROM strategy_deployments
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = strategy_deployments.user_id
                  AND p.strategy_id = strategy_deployments.strategy_id
            )
            """
        )
        conn.execute(
            """
            DELETE FROM user_strategy_configs
            WHERE EXISTS (
                SELECT 1 FROM pivot_strategy_removal p
                WHERE p.user_id = user_strategy_configs.user_id
                  AND p.strategy_id = user_strategy_configs.strategy_id
            )
            """
        )
        conn.execute("DROP TABLE pivot_strategy_removal")
        summary = json.dumps({
            "strategies": strategy_count,
            "templates": template_count,
            "batches": batch_count,
            "tasks": task_count,
            "removed_at": _now_ts(),
        }, ensure_ascii=False)
        conn.execute(
            "INSERT INTO app_meta(key, value) VALUES(?, ?)",
            (migration_key, summary),
        )

    @staticmethod
    def _migrate_ea_activation_codes(conn: sqlite3.Connection) -> None:
        """允许下载激活码时尚未发现具体 MT5 账户。"""
        columns = {
            row["name"]: row for row in conn.execute(
                "PRAGMA table_info(ea_activation_codes)"
            )
        }
        account_column = columns.get("account_id")
        if account_column is None or not int(account_column["notnull"]):
            return

        conn.execute("ALTER TABLE ea_activation_codes RENAME TO ea_activation_codes_legacy")
        conn.execute("DROP INDEX IF EXISTS idx_ea_activation_codes_account")
        conn.executescript(
            """
            CREATE TABLE ea_activation_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                account_id INTEGER,
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                created_at INTEGER NOT NULL,
                mt5_login TEXT,
                mt5_server TEXT,
                ea_version TEXT,
                program_name TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE SET NULL
            );

            INSERT INTO ea_activation_codes(
                id, code_hash, user_id, account_id, expires_at, used_at,
                created_at, mt5_login, mt5_server, ea_version, program_name
            )
            SELECT id, code_hash, user_id, account_id, expires_at, used_at,
                   created_at, mt5_login, mt5_server, ea_version, program_name
            FROM ea_activation_codes_legacy;

            DROP TABLE ea_activation_codes_legacy;

            CREATE INDEX idx_ea_activation_codes_account
            ON ea_activation_codes(account_id, expires_at);
            """
        )

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
            SELECT id, username, email, password_hash, salt, role, token_version,
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
            SELECT id, username, email, password_hash, salt, role, token_version,
                   created_at, updated_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        )
        return self._row_to_user(row)

    def get_by_email(self, email: str) -> Optional[UserRecord]:
        row = self.storage.fetchone(
            """
            SELECT id, username, email, password_hash, salt, role, token_version,
                   created_at, updated_at
            FROM users
            WHERE email = ?
            """,
            (email,),
        )
        return self._row_to_user(row)

    def create_user(
        self,
        username: str,
        password_hash: str,
        salt: str,
        role: str = "user",
        email: Optional[str] = None,
    ) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO users(
                username, email, password_hash, salt, role, token_version,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (username, email, password_hash, salt, role, now, now),
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

    def list_users(self) -> List[UserRecord]:
        rows = self.storage.fetchall(
            """
            SELECT id, username, email, password_hash, salt, role, token_version,
                   created_at, updated_at
            FROM users
            ORDER BY created_at, id
            """
        )
        return [user for row in rows if (user := self._row_to_user(row)) is not None]

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
            email=row["email"],
            password_hash=row["password_hash"],
            salt=row["salt"],
            role=row["role"],
            token_version=int(row["token_version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class TradingAccountRepository:
    """统一交易账户仓库；MT5 连接信息由独立连接表维护。"""

    DEFAULT_ACCOUNT_KEY = "default"
    ACCOUNT_TYPES = {"mt5", "paper", "backtest"}
    ACCOUNT_SELECT = """
        SELECT a.id, a.user_id, a.account_key, a.account_name,
               a.account_type, a.environment, a.currency,
               a.initial_balance, a.balance, a.equity, a.free_margin,
               a.margin, a.status, a.financial_updated_at, a.enabled,
               a.trading_enabled, a.auto_trading_enabled,
               a.max_total_positions, a.max_single_volume,
               a.daily_loss_limit, a.daily_order_limit, a.archived_at,
               COALESCE(c.last_seen_at, a.last_seen_at) AS last_seen_at,
               COALESCE(c.mt5_login, a.mt5_login) AS mt5_login,
               COALESCE(c.mt5_server, a.mt5_server) AS mt5_server,
               COALESCE(c.ea_version, a.ea_version) AS ea_version,
               COALESCE(c.activated_at, a.activated_at) AS activated_at,
               a.created_at, a.updated_at
        FROM trading_accounts a
        LEFT JOIN mt5_account_connections c ON c.account_id = a.id
    """

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get_default(self, user_id: int) -> Optional[TradingAccountRecord]:
        row = self.storage.fetchone(
            self.ACCOUNT_SELECT + " WHERE a.user_id = ? AND a.account_key = ?",
            (user_id, self.DEFAULT_ACCOUNT_KEY),
        )
        return self._row_to_account(row)

    def get_primary_mt5(self, user_id: int) -> Optional[TradingAccountRecord]:
        """返回最近在线的已发现 MT5 账户，兼容历史默认账户。"""
        row = self.storage.fetchone(
            self.ACCOUNT_SELECT
            + """
              WHERE a.user_id = ? AND a.account_type = 'mt5'
                AND a.status = 'active'
                AND COALESCE(c.activated_at, a.activated_at) IS NOT NULL
              ORDER BY COALESCE(c.last_seen_at, a.last_seen_at, 0) DESC,
                       CASE WHEN a.account_key = ? THEN 0 ELSE 1 END,
                       a.created_at
              LIMIT 1
              """,
            (user_id, self.DEFAULT_ACCOUNT_KEY),
        )
        return self._row_to_account(row)

    def get_by_id(
        self, user_id: int, account_id: int
    ) -> Optional[TradingAccountRecord]:
        row = self.storage.fetchone(
            self.ACCOUNT_SELECT + " WHERE a.user_id = ? AND a.id = ?",
            (user_id, account_id),
        )
        return self._row_to_account(row)

    def list_for_user(
        self, user_id: int, include_backtest: bool = False
    ) -> List[TradingAccountRecord]:
        sql = self.ACCOUNT_SELECT + " WHERE a.user_id = ?"
        params: tuple = (user_id,)
        if not include_backtest:
            sql += " AND a.account_type != 'backtest'"
        sql += (
            " AND (a.account_type != 'mt5' "
            "OR COALESCE(c.activated_at, a.activated_at) IS NOT NULL)"
        )
        sql += " ORDER BY CASE a.account_type WHEN 'mt5' THEN 0 WHEN 'paper' THEN 1 ELSE 2 END, a.created_at"
        return [self._row_to_account(row) for row in self.storage.fetchall(sql, params)]

    def update_controls(
        self,
        user_id: int,
        account_id: int,
        *,
        account_name: Optional[str] = None,
        trading_enabled: Optional[bool] = None,
        auto_trading_enabled: Optional[bool] = None,
        max_total_positions: Optional[int] = None,
        max_single_volume: Optional[float] = None,
        daily_loss_limit: Optional[float] = None,
        daily_order_limit: Optional[int] = None,
    ) -> TradingAccountRecord:
        account = self.get_by_id(user_id, account_id)
        if account is None:
            raise ValueError("交易账户不存在")
        values = {
            "account_name": account.account_name,
            "trading_enabled": account.trading_enabled,
            "auto_trading_enabled": account.auto_trading_enabled,
            "max_total_positions": account.max_total_positions,
            "max_single_volume": account.max_single_volume,
            "daily_loss_limit": account.daily_loss_limit,
            "daily_order_limit": account.daily_order_limit,
        }
        if account_name is not None:
            name = str(account_name).strip()
            if not name:
                raise ValueError("账户备注名不能为空")
            values["account_name"] = name[:100]
        if trading_enabled is not None:
            values["trading_enabled"] = bool(trading_enabled)
        if auto_trading_enabled is not None:
            values["auto_trading_enabled"] = bool(auto_trading_enabled)
        if max_total_positions is not None:
            values["max_total_positions"] = int(max_total_positions)
        if max_single_volume is not None:
            values["max_single_volume"] = float(max_single_volume)
        if daily_loss_limit is not None:
            values["daily_loss_limit"] = float(daily_loss_limit)
        if daily_order_limit is not None:
            values["daily_order_limit"] = int(daily_order_limit)
        if not 1 <= values["max_total_positions"] <= 100:
            raise ValueError("最大持仓数必须在 1 到 100 之间")
        if not 0.01 <= values["max_single_volume"] <= 1000:
            raise ValueError("单笔最大手数必须在 0.01 到 1000 之间")
        if not 0.1 <= values["daily_loss_limit"] <= 100:
            raise ValueError("每日最大亏损必须在 0.1% 到 100% 之间")
        if not 1 <= values["daily_order_limit"] <= 10000:
            raise ValueError("每日订单上限必须在 1 到 10000 之间")
        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                UPDATE trading_accounts
                SET account_name = ?, trading_enabled = ?,
                    auto_trading_enabled = ?, max_total_positions = ?,
                    max_single_volume = ?, daily_loss_limit = ?,
                    daily_order_limit = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    values["account_name"], int(values["trading_enabled"]),
                    int(values["auto_trading_enabled"]),
                    values["max_total_positions"], values["max_single_volume"],
                    values["daily_loss_limit"], values["daily_order_limit"],
                    now, account_id, user_id,
                ),
            )
            if not values["trading_enabled"]:
                conn.execute(
                    """
                    UPDATE paper_orders
                    SET status = 'canceled', canceled_at = ?, updated_at = ?,
                        rejection_reason = '账户交易已暂停'
                    WHERE account_id = ? AND status = 'pending'
                    """,
                    (now, now, account_id),
                )
            conn.commit()
        return self.get_by_id(user_id, account_id)

    def set_archived(
        self, user_id: int, account_id: int, archived: bool
    ) -> TradingAccountRecord:
        account = self.get_by_id(user_id, account_id)
        if account is None:
            raise ValueError("交易账户不存在")
        now = _now_ts()
        if archived:
            if account.account_type == "mt5" and account.last_seen_at:
                if now - account.last_seen_at <= 120:
                    raise ValueError("MT5 终端在线时不能归档，请先移除或停止 EA")
            open_items = self.storage.fetchone(
                """
                SELECT (
                    SELECT COUNT(*) FROM paper_positions
                    WHERE account_id = ? AND status = 'open'
                ) + (
                    SELECT COUNT(*) FROM paper_orders
                    WHERE account_id = ? AND status = 'pending'
                ) AS count
                """,
                (account_id, account_id),
            )
            if open_items and int(open_items["count"]):
                raise ValueError("账户仍有模拟持仓或待成交订单，不能归档")
        status = "archived" if archived else "active"
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                UPDATE trading_accounts
                SET status = ?, trading_enabled = ?, archived_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, 0 if archived else 1, now if archived else None, now,
                 account_id, user_id),
            )
            if archived:
                conn.execute(
                    """
                    UPDATE strategy_deployments SET status = 'paused', updated_at = ?
                    WHERE account_id = ? AND status = 'active'
                    """,
                    (now, account_id),
                )
            conn.commit()
        return self.get_by_id(user_id, account_id)

    def ensure_default(
        self,
        user_id: int,
        account_name: str = "MT5",
    ) -> TradingAccountRecord:
        """创建默认 MT5 账户，但不轮换已在使用的 EA 凭证。"""
        self.storage.initialize()
        now = _now_ts()
        placeholder_hash = self._hash_token(secrets.token_urlsafe(32))
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO trading_accounts(
                    user_id, account_key, account_name, account_type,
                    environment, currency, token_hash, enabled, status,
                    created_at, updated_at
                ) VALUES(?, ?, ?, 'mt5', 'unknown', 'USD', ?, 1, 'active', ?, ?)
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
            row = conn.execute(
                "SELECT id, token_hash FROM trading_accounts WHERE user_id = ? AND account_key = ?",
                (user_id, self.DEFAULT_ACCOUNT_KEY),
            ).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO mt5_account_connections(
                    account_id, token_hash, created_at, updated_at
                ) VALUES(?, ?, ?, ?)
                """,
                (int(row["id"]), row["token_hash"], now, now),
            )
            conn.commit()
        account = self.get_default(user_id)
        if account is None:
            raise RuntimeError("创建 MT5 账户绑定失败")
        return account

    def create_or_rotate_default(
        self,
        user_id: int,
        account_name: str = "MT5",
    ) -> tuple[TradingAccountRecord, str]:
        account = self.ensure_default(user_id, account_name)
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                UPDATE trading_accounts
                SET account_name = ?, token_hash = ?, enabled = 1,
                    status = 'active', updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    account_name.strip() or "MT5",
                    token_hash,
                    now,
                    account.account_id,
                    user_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO mt5_account_connections(
                    account_id, token_hash, created_at, updated_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    updated_at = excluded.updated_at
                """,
                (account.account_id, token_hash, now, now),
            )
            conn.commit()
        refreshed = self.get_default(user_id)
        if refreshed is None:
            raise RuntimeError("创建 MT5 账户绑定失败")
        return refreshed, token

    def create_paper_account(
        self,
        user_id: int,
        account_name: str,
        initial_balance: float = 100000,
        currency: str = "USD",
        leverage: float = 100,
        spread_points: float = 0,
        slippage_points: float = 0,
        commission_per_lot: float = 0,
    ) -> TradingAccountRecord:
        name = str(account_name or "").strip()
        if not name:
            raise ValueError("请输入模拟账户名称")
        balance = float(initial_balance)
        if not math.isfinite(balance) or balance <= 0:
            raise ValueError("初始资金必须大于 0")
        normalized_currency = str(currency or "USD").strip().upper()
        if not normalized_currency or len(normalized_currency) > 8:
            raise ValueError("账户币种无效")
        settings = tuple(float(value) for value in (
            leverage, spread_points, slippage_points, commission_per_lot
        ))
        if not all(math.isfinite(value) for value in settings):
            raise ValueError("模拟撮合参数无效")
        if settings[0] <= 0 or any(value < 0 for value in settings[1:]):
            raise ValueError("杠杆必须大于 0，交易成本不能为负数")
        now = _now_ts()
        account_key = f"paper-{str(uuid.uuid4())[:12]}"
        placeholder_hash = self._hash_token(secrets.token_urlsafe(32))
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.execute(
                """
                INSERT INTO trading_accounts(
                    user_id, account_key, account_name, account_type,
                    environment, currency, initial_balance, balance, equity,
                    free_margin, margin, status, token_hash, enabled,
                    financial_updated_at, created_at, updated_at
                ) VALUES(?, ?, ?, 'paper', 'simulated', ?, ?, ?, ?, ?, 0,
                         'active', ?, 1, ?, ?, ?)
                """,
                (
                    user_id, account_key, name[:100], normalized_currency,
                    balance, balance, balance, balance, placeholder_hash,
                    now, now, now,
                ),
            )
            account_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO paper_account_settings(
                    account_id, leverage, spread_points, slippage_points,
                    commission_per_lot, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (account_id, *settings, now, now),
            )
            conn.commit()
        return self.get_by_id(user_id, account_id)

    def authenticate(self, user_id: int, token: str) -> Optional[TradingAccountRecord]:
        token_hash = self._hash_token(token)
        row = self.storage.fetchone(
            """
            SELECT a.id, c.token_hash
            FROM trading_accounts a
            JOIN mt5_account_connections c ON c.account_id = a.id
            WHERE a.user_id = ? AND c.token_hash = ?
              AND a.account_type = 'mt5' AND a.enabled = 1
              AND a.status = 'active'
            """,
            (user_id, token_hash),
        )
        if row is None or not hmac.compare_digest(row["token_hash"], token_hash):
            return None

        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                "UPDATE mt5_account_connections SET last_seen_at = ?, updated_at = ? WHERE account_id = ?",
                (now, now, int(row["id"])),
            )
            # 兼容仍读取旧字段的旧版本服务。
            conn.execute(
                "UPDATE trading_accounts SET last_seen_at = ? WHERE id = ?",
                (now, int(row["id"])),
            )
            conn.commit()
        return self.get_by_id(user_id, int(row["id"]))

    def update_financial_snapshot(
        self,
        account_id: int,
        *,
        balance: float,
        equity: float,
        free_margin: float,
        margin: float = 0,
    ) -> bool:
        values = tuple(float(value) for value in (
            balance, equity, free_margin, margin
        ))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("账户资金数据无效")
        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE trading_accounts
                SET initial_balance = CASE WHEN initial_balance = 0 THEN ? ELSE initial_balance END,
                    balance = ?, equity = ?, free_margin = ?, margin = ?,
                    financial_updated_at = ?, updated_at = ?
                WHERE id = ? AND account_type = 'mt5'
                """,
                (values[0], *values, now, now, account_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    @staticmethod
    def infer_mt5_environment(server: str) -> str:
        return "demo" if "demo" in str(server or "").lower() else "unknown"

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
            account_type=row["account_type"],
            environment=row["environment"],
            currency=row["currency"],
            initial_balance=float(row["initial_balance"]),
            balance=float(row["balance"]),
            equity=float(row["equity"]),
            free_margin=float(row["free_margin"]),
            margin=float(row["margin"]),
            status=row["status"],
            financial_updated_at=(
                int(row["financial_updated_at"])
                if row["financial_updated_at"] is not None else None
            ),
            enabled=bool(row["enabled"]),
            trading_enabled=bool(row["trading_enabled"]),
            auto_trading_enabled=bool(row["auto_trading_enabled"]),
            max_total_positions=int(row["max_total_positions"]),
            max_single_volume=float(row["max_single_volume"]),
            daily_loss_limit=float(row["daily_loss_limit"]),
            daily_order_limit=int(row["daily_order_limit"]),
            archived_at=(
                int(row["archived_at"])
                if row["archived_at"] is not None else None
            ),
            last_seen_at=(
                int(row["last_seen_at"])
                if row["last_seen_at"] is not None else None
            ),
            mt5_login=row["mt5_login"],
            mt5_server=row["mt5_server"],
            ea_version=row["ea_version"],
            activated_at=(
                int(row["activated_at"])
                if row["activated_at"] is not None else None
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

    def create(
        self, user_id: int, ttl_seconds: int = 10 * 60,
    ) -> tuple[str, int]:
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
                        WHERE user_id = ? AND used_at IS NULL
                        """,
                        (now, user_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO ea_activation_codes(
                            code_hash, user_id, expires_at, created_at
                        )
                        VALUES(?, ?, ?, ?)
                        """,
                        (
                            code_hash,
                            user_id,
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
        login = str(mt5_login or "").strip()
        server = str(mt5_server or "").strip()
        if len(normalized_code) != self.CODE_LENGTH or not login or not server:
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

            identity_key = hashlib.sha256(
                f"{server.casefold()}\0{login}".encode("utf-8")
            ).hexdigest()[:24]
            account_row = conn.execute(
                """
                SELECT a.id
                FROM trading_accounts a
                LEFT JOIN mt5_account_connections c ON c.account_id = a.id
                WHERE a.user_id = ? AND a.account_type = 'mt5'
                  AND trim(COALESCE(c.mt5_login, a.mt5_login, '')) = ?
                  AND lower(trim(COALESCE(c.mt5_server, a.mt5_server, ''))) = lower(?)
                ORDER BY COALESCE(c.activated_at, a.activated_at, 0) DESC, a.id
                LIMIT 1
                """,
                (int(row["user_id"]), login, server),
            ).fetchone()
            if account_row is None and row["account_id"] is not None:
                # 兼容升级前生成的账户级激活码，保留原账户及其策略绑定。
                account_row = conn.execute(
                    """
                    SELECT id FROM trading_accounts
                    WHERE id = ? AND user_id = ? AND account_type = 'mt5'
                      AND activated_at IS NULL
                    """,
                    (int(row["account_id"]), int(row["user_id"])),
                ).fetchone()
            if account_row is None:
                # 老用户可能已有尚未识别身份的默认槽位，首次上报时直接接管。
                account_row = conn.execute(
                    """
                    SELECT a.id
                    FROM trading_accounts a
                    LEFT JOIN mt5_account_connections c ON c.account_id = a.id
                    WHERE a.user_id = ? AND a.account_type = 'mt5'
                      AND a.account_key = ?
                      AND COALESCE(c.activated_at, a.activated_at) IS NULL
                    LIMIT 1
                    """,
                    (int(row["user_id"]), TradingAccountRepository.DEFAULT_ACCOUNT_KEY),
                ).fetchone()
            if account_row is None:
                placeholder_hash = TradingAccountRepository._hash_token(
                    secrets.token_urlsafe(32)
                )
                conn.execute(
                    """
                    INSERT INTO trading_accounts(
                        user_id, account_key, account_name, account_type,
                        environment, currency, token_hash, enabled, status,
                        created_at, updated_at
                    ) VALUES(?, ?, ?, 'mt5', ?, 'USD', ?, 1, 'active', ?, ?)
                    ON CONFLICT(user_id, account_key) DO NOTHING
                    """,
                    (
                        int(row["user_id"]), f"mt5-{identity_key}",
                        f"MT5 {login}",
                        TradingAccountRepository.infer_mt5_environment(server),
                        placeholder_hash, now, now,
                    ),
                )
                account_row = conn.execute(
                    "SELECT id FROM trading_accounts WHERE user_id = ? AND account_key = ?",
                    (int(row["user_id"]), f"mt5-{identity_key}"),
                ).fetchone()
            account_id = int(account_row["id"])

            cursor = conn.execute(
                """
                UPDATE ea_activation_codes
                SET used_at = ?, account_id = ?, mt5_login = ?, mt5_server = ?,
                    ea_version = ?, program_name = ?
                WHERE id = ? AND used_at IS NULL
                """,
                (
                    now,
                    account_id,
                    login,
                    server,
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
                    activated_at = ?, environment = ?, status = 'active',
                    archived_at = NULL,
                    updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    token_hash,
                    now,
                    login,
                    server,
                    ea_version.strip(),
                    now,
                    TradingAccountRepository.infer_mt5_environment(mt5_server),
                    now,
                    account_id,
                    int(row["user_id"]),
                ),
            )
            conn.execute(
                """
                INSERT INTO mt5_account_connections(
                    account_id, token_hash, last_seen_at, mt5_login,
                    mt5_server, ea_version, program_name, activated_at,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    token_hash = excluded.token_hash,
                    last_seen_at = excluded.last_seen_at,
                    mt5_login = excluded.mt5_login,
                    mt5_server = excluded.mt5_server,
                    ea_version = excluded.ea_version,
                    program_name = excluded.program_name,
                    activated_at = excluded.activated_at,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    token_hash,
                    now,
                    login,
                    server,
                    ea_version.strip(),
                    program_name.strip(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()

        account = self.accounts.get_by_id(
            int(row["user_id"]), account_id
        )
        if account is None:
            raise RuntimeError("EA 激活后未找到绑定账户")
        return account, token

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()


class StrategyDeploymentRepository:
    """查询账户级策略部署关系。"""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def list_active_strategy_ids(
        self, user_id: int, account_id: int, execution_mode: str
    ) -> List[str]:
        rows = self.storage.fetchall(
            """
            SELECT strategy_id FROM strategy_deployments
            WHERE user_id = ? AND account_id = ? AND execution_mode = ?
              AND status = 'active'
            ORDER BY created_at, strategy_id
            """,
            (user_id, account_id, execution_mode),
        )
        return [str(row["strategy_id"]) for row in rows]


class TradeExecutionRepository:
    """MT5 对服务端交易指令的即时执行回报。"""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def record(self, user_id: int, account_id: int, payload: Dict) -> Dict:
        instruction_id = str(payload.get("instruction_id", "")).strip()
        if not instruction_id:
            raise ValueError("执行回报缺少 instruction_id")
        action = str(payload.get("action", "")).strip().lower()
        requested_price = float(payload.get("requested_price", 0) or 0)
        executed_price = float(payload.get("executed_price", 0) or 0)
        raw_slippage = executed_price - requested_price
        slippage = raw_slippage if action in {"b", "buy"} else -raw_slippage
        now = _now_ts()
        values = (
            user_id, account_id, instruction_id,
            str(payload.get("order_id", "") or ""),
            str(payload.get("symbol", "") or ""), action,
            int(bool(payload.get("success", False))),
            requested_price, executed_price,
            float(payload.get("requested_volume", 0) or 0),
            float(payload.get("executed_volume", 0) or 0),
            slippage,
            int(payload.get("mt5_order", 0) or 0),
            int(payload.get("mt5_deal", 0) or 0),
            int(payload.get("retcode", 0) or 0),
            str(payload.get("error_message", "") or "")[:500],
            now, json.dumps(payload, ensure_ascii=False),
        )
        self.storage.execute(
            """
            INSERT INTO trade_execution_reports(
                user_id, account_id, instruction_id, order_id, symbol, action,
                success, requested_price, executed_price, requested_volume,
                executed_volume, slippage, mt5_order, mt5_deal, retcode,
                error_message, reported_at, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, instruction_id) DO UPDATE SET
                success = excluded.success,
                executed_price = excluded.executed_price,
                executed_volume = excluded.executed_volume,
                slippage = excluded.slippage,
                mt5_order = excluded.mt5_order,
                mt5_deal = excluded.mt5_deal,
                retcode = excluded.retcode,
                error_message = excluded.error_message,
                reported_at = excluded.reported_at,
                payload_json = excluded.payload_json
            """,
            values,
        )
        row = self.storage.fetchone(
            """
            SELECT * FROM trade_execution_reports
            WHERE account_id = ? AND instruction_id = ?
            """,
            (account_id, instruction_id),
        )
        return dict(row)

    def list_for_account(
        self, user_id: int, account_id: int, count: int = 100
    ) -> List[Dict]:
        return [dict(row) for row in self.storage.fetchall(
            """
            SELECT * FROM trade_execution_reports
            WHERE user_id = ? AND account_id = ?
            ORDER BY reported_at DESC, id DESC LIMIT ?
            """,
            (user_id, account_id, max(1, min(int(count), 500))),
        )]

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
        from market.models.llm_config import (
            DEFAULT_ANALYSIS_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT, LLMConfig,
        )

        row = self.storage.fetchone(
            """
            SELECT api_key, api_base, model, system_prompt,
                   analysis_prompt_template, prompt_version
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
                system_prompt=row["system_prompt"] or DEFAULT_SYSTEM_PROMPT,
                analysis_prompt_template=(
                    row["analysis_prompt_template"]
                    or DEFAULT_ANALYSIS_PROMPT_TEMPLATE
                ),
                prompt_version=int(row["prompt_version"] or 1),
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
        system_prompt: Optional[str] = None,
        analysis_prompt_template: Optional[str] = None,
    ) -> "LLMConfig":
        from market.models.llm_config import LLMConfig

        current = self.get_config(user_id) if self._exists(user_id) else LLMConfig.from_dict(self.DEFAULT_CONFIG)
        next_system_prompt = current.system_prompt if system_prompt is None else str(system_prompt).strip()
        next_template = (
            current.analysis_prompt_template
            if analysis_prompt_template is None
            else str(analysis_prompt_template).strip()
        )
        if not next_system_prompt:
            raise ValueError("系统提示词不能为空")
        if len(next_system_prompt) > 10000:
            raise ValueError("系统提示词不能超过 10000 个字符")
        if not next_template:
            raise ValueError("分析提示词模板不能为空")
        for placeholder in ("{{strategy_context}}", "{{market_data}}"):
            if placeholder not in next_template:
                raise ValueError(f"分析提示词模板必须包含 {placeholder}")
        if len(next_template) > 50000:
            raise ValueError("分析提示词模板不能超过 50000 个字符")
        prompt_changed = (
            next_system_prompt != current.system_prompt
            or next_template != current.analysis_prompt_template
        )
        next_config = LLMConfig(
            api_key=current.api_key if api_key is None else api_key,
            api_base=current.api_base if api_base is None else api_base,
            model=current.model if model is None else model,
            system_prompt=next_system_prompt,
            analysis_prompt_template=next_template,
            prompt_version=current.prompt_version + 1 if prompt_changed else current.prompt_version,
        )

        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO user_llm_configs(
                user_id, api_key, api_base, model, system_prompt,
                analysis_prompt_template, prompt_version, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                api_key = excluded.api_key,
                api_base = excluded.api_base,
                model = excluded.model,
                system_prompt = excluded.system_prompt,
                analysis_prompt_template = excluded.analysis_prompt_template,
                prompt_version = excluded.prompt_version,
                updated_at = excluded.updated_at
            """,
            (
                user_id, next_config.api_key, next_config.api_base,
                next_config.model, next_config.system_prompt,
                next_config.analysis_prompt_template,
                next_config.prompt_version, now, now,
            ),
        )
        return next_config

    def reset_prompts(self, user_id: int) -> "LLMConfig":
        from market.models.llm_config import (
            DEFAULT_ANALYSIS_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT,
        )
        return self.save_config(
            user_id,
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            analysis_prompt_template=DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
        )

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


class SharedAIRuntimeRepository:
    """Stores opt-in AI analysis snapshots that other users may use as context."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def list_shared(
        self, viewer_user_id: int, symbol: Optional[str] = None,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT runtime.*, users.username AS owner_username
            FROM shared_ai_runtime_data AS runtime
            JOIN users ON users.id = runtime.owner_user_id
            ORDER BY runtime.updated_at DESC, runtime.share_id
            """,
            (),
        )
        items = [self._row_to_dict(row, viewer_user_id) for row in rows]
        if symbol:
            for item in items:
                item["symbol_similarity"] = self.symbol_similarity(
                    symbol, item["symbol"]
                )
            items.sort(key=lambda item: (
                -item["symbol_similarity"], -item["updated_at"], item["share_id"]
            ))
        return items

    @classmethod
    def symbol_similarity(cls, left: str, right: str) -> float:
        left_normalized = cls._normalize_symbol(left)
        right_normalized = cls._normalize_symbol(right)
        if not left_normalized or not right_normalized:
            return 0.0
        if left_normalized == right_normalized:
            return 1.0
        if cls._symbol_family(left_normalized) == cls._symbol_family(right_normalized):
            return 0.98
        if left_normalized.startswith(right_normalized) or right_normalized.startswith(
            left_normalized
        ):
            return 0.9
        return round(
            SequenceMatcher(None, left_normalized, right_normalized).ratio(), 4
        )

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())

    @staticmethod
    def _symbol_family(symbol: str) -> str:
        aliases = (
            (("XAU", "GOLD"), "GOLD"),
            (("XAG", "SILVER"), "SILVER"),
            (("BTC", "XBT"), "BTC"),
            (("ETH",), "ETH"),
            (("WTI", "USOIL", "XTI"), "WTI"),
            (("BRENT", "UKOIL", "XBR"), "BRENT"),
        )
        for markers, family in aliases:
            if any(marker in symbol for marker in markers):
                return family
        return symbol

    def get_shared(self, share_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT runtime.*, users.username AS owner_username
            FROM shared_ai_runtime_data AS runtime
            JOIN users ON users.id = runtime.owner_user_id
            WHERE runtime.share_id = ?
            """,
            (str(share_id),),
        )
        return self._row_to_dict(row, None) if row else None

    def publish(
        self,
        user_id: int,
        strategy: Dict,
        source: Dict,
        result: Dict,
        model: str,
        system_prompt: str,
        analysis_prompt_template: str,
    ) -> Dict:
        strategy_id = str(strategy.get("strategy_id", ""))
        source_id = str(source.get("signal_source_id", ""))
        share_id = f"{int(user_id)}:{strategy_id}:{source_id}"
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO shared_ai_runtime_data(
                share_id, owner_user_id, strategy_id, signal_source_id,
                symbol, period, model, signal_params_json, system_prompt,
                analysis_prompt_template, strategy_name, strategy_lifecycle,
                result_json, last_run_at, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(share_id) DO UPDATE SET
                symbol = excluded.symbol,
                period = excluded.period,
                model = excluded.model,
                signal_params_json = excluded.signal_params_json,
                system_prompt = excluded.system_prompt,
                analysis_prompt_template = excluded.analysis_prompt_template,
                strategy_name = excluded.strategy_name,
                strategy_lifecycle = excluded.strategy_lifecycle,
                result_json = excluded.result_json,
                last_run_at = excluded.last_run_at,
                updated_at = excluded.updated_at
            """,
            (
                share_id, int(user_id), strategy_id, source_id,
                str(strategy.get("symbol", "")), str(source.get("period", "")),
                str(model), json.dumps(source.get("params") or {}, ensure_ascii=False),
                str(system_prompt), str(analysis_prompt_template),
                str(strategy.get("strategy_name", "")),
                str(strategy.get("lifecycle_status", "draft")),
                json.dumps(result or {}, ensure_ascii=False), now, now, now,
            ),
        )
        return self.get_shared(share_id) or {}

    def remove_for_source(
        self, user_id: int, strategy_id: str, signal_source_id: str,
    ) -> None:
        self.storage.execute(
            """
            DELETE FROM shared_ai_runtime_data
            WHERE owner_user_id = ? AND strategy_id = ? AND signal_source_id = ?
            """,
            (int(user_id), str(strategy_id), str(signal_source_id)),
        )

    def sync_strategy_visibility(self, user_id: int, strategy: Dict) -> None:
        self.storage.execute(
            """
            UPDATE shared_ai_runtime_data
            SET symbol = ?, strategy_name = ?, strategy_lifecycle = ?, updated_at = ?
            WHERE owner_user_id = ? AND strategy_id = ?
            """,
            (
                str(strategy.get("symbol", "")),
                str(strategy.get("strategy_name", "")),
                str(strategy.get("lifecycle_status", "draft")),
                _now_ts(), int(user_id), str(strategy.get("strategy_id", "")),
            ),
        )
        shared_source_ids = {
            str(source.get("signal_source_id", ""))
            for source in (strategy.get("signal_sources") or [])
            if source.get("source") == "ai_entry"
            and (source.get("params") or {}).get("share_runtime_data")
        }
        rows = self.storage.fetchall(
            """
            SELECT signal_source_id FROM shared_ai_runtime_data
            WHERE owner_user_id = ? AND strategy_id = ?
            """,
            (int(user_id), str(strategy.get("strategy_id", ""))),
        )
        for row in rows:
            if row["signal_source_id"] not in shared_source_ids:
                self.remove_for_source(
                    user_id, strategy.get("strategy_id", ""),
                    row["signal_source_id"],
                )

    def remove_for_strategy(self, user_id: int, strategy_id: str) -> None:
        self.storage.execute(
            """
            DELETE FROM shared_ai_runtime_data
            WHERE owner_user_id = ? AND strategy_id = ?
            """,
            (int(user_id), str(strategy_id)),
        )

    @staticmethod
    def _row_to_dict(row, viewer_user_id: Optional[int]) -> Dict:
        return {
            "share_id": row["share_id"],
            "owner_user_id": int(row["owner_user_id"]),
            "owner_username": row["owner_username"],
            "is_owner": (
                viewer_user_id is not None
                and int(row["owner_user_id"]) == int(viewer_user_id)
            ),
            "strategy_id": row["strategy_id"],
            "strategy_name": row["strategy_name"],
            "strategy_lifecycle": row["strategy_lifecycle"],
            "signal_source_id": row["signal_source_id"],
            "symbol": row["symbol"],
            "period": row["period"],
            "model": row["model"],
            "signal_params": json.loads(row["signal_params_json"] or "{}"),
            "system_prompt": row["system_prompt"],
            "analysis_prompt_template": row["analysis_prompt_template"],
            "result": json.loads(row["result_json"] or "{}"),
            "last_run_at": int(row["last_run_at"]),
            "updated_at": int(row["updated_at"]),
        }


class PositionManagementPolicyRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def list(self, user_id: int, enabled_only: bool = False):
        from market.models import PositionManagementPolicy

        sql = "SELECT * FROM position_management_policies WHERE user_id = ?"
        params = [int(user_id)]
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at, policy_id"
        return [
            PositionManagementPolicy.from_dict({
                "policy_id": row["policy_id"], "user_id": row["user_id"],
                "version": row["version"],
                "name": row["name"], "enabled": bool(row["enabled"]),
                "config": json.loads(row["config_json"]),
                "created_at": datetime.fromtimestamp(row["created_at"]),
                "updated_at": datetime.fromtimestamp(row["updated_at"]),
            })
            for row in self.storage.fetchall(sql, tuple(params))
        ]

    def get(self, user_id: int, policy_id: str):
        return next((item for item in self.list(user_id)
                     if item.policy_id == policy_id), None)

    def save(self, policy):
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO position_management_policies(
                policy_id, user_id, name, version, enabled, config_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                name = excluded.name, version = excluded.version,
                enabled = excluded.enabled,
                config_json = excluded.config_json, updated_at = excluded.updated_at
            """,
            (policy.policy_id, policy.user_id, policy.name, policy.version,
             int(policy.enabled),
             json.dumps(policy.config, ensure_ascii=False),
             int(policy.created_at.timestamp()), now),
        )
        policy.updated_at = datetime.fromtimestamp(now)
        return policy

    def invalidate_linked_strategies(
        self, user_id: int, policy_id: str,
        reason: str = "持仓管理方案已修改，需要重新验证",
    ) -> int:
        rows = self.storage.fetchall(
            "SELECT strategy_id, config_json FROM user_strategy_configs WHERE user_id = ?",
            (user_id,),
        )
        changed = 0
        now = datetime.now()
        for row in rows:
            data = json.loads(row["config_json"])
            if data.get("position_management_policy_id") != policy_id:
                continue
            previous = data.get("lifecycle_status", "draft")
            if previous != "draft":
                data.setdefault("lifecycle_history", []).append({
                    "from_status": previous, "to_status": "draft",
                    "changed_at": now.isoformat(), "reason": reason,
                })
            data["lifecycle_status"] = "draft"
            data["lifecycle_updated_at"] = now.isoformat()
            data["enabled"] = False
            data["auto_execute"] = False
            data["updated_at"] = now.isoformat()
            self.storage.execute(
                "UPDATE user_strategy_configs SET config_json = ?, updated_at = ? WHERE user_id = ? AND strategy_id = ?",
                (json.dumps(data, ensure_ascii=False), _now_ts(), user_id,
                 row["strategy_id"]),
            )
            changed += 1
        return changed

    def delete(self, user_id: int, policy_id: str) -> bool:
        referenced = self.storage.fetchone(
            """
            SELECT COUNT(*) AS count FROM user_strategy_configs
            WHERE user_id = ? AND json_extract(config_json, '$.position_management_policy_id') = ?
            """, (user_id, policy_id),
        )
        if referenced and int(referenced["count"]):
            raise ValueError("持仓管理方案正在被策略引用，不能删除")
        if not self.get(user_id, policy_id):
            return False
        self.storage.execute(
            "DELETE FROM position_management_policies WHERE user_id = ? AND policy_id = ?",
            (user_id, policy_id),
        )
        return True


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
            strategies = [
                TradingStrategy.from_dict(json.loads(row["config_json"]))
                for row in rows
            ]
            return sorted(
                strategies,
                key=lambda strategy: (
                    strategy.created_at or datetime.min,
                    strategy.strategy_id,
                ),
            )

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

    def list_shared_strategies(
        self, viewer_user_id: int, include_own: bool = False
    ) -> List[Dict]:
        from market.models.trading_strategy import TradingStrategy

        rows = self.storage.fetchall(
            """
            SELECT strategy.user_id, users.username, strategy.config_json
            FROM user_strategy_configs AS strategy
            JOIN users ON users.id = strategy.user_id
            ORDER BY strategy.updated_at DESC, strategy.strategy_id
            """,
            (),
        )
        shared = []
        for row in rows:
            owner_user_id = int(row["user_id"])
            if owner_user_id == int(viewer_user_id) and not include_own:
                continue
            strategy = TradingStrategy.from_dict(json.loads(row["config_json"]))
            if strategy.visibility != "shared":
                continue
            item = strategy.to_dict()
            item.update({
                "owner_user_id": owner_user_id,
                "owner_username": row["username"],
            })
            shared.append(item)
        return shared

    def copy_shared_strategy(
        self,
        target_user_id: int,
        owner_user_id: int,
        strategy_id: str,
        position_management_policy_id: str,
    ) -> Optional["TradingStrategy"]:
        from market.models.trading_strategy import StrategyLifecycle, TradingStrategy

        source = self.get_strategy_by_id(int(owner_user_id), strategy_id)
        if source is None or source.visibility != "shared":
            return None

        owner = UserRepository(self.storage).get_by_id(int(owner_user_id))
        now = datetime.now()
        payload = source.to_dict()
        payload.update({
            "strategy_id": "",
            "strategy_name": f"{source.strategy_name}（副本）",
            "visibility": "private",
            "is_shared": False,
            "enabled": False,
            "auto_execute": False,
            "lifecycle_status": StrategyLifecycle.DRAFT,
            "lifecycle_updated_at": now.isoformat(),
            "lifecycle_history": [],
            "position_management_policy_id": position_management_policy_id,
            "source_strategy_id": source.strategy_id,
            "source_owner_user_id": int(owner_user_id),
            "source_owner_username": owner.username if owner else "",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })
        copied = TradingStrategy.from_dict(payload)
        return self.save_strategy(int(target_user_id), copied)

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

    def trim_entities(self, entity_type: str, max_count: int) -> None:
        """只保留账户范围内指定类型最近更新的记录。"""
        self.storage.execute(
            """
            DELETE FROM runtime_entities
            WHERE rowid IN (
                SELECT rowid
                FROM runtime_entities
                WHERE user_id = ? AND account_id = ? AND entity_type = ?
                ORDER BY created_at DESC, updated_at DESC, entity_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.user_id, self.account_id, entity_type, max(0, int(max_count))),
        )

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
