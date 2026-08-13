#!/usr/bin/env python3
"""实时模拟账户撮合测试。"""

import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from paper_trading import PaperTradingService
from market.models import PositionManagementPolicy
from sqlite_storage import (
    PositionManagementPolicyRepository, SQLiteStorage,
    TradingAccountRepository, UserRepository,
)


class PaperTradingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "paper.db"))
        self.storage.initialize()
        self.user = UserRepository(self.storage).create_user(
            "paper-user", "hash", "salt"
        )
        self.account = TradingAccountRepository(self.storage).create_paper_account(
            self.user.user_id,
            "GOLD Paper",
            10000,
            commission_per_lot=2,
        )
        now = 100
        config = {
            "strategy_id": "strategy-1",
            "strategy_name": "Gold Auto",
            "symbol": "GOLD_",
            "enabled": True,
            "auto_execute": True,
            "max_positions": 3,
            "max_same_direction": 2,
            "position_management_policy_id": "policy-1",
        }
        PositionManagementPolicyRepository(self.storage).save(
            PositionManagementPolicy(
                policy_id="policy-1", user_id=self.user.user_id,
                name="Test exits", config={
                    "initial_stop_rules": [{"type": "signal"}],
                    "initial_take_profit_rules": [{"type": "signal"}],
                    "management_rules": [
                        {"type": "trailing_stop", "activation_r": 1,
                         "distance_r": 1},
                    ],
                    "min_risk_reward": 0,
                },
            )
        )
        self.storage.execute(
            """
            INSERT INTO user_strategy_configs(
                user_id, strategy_id, symbol, config_json, created_at, updated_at
            ) VALUES(?, 'strategy-1', 'GOLD_', ?, ?, ?)
            """,
            (self.user.user_id, json.dumps(config), now, now),
        )
        self.service = PaperTradingService(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def decision(decision_id="decision-1"):
        return {
            "decision_id": decision_id,
            "strategy_id": "strategy-1",
            "strategy_name": "Gold Auto",
            "symbol": "GOLD_",
            "action": "buy",
            "status": "confirmed",
            "auto_execute": True,
            "entry_price": 3000,
            "sl": 2990,
            "tp": 3010,
            "volume": 0.1,
            "confidence_score": 80,
        }

    def test_realtime_order_fill_take_profit_and_accounting(self):
        deployment = self.service.deploy(
            self.user.user_id, self.account.account_id, "strategy-1"
        )

        self.assertEqual(
            self.service.enqueue_decisions(
                self.user.user_id, [self.decision()]
            ),
            1,
        )
        fill = self.service.process_tick(
            self.user.user_id, "GOLD_", 3000, 3000.2, timestamp=1000
        )
        close = self.service.process_tick(
            self.user.user_id, "GOLD_", 3011, 3011.2, timestamp=1060
        )
        detail = self.service.get_account_detail(
            self.user.user_id, self.account.account_id
        )

        self.assertEqual(deployment["status"], "active")
        self.assertEqual(fill["filled"], 1)
        self.assertEqual(close["closed"], 1)
        self.assertEqual(detail["orders"][0]["status"], "filled")
        self.assertEqual(detail["positions"], [])
        self.assertEqual(detail["trades"][0]["exit_reason"], "take_profit")
        self.assertAlmostEqual(detail["trades"][0]["net_profit"], 107.6)
        self.assertAlmostEqual(detail["account"]["balance"], 10107.6)
        self.assertEqual(len(detail["equity_curve"]), 2)

    def test_moving_average_paper_position_uses_trailing_exit(self):
        row = self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )
        config = json.loads(row["config_json"])
        config["signal_sources"] = [{
            "signal_source_id": "ma-m1",
            "source": "moving_average",
            "enabled": True,
            "period": "M1",
            "weight": 100,
            "params": {
                "fast_period": 5, "slow_period": 20, "ma_type": "sma",
                "stop_loss_pct": 0.002, "risk_reward_ratio": 2,
                "exit_mode": "trailing_reverse",
                "trailing_activation_r": 1, "trailing_distance_r": 1,
                "cooldown_seconds": 0,
            },
        }]
        self.storage.execute(
            "UPDATE user_strategy_configs SET config_json = ? WHERE strategy_id = 'strategy-1'",
            (json.dumps(config),),
        )
        self.service.deploy(
            self.user.user_id, self.account.account_id, "strategy-1"
        )
        decision = self.decision("ma-trailing")
        decision["signal_summary"] = {
            "selected_signal_source": "moving_average",
            "selected_signal_source_id": "ma-m1",
        }
        decision["tp"] = 0

        self.service.enqueue_decisions(self.user.user_id, [decision])
        self.service.process_tick(
            self.user.user_id, "GOLD_", 3000, 3000.2, timestamp=1000
        )
        order = self.storage.fetchone(
            "SELECT * FROM paper_orders WHERE decision_id = 'ma-trailing'"
        )
        self.assertEqual(order["take_profit"], 0)
        self.assertEqual(order["exit_mode"], "position_manager")
        self.service.process_tick(
            self.user.user_id, "GOLD_", 3012, 3012.2, timestamp=1060
        )
        closed = self.service.process_tick(
            self.user.user_id, "GOLD_", 3001, 3001.2, timestamp=1120
        )
        trade = self.storage.fetchone(
            "SELECT * FROM paper_trades WHERE order_id = ?", (order["order_id"],)
        )

        self.assertEqual(closed["closed"], 1)
        self.assertEqual(trade["exit_reason"], "stop_loss")

    def test_pausing_deployment_cancels_pending_and_stops_new_orders(self):
        deployment = self.service.deploy(
            self.user.user_id, self.account.account_id, "strategy-1"
        )
        self.service.enqueue_decisions(self.user.user_id, [self.decision()])

        paused = self.service.set_deployment_status(
            self.user.user_id,
            self.account.account_id,
            deployment["deployment_id"],
            False,
        )

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(
            self.service.enqueue_decisions(
                self.user.user_id, [self.decision("decision-2")]
            ),
            0,
        )
        order = self.storage.fetchone(
            "SELECT * FROM paper_orders WHERE decision_id = 'decision-1'"
        )
        self.assertEqual(order["status"], "canceled")

    def test_paper_deployment_executes_without_enabling_live_auto_trade(self):
        config_row = self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )
        config = json.loads(config_row["config_json"])
        config["auto_execute"] = False
        self.storage.execute(
            "UPDATE user_strategy_configs SET config_json = ? WHERE strategy_id = 'strategy-1'",
            (json.dumps(config),),
        )
        self.service.deploy(
            self.user.user_id, self.account.account_id, "strategy-1"
        )
        decision = self.decision("paper-only")
        decision["auto_execute"] = False

        self.assertEqual(
            self.service.enqueue_decisions(self.user.user_id, [decision]), 1
        )

    def test_account_detail_is_scoped_to_owner(self):
        other = UserRepository(self.storage).create_user(
            "other-paper-user", "hash", "salt"
        )

        with self.assertRaisesRegex(ValueError, "模拟账户不存在"):
            self.service.get_account_detail(
                other.user_id, self.account.account_id
            )

    def test_deleted_strategy_rejects_pending_order_without_blocking_tick(self):
        self.service.deploy(
            self.user.user_id, self.account.account_id, "strategy-1"
        )
        self.service.enqueue_decisions(self.user.user_id, [self.decision()])
        self.storage.execute(
            "DELETE FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )

        result = self.service.process_tick(
            self.user.user_id, "GOLD_", 3000, 3000.2, timestamp=1000
        )

        self.assertEqual(result["rejected"], 1)
        order = self.storage.fetchone(
            "SELECT * FROM paper_orders WHERE decision_id = 'decision-1'"
        )
        self.assertEqual(order["status"], "rejected")
        self.assertEqual(order["rejection_reason"], "来源策略已删除")

    def test_daily_order_limit_ignores_rejected_orders(self):
        now = int(time.time())
        for index in range(100):
            self.storage.execute(
                """
                INSERT INTO paper_orders(
                    order_id, user_id, account_id, deployment_id, strategy_id,
                    decision_id, symbol, direction, status, requested_volume,
                    requested_price, stop_loss, take_profit, confidence,
                    rejection_reason, requested_at, created_at, updated_at
                ) VALUES(?, ?, ?, 'deployment-1', 'strategy-1', ?, 'GOLD_',
                         'buy', 'rejected', 0.1, 3000, 2990, 3010, 80,
                         '测试拒单', ?, ?, ?)
                """,
                (
                    f"rejected-{index}", self.user.user_id,
                    self.account.account_id, f"rejected-decision-{index}",
                    now, now, now,
                ),
            )

        allowed = self.service._paper_risk_check(
            self.account.account_id, "GOLD_", 0.1, 3000
        )
        self.assertTrue(allowed["allowed"])

        for index in range(100):
            self.storage.execute(
                """
                INSERT INTO paper_orders(
                    order_id, user_id, account_id, deployment_id, strategy_id,
                    decision_id, symbol, direction, status, requested_volume,
                    requested_price, stop_loss, take_profit, confidence,
                    requested_at, created_at, updated_at
                ) VALUES(?, ?, ?, 'deployment-1', 'strategy-1', ?, 'GOLD_',
                         'buy', 'pending', 0.1, 3000, 2990, 3010, 80,
                         ?, ?, ?)
                """,
                (
                    f"pending-{index}", self.user.user_id,
                    self.account.account_id, f"pending-decision-{index}",
                    now, now, now,
                ),
            )

        blocked = self.service._paper_risk_check(
            self.account.account_id, "GOLD_", 0.1, 3000
        )
        self.assertFalse(blocked["allowed"])
        self.assertIn("已达到账户每日订单上限", blocked["warnings"])

    def test_each_paper_account_has_an_independent_decision_scope(self):
        second = TradingAccountRepository(self.storage).create_paper_account(
            self.user.user_id, "Second Paper", 10000
        )
        self.service.deploy(self.user.user_id, self.account.account_id, "strategy-1")
        self.service.deploy(self.user.user_id, second.account_id, "strategy-1")

        class FakeDecision:
            status = "pending"

            def __init__(self, decision_id):
                self.decision_id = decision_id

            def to_dict(self):
                data = PaperTradingServiceTests.decision(self.decision_id)
                data["signals"] = []
                return data

        class FakeSignals:
            @staticmethod
            def get_active_signals(symbol):
                return []

        class FakeStore:
            @staticmethod
            def get_strategy_by_id(strategy_id):
                return object()

        class FakeStrategyService:
            signal_service = FakeSignals()
            strategy_store = FakeStore()

            def __init__(self):
                self.scopes = []
                self.calls = []

            def make_decision(self, *args, **kwargs):
                self.scopes.append(kwargs["cooldown_scope"])
                self.calls.append(kwargs)
                return FakeDecision(uuid.uuid4().hex)

        runtime = FakeStrategyService()
        created = self.service.process_strategy_signals(
            self.user.user_id, "GOLD_", 3000, runtime
        )

        self.assertEqual(created, 2)
        self.assertEqual(len(set(runtime.scopes)), 2)
        self.assertTrue(all(scope.startswith("paper:") for scope in runtime.scopes))
        self.assertTrue(all(call["execution_mode"] == "paper" for call in runtime.calls))
        self.assertTrue(all(callable(call["volume_calculator"]) for call in runtime.calls))
        self.assertTrue(all(callable(call["position_checker"]) for call in runtime.calls))
        self.assertTrue(all(callable(call["risk_checker"]) for call in runtime.calls))

    def test_report_summarizes_closed_trades(self):
        self.service.deploy(self.user.user_id, self.account.account_id, "strategy-1")
        self.service.enqueue_decisions(self.user.user_id, [self.decision()])
        self.service.process_tick(self.user.user_id, "GOLD_", 3000, 3000.2, timestamp=1000)
        self.service.process_tick(self.user.user_id, "GOLD_", 3011, 3011.2, timestamp=1060)

        report = self.service.build_report(
            self.user.user_id, self.account.account_id
        )

        self.assertEqual(report["summary"]["trade_count"], 1)
        self.assertEqual(report["summary"]["win_rate"], 100.0)
        self.assertEqual(report["by_strategy"][0]["name"], "strategy-1")

    def test_backtest_deployment_keeps_immutable_snapshot_and_expires(self):
        row = self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )
        current = json.loads(row["config_json"])
        current.update({"lifecycle_status": "backtesting", "min_confidence": 61})
        self.storage.execute(
            "UPDATE user_strategy_configs SET config_json = ? WHERE strategy_id = 'strategy-1'",
            (json.dumps(current),),
        )
        snapshot = {**current, "symbol": "GOLD_"}
        now = 100
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-paper', ?, 'Paper source', 'completed', 1,
                         'strategy-1', 'Gold Auto', ?, 'hash', '{}', ?)
                """,
                (self.user.user_id, json.dumps(snapshot), now),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, result_json, created_at, completed_at
                ) VALUES('task-paper', 'batch-paper', ?, 'completed', '/tmp/source.csv',
                         '{}', ?, ?, ?)
                """,
                (
                    self.user.user_id,
                    json.dumps({
                        "trade_count": 30, "total_return_pct": 8,
                        "net_profit": 800,
                        "win_rate_pct": 60, "profit_factor": 1.5,
                        "max_drawdown_pct": 5,
                    }),
                    now, now,
                ),
            )
            conn.commit()

        deployment = self.service.deploy_backtest(
            self.user.user_id, self.account.account_id, "task-paper", 30
        )
        promoted = json.loads(self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )["config_json"])
        stored = json.loads(deployment["strategy_snapshot_json"])
        current["min_confidence"] = 95
        self.storage.execute(
            "UPDATE user_strategy_configs SET config_json = ? WHERE strategy_id = 'strategy-1'",
            (json.dumps(current),),
        )

        self.assertEqual(stored["min_confidence"], 61)
        self.assertEqual(promoted["lifecycle_status"], "backtest_passed")
        self.assertEqual(deployment["source_backtest_task_id"], "task-paper")
        self.assertGreater(deployment["scheduled_end_at"], deployment["created_at"])

        class SnapshotRuntime:
            class Signals:
                @staticmethod
                def get_active_signals(symbol):
                    return []

            signal_service = Signals()

            def __init__(self):
                self.seen_strategy = None

            def make_decision(self, *args, **kwargs):
                self.seen_strategy = kwargs["strategy"]
                return None

        runtime = SnapshotRuntime()
        self.service.process_strategy_signals(
            self.user.user_id, "GOLD_", 3000, runtime
        )
        self.assertEqual(runtime.seen_strategy.min_confidence, 61)

        report = self.service.build_report(
            self.user.user_id, self.account.account_id, "strategy-1"
        )
        self.assertEqual(report["backtest_benchmark"]["task_id"], "task-paper")
        self.assertEqual(report["comparison"]["return_pct"], -8.0)

        self.storage.execute(
            "UPDATE strategy_deployments SET scheduled_end_at = 1 WHERE deployment_id = ?",
            (deployment["deployment_id"],),
        )
        expired = self.service.list_deployments(
            self.user.user_id, self.account.account_id
        )[0]
        self.assertEqual(expired["status"], "completed")

    def test_ai_only_backtest_deployment_skips_trade_count_gate(self):
        row = self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )
        current = json.loads(row["config_json"])
        current.update({
            "lifecycle_status": "backtesting",
            "signal_sources": [{
                "signal_source_id": "ai-m1",
                "source": "ai_entry",
                "enabled": True,
                "period": "M1",
                "weight": 100,
                "params": {},
            }],
        })
        self.storage.execute(
            "UPDATE user_strategy_configs SET config_json = ? WHERE strategy_id = 'strategy-1'",
            (json.dumps(current),),
        )
        snapshot = {**current, "symbol": "GOLD_"}
        now = 100
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-ai-paper', ?, 'AI short paper', 'completed', 1,
                         'strategy-1', 'Gold Auto', ?, 'hash', '{}', ?)
                """,
                (self.user.user_id, json.dumps(snapshot), now),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, result_json, created_at, completed_at
                ) VALUES('task-ai-paper', 'batch-ai-paper', ?, 'completed', '/tmp/source.csv',
                         '{}', ?, ?, ?)
                """,
                (
                    self.user.user_id,
                    json.dumps({
                        "enabled_signal_sources": ["ai_entry"],
                        "signal_source_trade_counts": {"ai_entry": 2},
                        "trade_count": 2,
                        "net_profit": 31.58,
                        "profit_factor": None,
                        "max_drawdown_pct": 0.01,
                    }),
                    now, now,
                ),
            )
            conn.commit()

        deployment = self.service.deploy_backtest(
            self.user.user_id, self.account.account_id, "task-ai-paper", 30
        )
        promoted = json.loads(self.storage.fetchone(
            "SELECT config_json FROM user_strategy_configs WHERE strategy_id = 'strategy-1'"
        )["config_json"])

        self.assertEqual(promoted["lifecycle_status"], "backtest_passed")
        self.assertEqual(deployment["source_backtest_task_id"], "task-ai-paper")

    def test_background_maintenance_records_heartbeat_and_runtime_events(self):
        self.service.deploy(self.user.user_id, self.account.account_id, "strategy-1")
        self.service.enqueue_decisions(self.user.user_id, [self.decision()])
        self.service.process_tick(
            self.user.user_id, "GOLD_", 3000, 3000.2, timestamp=1000
        )

        summary = self.service.run_maintenance()
        detail = self.service.get_account_detail(
            self.user.user_id, self.account.account_id
        )

        self.assertGreaterEqual(summary["quotes"], 1)
        self.assertTrue(any(
            item["event_type"] == "execution"
            for item in detail["runtime_logs"]
        ))
        self.assertTrue(any(
            item["event_type"] == "heartbeat"
            for item in detail["runtime_logs"]
        ))


if __name__ == "__main__":
    unittest.main()
