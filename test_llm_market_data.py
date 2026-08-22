import unittest

from market.services.llm_service import LLMService


class LLMMarketDataTests(unittest.TestCase):
    def test_preserves_forex_price_precision_in_prompt(self):
        service = LLMService.__new__(LLMService)
        service.llm_store = type(
            "Store", (), {"get_config": lambda self: type("Config", (), {
                "analysis_prompt_template": "{{market_data}}",
            })()}
        )()
        plan = {
            "EURUSD": {
                "periods": {"M5": {"kline_count": 1}},
                "strategies": [{"symbol": "EURUSD", "periods": {"M5": 100}}],
            }
        }
        prompt = service.build_analysis_prompt(
            {"EURUSD": {"M5": [{
                "timestamp": 1,
                "open": 1.08543,
                "high": 1.08601,
                "low": 1.08502,
                "close": 1.08578,
            }]}},
            plan,
            analysis_prompt_template="{{market_data}}",
        )

        self.assertIn("1.08543", prompt)
        self.assertIn("1.08601", prompt)
        self.assertIn("1.08502", prompt)

    def test_reports_insufficient_primary_kline_data(self):
        missing = LLMService._missing_primary_kline_data(
            {"BTCUSD": {"M5": [{}, {}]}},
            {"BTCUSD": {"periods": {"M5": {"kline_count": 300}}}},
        )

        self.assertEqual(["BTCUSD/M5 (2/300)"], missing)

    def test_injects_latest_observable_price_into_prompt(self):
        service = LLMService.__new__(LLMService)
        service.llm_store = type(
            "Store", (), {"get_config": lambda self: type("Config", (), {
                "analysis_prompt_template": "{{current_price}}",
            })()}
        )()
        plan = {
            "BTCUSD": {
                "periods": {"M5": {"kline_count": 1}},
                "strategies": [{"symbol": "BTCUSD", "periods": {"M5": 1}}],
            }
        }
        prompt = service.build_analysis_prompt(
            {"BTCUSD": {"M5": [{"timestamp": 123, "open": 100,
                                   "high": 101, "low": 99, "close": 100.5}]}},
            plan,
            analysis_prompt_template="{{current_price}}",
        )

        self.assertIn("BTCUSD: 100.5", prompt)
        self.assertIn("报价时间: 123", prompt)


if __name__ == "__main__":
    unittest.main()
