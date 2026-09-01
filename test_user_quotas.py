#!/usr/bin/env python3
"""User resource quota tests."""

import os
import tempfile
import unittest

from market.models.trading_strategy import TradingStrategy
from mysql_repositories import (
    StrategyConfigRepository,
    get_storage,
    reset_storage,
)
from user_quotas import QuotaExceededError, UserQuotaService


class UserQuotaServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_DB_FILE"] = os.path.join(
            self.temp_dir.name, "ai-trader.db"
        )
        reset_storage()
        self.storage = get_storage()
        self.storage.initialize()
        self.storage.execute(
            """
            INSERT INTO users(username, email, password_hash, salt, role,
                              token_version, created_at, updated_at)
            VALUES('quota-user', 'quota@example.com', 'hash', 'salt', 'user', 1, 1, 1)
            """
        )
        self.user_id = int(self.storage.fetchone(
            "SELECT id FROM users WHERE username = 'quota-user'"
        )["id"])
        self.service = UserQuotaService(self.storage)
        self.strategies = StrategyConfigRepository(self.storage)

    def tearDown(self):
        reset_storage()
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    def _save_strategy(self, index, source_count=0):
        strategy = TradingStrategy(
            symbol=f"GOLD{index}",
            strategy_name=f"Quota {index}",
            signal_sources=[
                {"signal_source_id": f"ma-{index}", "source": "moving_average",
                 "period": "M5", "enabled": True, "weight": 100},
                {"signal_source_id": f"ai-{index}", "source": "ai_entry",
                 "period": "M15", "enabled": True, "weight": 100},
            ][:source_count],
        )
        self.strategies.save_strategy(self.user_id, strategy)
        return strategy

    def test_defaults_overrides_and_admin_unlimited(self):
        self._save_strategy(1, 2)
        summary = self.service.get_summary(self.user_id, "user")
        self.assertEqual(summary["limits"], {
            "datasets": 10, "strategies": 5, "signal_sources": 10,
        })
        self.assertEqual(summary["usage"]["strategies"], 1)
        self.assertEqual(summary["usage"]["signal_sources"], 2)

        overrides = self.service.repository.save_overrides(
            self.user_id,
            {"datasets": 25, "strategies": 8, "signal_sources": 30},
            updated_by=self.user_id,
        )
        self.assertEqual(overrides["strategies"], 8)
        self.assertEqual(
            self.service.get_summary(self.user_id, "user")["limits"]["datasets"], 25
        )
        self.assertIsNone(
            self.service.get_summary(self.user_id, "admin")["limits"]["strategies"]
        )

    def test_strategy_and_signal_source_limits_are_enforced(self):
        for index in range(5):
            self._save_strategy(index, 2)
        with self.assertRaisesRegex(QuotaExceededError, "策略已达上限"):
            self.service.assert_can_create(self.user_id, "user", "strategies")
        with self.assertRaisesRegex(QuotaExceededError, "信号源已达上限"):
            self.service.assert_strategy_sources(
                self.user_id, "user", "missing", [{"source": "moving_average"}]
            )


if __name__ == "__main__":
    unittest.main()
