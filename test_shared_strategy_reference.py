#!/usr/bin/env python3
"""Regression tests for shared strategy reference state rendering."""

import unittest

from market.models import TradingStrategy
from market.models.trading_strategy import signal_source_defaults
from mysql_repositories import StrategyConfigRepository


class SharedStrategyReferenceTests(unittest.TestCase):
    def test_invalid_reference_marker_is_idempotent_and_specific(self):
        repository = object.__new__(StrategyConfigRepository)
        strategy = TradingStrategy(
            symbol="BTCUSD",
            strategy_name=(
                "比特币策略5分钟大模型（来源已失效）"
                "（来源已失效）"
            ),
        )

        result = repository._invalid_strategy_reference(
            strategy, "共享策略包含未开放运行数据的 AI 信号源",
        )

        self.assertEqual(
            result.strategy_name, "比特币策略5分钟大模型（AI运行数据未共享）"
        )
        self.assertEqual(result.lifecycle_status, "draft")

    def test_strategy_binding_drops_legacy_runtime_share_flag(self):
        source = signal_source_defaults("ai_entry", "M5")
        source["params"]["share_runtime_data"] = True

        strategy = TradingStrategy(symbol="BTCUSD", signal_sources=[source])

        self.assertNotIn(
            "share_runtime_data",
            strategy.get_signal_sources("ai_entry")[0]["params"],
        )


if __name__ == "__main__":
    unittest.main()
