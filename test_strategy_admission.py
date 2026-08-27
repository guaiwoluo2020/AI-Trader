#!/usr/bin/env python3
"""策略准入证据测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from market.models import StrategyLifecycle, TradingStrategy
from sqlite_storage import SQLiteStorage, UserRepository
from strategy_admission import StrategyAdmissionService


class FakePaperTrading:
    def build_report(self, *args, **kwargs):
        raise AssertionError("没有部署时不应读取报告")


class PassingPaperTrading:
    def build_report(self, *args, **kwargs):
        return {
            "summary": {
                "trade_count": 25,
                "net_profit": 300,
                "profit_factor": 1.8,
                "max_drawdown_pct": 6,
            }
        }


class StrategyAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "admission.db"))
        self.storage.initialize()
        self.user = UserRepository(self.storage).create_user("admission-user", "h", "s")
        self.strategy = TradingStrategy(
            symbol="GOLD_", strategy_name="Gold Admission", enabled=False,
            lifecycle_status=StrategyLifecycle.BACKTESTING,
        )
        self.service = StrategyAdmissionService(FakePaperTrading(), self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_current_strategy_backtest_evidence_gates_lifecycle(self):
        snapshot = self.strategy.to_dict()
        now = 100
        self.storage.execute(
            """
            INSERT INTO backtest_batches(
                batch_id, user_id, batch_name, status, task_count,
                strategy_id, strategy_name, strategy_snapshot_json,
                strategy_snapshot_hash, template_snapshot_json, created_at
            ) VALUES('batch-1', ?, 'Admission', 'completed', 1, ?, ?, ?, 'hash', '{}', ?)
            """,
            (
                self.user.user_id, self.strategy.strategy_id,
                self.strategy.strategy_name, json.dumps(snapshot), now,
            ),
        )
        result = {
            "trade_count": 25, "net_profit": 800, "profit_factor": 1.6,
            "max_drawdown_pct": 8, "win_rate_pct": 55,
        }
        self.storage.execute(
            """
            INSERT INTO backtest_tasks(
                task_id, batch_id, user_id, status, progress,
                dataset_file_path, dataset_snapshot_json, result_json,
                created_at, completed_at
            ) VALUES('task-1', 'batch-1', ?, 'completed', 100, '', '{}', ?, ?, ?)
            """,
            (self.user.user_id, json.dumps(result), now, now),
        )

        admission = self.service.evaluate(self.user.user_id, self.strategy)

        self.assertTrue(admission["backtest"]["passed"])
        self.assertTrue(admission["eligible_for_paper"])
        self.assertFalse(admission["eligible_for_production"])
        self.service.validate_transition(
            self.user.user_id, self.strategy, StrategyLifecycle.BACKTEST_PASSED
        )
        with self.assertRaisesRegex(ValueError, "模拟盘准入"):
            self.service.validate_transition(
                self.user.user_id, self.strategy, StrategyLifecycle.PRODUCTION
            )

    def test_ai_only_backtest_skips_trade_count_gate(self):
        snapshot = self.strategy.to_dict()
        now = 100
        self.storage.execute(
            """
            INSERT INTO backtest_batches(
                batch_id, user_id, batch_name, status, task_count,
                strategy_id, strategy_name, strategy_snapshot_json,
                strategy_snapshot_hash, template_snapshot_json, created_at
            ) VALUES('batch-ai', ?, 'AI short run', 'completed', 1, ?, ?, ?, 'hash', '{}', ?)
            """,
            (
                self.user.user_id, self.strategy.strategy_id,
                self.strategy.strategy_name, json.dumps(snapshot), now,
            ),
        )
        result = {
            "enabled_signal_sources": ["ai_entry"],
            "signal_source_trade_counts": {"ai_entry": 2},
            "trade_count": 2,
            "net_profit": 31.58,
            "profit_factor": None,
            "max_drawdown_pct": 0.01,
            "win_rate_pct": 100,
        }
        self.storage.execute(
            """
            INSERT INTO backtest_tasks(
                task_id, batch_id, user_id, status, progress,
                dataset_file_path, dataset_snapshot_json, result_json,
                created_at, completed_at
            ) VALUES('task-ai', 'batch-ai', ?, 'completed', 100, '', '{}', ?, ?, ?)
            """,
            (self.user.user_id, json.dumps(result), now, now),
        )

        admission = self.service.evaluate(self.user.user_id, self.strategy)
        trade_check = next(
            item for item in admission["backtest"]["checks"]
            if item["key"] == "trade_count"
        )

        self.assertTrue(admission["backtest"]["passed"])
        self.assertTrue(admission["eligible_for_paper"])
        self.assertTrue(trade_check["passed"])
        self.assertTrue(trade_check["skipped"])

    def test_changed_strategy_invalidates_old_backtest(self):
        admission = self.service.evaluate(self.user.user_id, self.strategy)
        self.assertFalse(admission["backtest"]["passed"])
        self.assertIn("当前策略版本", admission["backtest"]["message"])

    def test_pivot_strategy_can_enter_paper_without_backtest(self):
        self.strategy.lifecycle_status = StrategyLifecycle.DRAFT
        self.strategy.signal_sources = [{
            "signal_source_id": "pivot-m1",
            "source": "pivot",
            "period": "M1",
            "enabled": True,
            "weight": 100,
            "params": {},
        }]

        admission = self.service.evaluate(self.user.user_id, self.strategy)

        self.assertFalse(admission["backtest"]["passed"])
        self.assertTrue(admission["direct_paper_strategy"])
        self.assertTrue(admission["eligible_for_paper"])
        self.assertFalse(admission["eligible_for_production"])
        self.service.validate_transition(
            self.user.user_id, self.strategy, StrategyLifecycle.PAPER_TRADING
        )

    def test_ai_strategy_can_reach_production_with_passing_paper_only(self):
        self.strategy.lifecycle_status = StrategyLifecycle.PAPER_TRADING
        self.strategy.signal_sources = [{
            "signal_source_id": "ai-m1",
            "source": "ai_entry",
            "period": "M1",
            "enabled": True,
            "weight": 30,
            "params": {},
        }]
        self.storage.execute(
            """
            INSERT INTO strategy_deployments(
                deployment_id, user_id, account_id, strategy_id, symbol,
                execution_mode, status, created_at, updated_at
            ) VALUES('dep-ai', ?, 1, ?, 'GOLD_', 'paper', 'active', 100, 100)
            """,
            (self.user.user_id, self.strategy.strategy_id),
        )
        self.storage.execute(
            """
            INSERT INTO trading_accounts(
                id, user_id, account_key, account_name, account_type,
                token_hash, status, enabled, created_at, updated_at
            ) VALUES(1, ?, 'paper-test', 'Paper', 'paper', 'h', 'active', 1, 100, 100)
            """,
            (self.user.user_id,),
        )
        service = StrategyAdmissionService(PassingPaperTrading(), self.storage)

        admission = service.evaluate(self.user.user_id, self.strategy)

        self.assertFalse(admission["backtest"]["passed"])
        self.assertTrue(admission["paper"]["passed"])
        self.assertTrue(admission["ai_strategy"])
        self.assertTrue(admission["eligible_for_production"])
        service.validate_transition(
            self.user.user_id, self.strategy, StrategyLifecycle.PRODUCTION
        )

    def test_non_ai_strategy_still_requires_backtest_for_production(self):
        self.strategy.lifecycle_status = StrategyLifecycle.PAPER_TRADING
        self.strategy.signal_sources = [{
            "signal_source_id": "ma-m1",
            "source": "moving_average",
            "period": "M1",
            "enabled": True,
            "weight": 30,
            "params": {},
        }]
        self.storage.execute(
            """
            INSERT INTO strategy_deployments(
                deployment_id, user_id, account_id, strategy_id, symbol,
                execution_mode, status, created_at, updated_at
            ) VALUES('dep-ma', ?, 1, ?, 'GOLD_', 'paper', 'active', 100, 100)
            """,
            (self.user.user_id, self.strategy.strategy_id),
        )
        self.storage.execute(
            """
            INSERT INTO trading_accounts(
                id, user_id, account_key, account_name, account_type,
                token_hash, status, enabled, created_at, updated_at
            ) VALUES(1, ?, 'paper-test', 'Paper', 'paper', 'h', 'active', 1, 100, 100)
            """,
            (self.user.user_id,),
        )
        service = StrategyAdmissionService(PassingPaperTrading(), self.storage)

        admission = service.evaluate(self.user.user_id, self.strategy)

        self.assertFalse(admission["ai_strategy"])
        self.assertTrue(admission["paper"]["passed"])
        self.assertFalse(admission["eligible_for_production"])
        with self.assertRaisesRegex(ValueError, "模拟盘准入"):
            service.validate_transition(
                self.user.user_id, self.strategy, StrategyLifecycle.PRODUCTION
            )


if __name__ == "__main__":
    unittest.main()
