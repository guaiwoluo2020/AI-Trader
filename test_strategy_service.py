#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略决策服务测试。"""

import unittest

from market.models import (
    StrategyLifecycle,
    TradingDecision,
    TradingSignal,
    TradingStrategy,
)
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


class _PendingOrderService:
    def __init__(self):
        self.created = []
        self.confirmed = []

    def create_order(self, **kwargs):
        self.created.append(kwargs)
        return "order-1"

    def confirm_order(self, order_id):
        self.confirmed.append(order_id)
        return {"order_id": order_id}


class StrategyServiceTestCase(unittest.TestCase):
    def test_strategy_lifecycle_follows_validation_sequence(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            enabled=False,
            lifecycle_status=StrategyLifecycle.DRAFT,
        )

        strategy.transition_lifecycle(StrategyLifecycle.BACKTESTING)
        strategy.transition_lifecycle(StrategyLifecycle.BACKTEST_PASSED)
        strategy.transition_lifecycle(StrategyLifecycle.PAPER_TRADING)
        strategy.transition_lifecycle(StrategyLifecycle.PRODUCTION)

        self.assertEqual(
            strategy.lifecycle_status, StrategyLifecycle.PRODUCTION
        )
        self.assertFalse(strategy.enabled)
        self.assertEqual(len(strategy.lifecycle_history), 4)
        self.assertEqual(
            strategy.lifecycle_history[-1]["to_status"],
            StrategyLifecycle.PRODUCTION,
        )

    def test_strategy_lifecycle_rejects_skipping_validation(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            enabled=False,
            lifecycle_status=StrategyLifecycle.DRAFT,
        )

        with self.assertRaisesRegex(ValueError, "不允许"):
            strategy.transition_lifecycle(StrategyLifecycle.PRODUCTION)

    def test_material_change_returns_production_strategy_to_draft(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            enabled=True,
            auto_execute=True,
            lifecycle_status=StrategyLifecycle.PRODUCTION,
        )

        strategy.update({"min_confidence": 80})

        self.assertEqual(strategy.lifecycle_status, StrategyLifecycle.DRAFT)
        self.assertFalse(strategy.enabled)
        self.assertFalse(strategy.auto_execute)
        self.assertEqual(
            strategy.lifecycle_history[-1]["reason"],
            "策略参数已修改，需要重新验证",
        )

    def test_draft_strategy_does_not_generate_decision(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            enabled=False,
            lifecycle_status=StrategyLifecycle.DRAFT,
        )
        service = StrategyService(
            strategy_store=_StrategyStore(strategy),
            risk_manager=_RiskManager(),
        )
        signal = TradingSignal(
            symbol="GOLD_",
            action="buy",
            confidence=90,
            source="key_level",
            suggested_entry=4000.0,
            suggested_sl=3990.0,
            suggested_tp=4020.0,
        )

        decision = service.make_decision(
            "GOLD_", 4000.0, force_signals=[signal]
        )

        self.assertIsNone(decision)

    def test_retired_strategy_cannot_be_modified(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            enabled=False,
            lifecycle_status=StrategyLifecycle.RETIRED,
        )

        with self.assertRaisesRegex(ValueError, "不可修改"):
            strategy.update({"min_confidence": 80})

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

    def test_account_strategy_filter_only_runs_bound_strategy(self):
        strategies = [
            TradingStrategy(
                symbol="GOLD_", strategy_name=name,
                signal_config={"key_level": {"enabled": True, "weight": 100}},
                min_confidence=70,
            )
            for name in ("账户A策略", "账户B策略")
        ]
        service = StrategyService(
            strategy_store=_StrategyStore(strategies), risk_manager=_RiskManager()
        )
        service.set_allowed_strategy_ids([strategies[1].strategy_id])
        signal = TradingSignal(
            symbol="GOLD_", action="sell", confidence=90,
            source="key_level", suggested_entry=4113.78,
            suggested_sl=4124.60, suggested_tp=4059.67,
        )

        decisions = service.make_decisions(
            "GOLD_", 4113.78, force_signals=[signal]
        )

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].strategy_id, strategies[1].strategy_id)

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

    def test_auto_execute_strategy_confirms_order_and_uses_ea_action(self):
        strategy = TradingStrategy(symbol="GOLD_", auto_execute=True)
        restored_strategy = TradingStrategy.from_dict(strategy.to_dict())
        pending_orders = _PendingOrderService()
        service = StrategyService(
            strategy_store=_StrategyStore(strategy),
            risk_manager=_RiskManager(),
        )
        service.set_pending_order_service(pending_orders)
        decision = TradingDecision(
            symbol="GOLD_",
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.strategy_name,
            auto_execute=True,
            action="buy",
            entry_price=4020.0,
            sl=4010.0,
            tp=4040.0,
            volume=0.01,
        )

        order_id = service.execute_decision(decision)

        self.assertEqual(order_id, "order-1")
        self.assertTrue(restored_strategy.auto_execute)
        self.assertEqual(pending_orders.created[0]["action"], "b")
        self.assertEqual(pending_orders.confirmed, ["order-1"])
        self.assertTrue(decision.auto_executed)
        self.assertEqual(decision.status, "confirmed")

    def test_manual_strategy_keeps_order_pending(self):
        strategy = TradingStrategy(symbol="GOLD_", auto_execute=False)
        pending_orders = _PendingOrderService()
        service = StrategyService(
            strategy_store=_StrategyStore(strategy),
            risk_manager=_RiskManager(),
        )
        service.set_pending_order_service(pending_orders)
        decision = TradingDecision(
            symbol="GOLD_",
            strategy_id=strategy.strategy_id,
            auto_execute=False,
            action="sell",
            entry_price=4020.0,
            sl=4030.0,
            tp=4000.0,
        )

        service.execute_decision(decision)

        self.assertEqual(pending_orders.created[0]["action"], "s")
        self.assertEqual(pending_orders.confirmed, [])
        self.assertFalse(decision.auto_executed)
        self.assertEqual(decision.status, "pending")


if __name__ == "__main__":
    unittest.main()
