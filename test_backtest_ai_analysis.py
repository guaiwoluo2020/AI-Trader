#!/usr/bin/env python3
"""Tests for asynchronous AI review of backtest results."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from backtest_ai_analysis import BacktestAIAnalysisService
from backtest_data import BacktestDatasetRepository, BacktestDatasetService
from backtest_tasks import BacktestTemplateService
from market.models import PositionManagementPolicy
from market.models.trading_strategy import TradingStrategy
from sqlite_storage import (
    PositionManagementPolicyRepository,
    SQLiteStorage,
    StrategyConfigRepository,
    TradingAccountRepository,
    UserRepository,
)


class _FakeLLMStore:
    def get_config(self):
        return SimpleNamespace(enabled=True, model="test-model")


class _FakeLLMService:
    def __init__(self, result=None):
        self.llm_store = _FakeLLMStore()
        self.result = result or {
            "executive_summary": "策略有正期望，但样本量不足。",
            "data_quality": {"level": "medium", "notes": ["仅一个数据集"]},
            "diagnosis": [],
            "optimization_suggestions": [],
            "risk_warnings": ["先进行样本外验证"],
            "next_backtest_plan": {"changes": [], "acceptance_criteria": []},
        }
        self.prompt = ""

    def call_llm_stream(self, prompt, **_kwargs):
        self.prompt = prompt
        return self.result


class BacktestAIAnalysisTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "test.db"))
        self.storage.initialize()
        users = UserRepository(self.storage)
        self.user = users.create_user("ai-review-admin", "hash", "salt", role="admin")
        self.other = users.create_user("ai-review-other", "hash", "salt")
        account, _ = TradingAccountRepository(self.storage).create_or_rotate_default(
            self.user.user_id
        )
        PositionManagementPolicyRepository(self.storage).save(
            PositionManagementPolicy(
                policy_id="policy-1", user_id=self.user.user_id,
                name="Review exits", config={
                    "initial_stop_rules": [{"type": "signal"}],
                    "initial_take_profit_rules": [{"type": "signal"}],
                    "management_rules": [],
                },
            )
        )
        strategy = TradingStrategy(
            symbol="GOLD_", strategy_name="Gold Review",
            position_management_policy_id="policy-1",
        )
        StrategyConfigRepository(self.storage).save_strategy(self.user.user_id, strategy)
        datasets = BacktestDatasetRepository(self.storage)
        dataset_service = BacktestDatasetService(
            datasets, Path(self.temp_dir.name) / "data"
        )
        dataset = dataset_service.create_dataset(
            self.user.user_id, account.account_id, "Review data", "GOLD_",
            1767225600, 1767232800, warmup_days=0,
        )
        datasets.mark_ready(
            dataset["dataset_id"],
            received_bars=121,
            duplicate_count=0,
            gap_count=0,
            invalid_count=0,
            quality_score=100,
            data_format="csv.gz",
            file_path=str(Path(self.temp_dir.name) / "data.csv.gz"),
            data_hash="review-hash",
        )
        template_service = BacktestTemplateService(self.storage)
        template = template_service.create_template(self.user.user_id, {
            "template_name": "Review template",
            "strategy_id": strategy.strategy_id,
            "dataset_ids": [dataset["dataset_id"]],
            "initial_capital": 100000,
        })
        batch = template_service.run_template(self.user.user_id, template["template_id"])
        self.task_id = batch["tasks"][0]["task_id"]
        self.fake_llm = _FakeLLMService()
        self.service = BacktestAIAnalysisService(
            self.storage, llm_factory=lambda _user_id: self.fake_llm
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _complete_task(self):
        result = {
            "net_profit": 1200,
            "total_return_pct": 1.2,
            "trade_count": 12,
            "equity_curve": [{"time": index, "equity": 100000 + index} for index in range(200)],
            "drawdown_curve": [{"time": index, "drawdown_pct": 1} for index in range(200)],
            "signal_source_stats": [{"signal_source": "pivot", "trade_count": 12}],
        }
        self.storage.execute(
            """
            UPDATE backtest_tasks SET status = 'completed', progress = 100,
                result_json = ?, completed_at = ? WHERE task_id = ?
            """,
            (json.dumps(result), int(time.time()), self.task_id),
        )

    def test_only_finished_or_canceled_task_can_be_analyzed(self):
        with self.assertRaisesRegex(ValueError, "已完成或已取消"):
            self.service.start_analysis(
                self.user.user_id, self.user.role, self.task_id
            )

    def test_analysis_is_user_scoped(self):
        self._complete_task()
        self.assertIsNone(self.service.get_analysis(self.other.user_id, self.task_id))
        with self.assertRaisesRegex(LookupError, "不存在"):
            self.service.start_analysis(
                self.other.user_id, self.other.role, self.task_id
            )

    def test_completed_analysis_is_persisted_and_prompt_is_compact(self):
        self._complete_task()
        started = self.service.start_analysis(
            self.user.user_id, self.user.role, self.task_id
        )
        self.assertIn(started["status"], {"queued", "running", "completed"})

        deadline = time.time() + 2
        analysis = started
        while time.time() < deadline and analysis["status"] in {"queued", "running"}:
            time.sleep(0.01)
            analysis = self.service.get_analysis(self.user.user_id, self.task_id)

        self.assertEqual(analysis["status"], "completed")
        self.assertEqual(analysis["model"], "test-model")
        self.assertEqual(
            analysis["result"]["executive_summary"],
            "策略有正期望，但样本量不足。",
        )
        self.assertIn('"equity_sample"', self.fake_llm.prompt)
        self.assertNotIn('"equity_curve"', self.fake_llm.prompt)
        self.assertNotIn('"drawdown_curve"', self.fake_llm.prompt)
        self.assertNotIn('"signal_config"', self.fake_llm.prompt)
        self.assertNotIn('"signal_weights"', self.fake_llm.prompt)
        self.assertNotIn('"period_weights"', self.fake_llm.prompt)
        self.assertTrue(analysis["prompt_hash"])

    def test_trade_and_equity_sampling_are_bounded(self):
        trades = [
            {"opened_at": index, "closed_at": index + 1, "entry_price": index, "net_profit": index - 100}
            for index in range(250)
        ]
        equity = [{"time": index, "equity": index} for index in range(500)]

        self.assertLessEqual(len(self.service._sample_trades(trades)), 100)
        sampled = self.service._downsample(equity, 120)
        self.assertEqual(len(sampled), 120)
        self.assertEqual(sampled[0], equity[0])
        self.assertEqual(sampled[-1], equity[-1])

    def test_analysis_snapshot_only_contains_enabled_signal_sources(self):
        snapshot = {
            "strategy_id": "strategy-filter",
            "strategy_name": "Filter test",
            "symbol": "GOLD_",
            "signal_config": {
                "ai_entry": {
                    "enabled": True,
                    "periods": {"M5": {"enabled": True, "weight": 30}},
                }
            },
            "signal_weights": {"key_level": 30, "ai_entry": 30},
            "period_weights": {"M1": 10, "M5": 20},
            "signal_sources": [
                {
                    "signal_source_id": "key-m1",
                    "source": "key_level",
                    "enabled": True,
                    "period": "M1",
                    "weight": 50,
                    "params": {"level_mode": "automatic"},
                },
                {
                    "signal_source_id": "ai-m5-disabled",
                    "source": "ai_entry",
                    "enabled": False,
                    "period": "M5",
                    "weight": 30,
                    "params": {"kline_count": 100},
                },
                {
                    "signal_source_id": "ma-m15-zero",
                    "source": "moving_average",
                    "enabled": True,
                    "period": "M15",
                    "weight": 0,
                    "params": {"fast_period": 5, "slow_period": 20},
                },
            ],
        }

        cleaned = self.service._strategy_for_analysis(snapshot)

        self.assertNotIn("signal_config", cleaned)
        self.assertNotIn("signal_weights", cleaned)
        self.assertNotIn("period_weights", cleaned)
        self.assertEqual(
            [item["signal_source_id"] for item in cleaned["signal_sources"]],
            ["key-m1"],
        )
        self.assertNotIn("enabled", cleaned["signal_sources"][0])


if __name__ == "__main__":
    unittest.main()
