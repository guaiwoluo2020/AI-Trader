#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite 多用户配置测试
"""

import os
import tempfile
import unittest

from market.models import TradingStrategy
from sqlite_storage import (
    LLMConfigRepository,
    StrategyConfigRepository,
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

    def test_strategy_configs_are_isolated_per_user(self):
        strategy_a = TradingStrategy(symbol="GOLD#", strategy_name="AliceGold")
        strategy_b = TradingStrategy(symbol="GOLD#", strategy_name="BobGold")

        self.strategy_repo.save_strategy(self.user_a.user_id, strategy_a)
        self.strategy_repo.save_strategy(self.user_b.user_id, strategy_b)

        alice_strategy = self.strategy_repo.get_strategy(self.user_a.user_id, "GOLD#")
        bob_strategy = self.strategy_repo.get_strategy(self.user_b.user_id, "GOLD#")

        self.assertEqual(alice_strategy.strategy_name, "AliceGold")
        self.assertEqual(bob_strategy.strategy_name, "BobGold")


if __name__ == "__main__":
    unittest.main()
