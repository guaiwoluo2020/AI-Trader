#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略决策服务测试。"""

import unittest

from market.models import TradingSignal, TradingStrategy
from market.services.strategy.strategy_service import StrategyService


class _StrategyStore:
    def __init__(self, strategy):
        self.strategy = strategy

    def get_or_create_strategy(self, symbol):
        return self.strategy


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


if __name__ == "__main__":
    unittest.main()
