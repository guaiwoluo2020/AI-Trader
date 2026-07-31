#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略决策服务测试。"""

import unittest

from market.models import TradingSignal, TradingStrategy
from market.services.strategy.strategy_service import StrategyService


class _StrategyStore:
    def __init__(self, strategy):
        self.strategies = strategy if isinstance(strategy, list) else [strategy]

    def get_or_create_strategy(self, symbol):
        return self.strategies[0]

    def get_strategies(self, symbol):
        return [strategy for strategy in self.strategies if strategy.symbol == symbol]


class _RiskManager:
    def calculate_volume(self, symbol, risk_points, strategy):
        return 0.01

    def check_position_limit(
        self,
        symbol,
        strategy,
        current_positions,
        same_direction,
        opposite_direction,
        action,
    ):
        return {
            "allowed": False,
            "current_positions": current_positions,
            "same_direction": same_direction,
            "opposite_direction": opposite_direction,
            "max_positions": strategy.max_positions,
            "max_same_direction": strategy.max_same_direction,
            "warnings": ["同向持仓将超过限制 2"],
        }

    def check_risk(self, symbol, volume, risk_points):
        return {"allowed": True, "warnings": []}


class _PositionService:
    def get_positions(self, symbol):
        return [{"direction": "sell"}, {"direction": "sell"}]


class StrategyServiceTestCase(unittest.TestCase):
    def test_rejected_decision_preserves_proposed_trade_parameters(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_config={"key_level": {"enabled": True, "weight": 100}},
            min_confidence=70,
            max_positions=3,
            max_same_direction=2,
        )
        service = StrategyService(
            strategy_store=_StrategyStore(strategy),
            risk_manager=_RiskManager(),
        )
        service.set_position_service(_PositionService())
        signal = TradingSignal(
            symbol="GOLD_",
            action="sell",
            confidence=90,
            source="key_level",
            suggested_entry=4113.78,
            suggested_sl=4124.60,
            suggested_tp=4059.67,
        )

        decision = service.make_decision(
            "GOLD_",
            current_price=4113.78,
            force_signals=[signal],
        )

        self.assertEqual(decision.status, "rejected")
        self.assertEqual(decision.action, "sell")
        self.assertEqual(decision.entry_price, 4113.78)
        self.assertEqual(decision.sl, 4124.60)
        self.assertEqual(decision.tp, 4059.67)
        self.assertEqual(decision.volume, 0.01)
        self.assertIn("同向持仓将超过限制 2", decision.decision_reason)
        self.assertIsNone(service.execute_decision(decision))

    def test_same_symbol_strategies_make_independent_decisions(self):
        strategies = [
            TradingStrategy(
                symbol="GOLD_",
                strategy_name="趋势策略",
                signal_config={"key_level": {"enabled": True, "weight": 100}},
                min_confidence=70,
            ),
            TradingStrategy(
                symbol="GOLD_",
                strategy_name="突破策略",
                signal_config={"key_level": {"enabled": True, "weight": 100}},
                min_confidence=80,
            ),
        ]
        service = StrategyService(
            strategy_store=_StrategyStore(strategies),
            risk_manager=_RiskManager(),
        )
        signal = TradingSignal(
            symbol="GOLD_",
            action="sell",
            confidence=90,
            source="key_level",
            suggested_entry=4113.78,
            suggested_sl=4124.60,
            suggested_tp=4059.67,
        )

        decisions = service.make_decisions(
            "GOLD_", 4113.78, force_signals=[signal]
        )

        self.assertEqual(len(decisions), 2)
        self.assertEqual(
            {decision.strategy_name for decision in decisions},
            {"趋势策略", "突破策略"},
        )
        self.assertEqual(len({decision.strategy_id for decision in decisions}), 2)

    def test_decision_confidence_is_normalized_after_signal_weighting(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_config={
                "ai_entry": {
                    "enabled": True,
                    "periods": {
                        "M1": {"enabled": True, "weight": 15},
                        "M5": {"enabled": False, "weight": 20},
                    },
                },
                "pivot": {
                    "enabled": False,
                    "periods": {"M1": {"enabled": False, "weight": 30}},
                },
            },
            min_confidence=70,
        )
        service = StrategyService(
            strategy_store=_StrategyStore(strategy),
            risk_manager=_RiskManager(),
        )
        enabled_signal = TradingSignal(
            symbol="GOLD_",
            action="sell",
            confidence=75,
            source="ai_entry",
            source_period="M1",
            suggested_entry=4038.24,
            suggested_sl=4045.0,
            suggested_tp=4028.0,
        )
        unrelated_signal = TradingSignal(
            symbol="GOLD_",
            action="sell",
            confidence=90,
            source="ai_entry",
            source_period="M5",
            suggested_entry=4038.24,
            suggested_sl=4045.0,
            suggested_tp=4028.0,
        )

        decision = service.make_decision(
            "GOLD_",
            current_price=4038.24,
            force_signals=[enabled_signal, unrelated_signal],
        )

        self.assertEqual(decision.confidence_score, 75)
        self.assertEqual(decision.signal_summary["sell_weighted_score"], 11.25)
        self.assertEqual(len(decision.signals), 1)
        self.assertEqual(decision.signals[0]["source_period"], "M1")


if __name__ == "__main__":
    unittest.main()
