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

from mysql_storage import MySQLStorage

if TYPE_CHECKING:
    from market.models import LLMConfig, TradingStrategy


ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("AI_TRADER_DATA_DIR") or (ROOT_DIR / "data"))
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


def _get_env_default_admin_email() -> str:
    return os.getenv(
        "AI_TRADER_DEFAULT_ADMIN_EMAIL", "xingxing.wxx@foxmail.com"
    ).strip().lower()


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
    membership_level: str
    live_trading_enabled: bool
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
        conn = sqlite3.connect(
            self.db_file, check_same_thread=False, timeout=30
        )
        conn.execute("PRAGMA busy_timeout=30000")
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

                    CREATE TABLE IF NOT EXISTS llm_provider_configs (
                        provider_id TEXT PRIMARY KEY,
                        admin_user_id INTEGER NOT NULL,
                        provider_name TEXT NOT NULL,
                        api_key TEXT NOT NULL DEFAULT '',
                        api_base TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
                        model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
                        active INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(admin_user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_provider_active
                    ON llm_provider_configs(admin_user_id)
                    WHERE active = 1;

                    CREATE INDEX IF NOT EXISTS idx_llm_provider_owner
                    ON llm_provider_configs(admin_user_id, updated_at DESC);

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

                    CREATE TABLE IF NOT EXISTS llm_models (
                        model_id TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        available INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        discovered_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS llm_scene_policies (
                        scene_code TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL,
                        frequency_class TEXT NOT NULL,
                        requires_access INTEGER NOT NULL DEFAULT 0,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        default_model_id TEXT NOT NULL DEFAULT '',
                        allow_user_selection INTEGER NOT NULL DEFAULT 0,
                        system_prompt TEXT NOT NULL DEFAULT '',
                        user_prompt_template TEXT NOT NULL DEFAULT '',
                        updated_by INTEGER,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(updated_by) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE TABLE IF NOT EXISTS llm_scene_models (
                        scene_code TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        PRIMARY KEY(scene_code, model_id),
                        FOREIGN KEY(scene_code) REFERENCES llm_scene_policies(scene_code) ON DELETE CASCADE,
                        FOREIGN KEY(model_id) REFERENCES llm_models(model_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS llm_scene_prompts (
                        prompt_id TEXT PRIMARY KEY,
                        scene_code TEXT NOT NULL,
                        prompt_name TEXT NOT NULL,
                        system_prompt TEXT NOT NULL,
                        user_prompt_template TEXT NOT NULL,
                        is_default INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(scene_code) REFERENCES llm_scene_policies(scene_code) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_llm_scene_prompts_scene
                    ON llm_scene_prompts(scene_code, is_default, updated_at);

                    CREATE TABLE IF NOT EXISTS llm_call_logs (
                        call_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        scene_code TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'reserved',
                        object_type TEXT NOT NULL DEFAULT '',
                        object_id TEXT NOT NULL DEFAULT '',
                        duration_ms INTEGER,
                        prompt_tokens INTEGER,
                        completion_tokens INTEGER,
                        total_tokens INTEGER,
                        error_message TEXT NOT NULL DEFAULT '',
                        result_summary VARCHAR(4096) NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        completed_at INTEGER,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_llm_call_quota
                    ON llm_call_logs(user_id, created_at, scene_code);

                    CREATE TABLE IF NOT EXISTS ai_trade_suggestions (
                        suggestion_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        signal_source_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        period TEXT NOT NULL,
                        plan_fingerprint TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        confidence INTEGER NOT NULL DEFAULT 0,
                        entry_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        analysis_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        suggestion_count INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_ai_trade_suggestions_source_time
                    ON ai_trade_suggestions(user_id, signal_source_id, last_seen_at DESC);

                    CREATE TABLE IF NOT EXISTS system_event_logs (
                        event_id TEXT PRIMARY KEY,
                        occurred_at INTEGER NOT NULL,
                        level TEXT NOT NULL,
                        category TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        event_name TEXT NOT NULL,
                        user_id INTEGER,
                        account_id INTEGER,
                        symbol TEXT NOT NULL DEFAULT '',
                        actor_type TEXT NOT NULL DEFAULT 'system',
                        actor_id TEXT NOT NULL DEFAULT '',
                        entity_type TEXT NOT NULL DEFAULT '',
                        entity_id TEXT NOT NULL DEFAULT '',
                        correlation_id TEXT NOT NULL DEFAULT '',
                        message TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        detail_json TEXT NOT NULL DEFAULT '{}',
                        request_id TEXT NOT NULL DEFAULT '',
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_system_event_scope_time
                    ON system_event_logs(user_id, account_id, occurred_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_system_event_category_time
                    ON system_event_logs(category, level, occurred_at DESC);

                    CREATE INDEX IF NOT EXISTS idx_system_event_correlation
                    ON system_event_logs(correlation_id, occurred_at);

                    CREATE TABLE IF NOT EXISTS data_maintenance_runs (
                        run_id TEXT PRIMARY KEY,
                        trigger_type TEXT NOT NULL DEFAULT 'scheduled',
                        status TEXT NOT NULL DEFAULT 'running',
                        started_at INTEGER NOT NULL,
                        completed_at INTEGER,
                        duration_ms INTEGER,
                        db_size_before INTEGER NOT NULL DEFAULT 0,
                        db_size_after INTEGER NOT NULL DEFAULT 0,
                        page_count INTEGER NOT NULL DEFAULT 0,
                        free_page_count INTEGER NOT NULL DEFAULT 0,
                        reclaimable_bytes INTEGER NOT NULL DEFAULT 0,
                        free_ratio REAL NOT NULL DEFAULT 0,
                        checkpoint_status TEXT NOT NULL DEFAULT '',
                        vacuum_status TEXT NOT NULL DEFAULT 'not_due',
                        vacuum_reason TEXT NOT NULL DEFAULT '',
                        cleanup_json TEXT NOT NULL DEFAULT '{}',
                        error_message TEXT NOT NULL DEFAULT ''
                    );

                    CREATE INDEX IF NOT EXISTS idx_data_maintenance_started
                    ON data_maintenance_runs(started_at DESC);

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

                    CREATE TABLE IF NOT EXISTS invitation_codes (
                        invitation_id TEXT PRIMARY KEY,
                        code_hash TEXT NOT NULL UNIQUE,
                        code_prefix TEXT NOT NULL,
                        label TEXT NOT NULL DEFAULT '',
                        max_uses INTEGER NOT NULL DEFAULT 1,
                        used_count INTEGER NOT NULL DEFAULT 0,
                        expires_at INTEGER,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_by INTEGER NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_invitation_codes_status
                    ON invitation_codes(active, expires_at, created_at DESC);

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

                    CREATE TABLE IF NOT EXISTS platform_instrument_mappings (
                        mapping_id TEXT PRIMARY KEY,
                        broker_name TEXT NOT NULL DEFAULT '',
                        broker_server TEXT NOT NULL,
                        native_symbol TEXT NOT NULL,
                        mapping_group TEXT NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        UNIQUE(broker_server, native_symbol)
                    );

                    CREATE INDEX IF NOT EXISTS idx_platform_instrument_group
                    ON platform_instrument_mappings(mapping_group, enabled);

                    CREATE TABLE IF NOT EXISTS ai_signal_sources (
                        signal_source_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        period TEXT NOT NULL,
                        config_json TEXT NOT NULL DEFAULT '{}',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        share_runtime_data INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_ai_signal_sources_owner
                    ON ai_signal_sources(user_id, enabled, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS position_management_policies (
                        policy_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        visibility TEXT NOT NULL DEFAULT 'private',
                        source_policy_id TEXT NOT NULL DEFAULT '',
                        source_owner_user_id INTEGER NOT NULL DEFAULT 0,
                        source_owner_username TEXT NOT NULL DEFAULT '',
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
                        daily_order_limit INTEGER NOT NULL DEFAULT 100,
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
                        mt5_position_id INTEGER NOT NULL DEFAULT 0,
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

                    CREATE TABLE IF NOT EXISTS live_trade_deals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        ticket INTEGER NOT NULL,
                        mt5_order INTEGER NOT NULL DEFAULT 0,
                        mt5_position_id INTEGER NOT NULL DEFAULT 0,
                        symbol TEXT NOT NULL DEFAULT '',
                        deal_type INTEGER NOT NULL DEFAULT 0,
                        entry_type INTEGER NOT NULL DEFAULT 0,
                        volume REAL NOT NULL DEFAULT 0,
                        price REAL NOT NULL DEFAULT 0,
                        profit REAL NOT NULL DEFAULT 0,
                        swap REAL NOT NULL DEFAULT 0,
                        commission REAL NOT NULL DEFAULT 0,
                        deal_time TEXT NOT NULL DEFAULT '',
                        comment TEXT NOT NULL DEFAULT '',
                        received_at INTEGER NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        UNIQUE(account_id, ticket),
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_live_trade_deals_account
                    ON live_trade_deals(account_id, deal_time DESC, received_at DESC);

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
                        broker_utc_offset_seconds INTEGER NOT NULL DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        PRIMARY KEY(dataset_id, chunk_index),
                        FOREIGN KEY(dataset_id) REFERENCES backtest_datasets(dataset_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS backtest_templates (
                        template_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        template_name TEXT NOT NULL,
                        visibility TEXT NOT NULL DEFAULT 'private',
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
                        remaining_volume REAL NOT NULL DEFAULT 0,
                        partial_levels_done_json TEXT NOT NULL DEFAULT '[]',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(order_id) REFERENCES backtest_orders(order_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_positions_task
                    ON backtest_positions(task_id, status, opened_at);

                    CREATE TABLE IF NOT EXISTS backtest_position_events (
                        event_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        position_id TEXT NOT NULL,
                        order_id TEXT NOT NULL DEFAULT '',
                        user_id INTEGER NOT NULL,
                        event_time INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        rule_type TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        message TEXT NOT NULL DEFAULT '',
                        price REAL NOT NULL DEFAULT 0,
                        stop_loss REAL NOT NULL DEFAULT 0,
                        take_profit REAL NOT NULL DEFAULT 0,
                        volume REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES backtest_tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY(position_id) REFERENCES backtest_positions(position_id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_backtest_position_events
                    ON backtest_position_events(task_id, position_id, event_time);

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
                        remaining_volume REAL NOT NULL DEFAULT 0,
                        partial_levels_done_json TEXT NOT NULL DEFAULT '[]',
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

                    CREATE TABLE IF NOT EXISTS position_management_events (
                        event_id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        position_key TEXT NOT NULL,
                        position_id TEXT NOT NULL DEFAULT '',
                        ticket INTEGER,
                        symbol TEXT NOT NULL DEFAULT '',
                        event_time INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        rule_type TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT '',
                        message TEXT NOT NULL DEFAULT '',
                        price REAL NOT NULL DEFAULT 0,
                        stop_loss REAL NOT NULL DEFAULT 0,
                        take_profit REAL NOT NULL DEFAULT 0,
                        volume REAL NOT NULL DEFAULT 0,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at INTEGER NOT NULL,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_position_management_events
                    ON position_management_events(user_id, account_id, position_key, event_time);

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

                    CREATE TABLE IF NOT EXISTS live_equity_points (
                        account_id INTEGER NOT NULL,
                        point_time INTEGER NOT NULL,
                        user_id INTEGER NOT NULL,
                        balance REAL NOT NULL,
                        equity REAL NOT NULL,
                        free_margin REAL NOT NULL,
                        margin REAL NOT NULL,
                        PRIMARY KEY(account_id, point_time),
                        FOREIGN KEY(account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_live_equity_points_account
                    ON live_equity_points(account_id, point_time DESC);

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
                    conn, "trade_execution_reports", "mt5_position_id",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                for table in ("trade_execution_reports", "live_trade_deals"):
                    self._ensure_column(
                        conn, table, "position_attribution_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    )
                self._ensure_column(
                    conn, "llm_call_logs", "result_summary", "VARCHAR(4096) NOT NULL DEFAULT ''"
                )
                self._ensure_column(
                    conn, "position_management_policies", "version",
                    "INTEGER NOT NULL DEFAULT 1",
                )
                self._ensure_column(
                    conn, "position_management_policies", "visibility",
                    "TEXT NOT NULL DEFAULT 'private'",
                )
                self._ensure_column(
                    conn, "position_management_policies", "source_policy_id",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "position_management_policies", "source_owner_user_id",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "position_management_policies", "source_owner_username",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "strategy_deployments", "strategy_version_at",
                    "INTEGER NOT NULL DEFAULT 0",
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
                self._ensure_column(
                    conn, "llm_scene_policies", "system_prompt",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "llm_scene_policies", "user_prompt_template",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn, "llm_provider_configs", "provider_name",
                    "TEXT NOT NULL DEFAULT '默认供应商'",
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
                        conn, table, "position_attribution_json",
                        "TEXT NOT NULL DEFAULT '{}'",
                    )
                self._ensure_column(
                    conn, "paper_trades", "position_attribution_json",
                    "TEXT NOT NULL DEFAULT '{}'",
                )
                for table in (
                    "backtest_orders", "backtest_positions", "backtest_trades",
                ):
                    self._ensure_column(
                        conn, table, "position_attribution_json",
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
                    conn, "paper_positions", "remaining_volume", "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "paper_positions", "partial_levels_done_json",
                    "TEXT NOT NULL DEFAULT '[]'",
                )
                self._ensure_column(
                    conn, "backtest_positions", "remaining_volume",
                    "REAL NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn, "backtest_positions", "partial_levels_done_json",
                    "TEXT NOT NULL DEFAULT '[]'",
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
                    conn, "platform_instrument_mappings", "broker_name",
                    "TEXT NOT NULL DEFAULT ''",
                )
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
                    "INTEGER NOT NULL DEFAULT 100",
                )
                daily_limit_migration = "paper_daily_order_limit_100_v1"
                if conn.execute(
                    "SELECT 1 FROM app_meta WHERE key = ?",
                    (daily_limit_migration,),
                ).fetchone() is None:
                    conn.execute(
                        """
                        UPDATE trading_accounts
                        SET daily_order_limit = 100, updated_at = ?
                        WHERE account_type = 'paper'
                          AND daily_order_limit IN (20, 60)
                        """,
                        (_now_ts(),),
                    )
                    conn.execute(
                        "INSERT INTO app_meta(key, value) VALUES(?, ?)",
                        (daily_limit_migration, str(_now_ts())),
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
                    "TEXT NOT NULL DEFAULT 'private'",
                )
                template_visibility_migration = "backtest_templates_private_v1"
                if conn.execute(
                    "SELECT 1 FROM app_meta WHERE key = ?",
                    (template_visibility_migration,),
                ).fetchone() is None:
                    conn.execute(
                        "UPDATE backtest_templates SET visibility = 'private'"
                    )
                    conn.execute(
                        "INSERT INTO app_meta(key, value) VALUES(?, ?)",
                        (template_visibility_migration, str(_now_ts())),
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
                self._ensure_column(
                    conn,
                    "users",
                    "membership_level",
                    "TEXT NOT NULL DEFAULT 'silver'",
                )
                self._ensure_column(
                    conn,
                    "users",
                    "live_trading_enabled",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(conn, "users", "email", "TEXT")
                self._ensure_column(
                    conn, "email_verification_codes", "purpose",
                    "TEXT NOT NULL DEFAULT 'registration'",
                )
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
                admin_email = _get_env_default_admin_email()
                conn.execute(
                    """
                    UPDATE users
                    SET email = ?, updated_at = ?
                    WHERE lower(username) = ? AND email IS NULL
                      AND NOT EXISTS(SELECT 1 FROM users WHERE email = ?)
                    """,
                    (admin_email, _now_ts(), admin_username, admin_email),
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


_STORAGE: Optional[MySQLStorage] = None


def get_storage() -> MySQLStorage:
    global _STORAGE
    if _STORAGE is None:
        _STORAGE = MySQLStorage()
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
            SELECT id, username, email, password_hash, salt, role,
                   membership_level, live_trading_enabled, token_version,
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
            SELECT id, username, email, password_hash, salt, role,
                   membership_level, live_trading_enabled, token_version,
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
            SELECT id, username, email, password_hash, salt, role,
                   membership_level, live_trading_enabled, token_version,
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
        membership_level: str = "silver",
        live_trading_enabled: bool = False,
    ) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO users(
                username, email, password_hash, salt, role, membership_level,
                live_trading_enabled, token_version,
                created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                username, email, password_hash, salt, role, membership_level,
                int(live_trading_enabled), now, now,
            ),
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

    def rotate_token_version(self, user_id: int) -> UserRecord:
        """Invalidate all existing web sessions and return the new user record."""
        now = _now_ts()
        self.storage.execute(
            """
            UPDATE users
            SET token_version = token_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (now, int(user_id)),
        )
        user = self.get_by_id(user_id)
        if user is None:
            raise RuntimeError("刷新登录会话后未找到用户")
        return user

    def count(self) -> int:
        row = self.storage.fetchone("SELECT COUNT(*) AS total FROM users")
        return int(row["total"]) if row else 0

    def list_users(self) -> List[UserRecord]:
        rows = self.storage.fetchall(
            """
            SELECT id, username, email, password_hash, salt, role,
                   membership_level, live_trading_enabled, token_version,
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
        email = _get_env_default_admin_email() if role == "admin" else None
        return self.create_user(
            username, password_hash, salt, role=role, email=email
        )

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
            membership_level=row["membership_level"],
            live_trading_enabled=bool(row["live_trading_enabled"]),
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
                    daily_order_limit, financial_updated_at, created_at, updated_at
                ) VALUES(?, ?, ?, 'paper', 'simulated', ?, ?, ?, ?, ?, 0,
                         'active', ?, 1, 100, ?, ?, ?)
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
            account_row = conn.execute(
                "SELECT user_id FROM trading_accounts WHERE id = ? AND account_type = 'mt5'",
                (account_id,),
            ).fetchone()
            if account_row is not None:
                conn.execute(
                    """
                    INSERT INTO live_equity_points(
                        account_id, point_time, user_id, balance, equity, free_margin, margin
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, point_time) DO UPDATE SET
                        balance = excluded.balance, equity = excluded.equity,
                        free_margin = excluded.free_margin, margin = excluded.margin
                    """,
                    (account_id, now, int(account_row["user_id"]), *values),
                )
            conn.commit()
            return cursor.rowcount == 1

    def list_live_equity_points(
        self, user_id: int, account_id: int, count: int = 1440,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT point_time AS time, balance, equity, free_margin, margin
            FROM live_equity_points
            WHERE user_id = ? AND account_id = ?
            ORDER BY point_time DESC LIMIT ?
            """,
            (user_id, account_id, max(1, min(int(count), 5000))),
        )
        return [dict(row) for row in rows][::-1]

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

    def list_for_strategy(self, user_id: int, strategy_id: str) -> List[Dict]:
        """Return a user's account deployments for navigation and audit views."""
        rows = self.storage.fetchall(
            """
            SELECT deployment.deployment_id, deployment.account_id,
                   deployment.execution_mode, deployment.status,
                   deployment.symbol,
                   account.account_name, account.account_type
            FROM strategy_deployments AS deployment
            JOIN trading_accounts AS account ON account.id = deployment.account_id
            WHERE deployment.user_id = ? AND deployment.strategy_id = ?
              AND deployment.execution_mode IN ('paper', 'live')
            ORDER BY CASE deployment.execution_mode WHEN 'paper' THEN 0 ELSE 1 END,
                     CASE deployment.status WHEN 'active' THEN 0 ELSE 1 END,
                     deployment.updated_at DESC
            """,
            (int(user_id), str(strategy_id)),
        )
        return [dict(row) for row in rows]


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
        now = int(payload.get("reported_timestamp", 0) or _now_ts())
        runtime = self.storage.fetchone(
            "SELECT payload_json FROM runtime_entities WHERE user_id = ? "
            "AND account_id = ? AND entity_type = 'trading_instruction' "
            "AND entity_id = ?",
            (int(user_id), int(account_id), instruction_id),
        )
        instruction = json.loads(runtime["payload_json"] or "{}") if runtime else {}
        attribution = dict(instruction.get("position_attribution") or {})
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
            int(
                payload.get("mt5_position_id")
                or payload.get("mt5_position")
                or payload.get("position_id")
                or payload.get("position_ticket")
                or 0
            ),
            int(payload.get("retcode", 0) or 0),
            str(payload.get("error_message", "") or "")[:500],
            now, json.dumps(payload, ensure_ascii=False),
            json.dumps(attribution, ensure_ascii=False),
        )
        self.storage.execute(
            """
            INSERT INTO trade_execution_reports(
                user_id, account_id, instruction_id, order_id, symbol, action,
                success, requested_price, executed_price, requested_volume,
                executed_volume, slippage, mt5_order, mt5_deal, mt5_position_id, retcode,
                error_message, reported_at, payload_json
                , position_attribution_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, instruction_id) DO UPDATE SET
                success = excluded.success,
                executed_price = excluded.executed_price,
                executed_volume = excluded.executed_volume,
                slippage = excluded.slippage,
                mt5_order = excluded.mt5_order,
                mt5_deal = excluded.mt5_deal,
                mt5_position_id = excluded.mt5_position_id,
                retcode = excluded.retcode,
                error_message = excluded.error_message,
                reported_at = excluded.reported_at,
                payload_json = excluded.payload_json
                , position_attribution_json = excluded.position_attribution_json
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
        return self._deserialize(row)

    @staticmethod
    def _deserialize(row) -> Optional[Dict]:
        if row is None:
            return None
        item = dict(row)
        try:
            item["position_attribution"] = json.loads(
                item.get("position_attribution_json") or "{}"
            )
        except (TypeError, ValueError):
            item["position_attribution"] = {}
        return item

    def find_for_position(
        self, user_id: int, account_id: int, mt5_position_id: int,
    ) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT * FROM trade_execution_reports WHERE user_id = ? "
            "AND account_id = ? AND mt5_position_id = ? AND success = 1 "
            "ORDER BY reported_at DESC, id DESC LIMIT 1",
            (int(user_id), int(account_id), int(mt5_position_id or 0)),
        )
        return self._deserialize(row)

    def list_for_account(
        self, user_id: int, account_id: int, count: int = 100
    ) -> List[Dict]:
        return [self._deserialize(row) for row in self.storage.fetchall(
            """
            SELECT * FROM trade_execution_reports
            WHERE user_id = ? AND account_id = ?
            ORDER BY reported_at DESC, id DESC LIMIT ?
            """,
            (user_id, account_id, max(1, min(int(count), 500))),
        )]


class LiveTradeDealRepository:
    """持久化 EA 上报的 MT5 成交流水。

    运行态内存/`runtime_entities` 仍用于风控的短期窗口；该表用于账户页、
    策略回放等需要稳定读取的最近成交，避免服务重启或 24 小时清理后消失。
    """

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def record_many(
        self, user_id: int, account_id: int, deals: List[Dict]
    ) -> Dict[str, int]:
        stats = {"inserted": 0, "updated": 0, "unchanged": 0, "invalid": 0}
        if not deals:
            return stats
        received_at = _now_ts()
        for deal in deals:
            from market.models.trade_history import TradeDeal
            parsed_deal = TradeDeal.from_ea_data(deal).to_dict()
            canonical_deal = {**deal, **parsed_deal}
            ticket = int(deal.get("ticket", 0) or 0)
            if ticket <= 0:
                stats["invalid"] += 1
                continue
            mt5_order = int(deal.get("order", 0) or 0)
            mt5_position_id = int(
                deal.get("position_id", deal.get("mt5_position_id", 0)) or 0
            )
            execution = self.storage.fetchone(
                "SELECT position_attribution_json FROM trade_execution_reports "
                "WHERE user_id = ? AND account_id = ? AND success = 1 "
                "AND ((mt5_position_id > 0 AND mt5_position_id = ?) "
                "OR (mt5_order > 0 AND mt5_order = ?)) "
                "ORDER BY reported_at DESC, id DESC LIMIT 1",
                (int(user_id), int(account_id), mt5_position_id, mt5_order),
            )
            attribution = json.loads(
                execution["position_attribution_json"] or "{}"
            ) if execution else {}
            entry_type = int(deal.get("entry", 0) or 0)
            if attribution and entry_type != 0:
                opening = self.storage.fetchone(
                    "SELECT price, deal_type FROM live_trade_deals WHERE user_id = ? "
                    "AND account_id = ? AND mt5_position_id = ? AND entry_type = 0 "
                    "ORDER BY deal_time, id LIMIT 1",
                    (int(user_id), int(account_id), mt5_position_id),
                )
                initial_risk = float(attribution.get("initial_risk") or 0)
                realized_r = 0.0
                if opening and initial_risk > 0:
                    sign = 1 if int(opening["deal_type"] or 0) == 0 else -1
                    realized_r = (
                        (float(deal.get("price", 0) or 0) - float(opening["price"]))
                        * sign / initial_risk
                    )
                from market.services.position_attribution import close_position_attribution
                comment = str(deal.get("comment") or "").strip().lower()
                exit_reason = (
                    "stop_loss" if comment.startswith("[sl")
                    else "take_profit" if comment.startswith("[tp")
                    else "forced_close" if comment.startswith("[so")
                    else "position_close"
                )
                attribution = close_position_attribution(
                    attribution,
                    exit_reason,
                    realized_r,
                )
            payload_json = json.dumps(
                canonical_deal, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            attribution_json = json.dumps(
                attribution, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            )
            comparable = (
                mt5_order, mt5_position_id,
                str(deal.get("symbol", "") or ""),
                int(deal.get("type", 0) or 0), int(deal.get("entry", 0) or 0),
                float(deal.get("volume", 0) or 0), float(deal.get("price", 0) or 0),
                float(deal.get("profit", 0) or 0), float(deal.get("swap", 0) or 0),
                float(deal.get("commission", 0) or 0),
                str(canonical_deal.get("time_beijing", "") or ""),
                int(canonical_deal.get("deal_timestamp", 0) or 0),
                int(deal.get("broker_utc_offset_seconds", 0) or 0),
                str(deal.get("comment", "") or ""), payload_json,
                attribution_json,
            )
            existing = self.storage.fetchone(
                "SELECT mt5_order, mt5_position_id, symbol, deal_type, entry_type, "
                "volume, price, profit, swap, commission, deal_time, deal_timestamp, "
                "broker_utc_offset_seconds, comment, payload_json, "
                "position_attribution_json FROM live_trade_deals "
                "WHERE account_id = ? AND ticket = ?",
                (int(account_id), ticket),
            )
            if existing:
                existing = dict(existing)

                def normalized_json(value):
                    if isinstance(value, (dict, list)):
                        return value
                    try:
                        return json.loads(value or "{}")
                    except (TypeError, ValueError):
                        return {}

                existing_comparable = (
                    int(existing.get("mt5_order") or 0),
                    int(existing.get("mt5_position_id") or 0),
                    str(existing.get("symbol") or ""),
                    int(existing.get("deal_type") or 0),
                    int(existing.get("entry_type") or 0),
                    float(existing.get("volume") or 0),
                    float(existing.get("price") or 0),
                    float(existing.get("profit") or 0),
                    float(existing.get("swap") or 0),
                    float(existing.get("commission") or 0),
                    str(existing.get("deal_time") or ""),
                    int(existing.get("deal_timestamp") or 0),
                    int(existing.get("broker_utc_offset_seconds") or 0),
                    str(existing.get("comment") or ""),
                    json.dumps(
                        normalized_json(existing.get("payload_json")),
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ),
                    json.dumps(
                        normalized_json(existing.get("position_attribution_json")),
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ),
                )
                if existing_comparable == comparable:
                    stats["unchanged"] += 1
                    continue

            values = (
                int(user_id), int(account_id), ticket,
                *comparable[:14], received_at, payload_json, attribution_json,
            )
            self.storage.execute(
                """
                INSERT INTO live_trade_deals(
                    user_id, account_id, ticket, mt5_order, mt5_position_id,
                    symbol, deal_type, entry_type, volume, price, profit, swap,
                    commission, deal_time, deal_timestamp,
                    broker_utc_offset_seconds, comment, received_at, payload_json
                    , position_attribution_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, ticket) DO UPDATE SET
                    mt5_order = excluded.mt5_order,
                    mt5_position_id = excluded.mt5_position_id,
                    symbol = excluded.symbol, deal_type = excluded.deal_type,
                    entry_type = excluded.entry_type, volume = excluded.volume,
                    price = excluded.price, profit = excluded.profit,
                    swap = excluded.swap, commission = excluded.commission,
                    deal_time = excluded.deal_time,
                    deal_timestamp = excluded.deal_timestamp,
                    broker_utc_offset_seconds = excluded.broker_utc_offset_seconds,
                    comment = excluded.comment,
                    payload_json = excluded.payload_json
                    , position_attribution_json = excluded.position_attribution_json
                """,
                values,
            )
            stats["updated" if existing else "inserted"] += 1
        return stats

    def list_for_account(self, user_id: int, account_id: int, count: int = 100) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT * FROM live_trade_deals
            WHERE user_id = ? AND account_id = ?
            ORDER BY deal_timestamp DESC, received_at DESC, id DESC LIMIT ?
            """,
            (int(user_id), int(account_id), max(1, min(int(count), 100))),
        )
        items = []
        for row in rows:
            item = dict(row)
            payload = json.loads(item.get("payload_json") or "{}")
            item["deal_timestamp"] = int(
                item.get("deal_timestamp") or payload.get("deal_timestamp") or 0
            )
            item["time_utc"] = payload.get("time_utc")
            item["time_beijing"] = payload.get("time_beijing") or item.get("deal_time")
            item["broker_server_time"] = payload.get("time")
            item["broker_utc_offset_seconds"] = int(
                item.get("broker_utc_offset_seconds")
                or payload.get("broker_utc_offset_seconds")
                or 0
            )
            item["position_attribution"] = json.loads(
                item.get("position_attribution_json") or "{}"
            )
            items.append(item)
        return items

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

    @staticmethod
    def _mask_key(api_key: str) -> str:
        if not api_key:
            return ""
        return api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"

    def _user_role(self, user_id: int) -> str:
        row = self.storage.fetchone("SELECT role FROM users WHERE id = ?", (user_id,))
        return str(row["role"]) if row else ""

    def _base_user_config(self, user_id: int) -> "LLMConfig":
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

    def _active_provider_row(self, admin_user_id: int):
        return self.storage.fetchone(
            """
            SELECT provider_id, provider_name, api_key, api_base, model,
                   active, created_at, updated_at
            FROM llm_provider_configs
            WHERE admin_user_id = ? AND active = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (admin_user_id,),
        )

    def list_provider_configs(self, admin_user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT provider_id, provider_name, api_key, api_base, model,
                   active, created_at, updated_at
            FROM llm_provider_configs
            WHERE admin_user_id = ?
            ORDER BY active DESC, updated_at DESC, provider_name
            """,
            (admin_user_id,),
        )
        return [{
            "provider_id": row["provider_id"],
            "provider_name": row["provider_name"],
            "api_key": self._mask_key(row["api_key"]),
            "api_key_set": bool(row["api_key"]),
            "api_base": row["api_base"],
            "model": row["model"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        } for row in rows]

    def save_provider_config(
        self,
        admin_user_id: int,
        provider_id: Optional[str] = None,
        provider_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        active: bool = False,
    ) -> Dict:
        current = None
        if provider_id:
            current = self.storage.fetchone(
                """
                SELECT * FROM llm_provider_configs
                WHERE admin_user_id = ? AND provider_id = ?
                """,
                (admin_user_id, provider_id),
            )
            if current is None:
                raise ValueError("大模型供应商配置不存在")
        provider_id = provider_id or uuid.uuid4().hex[:12]
        next_name = str(
            provider_name
            if provider_name is not None
            else (current["provider_name"] if current else "默认供应商")
        ).strip()
        if not next_name:
            raise ValueError("供应商名称不能为空")
        next_api_key = (
            current["api_key"] if current and api_key is None else str(api_key or "")
        )
        next_api_base = str(
            api_base
            if api_base is not None
            else (current["api_base"] if current else "https://api.openai.com/v1")
        ).strip().rstrip("/")
        next_model = str(
            model if model is not None else (current["model"] if current else "gpt-4o-mini")
        ).strip()
        if not next_api_base:
            raise ValueError("API Base URL不能为空")
        if not next_model:
            raise ValueError("默认模型不能为空")
        if active and not next_api_key:
            raise ValueError("设为有效配置时 API Key 不能为空")
        previous_active = self._active_provider_row(admin_user_id) if active else None
        should_invalidate_models = bool(
            active
            and previous_active
            and (
                previous_active["provider_id"] != provider_id
                or previous_active["api_base"] != next_api_base
            )
        )
        now = _now_ts()
        with self.storage._lock, self.storage._connect() as conn:
            if active:
                conn.execute(
                    "UPDATE llm_provider_configs SET active = 0 WHERE admin_user_id = ?",
                    (admin_user_id,),
                )
            conn.execute(
                """
                INSERT INTO llm_provider_configs(
                    provider_id, admin_user_id, provider_name, api_key,
                    api_base, model, active, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    provider_name = excluded.provider_name,
                    api_key = excluded.api_key,
                    api_base = excluded.api_base,
                    model = excluded.model,
                    active = CASE
                        WHEN excluded.active = 1 THEN 1
                        ELSE llm_provider_configs.active
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    provider_id, admin_user_id, next_name, next_api_key,
                    next_api_base, next_model, int(active), now, now,
                ),
            )
            conn.commit()
        if active:
            self.save_config(
                admin_user_id,
                api_key=next_api_key,
                api_base=next_api_base,
                model=next_model,
            )
            if should_invalidate_models:
                self.storage.execute("UPDATE llm_models SET available = 0")
        return next(
            item for item in self.list_provider_configs(admin_user_id)
            if item["provider_id"] == provider_id
        )

    def set_active_provider(self, admin_user_id: int, provider_id: str) -> Dict:
        row = self.storage.fetchone(
            """
            SELECT * FROM llm_provider_configs
            WHERE admin_user_id = ? AND provider_id = ?
            """,
            (admin_user_id, provider_id),
        )
        if row is None:
            raise ValueError("大模型供应商配置不存在")
        if not row["api_key"]:
            raise ValueError("设为有效配置时 API Key 不能为空")
        previous_active = self._active_provider_row(admin_user_id)
        should_invalidate_models = bool(
            previous_active
            and (
                previous_active["provider_id"] != provider_id
                or previous_active["api_base"] != row["api_base"]
            )
        )
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                "UPDATE llm_provider_configs SET active = 0 WHERE admin_user_id = ?",
                (admin_user_id,),
            )
            conn.execute(
                """
                UPDATE llm_provider_configs
                SET active = 1, updated_at = ?
                WHERE admin_user_id = ? AND provider_id = ?
                """,
                (_now_ts(), admin_user_id, provider_id),
            )
            conn.commit()
        self.save_config(
            admin_user_id,
            api_key=row["api_key"],
            api_base=row["api_base"],
            model=row["model"],
        )
        if should_invalidate_models:
            self.storage.execute("UPDATE llm_models SET available = 0")
        return next(
            item for item in self.list_provider_configs(admin_user_id)
            if item["provider_id"] == provider_id
        )

    def get_config(self, user_id: int) -> "LLMConfig":
        from market.models.llm_config import LLMConfig

        base = self._base_user_config(user_id)
        if self._user_role(user_id) == "admin":
            provider = self._active_provider_row(user_id)
            if provider:
                return LLMConfig(
                    api_key=provider["api_key"],
                    api_base=provider["api_base"],
                    model=provider["model"],
                    system_prompt=base.system_prompt,
                    analysis_prompt_template=base.analysis_prompt_template,
                    prompt_version=base.prompt_version,
                )
            if base.enabled and not self.list_provider_configs(user_id):
                self.save_provider_config(
                    user_id,
                    provider_name="默认供应商",
                    api_key=base.api_key,
                    api_base=base.api_base,
                    model=base.model,
                    active=True,
                )
                provider = self._active_provider_row(user_id)
                if provider:
                    return LLMConfig(
                        api_key=provider["api_key"],
                        api_base=provider["api_base"],
                        model=provider["model"],
                        system_prompt=base.system_prompt,
                        analysis_prompt_template=base.analysis_prompt_template,
                        prompt_version=base.prompt_version,
                    )
        return base

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


class AITradeSuggestionRepository:
    """Durable, source-scoped AI trade plan history."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def _plan_fingerprint(suggestion: Dict) -> str:
        """Ignore explanatory wording so an unchanged price plan stays grouped."""
        return "|".join((
            str(suggestion.get("direction") or "").lower(),
            str(suggestion.get("period") or "").upper(),
            str(suggestion.get("setup_type") or "").lower(),
            str(suggestion.get("entry_mode") or "").lower(),
            f"{float(suggestion.get('entry_price') or 0):.8f}",
            f"{float(suggestion.get('stop_loss') or 0):.8f}",
            f"{float(suggestion.get('take_profit') or 0):.8f}",
        ))

    @staticmethod
    def _confidence(value) -> int:
        try:
            return max(0, min(100, int(float(value or 0))))
        except (TypeError, ValueError):
            return 0

    def record_many(
        self, user_id: int, symbol: str, suggestions: List[Dict],
        analysis_at: Optional[int] = None,
    ) -> None:
        """Store one analysis batch, coalescing plans repeated from the prior run."""
        now = _now_ts()
        analysis_at = int(analysis_at or _now_ts())
        for suggestion in suggestions or []:
            if not isinstance(suggestion, dict):
                continue
            source_id = str(suggestion.get("signal_source_id") or "").strip()
            if not source_id:
                continue
            try:
                entry = float(suggestion.get("entry_price") or 0)
                stop_loss = float(suggestion.get("stop_loss") or 0)
                take_profit = float(suggestion.get("take_profit") or 0)
            except (TypeError, ValueError):
                continue
            if min(entry, stop_loss, take_profit) <= 0:
                continue
            fingerprint = self._plan_fingerprint(suggestion)
            previous = self.storage.fetchone(
                """
                SELECT suggestion_id FROM ai_trade_suggestions
                WHERE user_id = ? AND signal_source_id = ? AND plan_fingerprint = ?
                  AND analysis_at = (
                    SELECT MAX(analysis_at) FROM ai_trade_suggestions
                    WHERE user_id = ? AND signal_source_id = ? AND analysis_at < ?
                  )
                ORDER BY updated_at DESC LIMIT 1
                """,
                (int(user_id), source_id, fingerprint, int(user_id), source_id, analysis_at),
            )
            if previous:
                self.storage.execute(
                    """
                    UPDATE ai_trade_suggestions
                    SET confidence = ?, reason = ?, analysis_at = ?, last_seen_at = ?,
                        suggestion_count = suggestion_count + 1, updated_at = ?
                    WHERE suggestion_id = ?
                    """,
                    (
                        self._confidence(suggestion.get("confidence")),
                        str(suggestion.get("reason") or "")[:4000], analysis_at,
                        analysis_at, now, previous["suggestion_id"],
                    ),
                )
                continue
            self.storage.execute(
                """
                INSERT INTO ai_trade_suggestions(
                    suggestion_id, user_id, signal_source_id, symbol, period,
                    plan_fingerprint, direction, confidence, entry_price, stop_loss,
                    take_profit, reason, analysis_at, last_seen_at, suggestion_count,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    uuid.uuid4().hex, int(user_id), source_id, str(symbol or ""),
                    str(suggestion.get("period") or "").upper(), fingerprint,
                    str(suggestion.get("direction") or "").lower(),
                    self._confidence(suggestion.get("confidence")),
                    entry, stop_loss, take_profit,
                    str(suggestion.get("reason") or "")[:4000], analysis_at,
                    analysis_at, now, now,
                ),
            )

    def list_recent(self, user_id: int, signal_source_id: str, limit: int = 10) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT suggestion_id, symbol, period, direction, confidence, entry_price,
                   stop_loss, take_profit, reason, analysis_at, last_seen_at,
                   suggestion_count, created_at
            FROM ai_trade_suggestions
            WHERE user_id = ? AND signal_source_id = ?
            ORDER BY last_seen_at DESC, created_at DESC
            LIMIT ?
            """,
            (int(user_id), str(signal_source_id), max(1, min(100, int(limit)))),
        )
        return [dict(row) for row in rows]


class AISignalSourceRepository:
    """Independent, reusable AI analysis sources owned by a user."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def _row_to_dict(row) -> Dict:
        if row is None:
            return {}
        config = json.loads(row["config_json"] or "{}")
        config.setdefault("signal_source_version", "1.0")
        config.setdefault("analysis_template", "custom")
        if config.get("signal_source_version") == "2.0":
            config.setdefault("adaptive_enabled", True)
            config.setdefault("adaptive_sample_size", 7)
        return {
            "signal_source_id": row["signal_source_id"],
            "user_id": int(row["user_id"]),
            "name": row["name"],
            "symbol": row["symbol"],
            "period": row["period"],
            "market_data_account_id": int(row["market_data_account_id"] or 0),
            "config": config,
            "enabled": bool(row["enabled"]),
            "share_runtime_data": bool(row["share_runtime_data"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def list(self, user_id: int, enabled_only: bool = False) -> List[Dict]:
        sql = "SELECT * FROM ai_signal_sources WHERE user_id = ?"
        params: List = [int(user_id)]
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at ASC, signal_source_id ASC"
        return [self._row_to_dict(row) for row in self.storage.fetchall(sql, tuple(params))]

    def list_visible(self, viewer_user_id: int) -> List[Dict]:
        """Return owned sources plus opt-in shared sources without private prompts."""
        rows = self.storage.fetchall(
            """
            SELECT source.*, users.username AS owner_username
            FROM ai_signal_sources AS source
            JOIN users ON users.id = source.user_id
            WHERE source.user_id = ?
               OR (source.user_id != ? AND source.enabled = 1
                   AND source.share_runtime_data = 1)
            ORDER BY source.user_id = ? DESC, source.created_at ASC, source.signal_source_id ASC
            """,
            (int(viewer_user_id), int(viewer_user_id), int(viewer_user_id)),
        )
        items = []
        for row in rows:
            item = self._row_to_dict(row)
            item["owner_username"] = row["owner_username"]
            item["is_owner"] = item["user_id"] == int(viewer_user_id)
            if not item["is_owner"]:
                # Shared sources are executable references, not prompt templates.
                item["config"].pop("system_prompt", None)
                item["config"].pop("analysis_prompt_template", None)
            items.append(item)
        return items

    def get(self, user_id: int, signal_source_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT * FROM ai_signal_sources WHERE user_id = ? AND signal_source_id = ?",
            (int(user_id), str(signal_source_id)),
        )
        return self._row_to_dict(row) if row else None

    def get_visible(
        self, viewer_user_id: int, signal_source_id: str,
        owner_user_id: Optional[int] = None,
    ) -> Optional[Dict]:
        """Resolve a source the viewer owns or an enabled source shared by its owner."""
        clauses = ["source.signal_source_id = ?"]
        params: List = [str(signal_source_id)]
        if owner_user_id is not None:
            clauses.append("source.user_id = ?")
            params.append(int(owner_user_id))
        clauses.append(
            "(source.user_id = ? OR (source.enabled = 1 AND source.share_runtime_data = 1))"
        )
        params.append(int(viewer_user_id))
        row = self.storage.fetchone(
            """
            SELECT source.*, users.username AS owner_username
            FROM ai_signal_sources AS source
            JOIN users ON users.id = source.user_id
            WHERE %s
            """ % " AND ".join(clauses),
            tuple(params),
        )
        if row is None:
            return None
        item = self._row_to_dict(row)
        item["owner_username"] = row["owner_username"]
        item["is_owner"] = item["user_id"] == int(viewer_user_id)
        return item

    def find_shared_for_symbol_period(
        self,
        viewer_user_id: int,
        symbol: str,
        period: str,
        broker_server: str = "",
    ) -> Optional[Dict]:
        """Find the best reusable shared AI source for a broker/symbol/period.

        Runtime sharing is intentionally matched against the source owner's
        latest MT5 server.  Prompts and model credentials are never returned
        to the caller through the shared-source payload.
        """
        target_symbol = str(symbol or "").strip().upper()
        target_period = str(period or "M5").strip().upper()
        target_broker = str(broker_server or "").strip().split("-", 1)[0].strip().lower()
        if not target_symbol or not target_period:
            return None
        rows = self.storage.fetchall(
            """
            SELECT source.*, users.username AS owner_username,
                   COALESCE(c.mt5_server, a.mt5_server, '') AS owner_mt5_server,
                   COALESCE(c.last_seen_at, a.last_seen_at, 0) AS owner_last_seen
            FROM ai_signal_sources AS source
            JOIN users ON users.id = source.user_id
            LEFT JOIN trading_accounts AS a
              ON a.user_id = source.user_id AND a.account_type = 'mt5'
            LEFT JOIN mt5_account_connections AS c ON c.account_id = a.id
            WHERE source.enabled = 1
              AND source.share_runtime_data = 1
              AND upper(trim(source.period)) = ?
            ORDER BY owner_last_seen DESC, source.updated_at DESC,
                     source.created_at ASC, source.signal_source_id ASC
            """,
            (target_period,),
        )
        mapped_fallback = None
        for row in rows:
            source_symbol = str(row["symbol"] or "").strip().upper()
            same_symbol = source_symbol == target_symbol
            owner_server = str(row["owner_mt5_server"] or "")
            owner_broker = owner_server.split("-", 1)[0].strip().lower()
            if same_symbol and target_broker and owner_broker == target_broker:
                item = self._row_to_dict(row)
                item["owner_username"] = row["owner_username"]
                item["owner_mt5_server"] = owner_server
                item["is_owner"] = int(row["user_id"]) == int(viewer_user_id)
                if not item["is_owner"]:
                    item["config"].pop("system_prompt", None)
                    item["config"].pop("analysis_prompt_template", None)
                    item["config"].pop("api_key", None)
                return item
            if target_broker and owner_server:
                try:
                    target_mapping = PlatformInstrumentMappingRepository(
                        self.storage
                    ).compatible(
                        owner_server, str(row["symbol"] or ""),
                        broker_server, symbol,
                    )
                except Exception:
                    target_mapping = False
                if target_mapping and mapped_fallback is None:
                    mapped_fallback = row
        # A matching symbol name from another broker is not safe to reuse:
        # the quote scale and liquidity can differ.  Only an explicit platform
        # mapping may bridge different broker-native symbols.
        selected = mapped_fallback
        if selected is None:
            return None
        item = self._row_to_dict(selected)
        item["owner_username"] = selected["owner_username"]
        item["owner_mt5_server"] = str(selected["owner_mt5_server"] or "")
        item["is_owner"] = int(selected["user_id"]) == int(viewer_user_id)
        if not item["is_owner"]:
            item["config"].pop("system_prompt", None)
            item["config"].pop("analysis_prompt_template", None)
            item["config"].pop("api_key", None)
        return item

    def create(self, user_id: int, data: Dict) -> Dict:
        now = _now_ts()
        source_id = str(data.get("signal_source_id") or uuid.uuid4().hex[:12])
        config = dict(data.get("config") or {})
        # New sources use the structured 2.0 protocol by default. Existing
        # records without this field remain 1.0 and keep their custom prompt.
        config.setdefault("signal_source_version", "2.0")
        config.setdefault("analysis_template", "auto_structure")
        if config.get("signal_source_version") == "2.0":
            config.setdefault("adaptive_enabled", True)
            config.setdefault("adaptive_sample_size", 7)
        self.storage.execute(
            """
            INSERT INTO ai_signal_sources(
                signal_source_id, user_id, name, symbol, period, market_data_account_id, config_json,
                enabled, share_runtime_data, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, int(user_id), str(data.get("name") or "AI 信号源").strip(),
                str(data.get("symbol") or "").strip(), str(data.get("period") or "M5").upper(),
                int(data.get("market_data_account_id") or 0),
                json.dumps(config, ensure_ascii=False),
                int(bool(data.get("enabled", True))),
                int(bool(data.get("share_runtime_data", False))), now, now,
            ),
        )
        return self.get(user_id, source_id) or {}

    def update(
        self, user_id: int, signal_source_id: str, data: Dict,
        allow_hot_reload: bool = False,
    ) -> Dict:
        current = self.get(user_id, signal_source_id)
        if current is None:
            raise ValueError("AI 信号源不存在")
        if self.is_locked(user_id, signal_source_id) and not allow_hot_reload:
            raise ValueError("该 AI 信号源已被引用或用于已部署策略，不能修改；请复制后创建新版本")
        merged = {**current, **data}
        self.storage.execute(
            """
            UPDATE ai_signal_sources
            SET name = ?, symbol = ?, period = ?, market_data_account_id = ?, config_json = ?, enabled = ?,
                share_runtime_data = ?, updated_at = ?
            WHERE user_id = ? AND signal_source_id = ?
            """,
            (
                str(merged.get("name") or "AI 信号源").strip(),
                str(merged.get("symbol") or "").strip(), str(merged.get("period") or "M5").upper(),
                int(merged.get("market_data_account_id") or 0),
                json.dumps(merged.get("config") or {}, ensure_ascii=False),
                int(bool(merged.get("enabled", True))), int(bool(merged.get("share_runtime_data", False))),
                _now_ts(), int(user_id), str(signal_source_id),
            ),
        )
        return self.get(user_id, signal_source_id) or {}

    def update_adaptive_config(
        self, user_id: int, signal_source_id: str, config: Dict,
    ) -> Dict:
        """Persist a bounded tuner update even when a deployed source is locked."""
        current = self.get(user_id, signal_source_id)
        if current is None:
            raise ValueError("AI 信号源不存在")
        self.storage.execute(
            "UPDATE ai_signal_sources SET config_json = ?, updated_at = ? "
            "WHERE user_id = ? AND signal_source_id = ?",
            (
                json.dumps(config or {}, ensure_ascii=False), _now_ts(),
                int(user_id), str(signal_source_id),
            ),
        )
        return self.get(user_id, signal_source_id) or {}

    def set_analysis_paused(
        self, user_id: int, signal_source_id: str, paused: bool,
    ) -> Dict:
        """Pause/resume analysis without changing the locked source config."""
        current = self.get(user_id, signal_source_id)
        if current is None:
            raise ValueError("AI 信号源不存在")
        config = dict(current.get("config") or {})
        config["analysis_paused"] = bool(paused)
        self.storage.execute(
            "UPDATE ai_signal_sources SET config_json = ?, updated_at = ? "
            "WHERE user_id = ? AND signal_source_id = ?",
            (json.dumps(config, ensure_ascii=False), _now_ts(), int(user_id), str(signal_source_id)),
        )
        return self.get(user_id, signal_source_id) or {}

    def deployment_impact(self, user_id: int, signal_source_id: str) -> List[Dict]:
        """List deployed strategies/users that consume a source directly or shared."""
        source = self.get(user_id, signal_source_id)
        if not source:
            return []
        source_id = str(signal_source_id)
        share_id = f"{int(source['user_id'])}:ai:{source_id}"
        rows = self.storage.fetchall(
            """
            SELECT d.deployment_id, d.strategy_id, d.execution_mode, d.status AS deployment_status,
                   d.account_id, a.account_name, u.id AS user_id, u.username,
                   JSON_UNQUOTE(JSON_EXTRACT(s.config_json, '$.strategy_name')) AS strategy_name
            FROM strategy_deployments d
            JOIN user_strategy_configs s ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            JOIN trading_accounts a ON a.id = d.account_id
            JOIN users u ON u.id = d.user_id
            WHERE d.status IN ('active', 'paused', 'completed')
              AND (
                JSON_SEARCH(s.config_json, 'one', ?, NULL, '$.signal_sources[*].signal_source_id') IS NOT NULL
                OR JSON_SEARCH(s.config_json, 'one', ?, NULL, '$.signal_sources[*].params.ai_signal_source_id') IS NOT NULL
                OR JSON_SEARCH(s.config_json, 'one', ?, NULL, '$.signal_sources[*].params.shared_runtime_id') IS NOT NULL
              )
            ORDER BY u.username, a.account_name, d.execution_mode
            """,
            (source_id, source_id, share_id),
        )
        return [dict(row) for row in rows]

    def copy(self, user_id: int, signal_source_id: str) -> Dict:
        source = self.get(user_id, signal_source_id)
        if source is None:
            raise ValueError("AI 信号源不存在")
        return self.create(user_id, {
            **source,
            "signal_source_id": "",
            "name": f"{source['name']}（副本）",
            "enabled": True,
            "share_runtime_data": False,
        })

    def delete(self, user_id: int, signal_source_id: str) -> None:
        if self.is_locked(user_id, signal_source_id):
            raise ValueError("该 AI 信号源已被引用或用于已部署策略，不能删除；请保留或复制新版本")
        self.storage.execute(
            "DELETE FROM ai_signal_sources WHERE user_id = ? AND signal_source_id = ?",
            (int(user_id), str(signal_source_id)),
        )

    def is_locked(self, user_id: int, signal_source_id: str) -> bool:
        # Draft strategies are editable and do not freeze a source. Freeze only
        # when a referenced strategy is actively deployed to paper or live.
        share_id = f"{int(user_id)}:ai:{signal_source_id}"
        referenced_source = self.storage.fetchone(
            """
            SELECT 1 FROM ai_signal_sources
            WHERE user_id != ?
              AND json_extract(config_json, '$.shared_runtime_id') = ?
            LIMIT 1
            """, (int(user_id), share_id),
        )
        if referenced_source:
            return True
        referenced = self.storage.fetchone(
            """
            SELECT 1 FROM strategy_deployments AS deployment
            JOIN user_strategy_configs AS strategy
              ON strategy.user_id = deployment.user_id
             AND strategy.strategy_id = deployment.strategy_id
            WHERE deployment.status = 'active'
              AND deployment.execution_mode IN ('paper', 'live')
              AND JSON_SEARCH(
                  strategy.config_json, 'one', ?, NULL,
                  '$.signal_sources[*].params.shared_runtime_id'
              ) IS NOT NULL
            LIMIT 1
            """, (share_id,),
        )
        if referenced:
            return True
        direct_reference = self.storage.fetchone(
            """
            SELECT 1 FROM strategy_deployments AS deployment
            JOIN user_strategy_configs AS strategy
              ON strategy.user_id = deployment.user_id
             AND strategy.strategy_id = deployment.strategy_id
            WHERE deployment.status = 'active'
              AND deployment.execution_mode IN ('paper', 'live')
              AND JSON_SEARCH(
                  strategy.config_json, 'one', ?, NULL,
                  '$.signal_sources[*].params.ai_signal_source_id'
              ) IS NOT NULL
            LIMIT 1
            """, (str(signal_source_id),),
        )
        return bool(direct_reference)

    def locked_ids(self, user_id: int, signal_source_ids: List[str]) -> set:
        """Return locked source IDs using bulk queries instead of one call per source."""
        source_ids = [str(item) for item in signal_source_ids if str(item)]
        if not source_ids:
            return set()
        locked = set()
        share_ids = [f"{int(user_id)}:ai:{source_id}" for source_id in source_ids]
        share_placeholders = ",".join("?" for _ in share_ids)
        rows = self.storage.fetchall(
            "SELECT json_extract(config_json, '$.shared_runtime_id') AS share_id "
            f"FROM ai_signal_sources WHERE user_id != ? AND json_extract(config_json, '$.shared_runtime_id') IN ({share_placeholders})",
            (int(user_id), *share_ids),
        )
        for row in rows:
            share_id = str(row["share_id"] or "")
            if share_id in share_ids:
                locked.add(share_id.rsplit(":ai:", 1)[-1])

        def search_locked(values: List[str], path: str) -> None:
            predicates = " OR ".join(
                "JSON_SEARCH(config_json, 'one', ?, NULL, " + repr(path) + ") IS NOT NULL"
                for _ in values
            )
            rows = self.storage.fetchall(
                f"""
                SELECT strategy.config_json FROM strategy_deployments AS deployment
                JOIN user_strategy_configs AS strategy
                  ON strategy.user_id = deployment.user_id
                 AND strategy.strategy_id = deployment.strategy_id
                WHERE deployment.status = 'active'
                  AND deployment.execution_mode IN ('paper', 'live')
                  AND ({predicates})
                """,
                tuple(values),
            )
            for row in rows:
                payload = str(row["config_json"] or "")
                for value in values:
                    if value in payload:
                        locked.add(value.rsplit(":ai:", 1)[-1] if ":ai:" in value else value)

        search_locked(share_ids, "$.signal_sources[*].params.shared_runtime_id")
        search_locked(source_ids, "$.signal_sources[*].params.ai_signal_source_id")
        return locked


class PlatformInstrumentMappingRepository:
    """Platform-managed relationships between broker-specific symbols."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def _normalize(value: str) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def broker_name_from_server(server: str) -> str:
        """Use the stable prefix of an MT5 server as the platform broker key."""
        value = str(server or "").strip()
        if not value:
            return ""
        return value.split("-", 1)[0].strip()

    def list(self, enabled_only: bool = False) -> List[Dict]:
        sql = """
            SELECT *, COALESCE(NULLIF(broker_name, ''), broker_server) AS effective_broker_name
            FROM platform_instrument_mappings
        """
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY mapping_group, broker_server, native_symbol"
        return [dict(row) for row in self.storage.fetchall(sql)]

    def save(self, data: Dict) -> Dict:
        broker_name = str(
            data.get("broker_name") or data.get("broker_server") or ""
        ).strip()
        native_symbol = self._normalize(data.get("native_symbol"))
        mapping_group = self._normalize(data.get("mapping_group"))
        display_name = str(data.get("display_name") or "").strip()
        if not broker_name or not native_symbol or not mapping_group:
            raise ValueError("交易商、品种和关联组均不能为空")
        if len(broker_name) > 120 or len(native_symbol) > 40 or len(mapping_group) > 80:
            raise ValueError("映射字段长度无效")
        mapping_id = str(data.get("mapping_id") or uuid.uuid4().hex[:16])
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO platform_instrument_mappings(
                mapping_id, broker_name, broker_server, native_symbol, mapping_group,
                display_name, enabled, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(broker_server, native_symbol) DO UPDATE SET
                broker_name = excluded.broker_name,
                mapping_group = excluded.mapping_group,
                display_name = excluded.display_name,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (
                mapping_id, broker_name, broker_name, native_symbol, mapping_group,
                display_name, int(bool(data.get("enabled", True))), now, now,
            ),
        )
        row = self.storage.fetchone(
            "SELECT *, COALESCE(NULLIF(broker_name, ''), broker_server) AS effective_broker_name FROM platform_instrument_mappings WHERE broker_server = ? AND native_symbol = ?",
            (broker_name, native_symbol),
        )
        return dict(row) if row else {}

    def delete(self, mapping_id: str) -> bool:
        row = self.storage.fetchone(
            "SELECT mapping_id FROM platform_instrument_mappings WHERE mapping_id = ?",
            (str(mapping_id),),
        )
        if not row:
            return False
        self.storage.execute(
            "DELETE FROM platform_instrument_mappings WHERE mapping_id = ?",
            (str(mapping_id),),
        )
        return True

    def source_server(self, user_id: int, symbol: str) -> str:
        """Best-effort source broker lookup for a shared strategy or AI result."""
        row = self.storage.fetchone(
            """
            SELECT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
            FROM trading_accounts AS a
            LEFT JOIN mt5_account_connections AS c ON c.account_id = a.id
            WHERE a.user_id = ? AND a.account_type = 'mt5'
              AND COALESCE(c.mt5_server, a.mt5_server, '') != ''
            ORDER BY COALESCE(c.last_seen_at, a.last_seen_at, 0) DESC, a.id DESC
            LIMIT 1
            """,
            (int(user_id),),
        )
        return str(row["mt5_server"] or "") if row else ""

    def compatible(self, source_server: str, source_symbol: str,
                   target_server: str, target_symbol: str) -> bool:
        source_symbol = self._normalize(source_symbol)
        target_symbol = self._normalize(target_symbol)
        if not source_symbol or not target_symbol:
            return False
        if source_symbol == target_symbol:
            return True
        source_broker = self.broker_name_from_server(source_server)
        target_broker = self.broker_name_from_server(target_server)
        if not source_broker or not target_broker:
            return False
        rows = self.storage.fetchall(
            """
            SELECT mapping_group FROM platform_instrument_mappings
            WHERE enabled = 1
              AND (COALESCE(NULLIF(broker_name, ''), broker_server) = ? AND native_symbol = ?
                   OR COALESCE(NULLIF(broker_name, ''), broker_server) = ? AND native_symbol = ?)
            """,
            (source_broker, source_symbol, target_broker, target_symbol),
        )
        return len({str(row["mapping_group"]) for row in rows}) == 1 and len(rows) == 2

    def target_options(self, source_owner_user_id: int, source_symbol: str,
                       target_user_id: int) -> List[Dict]:
        source_server = self.source_server(source_owner_user_id, source_symbol)
        accounts = self.storage.fetchall(
            """
            SELECT DISTINCT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
            FROM trading_accounts AS a
            LEFT JOIN mt5_account_connections AS c ON c.account_id = a.id
            WHERE a.user_id = ? AND a.account_type = 'mt5'
              AND a.status = 'active' AND a.enabled = 1
              AND COALESCE(c.mt5_server, a.mt5_server, '') != ''
            """,
            (int(target_user_id),),
        )
        options = [{
            "symbol": self._normalize(source_symbol), "broker_server": "",
            "label": f"{self._normalize(source_symbol)}（同名品种）",
        }]
        for account in accounts:
            target_server = str(account["mt5_server"] or "")
            mappings = self.storage.fetchall(
                "SELECT native_symbol FROM platform_instrument_mappings WHERE COALESCE(NULLIF(broker_name, ''), broker_server) = ? AND enabled = 1 ORDER BY native_symbol",
                (self.broker_name_from_server(target_server),),
            )
            for mapping in mappings:
                target_symbol = str(mapping["native_symbol"])
                if not self.compatible(source_server, source_symbol, target_server, target_symbol):
                    continue
                option = {
                    "symbol": target_symbol, "broker_server": target_server,
                    "label": f"{target_symbol} · {self.broker_name_from_server(target_server)}",
                }
                if option not in options:
                    options.append(option)
        return options

    def user_can_use_symbol(self, source_owner_user_id: int, source_symbol: str,
                            target_user_id: int, target_symbol: str) -> bool:
        if self._normalize(source_symbol) == self._normalize(target_symbol):
            return True
        source_server = self.source_server(source_owner_user_id, source_symbol)
        accounts = self.storage.fetchall(
            """
            SELECT DISTINCT COALESCE(c.mt5_server, a.mt5_server, '') AS mt5_server
            FROM trading_accounts AS a
            LEFT JOIN mt5_account_connections AS c ON c.account_id = a.id
            WHERE a.user_id = ? AND a.account_type = 'mt5'
              AND a.status = 'active' AND a.enabled = 1
            """,
            (int(target_user_id),),
        )
        return any(
            self.compatible(source_server, source_symbol, row["mt5_server"], target_symbol)
            for row in accounts
        )


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
        share_id = (
            f"{int(user_id)}:ai:{source_id}"
            if strategy_id == "__independent__"
            else f"{int(user_id)}:{strategy_id}:{source_id}"
        )
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
                str(model), json.dumps(
                    self.sanitize_signal_params(source.get("params") or {}),
                    ensure_ascii=False,
                ),
                "", "",
                str(strategy.get("strategy_name", "独立 AI 信号源")),
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
        ai_sources = AISignalSourceRepository(self.storage)
        shared_source_ids = set()
        for source in strategy.get("signal_sources") or []:
            if source.get("source") != "ai_entry":
                continue
            params = source.get("params") or {}
            source_id = str(
                source.get("signal_source_id")
                or params.get("ai_signal_source_id") or ""
            )
            managed_source = ai_sources.get(int(user_id), source_id)
            if managed_source and managed_source.get("share_runtime_data"):
                shared_source_ids.add(source_id)
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

    CONFIDENTIAL_PARAM_KEYS = {
        "system_prompt", "analysis_prompt_template", "prompt",
        "prompt_template", "custom_prompt", "user_prompt",
    }

    @classmethod
    def sanitize_signal_params(cls, params: Dict) -> Dict:
        """Remove prompt material before data crosses a sharing boundary."""
        return {
            key: value
            for key, value in dict(params or {}).items()
            if key not in cls.CONFIDENTIAL_PARAM_KEYS | {"share_runtime_data"}
        }

    @classmethod
    def _row_to_dict(cls, row, viewer_user_id: Optional[int]) -> Dict:
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
            "signal_params": cls.sanitize_signal_params(
                json.loads(row["signal_params_json"] or "{}")
            ),
            "result": json.loads(row["result_json"] or "{}"),
            "last_run_at": int(row["last_run_at"]),
            "updated_at": int(row["updated_at"]),
        }


class PositionManagementPolicyRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def _row_to_policy(row):
        from market.models import PositionManagementPolicy

        return PositionManagementPolicy.from_dict({
            "policy_id": row["policy_id"], "user_id": row["user_id"],
            "version": row["version"],
            "name": row["name"], "enabled": bool(row["enabled"]),
            "visibility": row["visibility"],
            "source_policy_id": row["source_policy_id"],
            "source_owner_user_id": row["source_owner_user_id"],
            "source_owner_username": row["source_owner_username"],
            "config": json.loads(row["config_json"]),
            "created_at": datetime.fromtimestamp(row["created_at"]),
            "updated_at": datetime.fromtimestamp(row["updated_at"]),
        })

    def _raw_get(self, user_id: int, policy_id: str):
        row = self.storage.fetchone(
            "SELECT * FROM position_management_policies WHERE user_id = ? AND policy_id = ?",
            (int(user_id), str(policy_id)),
        )
        return self._row_to_policy(row) if row else None

    def _invalid_reference(self, reference, reason: str):
        from market.models import default_position_management_config

        reference.enabled = False
        reference.visibility = "private"
        reference.config = default_position_management_config()
        reference.name = f"{reference.name}（来源已失效）"
        reference.updated_at = datetime.now()
        reference.config["_invalid_reference_reason"] = reason
        return reference

    def _resolve_reference(self, policy):
        if not policy or not policy.source_owner_user_id or not policy.source_policy_id:
            return policy
        source = self._raw_get(policy.source_owner_user_id, policy.source_policy_id)
        if source is None or source.visibility != "shared" or not source.enabled:
            return self._invalid_reference(policy, "共享持仓管理方案已停用或取消共享")
        resolved = source
        resolved.policy_id = policy.policy_id
        resolved.user_id = policy.user_id
        resolved.visibility = "private"
        resolved.source_policy_id = policy.source_policy_id
        resolved.source_owner_user_id = policy.source_owner_user_id
        resolved.source_owner_username = policy.source_owner_username
        resolved.created_at = policy.created_at
        return resolved

    def list(self, user_id: int, enabled_only: bool = False):
        sql = "SELECT * FROM position_management_policies WHERE user_id = ?"
        params = [int(user_id)]
        if enabled_only:
            sql += " AND enabled = 1"
        sql += " ORDER BY created_at, policy_id"
        policies = [
            self._resolve_reference(self._row_to_policy(row))
            for row in self.storage.fetchall(sql, tuple(params))
        ]
        return [item for item in policies if item and (not enabled_only or item.enabled)]

    def get(self, user_id: int, policy_id: str):
        return self._resolve_reference(self._raw_get(user_id, policy_id))

    def get_for_strategy(self, user_id: int, strategy):
        """Resolve the policy from the strategy publisher when it is shared.

        Shared strategies are lightweight references.  Their position policy is
        part of the published strategy definition and must not be copied into
        the recipient's policy library.
        """
        source_owner_id = int(
            getattr(strategy, "source_owner_user_id", 0)
            if not isinstance(strategy, dict)
            else strategy.get("source_owner_user_id", 0)
            or 0
        )
        source_strategy_id = str(
            getattr(strategy, "source_strategy_id", "")
            if not isinstance(strategy, dict)
            else strategy.get("source_strategy_id", "")
            or ""
        )
        policy_id = str(
            getattr(strategy, "position_management_policy_id", "")
            if not isinstance(strategy, dict)
            else strategy.get("position_management_policy_id", "")
            or ""
        )
        policy_owner_id = source_owner_id if source_owner_id and source_strategy_id else int(user_id)
        return self.get(policy_owner_id, policy_id)

    def save(self, policy):
        now = _now_ts()
        self.storage.execute(
            """
            INSERT INTO position_management_policies(
                policy_id, user_id, name, version, enabled, visibility,
                source_policy_id, source_owner_user_id, source_owner_username,
                config_json,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                name = excluded.name, version = excluded.version,
                enabled = excluded.enabled, visibility = excluded.visibility,
                source_policy_id = excluded.source_policy_id,
                source_owner_user_id = excluded.source_owner_user_id,
                source_owner_username = excluded.source_owner_username,
                config_json = excluded.config_json, updated_at = excluded.updated_at
            """,
            (policy.policy_id, policy.user_id, policy.name, policy.version,
             int(policy.enabled), policy.visibility, policy.source_policy_id,
             int(policy.source_owner_user_id), policy.source_owner_username,
             json.dumps(policy.config, ensure_ascii=False),
             int(policy.created_at.timestamp()), now),
        )
        policy.updated_at = datetime.fromtimestamp(now)
        return policy

    def list_shared(self, viewer_user_id: int):
        rows = self.storage.fetchall(
            """
            SELECT policy.*, users.username AS owner_username
            FROM position_management_policies AS policy
            JOIN users ON users.id = policy.user_id
            WHERE policy.visibility = 'shared' AND policy.enabled = 1
              AND policy.user_id != ?
            ORDER BY policy.updated_at DESC, policy.policy_id
            """,
            (int(viewer_user_id),),
        )
        items = []
        for row in rows:
            policy = self._row_to_policy(row).to_dict()
            policy.update({
                "owner_user_id": int(row["user_id"]),
                "owner_username": row["owner_username"],
                "usage_notice": "使用后将保持动态引用；原作者后续修改、停用或取消共享会同步影响你的策略。",
            })
            items.append(policy)
        return items

    def use_shared_policy(
        self, target_user_id: int, owner_user_id: int, policy_id: str,
    ):
        from market.models import PositionManagementPolicy

        source = self._raw_get(owner_user_id, policy_id)
        if source is None or source.visibility != "shared" or not source.enabled:
            return None
        existing = self.storage.fetchone(
            """
            SELECT policy_id FROM position_management_policies
            WHERE user_id = ? AND source_owner_user_id = ? AND source_policy_id = ?
            ORDER BY created_at, policy_id
            LIMIT 1
            """,
            (int(target_user_id), int(owner_user_id), str(policy_id)),
        )
        if existing:
            return self.get(int(target_user_id), existing["policy_id"])
        owner = UserRepository(self.storage).get_by_id(int(owner_user_id))
        now = datetime.now()
        reference = PositionManagementPolicy(
            user_id=int(target_user_id),
            name=source.name,
            enabled=True,
            config=source.config,
            visibility="private",
            source_policy_id=source.policy_id,
            source_owner_user_id=int(owner_user_id),
            source_owner_username=owner.username if owner else "",
            created_at=now,
            updated_at=now,
        )
        return self.save(reference)

    def copy_policy(self, user_id: int, policy_id: str, name_suffix: str = " 副本"):
        from market.models import PositionManagementPolicy

        source = self.get(user_id, policy_id)
        if source is None:
            return None
        now = datetime.now()
        copied = PositionManagementPolicy(
            user_id=int(user_id),
            name=f"{source.name}{name_suffix}",
            enabled=False,
            config=source.config,
            visibility="private",
            created_at=now,
            updated_at=now,
        )
        return self.save(copied)

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
            data["enabled"] = True
            data.pop("auto_execute", None)
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

    def list_policy_references(self, owner_user_id: int, policy_id: str) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT policy.policy_id, policy.user_id, policy.name, users.email, users.username
            FROM position_management_policies AS policy
            JOIN users ON users.id = policy.user_id
            WHERE policy.source_owner_user_id = ? AND policy.source_policy_id = ?
            """,
            (int(owner_user_id), str(policy_id)),
        )
        return [dict(row) for row in rows]

    def policy_reference_count(self, owner_user_id: int, policy_id: str) -> int:
        return len(self.list_policy_references(owner_user_id, policy_id))

    def policy_application_count(self, user_id: int, policy_id: str) -> int:
        row = self.storage.fetchone(
            """
            SELECT COUNT(*) AS count FROM user_strategy_configs
            WHERE user_id = ?
              AND json_extract(config_json, '$.position_management_policy_id') = ?
            """,
            (int(user_id), str(policy_id)),
        )
        return self.policy_reference_count(user_id, policy_id) + (
            int(row["count"]) if row else 0
        )

    def active_deployment_count(self, user_id: int, policy_id: str) -> int:
        """统计仍处于 active 状态的策略部署引用该持仓方案的数量。

        模拟/实盘部署直接引用已冻结的策略配置；部署快照仅用于审计。
        因此修改/删除前必须以当前策略绑定关系判断，避免快照和运行配置
        不一致而遗漏活动部署。
        """
        row = self.storage.fetchone(
            """
            SELECT COUNT(*) AS count FROM strategy_deployments
            JOIN user_strategy_configs
              ON user_strategy_configs.user_id = strategy_deployments.user_id
             AND user_strategy_configs.strategy_id = strategy_deployments.strategy_id
            WHERE strategy_deployments.user_id = ?
              AND strategy_deployments.status = 'active'
              AND json_extract(user_strategy_configs.config_json,
                               '$.position_management_policy_id') = ?
            """,
            (int(user_id), str(policy_id)),
        )
        return int(row["count"]) if row else 0


class PositionManagementEventRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def record(
        self, user_id: int, account_id: int, position_key: str,
        event_type: str, message: str, *, symbol: str = "",
        position_id: str = "", ticket: Optional[int] = None,
        rule_type: str = "", status: str = "", price: float = 0,
        stop_loss: float = 0, take_profit: float = 0, volume: float = 0,
        payload: Optional[Dict] = None, event_time: Optional[int] = None,
    ) -> Dict:
        now = _now_ts()
        event = {
            "event_id": uuid.uuid4().hex,
            "user_id": int(user_id),
            "account_id": int(account_id),
            "position_key": str(position_key),
            "position_id": str(position_id or ""),
            "ticket": int(ticket) if ticket is not None else None,
            "symbol": str(symbol or ""),
            "event_time": int(event_time or now),
            "event_type": str(event_type or ""),
            "rule_type": str(rule_type or ""),
            "status": str(status or ""),
            "message": str(message or ""),
            "price": float(price or 0),
            "stop_loss": float(stop_loss or 0),
            "take_profit": float(take_profit or 0),
            "volume": float(volume or 0),
            "payload": payload or {},
            "created_at": now,
        }
        self.storage.execute(
            """
            INSERT INTO position_management_events(
                event_id, user_id, account_id, position_key, position_id, ticket,
                symbol, event_time, event_type, rule_type, status, message,
                price, stop_loss, take_profit, volume, payload_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_id"], event["user_id"], event["account_id"],
                event["position_key"], event["position_id"], event["ticket"],
                event["symbol"], event["event_time"], event["event_type"],
                event["rule_type"], event["status"], event["message"],
                event["price"], event["stop_loss"], event["take_profit"],
                event["volume"], json.dumps(event["payload"], ensure_ascii=False),
                event["created_at"],
            ),
        )
        return event

    def list_for_position(
        self, user_id: int, account_id: int, position_key: str,
        limit: int = 100,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT * FROM position_management_events
            WHERE user_id = ? AND account_id = ? AND position_key = ?
            ORDER BY event_time, created_at LIMIT ?
            """,
            (int(user_id), int(account_id), str(position_key), int(limit)),
        )
        return [self._row_to_dict(row) for row in rows]

    def list_for_account(
        self, user_id: int, account_id: int, symbol: str = "", limit: int = 200,
    ) -> List[Dict]:
        """Return recent management events for an account, optionally scoped to a symbol."""
        clauses = ["user_id = ?", "account_id = ?"]
        params = [int(user_id), int(account_id)]
        if symbol:
            clauses.append("symbol = ?")
            params.append(str(symbol))
        params.append(max(1, min(int(limit), 500)))
        rows = self.storage.fetchall(
            f"SELECT * FROM position_management_events WHERE {' AND '.join(clauses)} "
            "ORDER BY event_time DESC, created_at DESC LIMIT ?",
            params,
        )
        return [self._row_to_dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row) -> Dict:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json") or "{}")
        return data


class StrategyConfigRepository:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def _raw_strategy_by_id(
        self, user_id: int, strategy_id: str
    ) -> Optional["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        row = self.storage.fetchone(
            """
            SELECT config_json
            FROM user_strategy_configs
            WHERE user_id = ? AND strategy_id = ?
            """,
            (int(user_id), str(strategy_id)),
        )
        if not row:
            return None
        return TradingStrategy.from_dict(json.loads(row["config_json"]))

    def _invalid_strategy_reference(self, reference: "TradingStrategy", reason: str):
        now = datetime.now()
        # Materialized references can later be persisted by a strategy store.
        # Normalize an existing marker first so repeated reloads never keep
        # appending the same suffix to the recipient-visible strategy name.
        markers = ("（来源已失效）", "（AI运行数据未共享）")
        base_name = str(reference.strategy_name or "").strip()
        for marker in markers:
            base_name = base_name.replace(marker, "")
        marker = (
            "（AI运行数据未共享）"
            if "AI 信号源" in reason else "（来源已失效）"
        )
        reference.enabled = True
        reference.lifecycle_status = "draft"
        reference.lifecycle_updated_at = now
        reference.strategy_name = f"{base_name}{marker}"
        reference.updated_at = now
        reference.lifecycle_history.append({
            "from_status": "reference",
            "to_status": "draft",
            "changed_at": now.isoformat(),
            "reason": reason,
        })
        return reference

    def _materialize_shared_reference(
        self, reference: Optional["TradingStrategy"]
    ) -> Optional["TradingStrategy"]:
        if reference is None:
            return None
        if not reference.source_owner_user_id or not reference.source_strategy_id:
            return reference
        source = self._raw_strategy_by_id(
            reference.source_owner_user_id, reference.source_strategy_id
        )
        if source is None or source.visibility != "shared":
            return self._invalid_strategy_reference(
                reference, "共享策略已删除、停用或取消共享"
            )
        payload = self._sanitize_shared_strategy(source.to_dict())
        ai_signal_sources = AISignalSourceRepository(self.storage)
        signal_sources = []
        for signal_source in payload.get("signal_sources") or []:
            item = dict(signal_source)
            params = dict(item.get("params") or {})
            if item.get("source") == "ai_entry":
                source_id = str(
                    item.get("signal_source_id")
                    or params.get("ai_signal_source_id") or ""
                )
                managed_source = ai_signal_sources.get(
                    int(reference.source_owner_user_id), source_id
                ) if source_id else None
                # Runtime sharing is owned by the standalone AI source.  The
                # strategy JSON only holds a historical binding and may carry
                # an old share_runtime_data value.
                if not managed_source or not managed_source.get("share_runtime_data"):
                    return self._invalid_strategy_reference(
                        reference, "共享策略包含未开放运行数据的 AI 信号源"
                    )
                params = {
                    "analysis_mode": "shared_reference",
                    "shared_runtime_id": (
                        f"{int(reference.source_owner_user_id)}:"
                        f"{source.strategy_id}:{source_id}"
                    ),
                    "min_confidence": params.get("min_confidence", 70),
                    "entry_threshold": params.get("entry_threshold", 0.0008),
                    "reference_runtime_ids": [],
                }
            item["params"] = params
            signal_sources.append(item)
        payload.update({
            "strategy_id": reference.strategy_id,
            # Keep the recipient's broker-native symbol while inheriting the
            # publisher's strategy definition.
            "symbol": reference.symbol or source.symbol,
            "visibility": "private",
            "is_shared": False,
            # The management policy belongs to the publisher along with the
            # shared strategy.  Do not retain a recipient-local policy ID.
            "position_management_policy_id": source.position_management_policy_id,
            "source_strategy_id": source.strategy_id,
            "source_owner_user_id": int(reference.source_owner_user_id),
            "source_owner_username": reference.source_owner_username,
            "created_at": reference.created_at.isoformat(),
            "updated_at": source.updated_at.isoformat(),
        })
        payload["signal_sources"] = signal_sources
        from market.models.trading_strategy import TradingStrategy
        return TradingStrategy.from_dict(payload)

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
                self._materialize_shared_reference(
                    TradingStrategy.from_dict(json.loads(row["config_json"]))
                )
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

    def list_admin_strategies(self) -> List[Dict]:
        from market.models.trading_strategy import TradingStrategy

        rows = self.storage.fetchall(
            """
            SELECT strategy.user_id, users.username, users.email, users.role,
                   users.membership_level, users.live_trading_enabled,
                   strategy.strategy_id, strategy.symbol, strategy.config_json,
                   strategy.created_at, strategy.updated_at
            FROM user_strategy_configs AS strategy
            JOIN users ON users.id = strategy.user_id
            ORDER BY strategy.updated_at DESC, strategy.created_at DESC
            """
        )
        items = []
        for row in rows:
            strategy = self._materialize_shared_reference(
                TradingStrategy.from_dict(json.loads(row["config_json"]))
            )
            payload = strategy.to_dict()
            payload.update({
                "user_id": int(row["user_id"]),
                "username": row["username"],
                "email": row["email"],
                "user_role": row["role"],
                "membership_level": row["membership_level"],
                "live_trading_enabled": bool(row["live_trading_enabled"]),
            })
            items.append(payload)
        return items

    def get_strategy(self, user_id: int, symbol: str) -> Optional["TradingStrategy"]:
        """兼容旧调用，返回该品种创建最早的策略。"""
        strategies = self.get_strategies(user_id, symbol)
        return strategies[0] if strategies else None

    def get_strategy_by_id(
        self, user_id: int, strategy_id: str
    ) -> Optional["TradingStrategy"]:
        return self._materialize_shared_reference(
            self._raw_strategy_by_id(user_id, strategy_id)
        )

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
        return [
            self._materialize_shared_reference(
                TradingStrategy.from_dict(json.loads(row["config_json"]))
            )
            for row in rows
        ]

    def save_strategy(self, user_id: int, strategy: "TradingStrategy") -> "TradingStrategy":
        if strategy.visibility == "shared":
            self._enable_runtime_sharing_for_strategy(user_id, strategy)
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

    def _enable_runtime_sharing_for_strategy(
        self, user_id: int, strategy: "TradingStrategy"
    ) -> None:
        """Publish runtime output for AI sources bound to a shared strategy.

        Only generated runtime results become visible; prompts, models and
        provider credentials remain protected by the signal-source API.
        """
        source_ids = {
            str(item.get("signal_source_id") or (item.get("params") or {}).get(
                "ai_signal_source_id", ""
            )).strip()
            for item in strategy.signal_sources or []
            if item.get("source") == "ai_entry"
        }
        source_ids.discard("")
        if not source_ids:
            return
        now = _now_ts()
        for source_id in source_ids:
            self.storage.execute(
                "UPDATE ai_signal_sources SET share_runtime_data = 1, updated_at = ? "
                "WHERE user_id = ? AND signal_source_id = ?",
                (now, int(user_id), source_id),
            )

    def cleanup_legacy_ai_share_runtime_flags(self) -> int:
        """Remove retired per-strategy sharing flags from persisted JSON."""
        rows = self.storage.fetchall(
            "SELECT user_id, strategy_id, config_json FROM user_strategy_configs"
        )
        changed = 0
        for row in rows:
            try:
                payload = json.loads(row["config_json"] or "{}")
            except (TypeError, ValueError):
                continue
            dirty = False
            for source in payload.get("signal_sources") or []:
                params = source.get("params")
                if isinstance(params, dict) and "share_runtime_data" in params:
                    params.pop("share_runtime_data", None)
                    dirty = True
            if not dirty:
                continue
            self.storage.execute(
                "UPDATE user_strategy_configs SET config_json = ?, updated_at = ? "
                "WHERE user_id = ? AND strategy_id = ?",
                (json.dumps(payload, ensure_ascii=False), _now_ts(),
                 int(row["user_id"]), str(row["strategy_id"])),
            )
            changed += 1
        return changed

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
            item = self._sanitize_shared_strategy(strategy.to_dict())
            for signal_source in item.get("signal_sources") or []:
                signal_source["params"] = {}
            item.update({
                "owner_user_id": owner_user_id,
                "owner_username": row["username"],
            })
            shared.append(item)
        return shared

    @staticmethod
    def _sanitize_shared_strategy(payload: Dict) -> Dict:
        sanitized = dict(payload)
        sanitized["signal_sources"] = []
        for source in payload.get("signal_sources") or []:
            item = dict(source)
            item["params"] = SharedAIRuntimeRepository.sanitize_signal_params(
                source.get("params") or {}
            )
            sanitized["signal_sources"].append(item)
        return sanitized

    def use_shared_strategy(
        self,
        target_user_id: int,
        owner_user_id: int,
        strategy_id: str,
        target_symbol: str = "",
    ) -> Optional["TradingStrategy"]:
        from market.models.trading_strategy import TradingStrategy

        source = self._raw_strategy_by_id(int(owner_user_id), strategy_id)
        if source is None or source.visibility != "shared":
            return None
        existing = self.storage.fetchone(
            """
            SELECT strategy_id
            FROM user_strategy_configs
            WHERE user_id = ?
              AND json_extract(config_json, '$.source_owner_user_id') = ?
              AND json_extract(config_json, '$.source_strategy_id') = ?
            ORDER BY created_at, strategy_id
            LIMIT 1
            """,
            (int(target_user_id), int(owner_user_id), str(strategy_id)),
        )
        if existing:
            return self.get_strategy_by_id(
                int(target_user_id), existing["strategy_id"]
            )

        owner = UserRepository(self.storage).get_by_id(int(owner_user_id))
        now = datetime.now()
        payload = {
            "strategy_id": "",
            "strategy_name": source.strategy_name,
            "symbol": str(target_symbol or source.symbol).strip(),
            "visibility": "private",
            "is_shared": False,
            "signal_sources": [],
            "enabled": True,
            "lifecycle_status": "draft",
            "lifecycle_updated_at": now.isoformat(),
            # Store the source policy ID only.  Runtime resolution uses the
            # source owner ID below, so no local policy reference is created.
            "position_management_policy_id": source.position_management_policy_id,
            "source_strategy_id": source.strategy_id,
            "source_owner_user_id": int(owner_user_id),
            "source_owner_username": owner.username if owner else "",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        reference = TradingStrategy.from_dict(payload)
        self.save_strategy(int(target_user_id), reference)
        return self.get_strategy_by_id(int(target_user_id), reference.strategy_id)

    def list_strategy_references(self, owner_user_id: int, strategy_id: str) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT strategy.user_id, strategy.strategy_id, users.email, users.username
            FROM user_strategy_configs AS strategy
            JOIN users ON users.id = strategy.user_id
            WHERE json_extract(strategy.config_json, '$.source_owner_user_id') = ?
              AND json_extract(strategy.config_json, '$.source_strategy_id') = ?
            """,
            (int(owner_user_id), str(strategy_id)),
        )
        return [dict(row) for row in rows]

    def strategy_reference_count(self, owner_user_id: int, strategy_id: str) -> int:
        return len(self.list_strategy_references(owner_user_id, strategy_id))

    def strategy_application_count(self, user_id: int, strategy_id: str) -> int:
        row = self.storage.fetchone(
            """
            SELECT
              (SELECT COUNT(*) FROM strategy_deployments
               WHERE user_id = ? AND strategy_id = ?
                 AND status IN ('active', 'paused', 'pending')) AS deployment_count,
              (SELECT COUNT(*) FROM backtest_templates
               WHERE user_id = ? AND strategy_id = ?) AS template_count,
              (SELECT COUNT(*) FROM backtest_batches
               WHERE user_id = ? AND strategy_id = ?) AS batch_count
            """,
            (
                int(user_id), str(strategy_id),
                int(user_id), str(strategy_id),
                int(user_id), str(strategy_id),
            ),
        )
        if row is None:
            return 0
        return (
            self.strategy_reference_count(user_id, strategy_id)
            + int(row["deployment_count"])
            + int(row["template_count"])
            + int(row["batch_count"])
        )

    def strategy_deployment_count(self, user_id: int, strategy_id: str) -> int:
        row = self.storage.fetchone(
            """
            SELECT COUNT(*) AS count
            FROM strategy_deployments
            WHERE user_id = ? AND strategy_id = ?
              AND status IN ('active', 'paused', 'pending')
            """,
            (int(user_id), str(strategy_id)),
        )
        return int(row["count"]) if row else 0

    def copy_strategy(
        self, user_id: int, strategy: "TradingStrategy", name_suffix: str = " 副本",
    ) -> "TradingStrategy":
        from market.models.trading_strategy import TradingStrategy

        payload = self._sanitize_shared_strategy(strategy.to_dict())
        payload.update({
            "strategy_id": "",
            "strategy_name": f"{strategy.strategy_name}{name_suffix}",
            "visibility": "private",
            "is_shared": False,
            "enabled": True,
            "lifecycle_status": "draft",
            "source_strategy_id": "",
            "source_owner_user_id": 0,
            "source_owner_username": "",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "lifecycle_updated_at": datetime.now().isoformat(),
            "lifecycle_history": [],
        })
        copied = TradingStrategy.from_dict(payload)
        return self.save_strategy(int(user_id), copied)

    def list_alpha_references(self, alpha_id: str) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT strategy.user_id, strategy.strategy_id, users.email, users.username
            FROM user_strategy_configs AS strategy
            JOIN users ON users.id = strategy.user_id
            WHERE strategy.config_json LIKE ?
            """,
            (f'%"{str(alpha_id)}"%',),
        )
        matches = []
        for row in rows:
            data = json.loads(row["config_json"])
            for source in data.get("signal_sources") or []:
                params = source.get("params") or {}
                if source.get("source") == "alpha_factor" and params.get("alpha_id") == alpha_id:
                    matches.append(dict(row))
                    break
        return matches

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
        limit: Optional[int] = None,
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
        if limit is not None:
            sql += " ORDER BY created_at DESC, entity_id DESC LIMIT ?"
            params.append(max(1, int(limit)))
        else:
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
            DELETE target FROM runtime_entities AS target
            JOIN (
                SELECT entity_id FROM (
                    SELECT entity_id
                    FROM runtime_entities
                    WHERE user_id = ? AND account_id = ? AND entity_type = ?
                    ORDER BY created_at DESC, updated_at DESC, entity_id DESC
                    LIMIT 18446744073709551615 OFFSET ?
                ) AS overflow_rows
            ) AS obsolete ON obsolete.entity_id = target.entity_id
            WHERE target.user_id = ? AND target.account_id = ? AND target.entity_type = ?
            """,
            (
                self.user_id, self.account_id, entity_type, max(0, int(max_count)),
                self.user_id, self.account_id, entity_type,
            ),
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
