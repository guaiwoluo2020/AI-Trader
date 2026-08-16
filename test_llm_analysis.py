#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 分析状态与错误处理测试。"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from market.llm_analyzer import LLMAnalyzer
from market.models import LLMAnalysisResult, TradingStrategy
from market.services.llm_service import LLMRequestError, LLMService
from sqlite_storage import SQLiteStorage, TradingAccountRepository, UserRepository


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


class _RepoBackedStore(_Store):
    def __init__(self, storage, user_id):
        super().__init__()
        self._repo = type("Repo", (), {"storage": storage})()
        self._user_id = int(user_id)

    @property
    def user_id(self):
        return self._user_id


class LLMAnalysisTestCase(unittest.TestCase):
    def test_stale_threshold_allows_the_ea_kline_sync_window(self):
        self.assertEqual(LLMService.STALE_THRESHOLD, 360)

    def test_analysis_run_retains_periods_and_sources_that_were_not_due(self):
        previous = LLMAnalysisResult(
            symbol="BTCUSD",
            trend_analysis={
                "M1": {"trend": "震荡"},
                "M5": {"trend": "单边上涨", "confidence": 82},
            },
            trade_suggestions=[
                {"signal_source_id": "m1-source", "period": "M1"},
                {"signal_source_id": "m5-source", "period": "M5"},
            ],
        )
        incoming = {
            "trend_analysis": {"M1": {"trend": "单边下跌"}},
            "trade_suggestions": [
                {"signal_source_id": "m1-source", "period": "M1"},
            ],
        }

        LLMService._retain_previous_source_results(
            incoming, previous, {"m1-source"}, {"M1"},
        )

        self.assertEqual(incoming["trend_analysis"]["M1"]["trend"], "单边下跌")
        self.assertEqual(incoming["trend_analysis"]["M5"]["confidence"], 82)
        self.assertEqual(
            [item["signal_source_id"] for item in incoming["trade_suggestions"]],
            ["m5-source", "m1-source"],
        )

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

        with patch(
            "market.services.llm_service.requests.post", return_value=response
        ) as request:
            with self.assertRaisesRegex(LLMRequestError, "不是有效 JSON"):
                service.call_llm("生成候选")
        self.assertEqual(3, request.call_count)

    def test_call_llm_retries_invalid_json_and_uses_correction_prompt(self):
        service = LLMService(_Store(), _Klines())
        invalid = Mock(status_code=200)
        invalid.json.return_value = {
            "choices": [{"message": {"content": "这不是 JSON"}}],
        }
        valid = Mock(status_code=200)
        valid.json.return_value = {
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {"total_tokens": 2},
        }

        with patch(
            "market.services.llm_service.requests.post",
            side_effect=[invalid, valid],
        ) as request:
            result = service.call_llm("生成信号")

        self.assertEqual({"status": "ok"}, result)
        self.assertEqual(2, request.call_count)
        retry_prompt = request.call_args_list[1].kwargs["json"]["messages"][-1]["content"]
        self.assertIn("响应格式纠正", retry_prompt)
        self.assertIn("生成信号", retry_prompt)

    def test_call_llm_does_not_retry_http_error(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=500, text="provider unavailable")
        response.json.side_effect = ValueError("not json")

        with patch(
            "market.services.llm_service.requests.post", return_value=response
        ) as request:
            with self.assertRaisesRegex(LLMRequestError, "HTTP 500"):
                service.call_llm("生成信号")

        self.assertEqual(1, request.call_count)

    def test_call_llm_stream_retries_invalid_json(self):
        service = LLMService(_Store(), _Klines())

        def stream_response(content):
            response = Mock(status_code=200)
            chunk = json.dumps({
                "choices": [{"delta": {"content": content}}],
            }, ensure_ascii=False)
            response.iter_lines.return_value = [
                f"data: {chunk}".encode("utf-8"),
                b"data: [DONE]",
            ]
            return response

        with patch(
            "market.services.llm_service.requests.post",
            side_effect=[
                stream_response("不是 JSON"),
                stream_response('{"direction":"buy"}'),
            ],
        ) as request:
            result = service.call_llm_stream("分析行情")

        self.assertEqual({"direction": "buy"}, result)
        self.assertEqual(2, request.call_count)

    def test_call_llm_stream_retries_response_missing_requested_period(self):
        service = LLMService(_Store(), _Klines())

        def stream_response(content):
            response = Mock(status_code=200)
            chunk = json.dumps({
                "choices": [{"delta": {"content": content}}],
            }, ensure_ascii=False)
            response.iter_lines.return_value = [
                f"data: {chunk}".encode("utf-8"),
                b"data: [DONE]",
            ]
            return response

        plan = {
            "BTCUSD": {"periods": {"M5": {"weight": 100}}},
        }
        with patch(
            "market.services.llm_service.requests.post",
            side_effect=[
                stream_response('{"BTCUSD":{"trend_analysis":{}}}'),
                stream_response(
                    '{"BTCUSD":{"trend_analysis":{"M5":{"trend":"上涨","confidence":80}}}}'
                ),
            ],
        ) as request:
            result = service.call_llm_stream(
                "分析行情",
                response_validator=lambda payload: (
                    service._validate_analysis_response(payload, plan)
                ),
            )

        self.assertIn("BTCUSD", result)
        self.assertEqual(2, request.call_count)

    def test_call_llm_uses_reasoning_content_when_content_is_empty(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {
                "content": "",
                "reasoning_content": '{"candidates":[{"name":"趋势候选"}]}'
            }}],
            "usage": {"total_tokens": 1},
        }

        with patch("market.services.llm_service.requests.post", return_value=response):
            result = service.call_llm("生成候选")

        self.assertEqual("趋势候选", result["candidates"][0]["name"])

    def test_message_content_supports_segmented_text(self):
        content = LLMService._message_content({
            "content": [
                {"type": "text", "text": "{\"candidates\":"},
                {"type": "text", "text": "[]}"},
            ]
        })
        self.assertEqual('{"candidates":[]}', content)

    def test_call_llm_reads_choice_text_payload(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"text": '{"candidates":[{"name":"文本字段候选"}]}'}],
            "usage": {"total_tokens": 1},
        }

        with patch("market.services.llm_service.requests.post", return_value=response):
            result = service.call_llm("生成候选")

        self.assertEqual("文本字段候选", result["candidates"][0]["name"])

    def test_call_llm_reads_responses_output_payload(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=200)
        response.json.return_value = {
            "output": [{
                "content": [{
                    "type": "output_text",
                    "text": '{"candidates":[{"name":"Responses候选"}]}',
                }],
            }],
            "usage": {"total_tokens": 1},
        }

        with patch("market.services.llm_service.requests.post", return_value=response):
            result = service.call_llm("生成候选")

        self.assertEqual("Responses候选", result["candidates"][0]["name"])

    def test_call_llm_error_includes_response_preview(self):
        service = LLMService(_Store(), _Klines())
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"total_tokens": 1},
        }

        with patch("market.services.llm_service.requests.post", return_value=response):
            with self.assertRaisesRegex(LLMRequestError, "finish_reason"):
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

    def test_normalize_accepts_list_response_for_single_symbol(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            strategy_id="2e0ea156",
            strategy_name="大模型策略",
            signal_config={
                "ai_entry": {
                    "enabled": True,
                    "periods": {"M5": {"enabled": True, "weight": 30}},
                },
            },
        )
        service = LLMService(_Store(), _Klines())
        service.set_strategy_store(_StrategyStore([strategy]))
        plan = service._build_ai_analysis_plan(["GOLD_"])
        response = [{
            "period": "M5",
            "direction": "buy",
            "entry_price": 4100.0,
            "stop_loss": 4090.0,
            "take_profit": 4120.0,
            "confidence": 82,
        }]

        suggestions = service._normalize_analysis_response(
            response, plan
        )["GOLD_"]["trade_suggestions"]

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["strategy_id"], "2e0ea156")
        self.assertEqual(suggestions[0]["period"], "M5")

    def test_period_weight_items_accepts_list_period_profiles(self):
        service = LLMService(_Store(), _Klines())
        plan = {
            "GOLD_": {
                "periods": {"M5": {"weight": 30, "kline_count": 100}},
                "strategies": [{
                    "strategy_id": "s1",
                    "strategy_name": "列表周期策略",
                    "signal_source_id": "src1",
                    "periods": [{"period": "M5", "weight": 30}],
                    "min_confidence": 70,
                    "min_risk_reward": 1.0,
                    "kline_count": 100,
                }],
            },
        }

        prompt = service.build_analysis_prompt(
            {"GOLD_": {"M5": _Klines().get_klines("GOLD_", "M5", 1)}},
            plan,
        )
        groups = service._group_analysis_plans(plan)

        self.assertIn("M5(权重30)", prompt)
        self.assertEqual(groups[0]["plan"]["GOLD_"]["periods"]["M5"]["weight"], 30)

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

    def test_paper_deployment_is_included_in_ai_analysis_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = SQLiteStorage(str(Path(temp_dir) / "paper-ai.db"))
            storage.initialize()
            user = UserRepository(storage).create_user("paper-ai", "hash", "salt")
            account = TradingAccountRepository(storage).create_paper_account(
                user.user_id, "Paper AI", 1000
            )
            strategy = TradingStrategy.from_dict({
                "strategy_id": "paper-ai-strategy",
                "strategy_name": "模拟AI策略",
                "symbol": "GOLD_",
                "enabled": False,
                "auto_execute": False,
                "lifecycle_status": "backtest_passed",
                "signal_sources": [{
                    "signal_source_id": "paper-ai-source",
                    "source": "ai_entry",
                    "enabled": True,
                    "period": "M1",
                    "weight": 30,
                    "params": {"analysis_interval_minutes": 5},
                }],
            })
            now = int(time.time())
            storage.execute(
                """
                INSERT INTO strategy_deployments(
                    deployment_id, user_id, account_id, strategy_id, symbol,
                    source_backtest_task_id, strategy_version_at, scheduled_end_at,
                    execution_mode, status, created_at, updated_at
                ) VALUES('paper-ai-deploy', ?, ?, ?, 'GOLD_',
                         '', ?, NULL, 'paper', 'active', ?, ?)
                """,
                (
                    user.user_id, account.account_id, strategy.strategy_id,
                    now, now, now,
                ),
            )
            service = LLMService(_RepoBackedStore(storage, user.user_id), _Klines())
            service.set_strategy_store(_StrategyStore([]))
            service.set_allowed_strategy_ids([])

            plan = service._build_ai_analysis_plan(["GOLD_"], due_only=True)

            self.assertEqual(set(plan), {"GOLD_"})
            self.assertEqual(
                plan["GOLD_"]["strategies"][0]["strategy_id"],
                "paper-ai-strategy",
            )
            self.assertEqual(set(plan["GOLD_"]["periods"]), {"M1"})


if __name__ == "__main__":
    unittest.main()
