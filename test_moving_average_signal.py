#!/usr/bin/env python3
"""Moving-average signal source tests."""

import unittest
from types import SimpleNamespace

from backtest_engine import M1BacktestEngine, ReplaySignalEngine, SimPosition
from market.models.position import PositionData
from market.models import PositionManagementPolicy
from market.models.trading_strategy import TradingStrategy, normalize_signal_sources
from market.services.position_manager import PositionManager
from market.services.signal.moving_average_signal import MovingAverageSignalGenerator
from market.services.signal.signal_rules import detect_moving_average_cross
from server import TradingServer


def moving_source(**params):
    values = {
        "fast_period": 2,
        "slow_period": 3,
        "ma_type": "sma",
        "stop_loss_pct": 0.01,
        "risk_reward_ratio": 2,
        "exit_mode": "trailing_reverse",
        "trailing_activation_r": 1,
        "trailing_distance_r": 1,
        "cooldown_seconds": 0,
    }
    values.update(params)
    return {
        "signal_source_id": "ma-m1",
        "source": "moving_average",
        "enabled": True,
        "period": "M1",
        "weight": 30,
        "params": values,
    }


class MovingAverageSignalTests(unittest.TestCase):
    def test_detects_only_the_crossing_bar(self):
        self.assertEqual(
            detect_moving_average_cross([3, 2, 1, 4], 2, 3)[0], "buy"
        )
        self.assertEqual(
            detect_moving_average_cross([1, 2, 3, 0], 2, 3)[0], "sell"
        )
        self.assertIsNone(detect_moving_average_cross([1, 2, 3], 2, 3))
        self.assertIsNone(detect_moving_average_cross([1, 2, 3, 4], 2, 3))

    def test_normalization_requires_fast_period_below_slow_period(self):
        with self.assertRaisesRegex(ValueError, "快线周期"):
            normalize_signal_sources([
                moving_source(fast_period=20, slow_period=5)
            ])

    def test_normalization_removes_legacy_exit_parameters(self):
        normalized = normalize_signal_sources([moving_source(exit_mode="unknown")])
        self.assertNotIn("exit_mode", normalized[0]["params"])
        self.assertNotIn("trailing_distance_r", normalized[0]["params"])

    def test_live_generator_emits_once_even_without_cooldown(self):
        rows = [
            {"time": index * 60, "close": close}
            for index, close in enumerate([3, 2, 1, 4])
        ]

        class Store:
            @staticmethod
            def get_all_klines(_symbol, _period):
                return rows

        strategy = TradingStrategy(
            symbol="GOLD_", signal_sources=[moving_source()]
        )
        generator = MovingAverageSignalGenerator(Store())

        first = generator.generate_signals_for_strategy("GOLD_", 4, strategy)
        repeated = generator.generate_signals_for_strategy("GOLD_", 4, strategy)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].action, "buy")
        self.assertEqual(first[0].signal_source_id, "ma-m1")
        self.assertEqual(first[0].suggested_sl, 0)
        self.assertEqual(first[0].suggested_tp, 0)
        self.assertTrue(first[0].is_entry_trigger)
        self.assertEqual(len(repeated), 1)
        self.assertFalse(repeated[0].is_entry_trigger)
        self.assertEqual(repeated[0].market_direction, "up")

    def test_live_generator_keeps_pending_cross_until_confidence_qualifies(self):
        rows = [
            {"time": index * 60, "close": close}
            for index, close in enumerate([100, 99.99, 100, 100.05])
        ]

        class Store:
            @staticmethod
            def get_all_klines(_symbol, _period):
                return rows

        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_sources=[moving_source(min_confidence=90)],
        )
        generator = MovingAverageSignalGenerator(Store())

        first = generator.generate_signals_for_strategy("GOLD_", 100.05, strategy)
        rows.append({"time": 4 * 60, "close": 101})
        qualified = generator.generate_signals_for_strategy("GOLD_", 101, strategy)
        repeated = generator.generate_signals_for_strategy("GOLD_", 101, strategy)

        self.assertEqual(len(first), 1)
        self.assertFalse(first[0].is_entry_trigger)
        self.assertEqual(first[0].market_direction, "up")
        self.assertEqual(len(qualified), 1)
        self.assertTrue(qualified[0].is_entry_trigger)
        self.assertEqual(qualified[0].action, "buy")
        self.assertEqual(len(repeated), 1)
        self.assertFalse(repeated[0].is_entry_trigger)

    def test_replay_cross_has_no_signal_level_exits(self):
        strategy = TradingStrategy(
            symbol="GOLD_", signal_sources=[moving_source()]
        ).to_dict()
        seen = [
            {
                "time": index * 60, "open": close, "high": close,
                "low": close, "close": close, "tick_volume": 1,
            }
            for index, close in enumerate([3, 2, 1, 4])
        ]

        signals = ReplaySignalEngine(strategy).generate(seen, 4, 240, None)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].action, "buy")
        self.assertEqual(signals[0].suggested_sl, 0)
        self.assertEqual(signals[0].suggested_tp, 0)
        self.assertEqual(signals[0].risk_reward_ratio, 0)

    def test_live_reverse_cross_closes_only_tagged_position(self):
        strategy = TradingStrategy(
            strategy_id="strategy-1",
            symbol="GOLD_",
            signal_sources=[moving_source(exit_mode="reverse_cross")],
        )
        tagged = PositionData(
            ticket=101, symbol="GOLD_", volume=0.1, price_open=100,
            position_type="BUY", profit=1, sl=99,
            comment="AIT|strategy-1|ma-m1",
        )
        unrelated = PositionData(
            ticket=102, symbol="GOLD_", volume=0.1, price_open=100,
            position_type="BUY", profit=1, sl=99, comment="manual",
        )
        closed = []
        server = SimpleNamespace(
            position_service=SimpleNamespace(
                get_position_objects=lambda _symbol: [tagged, unrelated]
            ),
            _ma_trailing_extremes={},
            add_close_position_instruction=lambda _symbol, ticket: closed.append(ticket),
        )
        signal = SimpleNamespace(
            source="moving_average", signal_source_id="ma-m1", action="sell"
        )

        policy = PositionManagementPolicy(
            policy_id="policy-1", user_id=1, name="reverse",
            config={
                "initial_stop_rules": [{"type": "signal"}],
                "initial_take_profit_rules": [{"type": "signal"}],
                "management_rules": [{"type": "reverse_signal"}],
            },
        )
        strategy.position_management_policy_id = "policy-1"
        server.user_id = 1
        server.pivot_store = SimpleNamespace(
            get_all_periods=lambda _symbol: [], get_pivot_objects=lambda *_: []
        )
        server._position_policy_repository = SimpleNamespace(get=lambda *_: policy)
        server._managed_position_state = {}
        server._position_update_instructions = {}

        TradingServer._manage_strategy_positions(server, strategy, "GOLD_", 99, [signal])

        self.assertEqual(closed, [101])

    def test_backtest_trailing_stop_uses_initial_risk(self):
        action = PositionManager().evaluate(
            {"management_rules": [{
                "type": "trailing_stop", "activation_r": 1,
                "distance_r": 1,
            }]},
            {"direction": "buy", "entry_price": 100, "stop_loss": 99,
             "initial_risk": 1, "favorable_price": 101.5},
            {"price": 101},
        )
        self.assertEqual(action.action, "modify_sl")
        self.assertAlmostEqual(action.stop_loss, 100.5)


if __name__ == "__main__":
    unittest.main()
