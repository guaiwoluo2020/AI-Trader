#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 多用户配置测试
"""

import os
import json
import sqlite3
import tempfile
import unittest

from market.models import StrategyLifecycle, TradingStrategy
from market.store.llm_store import LLMStore
from sqlite_storage import (
    LLMAccessRepository,
    LLMConfigRepository,
    StrategyConfigRepository,
    SQLiteStorage,
    TradeConfigRepository,
    UserRepository,
    get_storage,
    reset_storage,
)


class SQLiteUserConfigIsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = os.path.join(self.temp_dir.name, "ai-trader.db")
        os.environ["AI_TRADER_DB_FILE"] = self.db_file
        reset_storage()

        self.storage = get_storage()
        self.user_repo = UserRepository(self.storage)
        self.trade_repo = TradeConfigRepository(self.storage)
        self.llm_repo = LLMConfigRepository(self.storage)
        self.llm_access_repo = LLMAccessRepository(self.storage)
        self.strategy_repo = StrategyConfigRepository(self.storage)

        self.user_a = self.user_repo.create_user("alice", "hash-a", "salt-a")
        self.user_b = self.user_repo.create_user("bob", "hash-b", "salt-b")

    def tearDown(self):
        reset_storage()
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    def test_trade_configs_are_isolated_per_user(self):
        config_a = self.trade_repo.get_config(self.user_a.user_id)
        config_b = self.trade_repo.get_config(self.user_b.user_id)

        config_a["default_volume"] = 0.11
        self.trade_repo.save_config(self.user_a.user_id, config_a)

        reloaded_a = self.trade_repo.get_config(self.user_a.user_id)
        reloaded_b = self.trade_repo.get_config(self.user_b.user_id)

        self.assertEqual(reloaded_a["default_volume"], 0.11)
        self.assertEqual(reloaded_b["default_volume"], config_b["default_volume"])

    def test_llm_configs_are_isolated_per_user(self):
        self.llm_repo.save_config(
            self.user_a.user_id,
            api_key="sk-user-a",
            api_base="https://example-a.test/v1",
            model="model-a",
        )
        config_a = self.llm_repo.get_config(self.user_a.user_id)
        config_b = self.llm_repo.get_config(self.user_b.user_id)

        self.assertEqual(config_a.api_key, "sk-user-a")
        self.assertEqual(config_a.model, "model-a")
        self.assertEqual(config_b.api_key, "")
        self.assertNotEqual(config_a.api_base, config_b.api_base)

    def test_llm_prompt_configuration_is_versioned_and_validated(self):
        initial = self.llm_repo.get_config(self.user_a.user_id)
        customized = self.llm_repo.save_config(
            self.user_a.user_id,
            system_prompt="你是严格的黄金分析师",
            analysis_prompt_template=(
                "策略={{strategy_context}}\n行情={{market_data}}\n只输出JSON"
            ),
        )

        self.assertEqual(customized.prompt_version, initial.prompt_version + 1)
        self.assertEqual(customized.system_prompt, "你是严格的黄金分析师")
        with self.assertRaisesRegex(ValueError, "market_data"):
            self.llm_repo.save_config(
                self.user_a.user_id,
                analysis_prompt_template="仅有{{strategy_context}}",
            )

        restored = self.llm_repo.reset_prompts(self.user_a.user_id)
        self.assertGreater(restored.prompt_version, customized.prompt_version)
        self.assertIn("{{market_data}}", restored.analysis_prompt_template)

    def test_approved_user_uses_shared_admin_llm_config(self):
        admin = self.user_repo.create_user(
            "admin", "hash-admin", "salt-admin", role="admin"
        )
        self.llm_repo.save_config(
            admin.user_id,
            api_key="sk-shared-admin",
            api_base="https://shared.example/v1",
            model="shared-model",
        )
        user_store = LLMStore(user_id=self.user_a.user_id)

        self.assertFalse(user_store.get_config().enabled)
        requested = self.llm_access_repo.request_access(self.user_a.user_id)
        self.assertEqual(requested["status"], "pending")
        self.assertFalse(user_store.get_config().enabled)

        reviewed = self.llm_access_repo.review(
            requested["request_id"], admin.user_id, "approved"
        )

        self.assertTrue(reviewed["access_granted"])
        effective = user_store.get_config()
        self.assertEqual(effective.api_key, "sk-shared-admin")
        self.assertEqual(effective.model, "shared-model")

    def test_rejected_user_can_apply_again(self):
        admin = self.user_repo.create_user(
            "admin", "hash-admin", "salt-admin", role="admin"
        )
        requested = self.llm_access_repo.request_access(self.user_b.user_id)
        self.llm_access_repo.review(
            requested["request_id"], admin.user_id, "rejected", "暂不开放"
        )

        rejected = self.llm_access_repo.get_status(self.user_b.user_id)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["review_note"], "暂不开放")

        reapplied = self.llm_access_repo.request_access(self.user_b.user_id)
        self.assertEqual(reapplied["status"], "pending")
        self.assertEqual(reapplied["review_note"], "")

    def test_strategy_configs_are_isolated_per_user(self):
        strategy_a = TradingStrategy(symbol="GOLD#", strategy_name="AliceGold")
        strategy_b = TradingStrategy(symbol="GOLD#", strategy_name="BobGold")

        self.strategy_repo.save_strategy(self.user_a.user_id, strategy_a)
        self.strategy_repo.save_strategy(self.user_b.user_id, strategy_b)

        alice_strategy = self.strategy_repo.get_strategy(self.user_a.user_id, "GOLD#")
        bob_strategy = self.strategy_repo.get_strategy(self.user_b.user_id, "GOLD#")

        self.assertEqual(alice_strategy.strategy_name, "AliceGold")
        self.assertEqual(bob_strategy.strategy_name, "BobGold")

    def test_same_user_can_store_multiple_strategies_for_symbol(self):
        first = TradingStrategy(symbol="GOLD#", strategy_name="TrendGold")
        second = TradingStrategy(symbol="GOLD#", strategy_name="BreakoutGold")

        self.strategy_repo.save_strategy(self.user_a.user_id, first)
        self.strategy_repo.save_strategy(self.user_a.user_id, second)

        strategies = self.strategy_repo.get_strategies(
            self.user_a.user_id, "GOLD#"
        )
        self.assertEqual(len(strategies), 2)
        self.assertEqual(
            {strategy.strategy_name for strategy in strategies},
            {"TrendGold", "BreakoutGold"},
        )

    def test_disabled_pivot_signal_is_persisted(self):
        strategy = TradingStrategy(symbol="GOLD#", strategy_name="PivotGold")
        strategy.signal_config["pivot"]["enabled"] = False

        self.strategy_repo.save_strategy(self.user_a.user_id, strategy)

        reloaded = self.strategy_repo.get_strategy_by_id(
            self.user_a.user_id, strategy.strategy_id
        )
        self.assertIsNotNone(reloaded)
        self.assertFalse(reloaded.signal_config["pivot"]["enabled"])

    def test_strategy_lifecycle_is_persisted(self):
        strategy = TradingStrategy(
            symbol="GOLD#",
            strategy_name="LifecycleGold",
            enabled=False,
            lifecycle_status=StrategyLifecycle.DRAFT,
        )
        strategy.transition_lifecycle(StrategyLifecycle.BACKTESTING)
        self.strategy_repo.save_strategy(self.user_a.user_id, strategy)

        reloaded = self.strategy_repo.get_strategy_by_id(
            self.user_a.user_id, strategy.strategy_id
        )

        self.assertEqual(
            reloaded.lifecycle_status, StrategyLifecycle.BACKTESTING
        )
        self.assertEqual(len(reloaded.lifecycle_history), 1)

    def test_strategy_without_lifecycle_keeps_legacy_production_status(self):
        strategy = TradingStrategy(symbol="GOLD#")
        payload = strategy.to_dict()
        payload.pop("lifecycle_status")
        payload.pop("lifecycle_label")
        payload.pop("lifecycle_updated_at")
        payload.pop("lifecycle_history")

        restored = TradingStrategy.from_dict(payload)

        self.assertEqual(
            restored.lifecycle_status, StrategyLifecycle.PRODUCTION
        )
        self.assertTrue(restored.is_runnable())

    def test_legacy_symbol_primary_key_is_migrated(self):
        legacy_db = os.path.join(self.temp_dir.name, "legacy.db")
        strategy = TradingStrategy(symbol="GOLD#", strategy_name="LegacyGold")
        with sqlite3.connect(legacy_db) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE user_strategy_configs (
                    user_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(user_id, symbol)
                );
                """
            )
            conn.execute(
                "INSERT INTO users VALUES(1, 'legacy', 'hash', 'salt', 1, 1)"
            )
            conn.execute(
                "INSERT INTO user_strategy_configs VALUES(?, ?, ?, ?, ?)",
                (1, "GOLD#", json.dumps(strategy.to_dict()), 1, 1),
            )

        storage = SQLiteStorage(legacy_db)
        repository = StrategyConfigRepository(storage)
        migrated = repository.get_strategies(1, "GOLD#")

        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated[0].strategy_id, strategy.strategy_id)
        columns = {
            row["name"]
            for row in storage.fetchall("PRAGMA table_info(user_strategy_configs)")
        }
        self.assertIn("strategy_id", columns)


if __name__ == "__main__":
    unittest.main()
