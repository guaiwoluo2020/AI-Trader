#!/usr/bin/env python3

import unittest
import json
import tempfile
import time
from unittest.mock import patch

import pandas as pd
import numpy as np
import optuna

from alpha_research import (
    AlphaBacktestEngine,
    AlphaCandidateService,
    AlphaOptimizationEngine,
    AlphaLibraryRepository,
    AlphaResearchRepository,
    FactorCatalog,
)
from sqlite_storage import SQLiteStorage
from market.services.signal.alpha_factor_signal import AlphaRuntimeExecutor


class AlphaBacktestFactorValidityTest(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([
            {
                "time": index * 60,
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "tick_volume": 10,
                "spread": 0,
            }
            for index in range(8)
        ])

    def test_factor_catalog_exposes_and_calculates_ema(self):
        catalog = FactorCatalog()
        names = {item["name"] for item in catalog.list()}
        self.assertIn("ema", names)
        values = catalog.calculate(self.frame, "ema", 3)
        self.assertEqual(len(self.frame), len(values))
        self.assertTrue(values.notna().any())
        ema = next(item for item in catalog.list() if item["name"] == "ema")
        self.assertEqual("指数移动平均", ema["display_name"])
        self.assertEqual("价格平滑", ema["category_label"])

    def test_time_session_factors_use_only_prior_same_slot_bars(self):
        rows = []
        start = pd.Timestamp("2026-01-05 01:00:00", tz="UTC")
        for day in range(4):
            for minute, multiplier in ((0, 1.01), (15, 0.99)):
                open_price = 100 + day * 2 + minute / 100
                rows.append({
                    "time": int((start + pd.Timedelta(days=day, minutes=minute)).timestamp()),
                    "open": open_price, "high": open_price * 1.02,
                    "low": open_price * 0.98, "close": open_price * multiplier,
                    "tick_volume": 100, "spread": 0,
                })
        frame = pd.DataFrame(rows)
        catalog = FactorCatalog()
        names = {item["name"] for item in catalog.list()}
        self.assertIn("previous_day_same_slot_return", names)
        self.assertIn("same_slot_mean_return", names)
        previous = catalog.calculate(
            frame, "previous_day_same_slot_return", 2, "Asia/Shanghai"
        )
        average = catalog.calculate(
            frame, "same_slot_mean_return", 2, "Asia/Shanghai"
        )
        self.assertAlmostEqual(0.01, previous.iloc[4], places=8)
        self.assertAlmostEqual(-0.01, previous.iloc[5], places=8)
        self.assertAlmostEqual(0.01, average.iloc[4], places=8)
        self.assertAlmostEqual(-0.01, average.iloc[5], places=8)

    def test_time_slot_report_uses_configured_local_time_zone(self):
        rows = []
        start = pd.Timestamp("2026-01-05 01:00:00", tz="UTC")
        for day in range(35):
            for minute, base_multiplier in ((0, 1.005), (15, 0.995)):
                open_price = 100 + day * 0.1 + minute / 100
                multiplier = base_multiplier + (day % 3 - 1) * 0.001
                rows.append({
                    "time": int((start + pd.Timedelta(days=day, minutes=minute)).timestamp()),
                    "open": open_price, "high": open_price * 1.01,
                    "low": open_price * 0.99, "close": open_price * multiplier,
                    "tick_volume": 100, "spread": 0,
                })
        frame = pd.DataFrame(rows)
        config = {
            "factors": [{
                "name": "same_slot_mean_return", "length_min": 5,
                "length_max": 5, "weight_min": 1, "weight_max": 1,
            }],
            "time_zone": "Asia/Shanghai", "same_slot_lookback_days": 5,
            "prediction_horizon": 1, "buy_threshold_min": 0.2,
            "sell_threshold_max": -0.2, "confirmation_bars": 1,
            "cooldown_bars": 0,
        }
        params = {
            "factor_0_length": 5, "factor_0_weight": 1,
            "buy_threshold": 0.2, "sell_threshold": -0.2,
        }
        report = AlphaBacktestEngine().time_slot_report(frame, config, params)
        self.assertEqual("Asia/Shanghai", report["time_zone"])
        self.assertIn("09:00", [item["slot"] for item in report["items"]])

    def test_optuna_can_search_a_factor_backtest(self):
        count = 600
        close = 100 + np.linspace(0, 8, count) + np.sin(np.arange(count) / 8)
        frame = pd.DataFrame({
            "time": np.arange(count) * 60,
            "open": close - 0.05,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "tick_volume": np.full(count, 100),
            "spread": np.zeros(count),
        })
        config = {
            "factors": [{
                "name": "ema", "length_min": 5, "length_max": 20,
                "weight_min": 0.5, "weight_max": 1.0,
            }],
            "buy_threshold_min": 0.2,
            "sell_threshold_max": -0.2,
            "prediction_horizon": 10,
            "confirmation_bars": 1,
            "cooldown_bars": 0,
        }
        engine = AlphaBacktestEngine()
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
        )

        def objective(trial):
            params = {
                "factor_0_length": trial.suggest_int("factor_0_length", 5, 20),
                "factor_0_weight": trial.suggest_float("factor_0_weight", 0.5, 1.0),
                "buy_threshold": trial.suggest_float("buy_threshold", 0.2, 1.5),
                "sell_threshold": trial.suggest_float("sell_threshold", -1.5, -0.2),
            }
            return engine.run(frame, config, params).score

        study.optimize(objective, n_trials=5)
        self.assertEqual(5, len(study.trials))
        self.assertIsInstance(study.best_value, float)

    def test_evaluation_start_keeps_warmup_but_scores_only_requested_segment(self):
        count = 120
        close = 100 + np.sin(np.arange(count) / 5)
        frame = pd.DataFrame({
            "time": np.arange(count) * 60,
            "open": close,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "tick_volume": np.full(count, 100),
            "spread": np.zeros(count),
        })
        config = {
            "factors": [{
                "name": "ema", "length_min": 5, "length_max": 5,
                "weight_min": 1, "weight_max": 1,
            }],
            "buy_threshold_min": 0.2,
            "sell_threshold_max": -0.2,
            "prediction_horizon": 1,
            "confirmation_bars": 1,
            "cooldown_bars": 0,
        }
        params = {
            "factor_0_length": 5, "factor_0_weight": 1,
            "buy_threshold": 0.2, "sell_threshold": -0.2,
        }
        result = AlphaBacktestEngine().run(frame, config, params, evaluation_start=100)
        self.assertLessEqual(result.metrics["sample_count"], 20)
        self.assertFalse(hasattr(result, "trades"))

    def test_rolling_ic_and_decay_measure_factor_predictive_power(self):
        alpha = pd.Series(np.linspace(-2, 2, 120))
        future = alpha * 0.01
        signals = pd.Series(np.where(alpha >= 0, 1, -1), dtype="int8")
        metrics = AlphaBacktestEngine._metrics(
            alpha,
            signals,
            future,
            {1: future, 3: future * 0.8, 5: future * 0.5},
        )
        self.assertAlmostEqual(1.0, metrics["rolling_ic_mean"], places=6)
        self.assertGreater(metrics["rolling_ic_count"], 1)
        self.assertEqual([1, 3, 5], [item["horizon"] for item in metrics["decay"]])
        self.assertTrue(all(item["rank_ic"] > 0.99 for item in metrics["decay"]))
        self.assertIn("ic_ir", metrics)
        self.assertIn("return_ir", metrics)
        self.assertIn("ic_t_stat", metrics)
        self.assertGreater(metrics["positive_rank_ic_ratio"], 0.9)
        self.assertGreater(metrics["quintile_analysis"]["monotonicity"], 0.7)

    def test_factor_preprocessing_does_not_leak_future_values(self):
        first = pd.Series(np.sin(np.arange(160) / 9) + np.arange(160) / 100)
        baseline = AlphaBacktestEngine._preprocess_factor(first, 10)
        extended = AlphaBacktestEngine._preprocess_factor(
            pd.concat([first, pd.Series([10000.0] * 20)], ignore_index=True), 10
        )
        pd.testing.assert_series_equal(baseline, extended.iloc[:160], check_names=False)


class AlphaIterationAuditTest(unittest.TestCase):
    def test_prompt_history_excludes_hidden_test(self):
        safe = AlphaCandidateService._prompt_iteration({
            "iteration": 1,
            "candidate": {"hypothesis": "trend", "factors": [{"name": "ema"}]},
            "metrics": {
                "train": {"score": 10},
                "validation": {"score": 8},
                "hidden_test": {"score": 999},
            },
        })
        encoded = json.dumps(safe)
        self.assertNotIn("hidden_test", encoded)
        self.assertNotIn("999", encoded)

    def test_repository_persists_iteration_prompt_and_trial_round(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(f"{directory}/alpha.db")
            storage.initialize()
            now = int(time.time())
            storage.execute(
                "INSERT INTO users(username, password_hash, salt, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?)",
                ("alpha-user", "hash", "salt", now, now),
            )
            storage.execute(
                "INSERT INTO alpha_research_runs(run_id, user_id, research_name, config_json, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                ("run-1", 1, "research", "{}", now),
            )
            repository = AlphaResearchRepository(storage)
            repository.save_trial(
                "run-1", 5, "completed", 1.2, {}, {}, 10,
                iteration_number=2,
            )
            repository.save_iteration(
                "run-1", 2, "completed", {"name": "candidate"},
                "Alpha = ema", {}, {"objective_score": 1.2},
                "validation-only prompt", {"candidate": {}}, "model-x",
                completed=True,
            )
            self.assertEqual(2, repository.list_trials("run-1")[0]["iteration_number"])
            iteration = repository.list_iterations("run-1")[0]
            self.assertEqual("validation-only prompt", iteration["feedback_prompt"])
            self.assertEqual("model-x", iteration["llm_model"])

    def test_two_round_optimization_refines_from_validation_history(self):
        count = 320
        close = 100 + np.linspace(0, 3, count) + np.sin(np.arange(count) / 7)
        bars = [
            {
                "time": index * 60,
                "open": float(close[index]),
                "high": float(close[index] + 0.2),
                "low": float(close[index] - 0.2),
                "close": float(close[index]),
                "tick_volume": 100,
                "spread": 0,
            }
            for index in range(count)
        ]

        class Repository:
            storage = object()

            def __init__(self):
                self.trials = []
                self.iterations = []

            def save_trial(self, *args, **kwargs):
                self.trials.append((args, kwargs))

            def save_iteration(self, *args, **kwargs):
                self.iterations.append((args, kwargs))

        class CandidateService:
            def __init__(self):
                self.histories = []

            def refine(self, user_id, description, timeframe, horizon, candidate, history):
                self.histories.append(json.loads(json.dumps(history)))
                self_test = json.dumps(history)
                if "hidden_test" in self_test:
                    raise AssertionError("hidden test leaked into refinement")
                return {
                    "candidate": {
                        "candidate_id": "round-2", "name": "RSI revision",
                        "theme": "momentum", "hypothesis": "momentum revision",
                        "buy_logic": "high alpha", "sell_logic": "low alpha",
                        "factors": [{
                            "name": "rsi", "length_min": 5, "length_max": 15,
                            "weight_min": 0.5, "weight_max": 1.5,
                        }],
                    },
                    "prompt": "validation-only",
                    "response": {"diagnosis": "replace trend with momentum"},
                    "model": "model-x",
                }

        repository = Repository()
        candidate_service = CandidateService()
        config = {
            "dataset_id": "dataset-1", "timeframe": "M1",
            "research_name": "two rounds", "research_description": "test iteration",
            "research_mode": "ai", "llm_iteration_count": 2,
            "candidate_meta": {"name": "EMA", "hypothesis": "trend"},
            "factors": [{
                "name": "ema", "length_min": 5, "length_max": 15,
                "weight_min": 0.5, "weight_max": 1.5,
            }],
            "prediction_horizon": 5, "confirmation_bars": 1,
            "cooldown_bars": 0,
            "buy_threshold_min": 0.2, "buy_threshold_max": 1.2,
            "sell_threshold_min": -1.2, "sell_threshold_max": -0.2,
            "trial_count": 5, "random_seed": 42,
        }
        engine = AlphaOptimizationEngine(
            repository, candidate_service=candidate_service
        )
        with patch("alpha_research.BacktestDatasetRepository") as datasets, patch(
            "alpha_research.HistoricalBarReader.read", return_value=bars
        ):
            datasets.return_value.get_visible.return_value = {
                "status": "ready", "file_path": "ignored", "data_format": "csv.gz"
            }
            result, _, _ = engine.run({
                "run_id": "run-2", "user_id": 1, "config": config
            })

        self.assertEqual(2, result["completed_iterations"])
        self.assertEqual(10, result["trial_count"])
        self.assertIn("hidden_test", result["splits"])
        self.assertEqual(10, result["experiment_cost"]["independent_runs"])
        self.assertGreater(result["experiment_cost"]["residual_candidates"], 0)
        self.assertEqual(2, result["experiment_cost"]["ablation_variants"])
        self.assertIn("independent_evaluation", result)
        self.assertIn("residual_evaluation", result)
        self.assertIn("ablation_experiment", result)
        self.assertEqual(1, len(candidate_service.histories))
        self.assertEqual(1, len(candidate_service.histories[0]))
        self.assertEqual(
            {1, 2},
            {kwargs["iteration_number"] for _, kwargs in repository.trials},
        )


class AlphaLibraryAndRuntimeTest(unittest.TestCase):
    @staticmethod
    def research_frame(count=300):
        close = 100 + np.linspace(0, 4, count) + np.sin(np.arange(count) / 7)
        return pd.DataFrame({
            "time": np.arange(count) * 60,
            "open": close, "high": close + 0.2, "low": close - 0.2,
            "close": close, "tick_volume": np.full(count, 100),
            "spread": np.zeros(count),
        })

    @staticmethod
    def research_config(factor_count=2):
        return {
            "factors": [{
                "name": "ema", "length_min": 8, "length_max": 8,
                "weight_min": 1, "weight_max": 1,
            } for _ in range(factor_count)],
            "prediction_horizon": 5,
            "buy_threshold_min": 0.5, "sell_threshold_max": -0.5,
            "confirmation_bars": 1, "cooldown_bars": 0,
        }

    @staticmethod
    def research_params(factor_count=2):
        params = {"buy_threshold": 0.5, "sell_threshold": -0.5}
        for index in range(factor_count):
            params[f"factor_{index}_length"] = 8
            params[f"factor_{index}_weight"] = 1
        return params

    def test_independent_gate_and_residual_evaluation_are_layered(self):
        frame = self.research_frame()
        config = self.research_config()
        params = self.research_params()
        engine = AlphaOptimizationEngine(object())
        independent = engine.independent_evaluation(
            frame, config, params, evaluation_start=180
        )
        residual = engine.residual_evaluation(frame, 180, config, params)
        self.assertEqual(2, independent["factor_count"])
        self.assertTrue(all("rank_ic" in item for item in independent["factors"]))
        self.assertEqual(2, residual["factor_count"])
        self.assertTrue(all(
            item["retained_variance_ratio"] < 0.001
            for item in residual["factors"]
        ))

    def test_final_ablation_runs_baseline_plus_each_factor(self):
        frame = self.research_frame()
        config = self.research_config()
        params = self.research_params()
        engine = AlphaOptimizationEngine(object())
        baseline = engine.backtest.run(frame, config, params, evaluation_start=180)
        experiment = engine.ablation_experiment(
            frame, 180, config, params, baseline
        )
        self.assertEqual(3, experiment["variant_count"])
        self.assertEqual(
            ["baseline", "remove_factor", "remove_factor"],
            [item["variant"] for item in experiment["variants"]],
        )

    def test_validated_run_can_be_published_and_read_as_shared(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = SQLiteStorage(f"{directory}/alpha-library.db")
            storage.initialize()
            now = int(time.time())
            for username in ("owner", "viewer"):
                storage.execute(
                    "INSERT INTO users(username, password_hash, salt, created_at, updated_at) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (username, "hash", "salt", now, now),
                )
            storage.execute(
                "INSERT INTO alpha_research_runs(run_id, user_id, research_name, status, config_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                ("run-1", 1, "EMA Alpha", "completed", "{}", now),
            )
            result = {
                "runtime_definition": {
                    "timeframe": "M5",
                    "factors": [{"name": "ema", "length_min": 5}],
                    "params": {"factor_0_length": 5, "factor_0_weight": 1},
                    "buy_threshold": 0.5,
                    "sell_threshold": -0.5,
                },
                "metrics": {
                    "coverage": 0.2, "factor_coverage": 0.9,
                    "rolling_ic_count": 10, "rank_ic": 0.2,
                    "positive_rank_ic_ratio": 0.7,
                    "quintile_analysis": {"monotonicity": 0.75, "top_bottom_spread": 0.01},
                },
                "splits": {"hidden_test": {"rank_ic": 0.1}},
                "ablation_experiment": {"useful_factor_ratio": 1.0},
            }
            repository = AlphaLibraryRepository(storage)
            alpha = repository.publish_run(1, {
                "run_id": "run-1", "research_name": "EMA Alpha",
                "status": "completed", "result": result,
            }, "shared")
            self.assertEqual("validated", alpha["status"])
            visible = repository.get_visible(2, alpha["alpha_id"])
            self.assertEqual("M5", visible["definition"]["timeframe"])
            self.assertFalse(visible["is_owner"])

    def test_runtime_executor_is_deterministic_for_same_snapshot(self):
        close = 100 + np.sin(np.arange(300) / 8) + np.arange(300) / 200
        bars = [{
            "time": index * 300, "open": value, "high": value + 0.2,
            "low": value - 0.2, "close": value, "tick_volume": 100,
            "spread": 0,
        } for index, value in enumerate(close)]
        definition = {
            "factors": [{"name": "ema", "length_min": 8}],
            "params": {"factor_0_length": 8, "factor_0_weight": 1},
            "buy_threshold": 0.5, "sell_threshold": -0.5,
        }
        live = AlphaRuntimeExecutor().evaluate(bars, definition)
        replay = AlphaRuntimeExecutor().evaluate(bars, definition)
        self.assertTrue(live["ready"])
        self.assertEqual(live, replay)


if __name__ == "__main__":
    unittest.main()
