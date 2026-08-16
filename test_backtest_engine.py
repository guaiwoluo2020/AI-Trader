#!/usr/bin/env python3
"""Backtest replay, LLM barrier, cache, and worker tests."""

import csv
import gzip
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backtest_engine import (
    BacktestCanceled,
    BacktestEngineError,
    BacktestLLMCache,
    BacktestTaskRepository,
    BacktestWorker,
    M1BacktestEngine,
    ReplaySignalEngine,
    aggregate_replay_bars,
    build_result,
    combine_signals,
    detect_confirmed_pivots,
    position_limit_reason,
)
from backtest_tasks import BacktestTaskStatus, BacktestTemplateService
from market.services.signal.ai_entry_signal import AIEntrySignalGenerator
from market.services.signal.key_level_signal import KeyLevelSignalGenerator
from sqlite_storage import SQLiteStorage, UserRepository


class FakeLLMProvider:
    def __init__(self):
        self.calls = []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "trade_suggestions": [{
                "period": "M1",
                "direction": "buy",
                "confidence": 88,
                "entry_price": 104.5,
                "stop_loss": 103,
                "take_profit": 130,
            }]
        }, False


def strategy_snapshot():
    return {
        "strategy_id": "strategy-1",
        "strategy_name": "AI M1",
        "symbol": "GOLD_",
        "signal_config": {
            "ai_entry": {
                "enabled": True,
                "periods": {"M1": {"enabled": True, "weight": 100}},
            }
        },
        "min_confidence": 50,
        "min_risk_reward": 1,
        "volume_mode": "fixed",
        "fixed_volume": 0.1,
        "position_management_policy_id": "policy-1",
        "position_management_policy_snapshot": {
            "policy_id": "policy-1", "user_id": 1, "name": "Test exits",
            "enabled": True,
            "config": {
                "initial_stop_rules": [{"type": "signal"}],
                "initial_take_profit_rules": [{"type": "signal"}],
                "management_rules": [], "min_risk_reward": 1,
            },
        },
    }


def template_snapshot():
    return {
        "initial_capital": 100000,
        "position_sizing_mode": "fixed",
        "fixed_volume": 0.1,
        "spread_points": 0,
        "slippage_points": 0,
        "commission_per_lot": 0,
        "max_positions": 1,
    }


class M1BacktestEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "GOLD_M1.csv.gz"
        self.start = 1767225600
        with gzip.open(self.path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "time", "open", "high", "low", "close",
                    "tick_volume", "real_volume", "spread",
                ],
            )
            writer.writeheader()
            for index in range(11):
                price = 100 + index
                writer.writerow({
                    "time": self.start + index * 60,
                    "open": price,
                    "high": price + 1,
                    "low": price - 1,
                    "close": price + 0.5,
                    "tick_volume": 10,
                    "real_volume": 0,
                    "spread": 0,
                })

    def tearDown(self):
        self.temp_dir.cleanup()

    def task(self):
        return {
            "task_id": "task-1",
            "user_id": 1,
            "dataset_file_path": str(self.path),
            "dataset_snapshot": {
                "dataset_id": "dataset-1",
                "symbol": "GOLD_",
                "requested_start": self.start,
                "requested_end": self.start + 10 * 60,
                "data_format": "csv.gz",
                "data_hash": "dataset-hash",
            },
            "strategy_snapshot": strategy_snapshot(),
            "strategy_snapshot_hash": "strategy-hash",
            "template_snapshot": template_snapshot(),
        }

    def test_five_bar_barrier_has_no_future_data_and_fills_next_open(self):
        provider = FakeLLMProvider()
        progress_updates = []
        usage_updates = []
        result = M1BacktestEngine(
            llm_provider=provider,
            progress_callback=progress_updates.append,
            llm_usage_callback=usage_updates.append,
        ).run(self.task())

        self.assertEqual(len(provider.calls), 2)
        first = provider.calls[0]
        self.assertEqual(first["analysis_time"], self.start + 5 * 60)
        self.assertEqual(len(first["klines"]["M1"]), 5)
        self.assertIn("00:04:00", first["klines"]["M1"][-1]["timestamp"])
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["trades"][0]["opened_at"], self.start + 5 * 60)
        self.assertEqual(result["trades"][0]["entry_price"], 105.0)
        self.assertGreaterEqual(len(progress_updates), 4)
        self.assertGreater(progress_updates[0], 0)
        self.assertEqual(usage_updates, [False, False])
        self.assertEqual(result["llm_call_count"], 2)

    def test_llm_failure_stops_replay_instead_of_skipping(self):
        class FailedProvider:
            def analyze(self, **kwargs):
                raise RuntimeError("LLM unavailable")

        with self.assertRaisesRegex(RuntimeError, "LLM unavailable"):
            M1BacktestEngine(llm_provider=FailedProvider()).run(self.task())

    def test_shared_reference_ai_backtest_fails_with_clear_message(self):
        task = self.task()
        task["strategy_snapshot"]["signal_sources"] = [{
            "signal_source_id": "shared-ai",
            "source": "ai_entry",
            "enabled": True,
            "period": "M1",
            "weight": 100,
            "params": {
                "analysis_mode": "shared_reference",
                "shared_runtime_id": "owner:strategy:source",
                "min_confidence": 70,
            },
        }]
        task["strategy_snapshot"]["signal_config"] = {}

        with self.assertRaisesRegex(BacktestEngineError, "共享AI引用信号源当前不支持历史回测"):
            M1BacktestEngine(llm_provider=FakeLLMProvider()).run(task)

    def test_cancel_callback_stops_replay_cooperatively(self):
        checkpoints = []
        engine = M1BacktestEngine(
            llm_provider=FakeLLMProvider(),
            checkpoint_callback=lambda progress, ledger: checkpoints.append(
                (progress, ledger)
            ),
            cancel_callback=lambda: True,
        )

        with self.assertRaisesRegex(BacktestCanceled, "用户停止"):
            engine.run(self.task())

        self.assertEqual(checkpoints[0][0], 0)
        self.assertEqual(checkpoints[0][1]["account"]["status"], "running")

    def test_runtime_checkpoint_contains_account_and_orders(self):
        checkpoints = []
        M1BacktestEngine(
            llm_provider=FakeLLMProvider(),
            checkpoint_callback=lambda progress, ledger: checkpoints.append(
                (progress, ledger)
            ),
        ).run(self.task())

        self.assertGreaterEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[-1][1]["account"]["initial_balance"], 100000)
        self.assertGreaterEqual(len(checkpoints[-1][1]["orders"]), 1)
        self.assertEqual(len(checkpoints[-1][1]["replay_bars"]), 11)
        self.assertEqual(checkpoints[-1][1]["replay_bars"][-1]["close"], 110.5)

    def test_replay_bars_are_ohlc_aggregated_without_losing_range(self):
        bars = [
            {"time": index * 60, "open": index, "high": index + 2,
             "low": index - 1, "close": index + 1, "tick_volume": 10}
            for index in range(10)
        ]

        replay = aggregate_replay_bars(bars, max_points=3)

        self.assertEqual(len(replay), 3)
        self.assertEqual(replay[0]["bar_count"], 4)
        self.assertEqual(replay[0]["open"], 0)
        self.assertEqual(replay[0]["close"], 4)
        self.assertEqual(replay[0]["high"], 5)
        self.assertEqual(replay[0]["low"], -1)

    def test_key_level_signal_is_replayed_when_enabled(self):
        task = self.task()
        task["strategy_snapshot"]["position_management_policy_snapshot"][
            "config"
        ].update({
            "initial_stop_rules": [{"type": "fixed_percent", "value": 0.002}],
            "initial_take_profit_rules": [{"type": "risk_reward", "value": 2}],
        })
        task["strategy_snapshot"]["signal_config"] = {
            "key_level": {"enabled": True, "weight": 100},
            "pivot": {"enabled": False, "periods": {}},
            "ai_entry": {"enabled": False, "periods": {}},
        }
        with gzip.open(self.path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "time", "open", "high", "low", "close",
                "tick_volume", "real_volume", "spread",
            ])
            writer.writeheader()
            for index in range(3):
                writer.writerow({
                    "time": self.start + index * 60,
                    "open": 3000 + index * 0.1,
                    "high": 3020,
                    "low": 2990,
                    "close": 3000.05 + index * 0.1,
                    "tick_volume": 10,
                    "real_volume": 0,
                    "spread": 0,
                })
        task["dataset_snapshot"]["requested_end"] = self.start + 120

        result = M1BacktestEngine(llm_provider=FakeLLMProvider()).run(task)

        self.assertGreaterEqual(result["trade_count"], 1)
        self.assertEqual(result["trades"][0]["signal_source"], "key_level")
        self.assertEqual(
            result["signal_source_trade_counts"]["key_level"],
            result["trade_count"],
        )

    def test_replay_and_live_key_level_use_identical_signal_rules(self):
        strategy = strategy_snapshot()
        strategy["signal_config"] = {
            "key_level": {"enabled": True, "weight": 100},
            "pivot": {"enabled": False, "periods": {}},
            "ai_entry": {"enabled": False, "periods": {}},
        }
        live_signal = KeyLevelSignalGenerator().generate_signal("GOLD_", 3000.05)
        replay_signal = ReplaySignalEngine(strategy).generate(
            [], 3000.05, self.start, None
        )[0]

        self.assertEqual(replay_signal.action, live_signal.action)
        self.assertEqual(replay_signal.key_level, live_signal.key_level)
        self.assertEqual(replay_signal.suggested_sl, live_signal.suggested_sl)
        self.assertEqual(replay_signal.suggested_tp, live_signal.suggested_tp)
        self.assertEqual(replay_signal.confidence, live_signal.confidence)

    def test_replay_and_live_ai_entry_use_identical_signal_rules(self):
        suggestion = {
            "period": "M1",
            "direction": "buy",
            "confidence": 88,
            "entry_price": 3000.05,
            "stop_loss": 2990,
            "take_profit": 3020,
        }

        class Analyzer:
            @staticmethod
            def check_entry_price_nearby(symbol, current_price, threshold):
                return [suggestion]

        live_engine = AIEntrySignalGenerator()
        live_engine.set_llm_analyzer(Analyzer())
        live_signal = live_engine.generate_signal("GOLD_", 3000.05)
        replay_signal = ReplaySignalEngine(strategy_snapshot()).generate(
            [], 3000.05, self.start, {"trade_suggestions": [suggestion]}
        )[0]

        self.assertEqual(replay_signal.action, live_signal.action)
        self.assertEqual(replay_signal.source_period, live_signal.source_period)
        self.assertEqual(replay_signal.suggested_sl, live_signal.suggested_sl)
        self.assertEqual(replay_signal.suggested_tp, live_signal.suggested_tp)
        self.assertEqual(replay_signal.confidence, live_signal.confidence)

    def test_ai_recommendation_emits_once_per_analysis(self):
        suggestion = {
            "_analysis_id": "analysis-1",
            "period": "M1",
            "direction": "buy",
            "confidence": 88,
            "entry_price": 3000.05,
            "stop_loss": 2990,
            "take_profit": 3020,
        }
        engine = ReplaySignalEngine(strategy_snapshot())

        first = engine.generate(
            [], 3000.05, self.start, {"trade_suggestions": [suggestion]}
        )
        repeated = engine.generate(
            [], 3000.05, self.start + 600,
            {"trade_suggestions": [suggestion]},
        )
        refreshed = engine.generate(
            [], 3000.05, self.start + 601,
            {"trade_suggestions": [{**suggestion, "_analysis_id": "analysis-2"}]},
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(repeated), 1)
        self.assertFalse(repeated[0].is_entry_trigger)
        self.assertFalse(repeated[0].state_ready)
        self.assertEqual(len(refreshed), 1)

    def test_live_ai_recommendation_emits_once_per_analysis(self):
        match = {
            "analyzed_at": "2026-08-02T10:00:00",
            "period": "M1",
            "direction": "buy",
            "confidence": 88,
            "entry_price": 3000.05,
            "stop_loss": 2990,
            "take_profit": 3020,
        }

        class Analyzer:
            @staticmethod
            def check_entry_price_nearby(*_args, **_kwargs):
                return [match]

        generator = AIEntrySignalGenerator()
        generator.set_llm_analyzer(Analyzer())

        self.assertEqual(len(generator.generate_signals("GOLD_", 3000.05)), 1)
        self.assertEqual(generator.generate_signals("GOLD_", 3000.05), [])
        match["analyzed_at"] = "2026-08-02T10:05:00"
        self.assertEqual(len(generator.generate_signals("GOLD_", 3000.05)), 1)

    def test_live_ai_signal_allows_policy_exit_fallback_after_price_drift(self):
        match = {
            "analyzed_at": "2026-08-02T10:00:00",
            "period": "M1",
            "direction": "buy",
            "confidence": 88,
            "entry_price": 3000,
            "stop_loss": 2990,
            # Valid at the suggested entry, but not after this price drift.
            "take_profit": 3011,
        }

        class Analyzer:
            @staticmethod
            def check_entry_price_nearby(*_args, **_kwargs):
                return [match]

        generator = AIEntrySignalGenerator()
        generator.set_llm_analyzer(Analyzer())

        signals = generator.generate_signals(
            "GOLD_", 3005, threshold=0.002, allow_exit_fallback=True,
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "buy")
        self.assertEqual(signals[0].suggested_sl, 0)
        self.assertEqual(signals[0].suggested_tp, 0)

    def test_shared_ai_runtime_becomes_current_strategy_signal_source(self):
        shared = {
            "share_id": "2:source-strategy:source-ai",
            "owner_username": "shared-user",
            "signal_source_id": "source-ai",
            "symbol": "XAUUSD",
            "period": "M5",
            "last_run_at": int(time.time()),
            "signal_params": {"analysis_interval_minutes": 5},
            "result": {
                "trend_analysis": {
                    "M5": {"trend": "up", "confidence": 88, "reason": "上升"}
                },
                "trade_suggestions": [{
                    "signal_source_id": "source-ai",
                    "period": "M5", "direction": "buy", "confidence": 88,
                    "entry_price": 3000.0, "stop_loss": 0,
                    "take_profit": 0, "reason": "共享机会",
                }],
            },
        }

        class SharedRepository:
            @staticmethod
            def get_shared(share_id):
                return shared if share_id == shared["share_id"] else None

        strategy = SimpleNamespace(
            strategy_id="consumer-strategy", min_confidence=50,
            get_signal_sources=lambda *_args, **_kwargs: [{
                "signal_source_id": "consumer-ai", "source": "ai_entry",
                "period": "M5", "enabled": True,
                "params": {
                    "analysis_mode": "shared_reference",
                    "shared_runtime_id": shared["share_id"],
                    "min_confidence": 80, "entry_threshold": 0.001,
                },
            }],
        )
        generator = AIEntrySignalGenerator(SharedRepository())

        first = generator.generate_signals_for_strategy("GOLD_", 3000.0, strategy)
        repeated = generator.generate_signals_for_strategy("GOLD_", 3000.0, strategy)

        self.assertEqual(first[0].action, "buy")
        self.assertTrue(first[0].is_entry_trigger)
        self.assertEqual(first[0].signal_source_id, "consumer-ai")
        self.assertIn("shared-user", first[0].trigger_reason)
        self.assertFalse(repeated[0].is_entry_trigger)

    def test_pivot_requires_right_hand_confirmation_bars(self):
        bars = []
        for index in range(13):
            distance = abs(index - 6)
            low = 3000 + distance * 0.05
            bars.append({
                "timestamp": str(index),
                "open": low + 0.1,
                "high": low + 1,
                "low": low,
                "close": low + 0.2,
            })

        self.assertEqual(detect_confirmed_pivots(bars[:12], 6), [])
        confirmed = detect_confirmed_pivots(bars, 6)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["direction"], "low")
        self.assertEqual(confirmed[0]["time"], "6")

    def test_pivot_configuration_no_longer_creates_backtest_trade(self):
        task = self.task()
        task["strategy_snapshot"]["signal_config"] = {
            "pivot": {
                "enabled": True,
                "periods": {"M1": {"enabled": True, "weight": 100}},
            },
            "key_level": {"enabled": False, "weight": 0},
            "ai_entry": {"enabled": False, "periods": {}},
        }
        with gzip.open(self.path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "time", "open", "high", "low", "close",
                "tick_volume", "real_volume", "spread",
            ])
            writer.writeheader()
            for index in range(14):
                low = 3000 + abs(index - 6) * 0.05
                writer.writerow({
                    "time": self.start + index * 60,
                    "open": low + 0.02,
                    "high": 3020 if index == 13 else low + 1,
                    "low": low,
                    "close": low + 0.01,
                    "tick_volume": 10,
                    "real_volume": 0,
                    "spread": 0,
                })
        task["dataset_snapshot"]["requested_end"] = self.start + 13 * 60

        result = M1BacktestEngine(llm_provider=FakeLLMProvider()).run(task)

        self.assertEqual(result["trade_count"], 0)

    def test_signal_combination_uses_strategy_weights(self):
        strategy = strategy_snapshot()
        strategy["signal_config"] = {
            "pivot": {
                "enabled": True,
                "periods": {"M1": {"enabled": True, "weight": 80}},
            },
            "key_level": {"enabled": True, "weight": 20},
            "ai_entry": {"enabled": False, "periods": {}},
        }
        signals = [
            {
                "source": "pivot", "period": "M1", "direction": "buy",
                "confidence": 60, "stop_loss": 90, "take_profit": 120,
            },
            {
                "source": "key_level", "period": "", "direction": "sell",
                "confidence": 90, "stop_loss": 110, "take_profit": 80,
            },
        ]

        decision = combine_signals(signals, strategy)

        self.assertEqual(decision["direction"], "sell")
        self.assertEqual(decision["source"], "key_level")

    def test_template_allows_multiple_concurrent_positions(self):
        task = self.task()
        task["dataset_snapshot"]["requested_end"] = self.start + 20 * 60
        task["template_snapshot"]["max_positions"] = 3
        task["strategy_snapshot"]["max_positions"] = 3
        task["strategy_snapshot"]["max_same_direction"] = 3
        with gzip.open(self.path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "time", "open", "high", "low", "close",
                "tick_volume", "real_volume", "spread",
            ])
            writer.writeheader()
            for index in range(21):
                writer.writerow({
                    "time": self.start + index * 60,
                    "open": 105,
                    "high": 105.5,
                    "low": 104,
                    "close": 104.5,
                    "tick_volume": 10,
                    "real_volume": 0,
                    "spread": 0,
                })

        result = M1BacktestEngine(llm_provider=FakeLLMProvider()).run(task)

        self.assertEqual(result["max_concurrent_positions"], 3)
        self.assertEqual(len(result["_ledger"]["positions"]), 3)
        self.assertGreater(result["order_status_counts"].get("rejected", 0), 0)

    def test_template_position_limits_override_live_strategy_limits(self):
        positions = [
            SimpleNamespace(direction="buy")
            for index in range(2)
        ]

        reason = position_limit_reason(
            "buy",
            positions,
            {"max_positions": 3, "max_same_direction": 2},
            {"max_positions": 10, "max_same_direction": 10},
        )

        self.assertEqual(reason, "")

    def test_report_metrics_and_attribution(self):
        day = 86400
        trades = [
            {
                "net_profit": 100, "commission": 2, "direction": "buy",
                "signal_source": "pivot", "exit_reason": "take_profit",
                "opened_at": day, "closed_at": day + 60,
            },
            {
                "net_profit": -40, "commission": 2, "direction": "sell",
                "signal_source": "key_level", "exit_reason": "stop_loss",
                "opened_at": day * 2, "closed_at": day * 2 + 180,
            },
            {
                "net_profit": -20, "commission": 2, "direction": "buy",
                "signal_source": "pivot", "exit_reason": "stop_loss",
                "opened_at": day * 3, "closed_at": day * 3 + 120,
            },
        ]
        curve = [
            {"time": day, "equity": 10100},
            {"time": day * 2, "equity": 10060},
            {"time": day * 3, "equity": 10040},
        ]

        result = build_result(
            10000, 10040, trades, curve, 0, 0, 0.01, 100,
            strategy_snapshot(),
        )

        self.assertEqual(result["profit_factor"], 1.67)
        self.assertEqual(result["payoff_ratio"], 3.33)
        self.assertEqual(result["max_consecutive_losses"], 2)
        self.assertEqual(result["average_holding_minutes"], 2)
        self.assertEqual(result["total_commission"], 6)
        self.assertEqual(result["signal_source_stats"][0]["signal_source"], "pivot")
        self.assertEqual(len(result["drawdown_curve"]), 3)


class BacktestPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "test.db"))
        self.storage.initialize()
        self.user = UserRepository(self.storage).create_user(
            "engine-user", "hash", "salt"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_llm_cache_round_trip(self):
        cache = BacktestLLMCache(self.storage)
        metadata = {
            "cache_key": "cache-1",
            "user_id": self.user.user_id,
            "dataset_hash": "dataset-hash",
            "strategy_hash": "strategy-hash",
            "analysis_time": 100,
            "model": "test-model",
            "prompt_hash": "prompt-hash",
        }
        result = {"trade_suggestions": [{"direction": "buy"}]}

        cache.save(metadata, result)

        self.assertEqual(cache.get("cache-1"), result)
        self.assertIsNone(cache.get("missing"))

    def test_worker_completes_task_and_batch(self):
        now = 100
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-1', ?, 'Batch', 'queued', 1, 'strategy-1',
                         'Strategy', ?, 'strategy-hash', ?, ?)
                """,
                (
                    self.user.user_id,
                    __import__("json").dumps(strategy_snapshot()),
                    __import__("json").dumps(template_snapshot()),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, created_at
                ) VALUES('task-1', 'batch-1', ?, 'queued', '/tmp/test.csv', ?, ?)
                """,
                (
                    self.user.user_id,
                    __import__("json").dumps({"symbol": "GOLD_"}),
                    now,
                ),
            )
            conn.commit()

        class SuccessfulEngine:
            def __init__(self, progress_callback):
                self.progress_callback = progress_callback

            def run(self, task):
                self.progress_callback(50)
                return {"trade_count": 2, "net_profit": 100}

        worker = BacktestWorker(
            self.storage, engine_factory=SuccessfulEngine, poll_seconds=0.01
        )
        self.assertTrue(worker.run_once())

        task = self.storage.fetchone(
            "SELECT * FROM backtest_tasks WHERE task_id = 'task-1'"
        )
        batch = self.storage.fetchone(
            "SELECT * FROM backtest_batches WHERE batch_id = 'batch-1'"
        )
        self.assertEqual(task["status"], BacktestTaskStatus.COMPLETED)
        self.assertEqual(task["progress"], 100)
        self.assertEqual(batch["status"], BacktestTaskStatus.COMPLETED)
        self.assertEqual(batch["completed_tasks"], 1)

    def test_completion_persists_normalized_simulation_ledger(self):
        now = 100
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-ledger', ?, 'Ledger', 'running', 1, 's', 'S',
                         '{}', 'h', '{}', ?)
                """,
                (self.user.user_id, now),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, created_at
                ) VALUES('task-ledger', 'batch-ledger', ?, 'running', 'x', '{}', ?)
                """,
                (self.user.user_id, now),
            )
            conn.commit()
        ledger = {
            "account": {
                "initial_balance": 10000, "balance": 10100,
                "equity": 10100, "free_margin": 10100,
                "margin": 0, "status": "completed",
            },
            "orders": [{
                "order_id": "order-1", "strategy_id": "s", "symbol": "GOLD_",
                "direction": "buy", "status": "filled", "requested_volume": 0.1,
                "filled_volume": 0.1, "requested_price": 3000,
                "filled_price": 3001, "stop_loss": 2990, "take_profit": 3020,
                "signal_source": "pivot", "contributing_sources": ["pivot"],
                "confidence": 60, "rejection_reason": "", "requested_at": 100,
                "filled_at": 160, "canceled_at": None,
            }],
            "positions": [{
                "position_id": "position-1", "order_id": "order-1",
                "symbol": "GOLD_", "direction": "buy", "status": "closed",
                "volume": 0.1, "entry_price": 3001, "stop_loss": 2990,
                "take_profit": 3020, "opened_at": 160, "closed_at": 220,
                "close_price": 3011, "close_reason": "take_profit", "net_profit": 100,
            }],
            "trades": [{
                "trade_id": "trade-1", "order_id": "order-1",
                "position_id": "position-1", "symbol": "GOLD_",
                "direction": "buy", "volume": 0.1, "entry_price": 3001,
                "exit_price": 3011, "gross_profit": 100, "commission": 0,
                "net_profit": 100, "exit_reason": "take_profit",
                "opened_at": 160, "closed_at": 220,
            }],
            "equity_points": [{
                "time": 220, "balance": 10100, "equity": 10100,
                "open_positions": 0,
            }],
            "replay_bars": [{
                "time": 160, "end_time": 160, "open": 3000,
                "high": 3012, "low": 2998, "close": 3011,
                "tick_volume": 20, "bar_count": 1,
            }],
        }

        BacktestTaskRepository(self.storage).complete(
            "task-ledger", {"trade_count": 1, "_ledger": ledger}
        )

        for table in (
            "backtest_accounts", "backtest_orders", "backtest_positions",
            "backtest_trades", "backtest_equity_points", "backtest_replay_bars",
        ):
            count = self.storage.fetchone(
                f"SELECT COUNT(*) AS count FROM {table} WHERE task_id = ?",
                ("task-ledger",),
            )
            self.assertEqual(int(count["count"]), 1, table)
        task = self.storage.fetchone(
            "SELECT result_json FROM backtest_tasks WHERE task_id = 'task-ledger'"
        )
        self.assertNotIn("_ledger", task["result_json"])
        service = BacktestTemplateService(self.storage)
        saved = service.get_task_ledger(self.user.user_id, "task-ledger")
        self.assertEqual(saved["orders"][0]["contributing_sources"], ["pivot"])
        self.assertEqual(saved["account"]["balance"], 10100)
        self.assertEqual(saved["replay_bars"][0]["close"], 3011)
        other_user = UserRepository(self.storage).create_user(
            "other-user", "hash", "salt"
        )
        self.assertIsNone(
            service.get_task_ledger(other_user.user_id, "task-ledger")
        )

    def test_stale_running_task_is_requeued(self):
        repository = BacktestTaskRepository(self.storage)
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-stale', ?, 'Batch', 'running', 1, 's', 'S',
                         '{}', 'h', '{}', 1)
                """,
                (self.user.user_id,),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, created_at, started_at, heartbeat_at
                ) VALUES('task-stale', 'batch-stale', ?, 'running', 'x', '{}', 1, 1, 1)
                """,
                (self.user.user_id,),
            )
            conn.commit()

        self.assertEqual(repository.recover_stale(stale_seconds=1), 1)
        row = self.storage.fetchone(
            "SELECT status FROM backtest_tasks WHERE task_id = 'task-stale'"
        )
        self.assertEqual(row["status"], BacktestTaskStatus.QUEUED)

    def test_cancel_queued_task_finishes_batch_as_canceled(self):
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-cancel', ?, 'Cancel', 'queued', 1, 's', 'S',
                         '{}', 'h', '{}', 1)
                """,
                (self.user.user_id,),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, created_at
                ) VALUES('task-cancel', 'batch-cancel', ?, 'queued', 'x', '{}', 1)
                """,
                (self.user.user_id,),
            )
            conn.commit()

        result = BacktestTaskRepository(self.storage).request_cancel_task(
            self.user.user_id, "task-cancel"
        )

        self.assertEqual(result["status"], BacktestTaskStatus.CANCELED)
        batch = self.storage.fetchone(
            "SELECT * FROM backtest_batches WHERE batch_id = 'batch-cancel'"
        )
        self.assertEqual(batch["status"], BacktestTaskStatus.CANCELED)
        self.assertEqual(batch["canceled_tasks"], 1)

    def test_checkpoint_persists_live_account_and_cancel_request(self):
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, user_id, batch_name, status, task_count,
                    strategy_id, strategy_name, strategy_snapshot_json,
                    strategy_snapshot_hash, template_snapshot_json, created_at
                ) VALUES('batch-live', ?, 'Live', 'running', 1, 's', 'S',
                         '{}', 'h', '{}', 1)
                """,
                (self.user.user_id,),
            )
            conn.execute(
                """
                INSERT INTO backtest_tasks(
                    task_id, batch_id, user_id, status, dataset_file_path,
                    dataset_snapshot_json, created_at
                ) VALUES('task-live', 'batch-live', ?, 'running', 'x', '{}', 1)
                """,
                (self.user.user_id,),
            )
            conn.commit()
        ledger = {
            "account": {
                "initial_balance": 10000, "balance": 10050, "equity": 10075,
                "free_margin": 10075, "margin": 0, "status": "running",
            },
            "orders": [], "positions": [], "trades": [],
            "equity_points": [],
        }
        repository = BacktestTaskRepository(self.storage)

        self.assertTrue(repository.checkpoint("task-live", 42.5, ledger))
        saved = BacktestTemplateService(self.storage).get_task_ledger(
            self.user.user_id, "task-live"
        )
        self.assertEqual(saved["account"]["equity"], 10075)
        repository.request_cancel_task(self.user.user_id, "task-live")
        self.assertTrue(repository.is_cancel_requested("task-live"))
        repository.cancel("task-live")
        task = self.storage.fetchone(
            "SELECT status FROM backtest_tasks WHERE task_id = 'task-live'"
        )
        self.assertEqual(task["status"], BacktestTaskStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()
