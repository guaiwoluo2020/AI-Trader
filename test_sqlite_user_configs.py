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
from datetime import datetime, timedelta

from market.models import (
    PositionManagementPolicy, StrategyLifecycle, TradingStrategy,
)
from market.models.trading_strategy import signal_source_defaults
from market.store.llm_store import LLMStore
from sqlite_storage import (
    LLMAccessRepository,
    LLMConfigRepository,
    PositionManagementPolicyRepository,
    SharedAIRuntimeRepository,
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
        self.shared_ai_runtime_repo = SharedAIRuntimeRepository(self.storage)
        self.strategy_repo = StrategyConfigRepository(self.storage)
        self.position_policy_repo = PositionManagementPolicyRepository(self.storage)

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

    def test_ai_signal_model_and_prompt_are_validated(self):
        source = signal_source_defaults("ai_entry", "M5")
        source["params"].update({
            "model": "qwen3.8-max",
            "system_prompt": "你是短线交易分析师",
            "analysis_prompt_template": (
                "策略={{strategy_context}}\n行情={{market_data}}"
            ),
            "reference_runtime_ids": ["share-a", "share-a", "share-b"],
        })
        strategy = TradingStrategy(symbol="GOLD#", signal_sources=[source])

        normalized = strategy.get_signal_sources("ai_entry")[0]["params"]

        self.assertEqual(normalized["model"], "qwen3.8-max")
        self.assertEqual(normalized["reference_runtime_ids"], ["share-a", "share-b"])
        dynamic = signal_source_defaults("ai_entry", "M5")
        dynamic["params"]["model"] = "provider/new-model"
        self.assertEqual(
            TradingStrategy(symbol="GOLD#", signal_sources=[dynamic])
            .get_signal_sources("ai_entry")[0]["params"]["model"],
            "provider/new-model",
        )
        invalid = signal_source_defaults("ai_entry", "M5")
        invalid["params"]["model"] = "x" * 201
        with self.assertRaisesRegex(ValueError, "大模型无效"):
            TradingStrategy(symbol="GOLD#", signal_sources=[invalid])

        shared_source = signal_source_defaults("ai_entry", "M5")
        shared_source["params"].update({
            "analysis_mode": "shared_reference",
            "shared_runtime_id": "2:strategy:source",
            "reference_runtime_ids": ["context-is-not-used"],
        })
        shared_strategy = TradingStrategy(
            symbol="GOLD#", signal_sources=[shared_source]
        )
        shared_params = shared_strategy.get_signal_sources("ai_entry")[0]["params"]
        self.assertEqual(shared_params["analysis_mode"], "shared_reference")
        self.assertEqual(shared_params["shared_runtime_id"], "2:strategy:source")
        self.assertEqual(shared_params["reference_runtime_ids"], [])
        self.assertNotIn("share_runtime_data", shared_params)

    def test_shared_ai_runtime_data_is_listed_by_symbol(self):
        source = signal_source_defaults("ai_entry", "M5")
        strategy = TradingStrategy(
            symbol="GOLD#", strategy_name="共享黄金AI", signal_sources=[source]
        )
        published = self.shared_ai_runtime_repo.publish(
            self.user_a.user_id,
            strategy.to_dict(),
            source,
            {"trade_suggestions": [{"direction": "buy"}]},
            "deepseek-v4-flash",
            "系统提示词",
            "{{strategy_context}} {{market_data}}",
        )

        btc_source = signal_source_defaults("ai_entry", "M15")
        btc_strategy = TradingStrategy(
            symbol="BTCUSD.a", strategy_name="共享比特币AI",
            signal_sources=[btc_source],
        )
        self.shared_ai_runtime_repo.publish(
            self.user_a.user_id, btc_strategy.to_dict(), btc_source,
            {"trade_suggestions": []}, "glm-5.2", "系统提示词",
            "{{strategy_context}} {{market_data}}",
        )

        visible = self.shared_ai_runtime_repo.list_shared(
            self.user_b.user_id, "XAUUSD.r"
        )

        self.assertEqual(len(visible), 2)
        self.assertEqual(visible[0]["share_id"], published["share_id"])
        self.assertEqual(visible[0]["symbol_similarity"], 0.98)
        self.assertFalse(visible[0]["is_owner"])
        self.assertEqual(visible[0]["strategy_name"], "共享黄金AI")
        self.assertNotIn("system_prompt", visible[0])
        self.assertNotIn("analysis_prompt_template", visible[0])
        self.assertNotIn("system_prompt", visible[0]["signal_params"])
        self.assertNotIn(
            "analysis_prompt_template", visible[0]["signal_params"]
        )
        self.assertEqual(
            visible[0]["result"]["trade_suggestions"][0]["direction"], "buy"
        )
        btc_visible = self.shared_ai_runtime_repo.list_shared(
            self.user_b.user_id, "BTCUSD"
        )
        self.assertEqual(btc_visible[0]["strategy_name"], "共享比特币AI")

        self.shared_ai_runtime_repo.remove_for_source(
            self.user_a.user_id, strategy.strategy_id, source["signal_source_id"]
        )
        self.shared_ai_runtime_repo.remove_for_source(
            self.user_a.user_id, btc_strategy.strategy_id,
            btc_source["signal_source_id"],
        )
        self.assertEqual(
            self.shared_ai_runtime_repo.list_shared(self.user_b.user_id), []
        )

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

    def test_shared_strategy_library_only_lists_shared_items(self):
        shared = TradingStrategy(
            symbol="GOLD#",
            strategy_name="AliceShared",
            visibility="shared",
        )
        private = TradingStrategy(
            symbol="BTCUSD",
            strategy_name="AlicePrivate",
        )
        self.strategy_repo.save_strategy(self.user_a.user_id, shared)
        self.strategy_repo.save_strategy(self.user_a.user_id, private)

        shared_items = self.strategy_repo.list_shared_strategies(
            self.user_b.user_id
        )

        self.assertEqual(len(shared_items), 1)
        self.assertEqual(shared_items[0]["strategy_name"], "AliceShared")
        self.assertEqual(shared_items[0]["owner_user_id"], self.user_a.user_id)
        self.assertEqual(shared_items[0]["owner_username"], "alice")

    def test_use_shared_strategy_creates_readonly_prompt_safe_reference(self):
        ai_source = signal_source_defaults("ai_entry", "M5")
        ai_source["params"].update({
            "system_prompt": "保密系统提示词",
            "analysis_prompt_template": (
                "保密分析提示词 {{strategy_context}} {{market_data}}"
            ),
        })
        source = TradingStrategy(
            symbol="GOLD#",
            strategy_name="平台策略",
            visibility="shared",
            enabled=True,
            auto_execute=True,
            lifecycle_status=StrategyLifecycle.PRODUCTION,
            position_management_policy_id="alice-policy",
            signal_sources=[ai_source],
        )
        self.strategy_repo.save_strategy(self.user_a.user_id, source)
        self.position_policy_repo.save(PositionManagementPolicy(
            policy_id="alice-policy", user_id=self.user_a.user_id,
            name="Alice exits",
        ))

        copied = self.strategy_repo.use_shared_strategy(
            self.user_b.user_id,
            self.user_a.user_id,
            source.strategy_id,
        )

        self.assertIsNotNone(copied)
        self.assertNotEqual(copied.strategy_id, source.strategy_id)
        self.assertEqual(copied.visibility, "private")
        self.assertTrue(copied.enabled)
        self.assertTrue(copied.auto_execute)
        self.assertEqual(copied.lifecycle_status, StrategyLifecycle.PRODUCTION)
        self.assertEqual(copied.position_management_policy_id, "alice-policy")
        self.assertEqual(copied.source_strategy_id, source.strategy_id)
        self.assertEqual(copied.source_owner_user_id, self.user_a.user_id)
        resolved_policy = self.position_policy_repo.get_for_strategy(
            self.user_b.user_id, copied
        )
        self.assertIsNotNone(resolved_policy)
        self.assertEqual(resolved_policy.user_id, self.user_a.user_id)

        bob_strategies = self.strategy_repo.get_all_strategies(
            self.user_b.user_id
        )
        self.assertEqual(len(bob_strategies), 1)
        self.assertEqual(bob_strategies[0].strategy_name, "平台策略")
        params = bob_strategies[0].signal_sources[0]["params"]
        self.assertEqual(params["analysis_mode"], "shared_reference")
        self.assertEqual(params["system_prompt"], "")
        self.assertEqual(params["analysis_prompt_template"], "")

        library_item = self.strategy_repo.list_shared_strategies(
            self.user_b.user_id
        )[0]
        library_params = library_item["signal_sources"][0]["params"]
        self.assertNotIn("system_prompt", library_params)
        self.assertNotIn("analysis_prompt_template", library_params)

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

    def test_strategies_are_ordered_by_creation_time_ascending(self):
        created_at = datetime(2026, 1, 1, 9, 0, 0)
        oldest = TradingStrategy(
            symbol="ZZZ#", strategy_name="最早策略", created_at=created_at
        )
        newest = TradingStrategy(
            symbol="AAA#",
            strategy_name="最新策略",
            created_at=created_at + timedelta(minutes=5),
        )
        # Reverse insertion and symbol order must not affect display order.
        self.strategy_repo.save_strategy(self.user_a.user_id, newest)
        self.strategy_repo.save_strategy(self.user_a.user_id, oldest)

        strategies = self.strategy_repo.get_all_strategies(
            self.user_a.user_id
        )

        self.assertEqual(
            [strategy.strategy_name for strategy in strategies],
            ["最早策略", "最新策略"],
        )

    def test_pivot_signal_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不再支持"):
            TradingStrategy(symbol="GOLD#", signal_sources=[{
                "signal_source_id": "pivot-m1",
                "source": "pivot",
                "period": "M1",
                "weight": 30,
                "params": {},
            }])

    def test_pivot_strategy_migration_removes_backtest_chain(self):
        pivot_config = json.dumps({
            "strategy_id": "pivot-strategy",
            "strategy_name": "Pivot",
            "symbol": "GOLD#",
            "signal_sources": [{"source": "pivot", "period": "M1"}],
        })
        with self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                "DELETE FROM app_meta WHERE key = ?",
                ("remove_pivot_signal_strategies_v1",),
            )
            conn.execute(
                """
                INSERT INTO user_strategy_configs(
                    user_id, strategy_id, symbol, config_json, created_at, updated_at
                ) VALUES(?, 'pivot-strategy', 'GOLD#', ?, 1, 1)
                """,
                (self.user_a.user_id, pivot_config),
            )
            conn.execute(
                """
                INSERT INTO backtest_templates(
                    template_id, user_id, template_name, strategy_id,
                    created_at, updated_at
                ) VALUES('pivot-template', ?, 'Pivot Template',
                         'pivot-strategy', 1, 1)
                """,
                (self.user_a.user_id,),
            )
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, template_id, user_id, batch_name, strategy_id,
                    strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('pivot-batch', 'pivot-template', ?, 'Pivot Batch',
                         'pivot-strategy', 'Pivot', '{}', 'hash', '{}', 1)
                """,
                (self.user_a.user_id,),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, dataset_file_path,
                    dataset_snapshot_json, created_at
                ) VALUES('pivot-task', 'pivot-batch', ?, '', '{}', 1)
                """,
                (self.user_a.user_id,),
            )
            SQLiteStorage._remove_pivot_signal_strategies(conn)

            for table in (
                "user_strategy_configs", "backtest_templates",
                "backtest_batches", "backtest_tasks",
            ):
                self.assertEqual(
                    conn.execute(f"SELECT COUNT(*) FROM {table} WHERE " + (
                        "strategy_id = 'pivot-strategy'" if table != "backtest_tasks"
                        else "task_id = 'pivot-task'"
                    )).fetchone()[0],
                    0,
                )

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
