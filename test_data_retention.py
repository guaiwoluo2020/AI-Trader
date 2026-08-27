#!/usr/bin/env python3
"""Retention rules for short-lived operational detail."""

import json
import tempfile
import unittest
from pathlib import Path

from data_retention import DataRetentionService
from sqlite_storage import SQLiteStorage, UserRepository


class DataRetentionServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "retention.db"))
        self.storage.initialize()
        self.user = UserRepository(self.storage).create_user(
            "retention-user", "hash", "salt"
        )
        self.service = DataRetentionService(self.storage)
        self.now = 2_000_000_000

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cleanup_respects_retention_and_running_tasks(self):
        old3 = self.now - 4 * 86400
        old7 = self.now - 8 * 86400
        recent = self.now - 86400
        self._insert_llm_call("old-llm", old3)
        self._insert_llm_call("recent-llm", recent)
        self._insert_paper_heartbeat("old-heartbeat", old3)
        self._insert_paper_heartbeat("recent-heartbeat", recent)
        self._insert_backtest("old-task", "completed", old7)
        self._insert_backtest("running-task", "running", old7)
        self._insert_alpha("old-alpha", "completed", old7)
        self._insert_alpha("running-alpha", "running", old7)

        result = self.service.cleanup(self.now)

        self.assertEqual(result["llm_call_logs"], 1)
        self.assertEqual(result["paper_heartbeat_logs"], 1)
        self.assertEqual(result["backtest_orders"], 1)
        self.assertEqual(result["backtest_replay_bars"], 1)
        self.assertEqual(result["alpha_research_signals"], 1)
        self.assertEqual(self._count("llm_call_logs"), 1)
        self.assertEqual(self._count("paper_runtime_logs"), 1)
        self.assertEqual(self._count("backtest_orders"), 1)
        self.assertEqual(self._count("backtest_replay_bars"), 1)
        self.assertEqual(self._count("alpha_research_signals"), 1)

    def test_maintenance_records_checkpoint_and_vacuum_decision(self):
        result = self.service.run_maintenance("manual", self.now)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["trigger_type"], "manual")
        self.assertIn("checkpointed=", result["checkpoint_status"])
        self.assertIn(result["vacuum_status"], {"executed", "skipped"})
        status = self.service.get_status()
        self.assertEqual(status["latest"]["run_id"], result["run_id"])
        self.assertEqual(len(status["runs"]), 1)

    def _insert_llm_call(self, call_id, created_at):
        self.storage.execute(
            """INSERT INTO llm_call_logs(
                call_id, user_id, scene_code, model_id, status, created_at
            ) VALUES(?, ?, 'test', 'model', 'completed', ?)""",
            (call_id, self.user.user_id, created_at),
        )

    def _insert_paper_heartbeat(self, message, created_at):
        self.storage.execute(
            """INSERT INTO trading_accounts(
                user_id, account_key, account_name, account_type, token_hash,
                created_at, updated_at
            ) VALUES(?, ?, 'Paper', 'paper', 'token', ?, ?)""",
            (self.user.user_id, message, created_at, created_at),
        )
        account_id = self.storage.fetchone(
            "SELECT id FROM trading_accounts WHERE account_key = ?", (message,)
        )["id"]
        self.storage.execute(
            """INSERT INTO paper_runtime_logs(
                user_id, account_id, event_type, message, created_at
            ) VALUES(?, ?, 'heartbeat', ?, ?)""",
            (self.user.user_id, account_id, message, created_at),
        )

    def _insert_backtest(self, task_id, status, completed_at):
        batch_id = f"batch-{task_id}"
        self.storage.execute(
            """INSERT INTO backtest_batches(
                batch_id, user_id, batch_name, status, task_count,
                strategy_id, strategy_name, strategy_snapshot_json,
                strategy_snapshot_hash, template_snapshot_json, created_at
            ) VALUES(?, ?, ?, ?, 1, 'strategy', 'Strategy', '{}', 'hash', '{}', ?)""",
            (batch_id, self.user.user_id, batch_id, status, completed_at),
        )
        self.storage.execute(
            """INSERT INTO backtest_tasks(
                task_id, batch_id, user_id, status, dataset_file_path,
                dataset_snapshot_json, created_at, completed_at
            ) VALUES(?, ?, ?, ?, '', '{}', ?, ?)""",
            (
                task_id, batch_id, self.user.user_id, status,
                completed_at, completed_at,
            ),
        )
        self.storage.execute(
            """INSERT INTO backtest_orders(
                order_id, task_id, user_id, strategy_id, symbol, direction,
                status, requested_at, created_at, updated_at
            ) VALUES(?, ?, ?, 'strategy', 'GOLD', 'buy', 'filled', ?, ?, ?)""",
            (f"order-{task_id}", task_id, self.user.user_id,
             completed_at, completed_at, completed_at),
        )
        self.storage.execute(
            """INSERT INTO backtest_replay_bars(
                task_id, bar_time, end_time, user_id, open, high, low, close
            ) VALUES(?, ?, ?, ?, 1, 1, 1, 1)""",
            (task_id, completed_at, completed_at, self.user.user_id),
        )

    def _insert_alpha(self, run_id, status, completed_at):
        self.storage.execute(
            """INSERT INTO alpha_research_runs(
                run_id, user_id, research_name, status, config_json,
                created_at, completed_at
            ) VALUES(?, ?, 'Research', ?, ?, ?, ?)""",
            (run_id, self.user.user_id, status, json.dumps({}),
             completed_at, completed_at),
        )
        self.storage.execute(
            """INSERT INTO alpha_research_signals(
                run_id, bar_time, direction, alpha_value, close_price
            ) VALUES(?, ?, 1, 1, 1)""",
            (run_id, completed_at),
        )

    def _count(self, table):
        return int(self.storage.fetchone(f"SELECT COUNT(*) count FROM {table}")["count"])


class MySQLRetentionPolicyTestCase(unittest.TestCase):
    def test_cleanup_includes_expired_strategy_pivots(self):
        service = DataRetentionService.__new__(DataRetentionService)
        calls = []

        def delete_in_batches(table, where, params):
            calls.append((table, where, params))
            return 0

        service._delete_in_batches = delete_in_batches
        service._delete_task_details = lambda *args: 0
        service._delete_alpha_signals = lambda *args: 0

        result = service.cleanup(2_000_000_000)

        self.assertEqual(result["strategy_pivot_points"], 0)
        self.assertIn(
            ("strategy_pivot_points", "valid_until < ?", (2_000_000_000,)),
            calls,
        )


if __name__ == "__main__":
    unittest.main()
