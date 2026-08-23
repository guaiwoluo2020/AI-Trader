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

    def test_includes_symbols_from_each_active_mt5_account(self):
        engines = {
            1: SimpleNamespace(kline_service=SimpleNamespace(
                get_symbols=lambda: ["EURUSD"]
            )),
            2: SimpleNamespace(kline_service=SimpleNamespace(
                get_symbols=lambda: ["BTCUSD"]
            )),
        }
        engine_manager = SimpleNamespace(
            get_engine_for_user=lambda user_id: engines[1],
            get_engine=lambda user_id, account_id: engines[account_id],
        )
        account_repo = SimpleNamespace(list_for_user=lambda user_id: [
            SimpleNamespace(account_id=1, account_type="mt5", status="active"),
            SimpleNamespace(account_id=2, account_type="mt5", status="active"),
            SimpleNamespace(account_id=3, account_type="paper", status="active"),
        ])
        empty_config = SimpleNamespace(get_config=lambda user_id: {"symbol_config": {}})
        empty_strategies = SimpleNamespace(get_all_strategies=lambda user_id: [])
        empty_sources = SimpleNamespace(list=lambda user_id: [])

        self.assertEqual(
            ["BTCUSD", "EURUSD"],
            collect_ai_signal_symbols(
                1, engine_manager, empty_config, empty_strategies,
                empty_sources, account_repo,
            ),
        )


if __name__ == "__main__":
    unittest.main()
