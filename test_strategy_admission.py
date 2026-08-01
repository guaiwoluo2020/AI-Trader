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

    def test_changed_strategy_invalidates_old_backtest(self):
        admission = self.service.evaluate(self.user.user_id, self.strategy)
        self.assertFalse(admission["backtest"]["passed"])
        self.assertIn("当前策略版本", admission["backtest"]["message"])


if __name__ == "__main__":
    unittest.main()
