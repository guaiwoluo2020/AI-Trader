#!/usr/bin/env python3
"""Regression tests for durable, deployment-scoped decision cooldowns."""

import unittest
from datetime import datetime

from market.models import TradingSignal, TradingStrategy
from market.services.strategy.strategy_service import StrategyService


class _StrategyStore:
    def __init__(self, strategy):
        self.strategy = strategy
        self._user_id = 7

    def get_or_create_strategy(self, symbol):
        return self.strategy


class _RiskManager:
    def calculate_volume(self, symbol, risk_points, strategy):
        return 0.01

    def check_risk(self, symbol, volume, risk_points):
        return {"allowed": True, "warnings": []}


class _CooldownRepository:
    def __init__(self):
        self.rows = {}
        self.writes = []

    def get_active_until(self, cooldown_key, now=None):
        return int(self.rows.get(cooldown_key, 0))

    def set_cooldown(self, cooldown_key, cooldown_until, **metadata):
        self.rows[cooldown_key] = int(cooldown_until)
        self.writes.append((cooldown_key, metadata))


def _strategy():
    return TradingStrategy(
        strategy_id="strategy-1",
        strategy_name="Structure M5",
        symbol="BTCUSD",
        min_confidence=50,
        min_risk_reward=1.0,
        signal_sources=[{
            "signal_source_id": "structure-m5",
            "source": "structure_plan",
            "period": "M5",
            "weight": 100,
            "params": {},
        }],
    )


def _signal(stage="initial", direction="buy", plan_id="plan-1"):
    price = 100.0
    return TradingSignal(
        signal_id=f"signal-{stage}-{direction}",
        strategy_id="strategy-1",
        signal_source_id="structure-m5",
        symbol="BTCUSD",
        action=direction,
        confidence=90,
        source="structure_plan",
        source_period="M5",
        suggested_entry=price,
        suggested_sl=99.5 if direction == "buy" else 100.5,
        suggested_tp=101.0 if direction == "buy" else 99.0,
        trade_plan_id=plan_id,
        trade_opportunity_stage=stage,
    )


def _make(service, signal, identity, at):
    return service.make_decision(
        "BTCUSD", 100.0,
        force_signals=[signal],
        strategy=_strategy(),
        execution_mode=identity["execution_mode"],
        cooldown_identity=identity,
        decision_time=at,
        volume_calculator=lambda *_: 0.01,
        position_checker=lambda *_: {"allowed": True, "warnings": []},
        risk_checker=lambda *_: {"allowed": True, "warnings": []},
        audit_no_action=True,
    )


class StrategyDecisionCooldownTests(unittest.TestCase):
    def setUp(self):
        self.repo = _CooldownRepository()
        self.at = datetime(2026, 9, 12, 10, 0, 0)
        self.identity = {
            "execution_mode": "paper",
            "user_id": 7,
            "account_id": 22,
            "deployment_id": "deployment-22",
        }

    def service(self):
        service = StrategyService(_StrategyStore(_strategy()), risk_manager=_RiskManager())
        service.set_cooldown_repository(self.repo, user_id=7, account_id=22)
        return service

    def test_cooldown_starts_only_after_order_creation_is_confirmed(self):
        service = self.service()

        decision = _make(service, _signal(), self.identity, self.at)

        self.assertEqual([], self.repo.writes)
        service.activate_decision_cooldown(decision, self.at)
        self.assertEqual(1, len(self.repo.writes))
        key, metadata = self.repo.writes[0]
        self.assertEqual(
            "paper:7:22:deployment-22:strategy-1:plan-1:initial:buy",
            key,
        )
        self.assertEqual("deployment-22", metadata["deployment_id"])
        self.assertEqual("initial", metadata["plan_stage"])

    def test_persisted_cooldown_survives_service_restart(self):
        first = self.service()
        decision = _make(first, _signal(), self.identity, self.at)
        first.activate_decision_cooldown(decision, self.at)

        restarted = self.service()
        blocked = _make(restarted, _signal(), self.identity, self.at)

        self.assertEqual("none", blocked.action)
        self.assertIn("决策冷却", blocked.decision_reason)

    def test_account_deployment_plan_stage_and_direction_are_isolated(self):
        first = self.service()
        decision = _make(first, _signal("initial", "buy"), self.identity, self.at)
        first.activate_decision_cooldown(decision, self.at)

        different_stage = _make(
            self.service(), _signal("breakout", "buy", "plan-1"),
            self.identity, self.at,
        )
        different_deployment = _make(
            self.service(), _signal("initial", "buy", "plan-1"),
            {**self.identity, "deployment_id": "deployment-other"}, self.at,
        )
        different_direction = _make(
            self.service(), _signal("initial", "sell", "plan-1"),
            self.identity, self.at,
        )

        self.assertEqual("buy", different_stage.action)
        self.assertEqual("buy", different_deployment.action)
        self.assertEqual("sell", different_direction.action)


if __name__ == "__main__":
    unittest.main()
