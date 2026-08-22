import unittest
from types import SimpleNamespace

from routes_market import collect_ai_signal_symbols


class AISignalOptionsTests(unittest.TestCase):
    def test_includes_ea_reported_symbols(self):
        engine = SimpleNamespace(
            kline_service=SimpleNamespace(
                get_symbols=lambda: ["GOLD_", "BTCUSD"]
            )
        )
        engine_manager = SimpleNamespace(
            get_engine_for_user=lambda user_id: engine
        )
        trade_config_repo = SimpleNamespace(
            get_config=lambda user_id: {
                "symbol_config": {"GOLD_": {}, "EURUSD": {}}
            }
        )
        strategy_repo = SimpleNamespace(
            get_all_strategies=lambda user_id: [
                SimpleNamespace(symbol="USDJPY"),
            ]
        )
        signal_source_repo = SimpleNamespace(
            list=lambda user_id: [
                {"symbol": "BTCUSD"},
                {"symbol": "GBPUSD"},
            ]
        )

        symbols = collect_ai_signal_symbols(
            1,
            engine_manager,
            trade_config_repo,
            strategy_repo,
            signal_source_repo,
        )

        self.assertEqual(
            ["BTCUSD", "EURUSD", "GBPUSD", "GOLD_", "USDJPY"],
            symbols,
        )


if __name__ == "__main__":
    unittest.main()
