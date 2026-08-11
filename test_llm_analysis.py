#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 分析状态与错误处理测试。"""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from market.llm_analyzer import LLMAnalyzer
from market.models import TradingStrategy
from market.services.llm_service import LLMRequestError, LLMService


class _Config:
    enabled = True
    api_key = "test-key"
    api_base = "https://example.test/v1"
    model = "test-model"


class _Store:
    def __init__(self):
        self.status = "idle"
        self.message = ""
        self.saved = {}

    def get_config(self):
        return _Config()

    def set_analysis_status(self, status, message):
        self.status = status
        self.message = message

    def update_market_status(self, *args, **kwargs):
        pass

    def save_analysis_dict(self, symbol, analysis):
        self.saved[symbol] = analysis

    def cleanup_entry_alerts(self):
        pass

    def get_last_analysis_time(self):
        return None

    def get_analyzed_symbols(self):
        return list(self.saved)


class _Klines:
    def get_symbols(self):
        return ["GOLD_"]

    def check_symbols_status(self, symbols, stale_threshold):
        return {"active": symbols, "stale": [], "closed": []}

    def get_klines(self, symbol, period, count):
        return [{
            "timestamp": "2026-07-30 23:30:00",
            "open": 4100.0,
            "high": 4101.0,
            "low": 4099.0,
            "close": 4100.5,
        }]


class _AsyncService:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.llm_store = _Store()

    def is_enabled(self):
        return True

    def run_analysis(self, on_status=None, on_complete=None):
        self.started.set()
        self.release.wait(1)
        return {"status": "ok", "analyzed_symbols": []}


class _StrategyStore:
    def __init__(self, strategies):
        self.strategies = strategies

    def get_all_strategies(self):
        return self.strategies


class LLMAnalysisTestCase(unittest.TestCase):
    def test_parse_llm_response_extracts_json_from_text(self):
        service = LLMService(_Store(), _Klines())
        content = (
            "下面是候选结果：\n"
            "```json\n"
            "{\"candidates\":[{\"name\":\"趋势动量\",\"factors\":[]}]}\n"
            "```\n"
            "请查收。"
        )

        parsed = service._parse_llm_response(content)

        self.assertEqual(parsed["candidates"][0]["name"], "趋势动量")

    def test_call_llm_raises_when_provider_returns_empty_content(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": ""}}],
            "usage": {"total_tokens": 1},
        }

        with patch("market.services.llm_service.requests.post", return_value=response):
            with self.assertRaisesRegex(LLMRequestError, "不是有效 JSON"):
                service.call_llm("生成候选")

    def test_due_ai_plan_respects_each_source_interval(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_sources=[
                {
                    "signal_source_id": "ai-m5",
                    "source": "ai_entry",
                    "period": "M5",
                    "weight": 30,
                    "params": {"analysis_interval_minutes": 5},
                },
                {
                    "signal_source_id": "ai-m15",
                    "source": "ai_entry",
                    "period": "M15",
                    "weight": 30,
                    "params": {"analysis_interval_minutes": 15},
                },
            ],
        )
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore([strategy]))
        service._source_last_analysis_at = {"ai-m5": 1000, "ai-m15": 1000}

        with patch("market.services.llm_service.time.monotonic", return_value=1301):
            plan = service._build_ai_analysis_plan(["GOLD_"], due_only=True)

        self.assertEqual(set(plan["GOLD_"]["periods"]), {"M5"})
        self.assertEqual(
            plan["GOLD_"]["strategies"][0]["signal_source_id"], "ai-m5"
        )

    def test_provider_error_is_returned_and_saved(self):
        store = _Store()
        service = LLMService(store, _Klines())
        response = Mock(status_code=404, text='{"message":"model_not_found"}')
        response.json.return_value = {"message": "model_not_found"}
        statuses = []

        with patch("market.services.llm_service.requests.post", return_value=response):
            result = service.run_analysis(
                on_status=lambda status, message: statuses.append((status, message))
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("model_not_found", result["message"])
        self.assertEqual(store.status, "error")
        self.assertIn("model_not_found", store.message)
        self.assertEqual(statuses[-1][0], "error")

    def test_manual_trigger_returns_immediately_and_rejects_duplicate(self):
        service = _AsyncService()
        analyzer = LLMAnalyzer(service)

        started_at = time.monotonic()
        first = analyzer.trigger_analysis()
        elapsed = time.monotonic() - started_at
        self.assertTrue(service.started.wait(0.5))
        second = analyzer.trigger_analysis()

        self.assertEqual(first["status"], "accepted")
        self.assertLess(elapsed, 0.5)
        self.assertEqual(second["status"], "busy")
        service.release.set()

    def test_analysis_plan_merges_enabled_ai_periods_for_same_symbol(self):
        first = TradingStrategy(
            symbol="GOLD_",
            strategy_name="短线策略",
            signal_config={
                "ai_entry": {
                    "enabled": True,
                    "periods": {
                        "M5": {"enabled": True, "weight": 20},
                        "H1": {"enabled": False, "weight": 40},
                    },
                },
            },
            min_confidence=70,
            min_risk_reward=1.5,
        )
        second = TradingStrategy(
            symbol="GOLD_",
            strategy_name="趋势策略",
            signal_config={
                "ai_entry": {
                    "enabled": True,
                    "periods": {
                        "M5": {"enabled": True, "weight": 35},
                        "H1": {"enabled": True, "weight": 30},
                    },
                },
            },
            min_confidence=80,
            min_risk_reward=2.0,
        )
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore([first, second]))

        plan = service._build_ai_analysis_plan(["GOLD_", "BTCUSD"])
        prompt = service.build_analysis_prompt(
            {"GOLD_": {"M5": _Klines().get_klines("GOLD_", "M5", 1)}},
            plan,
        )

        self.assertEqual(set(plan), {"GOLD_"})
        self.assertEqual(set(plan["GOLD_"]["periods"]), {"M5", "H1"})
        self.assertEqual(plan["GOLD_"]["periods"]["M5"]["weight"], 35)
        self.assertIn("短线策略", prompt)
        self.assertIn("趋势策略", prompt)
        self.assertIn("最低置信度 80%", prompt)

    def test_custom_prompt_injects_runtime_context_and_changes_hash(self):
        class Config:
            enabled = True
            api_key = "test-key"
            api_base = "https://example.test/v1"
            model = "test-model"
            system_prompt = "system-v1"
            analysis_prompt_template = (
                "CONSTRAINTS\n{{strategy_context}}\nDATA\n{{market_data}}"
            )
            prompt_version = 2

        class Store(_Store):
            def __init__(self):
                super().__init__()
                self.config = Config()

            def get_config(self):
                return self.config

        store = Store()
        service = LLMService(store, _Klines())
        prompt = service.build_analysis_prompt(
            {"GOLD_": {"M1": _Klines().get_klines("GOLD_", "M1", 1)}}
        )
        first_hash = service.prompt_hash(prompt)
        store.config.system_prompt = "system-v2"

        self.assertIn("CONSTRAINTS", prompt)
        self.assertIn("GOLD_", prompt)
        self.assertIn("2026-07-30 23:30:00", prompt)
        self.assertNotEqual(first_hash, service.prompt_hash(prompt))

    def test_analysis_skips_provider_when_no_strategy_enables_ai(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_config={
                "ai_entry": {
                    "enabled": False,
                    "periods": {
                        "M5": {"enabled": True, "weight": 20},
                    },
                },
            },
        )
        store = _Store()
        service = LLMService(store, _Klines())
        service.set_strategy_store(_StrategyStore([strategy]))

        with patch("market.services.llm_service.requests.post") as request:
            result = service.run_analysis()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(store.status, "skipped")
        request.assert_not_called()

    def test_model_period_and_risk_reward_are_normalized_for_strategy(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            strategy_id="2e0ea156",
            signal_config={
                "ai_entry": {
                    "enabled": True,
                    "periods": {"M1": {"enabled": True, "weight": 15}},
                },
            },
            min_risk_reward=1.3,
        )
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore([strategy]))
        plan = service._build_ai_analysis_plan(["GOLD_"])
        response = {
            "GOLD_": {
                "trade_suggestions": [{
                    "period": "1分钟大模型趋势 (2e0ea156)",
                    "direction": "sell",
                    "entry_price": 4037.37,
                    "stop_loss": 4062.0,
                    "take_profit": 4030.0,
                    "confidence": 80,
                }],
            },
        }

        normalized = service._normalize_analysis_response(response, plan)
        suggestion = normalized["GOLD_"]["trade_suggestions"][0]

        self.assertEqual(suggestion["period"], "M1")
        risk = suggestion["stop_loss"] - suggestion["entry_price"]
        reward = suggestion["entry_price"] - suggestion["take_profit"]
        self.assertAlmostEqual(reward / risk, 1.3)

    def test_ai_suggestions_are_split_and_bound_to_each_strategy(self):
        strategies = [
            TradingStrategy(
                symbol="GOLD_",
                strategy_name=name,
                signal_config={
                    "ai_entry": {
                        "enabled": True,
                        "periods": {"M5": {"enabled": True, "weight": 30}},
                    },
                },
                min_risk_reward=min_rr,
            )
            for name, min_rr in (("稳健策略", 1.5), ("进取策略", 2.0))
        ]
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore(strategies))
        plan = service._build_ai_analysis_plan(["GOLD_"])
        response = {
            "GOLD_": {
                "trade_suggestions": [{
                    "period": "M5",
                    "direction": "buy",
                    "entry_price": 4100.0,
                    "stop_loss": 4090.0,
                    "take_profit": 4112.0,
                    "confidence": 82,
                }],
            },
        }

        suggestions = service._normalize_analysis_response(
            response, plan
        )["GOLD_"]["trade_suggestions"]

        self.assertEqual(len(suggestions), 2)
        self.assertEqual(
            {item["strategy_id"] for item in suggestions},
            {strategy.strategy_id for strategy in strategies},
        )
        self.assertEqual(
            {item["strategy_name"] for item in suggestions},
            {strategy.strategy_name for strategy in strategies},
        )
        self.assertEqual(
            sorted(item["take_profit"] for item in suggestions),
            [4115.0, 4120.0],
        )

    def test_ai_plan_only_contains_strategies_deployed_on_account(self):
        strategies = [
            TradingStrategy(
                symbol="GOLD_",
                strategy_name=name,
                signal_config={
                    "ai_entry": {
                        "enabled": True,
                        "periods": {"M5": {"enabled": True, "weight": 30}},
                    },
                },
            )
            for name in ("账户策略", "未绑定策略")
        ]
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore(strategies))
        service.set_allowed_strategy_ids([strategies[0].strategy_id])

        plan = service._build_ai_analysis_plan(["GOLD_"])

        self.assertEqual(
            [item["strategy_id"] for item in plan["GOLD_"]["strategies"]],
            [strategies[0].strategy_id],
        )


if __name__ == "__main__":
    unittest.main()
