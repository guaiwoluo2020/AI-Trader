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
from market.services.signal.signal_service import SignalService
from market.services.signal.key_level_signal import (
    KeyLevelSignalGenerator,
    evaluate_key_level_expression,
)


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


class _StrategySignalGenerator:
    def generate_signals_for_strategy(self, symbol, current_price, strategy):
        return [TradingSignal(
            symbol=symbol,
            action="buy",
            confidence=90,
            source="key_level",
            source_period="M1",
            suggested_entry=current_price,
            suggested_sl=current_price - 10,
            suggested_tp=current_price + 20,
        )]


class StrategyServiceTestCase(unittest.TestCase):
    @staticmethod
    def _consensus_strategy(requirement="majority"):
        return TradingStrategy(
            symbol="GOLD_",
            min_confidence=50,
            consistency_requirement=requirement,
            signal_sources=[
                {"signal_source_id": "key", "source": "key_level", "period": "M1", "weight": 20, "params": {}},
                {"signal_source_id": "ma-m1", "source": "moving_average", "period": "M1", "weight": 20, "params": {}},
                {"signal_source_id": "ma-m5", "source": "moving_average", "period": "M5", "weight": 20, "params": {}},
                {"signal_source_id": "ai-m1", "source": "ai_entry", "period": "M1", "weight": 20, "params": {}},
                {"signal_source_id": "ai-m5", "source": "ai_entry", "period": "M5", "weight": 20, "params": {}},
            ],
        )

    @staticmethod
    def _state(strategy, source_id, direction, confidence=80, trigger=False):
        config = next(
            item for item in strategy.signal_sources
            if item["signal_source_id"] == source_id
        )
        return TradingSignal(
            symbol="GOLD_",
            action={"up": "buy", "down": "sell"}.get(direction, "none"),
            market_direction=direction,
            confidence=confidence,
            state_ready=True,
            is_entry_trigger=trigger,
            source=config["source"],
            source_period=config["period"],
            signal_source_id=source_id,
            suggested_entry=4100,
            suggested_sl=4090 if direction == "up" else 4110,
            suggested_tp=4120 if direction == "up" else 4080,
        )

    def test_majority_requires_at_least_sixty_percent_same_direction(self):
        strategy = self._consensus_strategy()
        service = StrategyService(
            strategy_store=_StrategyStore(strategy), risk_manager=_RiskManager()
        )
        sixty_percent = [
            self._state(strategy, "key", "up"),
            self._state(strategy, "ma-m1", "up"),
            self._state(strategy, "ma-m5", "up"),
            self._state(strategy, "ai-m1", "down"),
            self._state(strategy, "ai-m5", "sideways"),
        ]
        below_threshold = [
            self._state(strategy, "key", "up"),
            self._state(strategy, "ma-m1", "up"),
            self._state(strategy, "ma-m5", "sideways"),
            self._state(strategy, "ai-m1", "down"),
            self._state(strategy, "ai-m5", "sideways"),
        ]

        accepted = service.analyze_signals("GOLD_", sixty_percent, strategy)
        rejected = service.analyze_signals("GOLD_", below_threshold, strategy)

        self.assertEqual(accepted["action"], "buy")
        self.assertEqual(accepted["consistency"], 0.6)
        self.assertEqual(rejected["action"], "none")

    def test_high_weight_minority_cannot_override_vote_majority(self):
        strategy = self._consensus_strategy()
        for config in strategy.signal_sources:
            config["weight"] = 5 if config["signal_source_id"] != "ai-m1" else 100
        signals = [
            self._state(strategy, "key", "up", 70),
            self._state(strategy, "ma-m1", "up", 70),
            self._state(strategy, "ma-m5", "up", 70),
            self._state(strategy, "ai-m1", "down", 99),
            self._state(strategy, "ai-m5", "sideways", 80),
        ]

        analysis = StrategyService(
            strategy_store=_StrategyStore(strategy), risk_manager=_RiskManager()
        ).analyze_signals("GOLD_", signals, strategy)

        self.assertEqual(analysis["action"], "buy")
        self.assertGreater(analysis["sell_weighted_score"], analysis["buy_weighted_score"])

    def test_all_requires_every_configured_source_to_report_same_direction(self):
        strategy = self._consensus_strategy("all")
        service = StrategyService(
            strategy_store=_StrategyStore(strategy), risk_manager=_RiskManager()
        )
        four_of_five = [
            self._state(strategy, source_id, "up")
            for source_id in ("key", "ma-m1", "ma-m5", "ai-m1")
        ]
        all_five = four_of_five + [self._state(strategy, "ai-m5", "up")]

        self.assertEqual(
            service.analyze_signals("GOLD_", four_of_five, strategy)["action"],
            "none",
        )
        self.assertEqual(
            service.analyze_signals("GOLD_", all_five, strategy)["action"],
            "buy",
        )

    def test_persistent_consensus_does_not_repeat_without_new_trigger(self):
        strategy = TradingStrategy(
            symbol="GOLD_", min_confidence=50,
            consistency_requirement="majority",
            signal_sources=[{
                "signal_source_id": "key", "source": "key_level",
                "period": "M1", "weight": 100, "params": {},
            }],
        )
        service = StrategyService(
            strategy_store=_StrategyStore(strategy), risk_manager=_RiskManager()
        )
        service.decision_cooldown = 0
        state = self._state(strategy, "key", "up", trigger=False)

        first = service.make_decision(
            "GOLD_", 4100, force_signals=[state], strategy=strategy
        )
        repeated = service.make_decision(
            "GOLD_", 4100, force_signals=[state], strategy=strategy
        )

        self.assertIsNotNone(first)
        self.assertIsNone(repeated)

    def test_ai_interval_cannot_be_shorter_than_signal_period(self):
        strategy = TradingStrategy(symbol="GOLD_", signal_sources=[{
            "signal_source_id": "ai-m15",
            "source": "ai_entry",
            "period": "M15",
            "params": {"analysis_interval_minutes": 5},
        }, {
            "signal_source_id": "ai-h4",
            "source": "ai_entry",
            "period": "H4",
            "params": {"analysis_interval_minutes": 60},
        }])

        self.assertEqual(
            strategy.signal_sources[0]["params"]["analysis_interval_minutes"], 15
        )
        self.assertEqual(
            strategy.signal_sources[1]["params"]["analysis_interval_minutes"], 240
        )

    def test_signal_source_period_must_be_unique_within_same_type(self):
        duplicate = [
            {
                "signal_source_id": source_id,
                "source": "key_level",
                "period": "M5",
                "weight": 30,
                "params": {},
            }
            for source_id in ("key-a", "key-b")
        ]

        with self.assertRaisesRegex(ValueError, "不能重复添加"):
            TradingStrategy(symbol="GOLD_", signal_sources=duplicate)

    def test_multiple_validated_alphas_can_share_a_period(self):
        sources = []
        for index in (1, 2):
            sources.append({
                "signal_source_id": f"alpha-{index}",
                "source": "alpha_factor",
                "period": "M5",
                "weight": 30,
                "params": {
                    "alpha_id": f"library-{index}",
                    "alpha_snapshot": {
                        "timeframe": "M5", "factors": [{"name": "ema"}],
                        "params": {"factor_0_length": 5},
                    },
                },
            })
        strategy = TradingStrategy(symbol="GOLD_", signal_sources=sources)
        self.assertEqual(2, len(strategy.signal_sources))

    def test_key_level_cannot_mix_with_ai_or_moving_average_on_update(self):
        strategy = TradingStrategy(symbol="GOLD_", signal_sources=[])

        with self.assertRaisesRegex(ValueError, "不能和AI/均线信号源同时存在"):
            strategy.update({"signal_sources": [
                {
                    "signal_source_id": "key",
                    "source": "key_level",
                    "period": "M1",
                    "weight": 30,
                    "params": {},
                },
                {
                    "signal_source_id": "ai",
                    "source": "ai_entry",
                    "period": "M5",
                    "weight": 30,
                    "params": {},
                },
            ]})

    def test_legacy_periods_are_migrated_to_independent_sources(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            strategy_id="gold-strategy",
            min_confidence=82,
            signal_config={
                "pivot": {
                    "enabled": True,
                    "periods": {
                        "M1": {"enabled": True, "weight": 15},
                        "M5": {"enabled": True, "weight": 20},
                    },
                },
                "key_level": {"enabled": False, "weight": 40},
                "ai_entry": {
                    "enabled": True,
                    "periods": {"M15": {"enabled": True, "weight": 35}},
                },
            },
        )

        self.assertEqual(len(strategy.signal_sources), 1)
        self.assertEqual(
            {(item["source"], item["period"]) for item in strategy.signal_sources},
            {("ai_entry", "M15")},
        )
        ai_source = strategy.get_signal_sources("ai_entry")[0]
        self.assertEqual(ai_source["params"]["min_confidence"], 82)
        self.assertTrue(ai_source["signal_source_id"].startswith("gold-strategy-"))

    def test_key_level_expression_is_restricted_and_generates_signal(self):
        self.assertEqual(
            evaluate_key_level_expression("round(price / 100) * 100", 4102.0),
            [4100.0],
        )
        with self.assertRaises(ValueError):
            evaluate_key_level_expression("__import__('os').getcwd()", 4102.0)

        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_sources=[{
                "signal_source_id": "key-expression",
                "source": "key_level",
                "period": "M1",
                "weight": 40,
                "params": {
                    "level_mode": "expression",
                    "expression": "round(price / 100) * 100",
                    "proximity_threshold": 0.001,
                    "cooldown_seconds": 0,
                },
            }],
        )
        signals = KeyLevelSignalGenerator().generate_signals_for_strategy(
            "GOLD_", 4102.0, strategy
        )

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].key_level, 4100.0)
        self.assertEqual(signals[0].signal_source_id, "key-expression")

    def test_key_level_breakout_uses_configured_trigger_rules(self):
        strategy = TradingStrategy(
            symbol="GOLD_",
            signal_sources=[{
                "signal_source_id": "key-breakout",
                "source": "key_level",
                "period": "M15",
                "weight": 40,
                "params": {
                    "level_mode": "levels",
                    "levels": [4100],
                    "order_distance": 0.001,
                    "cooldown_seconds": 0,
                    "upward_approach_sell": False,
                    "downward_approach_buy": False,
                    "upward_breakout_buy": True,
                    "downward_breakout_sell": True,
                },
            }],
        )
        generator = KeyLevelSignalGenerator()

        first = generator.generate_signals_for_strategy("GOLD_", 4098.0, strategy)
        second = generator.generate_signals_for_strategy("GOLD_", 4101.0, strategy)

        self.assertFalse(first[0].is_entry_trigger)
        self.assertTrue(second[0].is_entry_trigger)
        self.assertEqual(second[0].action, "buy")
        self.assertEqual(second[0].source_period, "M1")
        self.assertEqual(second[0].suggested_sl, 0)
        self.assertEqual(second[0].suggested_tp, 0)

    def test_signal_generation_assigns_each_signal_to_its_strategy(self):
        service = SignalService()
        service.register_generator("key_level", _StrategySignalGenerator())
        strategies = [
            TradingStrategy(
                symbol="GOLD_",
                strategy_name=name,
                signal_sources=[{
                    "signal_source_id": f"key-{name}",
                    "source": "key_level",
                    "period": "M1",
                    "weight": 30,
                    "params": {},
                }],
            )
            for name in ("转折策略A", "转折策略B")
        ]

        generated = [
            service.generate_signals_for_strategy("GOLD_", 4100.0, strategy)[0]
            for strategy in strategies
        ]

        self.assertEqual(
            [signal.strategy_id for signal in generated],
            [strategy.strategy_id for strategy in strategies],
        )
        self.assertEqual(
            [signal.strategy_name for signal in generated],
            [strategy.strategy_name for strategy in strategies],
        )

    def test_strategy_does_not_consume_another_strategy_signal(self):
        first = TradingStrategy(
            symbol="GOLD_",
            signal_config={"key_level": {"enabled": True, "weight": 100}},
            min_confidence=70,
        )
        second = TradingStrategy(
            symbol="GOLD_",
            signal_config={"key_level": {"enabled": True, "weight": 100}},
            min_confidence=70,
        )
        service = StrategyService(
            strategy_store=_StrategyStore([first, second]),
            risk_manager=_RiskManager(),
        )
        signal = TradingSignal(
            symbol="GOLD_",
            action="sell",
            confidence=90,
            source="key_level",
            strategy_id=first.strategy_id,
            suggested_entry=4110.0,
            suggested_sl=4120.0,
            suggested_tp=4090.0,
        )

        self.assertIsNotNone(service.make_decision(
            "GOLD_", 4110.0, force_signals=[signal], strategy=first
        ))
        self.assertIsNone(service.make_decision(
            "GOLD_", 4110.0, force_signals=[signal], strategy=second
        ))

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
