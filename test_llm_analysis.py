#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 分析状态与错误处理测试。"""

import threading
import time
import unittest
from unittest.mock import Mock, patch

from market.llm_analyzer import LLMAnalyzer
from market.services.llm_service import LLMService


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


class LLMAnalysisTestCase(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
