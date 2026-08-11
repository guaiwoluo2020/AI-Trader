import unittest

from market.models import PositionManagementPolicy
from market.services.position_manager import PositionManager


def policy(config):
    return PositionManagementPolicy(name="test", user_id=1, config=config)


class PositionManagerTests(unittest.TestCase):
    def test_empty_signal_exits_fall_through_to_policy_rules(self):
        plan = PositionManager().create_plan(
            policy({
                "initial_stop_rules": [
                    {"type": "signal"},
                    {"type": "fixed_percent", "value": 0.01},
                ],
                "initial_take_profit_rules": [
                    {"type": "signal"},
                    {"type": "risk_reward", "value": 2},
                ],
                "management_rules": [],
                "min_risk_reward": 1,
            }),
            "buy", 100, signal_stop_loss=0, signal_take_profit=0,
        )
        self.assertEqual(plan.stop_loss, 99)
        self.assertEqual(plan.take_profit, 102)
        self.assertEqual(plan.stop_rule["type"], "fixed_percent")
        self.assertEqual(plan.take_profit_rule["type"], "risk_reward")

    def test_max_holding_bars_uses_rule_period(self):
        action = PositionManager().evaluate(
            {"management_rules": [{
                "type": "max_holding_bars", "period": "M5", "bars": 3,
            }]},
            {
                "direction": "buy", "entry_price": 100, "stop_loss": 99,
                "initial_risk": 1, "opened_at": 1000,
            },
            {"price": 101, "time": 1900},
        )
        self.assertEqual(action.action, "close")

    def test_pivot_rules_create_protected_long_plan(self):
        manager = PositionManager()
        plan = manager.create_plan(
            policy({
                "initial_stop_rules": [{
                    "type": "pivot", "period": "M5", "max_age_bars": 10,
                    "buffer": {"type": "fixed_points", "value": 1},
                }],
                "initial_take_profit_rules": [
                    {"type": "risk_reward", "value": 2}
                ],
                "management_rules": [], "min_risk_reward": 1,
            }),
            "buy", 100, pivots=[{
                "period": "M5", "direction": "low", "price": 95,
                "timestamp": 700, "confirmed_at": 900,
            }], current_time=1000,
        )
        self.assertEqual(plan.stop_loss, 94)
        self.assertEqual(plan.take_profit, 112)
        self.assertEqual(plan.risk_reward, 2)

    def test_unconfirmed_pivot_is_ignored_and_fallback_is_used(self):
        manager = PositionManager()
        plan = manager.create_plan(
            policy({
                "initial_stop_rules": [
                    {"type": "pivot", "period": "M5", "max_age_bars": 10},
                    {"type": "fixed_percent", "value": 0.01},
                ],
                "initial_take_profit_rules": [
                    {"type": "risk_reward", "value": 2}
                ],
                "management_rules": [], "min_risk_reward": 1,
            }),
            "buy", 100, pivots=[{
                "period": "M5", "direction": "low", "price": 95,
                "timestamp": 900, "confirmed_at": 1100,
            }], current_time=1000,
        )
        self.assertEqual(plan.stop_loss, 99)

    def test_runtime_rules_only_tighten_stop(self):
        manager = PositionManager()
        config = {
            "management_rules": [
                {"type": "break_even", "activation_r": 1, "offset_r": 0},
                {"type": "trailing_stop", "activation_r": 1.5,
                 "distance_r": 0.5},
            ]
        }
        action = manager.evaluate(config, {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5, "favorable_price": 110,
        }, {"price": 108})
        self.assertEqual(action.action, "modify_sl")
        self.assertEqual(action.stop_loss, 107.5)

        no_change = manager.evaluate(config, {
            "direction": "buy", "entry_price": 100, "stop_loss": 109,
            "initial_risk": 5, "favorable_price": 110,
        }, {"price": 108})
        self.assertEqual(no_change.action, "none")

    def test_no_fixed_take_profit_can_use_trailing_stop(self):
        manager = PositionManager()
        plan = manager.create_plan(
            policy({
                "initial_stop_rules": [
                    {"type": "fixed_percent", "value": 0.02},
                ],
                "initial_take_profit_rules": [
                    {"type": "none"},
                ],
                "management_rules": [
                    {"type": "trailing_stop", "activation_r": 1.5,
                     "distance_r": 0.5},
                ],
                "min_risk_reward": 0,
            }),
            "buy", 100,
        )
        self.assertEqual(plan.stop_loss, 98)
        self.assertEqual(plan.take_profit, 0)
        self.assertEqual(plan.take_profit_rule["type"], "none")

        action = manager.evaluate(plan.policy_snapshot["config"], {
            "direction": "buy", "entry_price": 100,
            "stop_loss": plan.stop_loss,
            "initial_risk": plan.initial_risk,
            "favorable_price": 104,
        }, {"price": 103.5})
        self.assertEqual(action.action, "modify_sl")
        self.assertEqual(action.stop_loss, 103)

    def test_reverse_signal_and_time_limit_close_position(self):
        manager = PositionManager()
        position = {
            "direction": "sell", "entry_price": 100, "stop_loss": 105,
            "initial_risk": 5, "holding_bars": 12,
        }
        action = manager.evaluate(
            {"management_rules": [{"type": "reverse_signal"}]},
            position, {"price": 99}, reverse_signal=True,
        )
        self.assertEqual(action.action, "close")
        self.assertEqual(action.reason, "reverse_signal")

        action = manager.evaluate(
            {"management_rules": [{
                "type": "max_holding_bars", "period": "M1", "bars": 10,
            }]},
            position, {"price": 99},
        )
        self.assertEqual(action.action, "close")
        self.assertEqual(action.reason, "max_holding_bars")


if __name__ == "__main__":
    unittest.main()
