import unittest

from market.models import PositionManagementPolicy
from market.services.position_manager import PositionManager


def policy(config):
    return PositionManagementPolicy(name="test", user_id=1, config=config)


class PositionManagerTests(unittest.TestCase):
    def test_multi_level_structure_plan_uses_virtual_exits_and_disaster_stop(self):
        plan = PositionManager().create_plan(
            policy({
                "management_mode": "multi_level_exit",
                "multi_level_exit": {
                    "disaster_stop_buffer_atr": 0.5,
                    "stop_close_percent": {
                        "internal": 30, "swing": 40, "external": 100,
                    },
                    "take_profit_close_percent": {
                        "internal": 30, "swing": 30, "external": 100,
                    },
                },
                "initial_stop_rules": [{"type": "signal"}],
                "initial_take_profit_rules": [{"type": "signal"}],
                "management_rules": [], "min_risk_reward": 0.5,
                "min_stop_percent": 0, "max_stop_percent": 0,
            }),
            "buy", 100, signal_stop_loss=98, signal_take_profit=104,
            atr=2,
            setup_context={"signal_source": "structure_plan"},
            signal_stop_candidates=[
                {"level_id": "sl-i", "structure_layer": "internal", "price": 99},
                {"level_id": "sl-s", "structure_layer": "swing", "price": 98},
                {"level_id": "sl-e", "structure_layer": "external", "price": 96},
            ],
            signal_target_candidates=[
                {"level_id": "tp-i", "structure_layer": "internal", "price": 102},
                {"level_id": "tp-s", "structure_layer": "swing", "price": 104},
                {"level_id": "tp-e", "structure_layer": "external", "price": 108},
            ],
        )
        self.assertEqual(plan.stop_loss, 95)
        self.assertEqual(plan.take_profit, 0)
        self.assertEqual(plan.reference_take_profit, 102)
        self.assertEqual(len(plan.exit_levels), 6)
        self.assertTrue(plan.exit_levels[2]["close_remaining"])

    def test_multi_level_exit_aggregates_crossed_levels_once(self):
        levels = [
            {"type": "stop_loss", "level_id": "sl-i", "price": 99,
             "close_percent": 30},
            {"type": "stop_loss", "level_id": "sl-s", "price": 98,
             "close_percent": 40},
            {"type": "stop_loss", "level_id": "sl-e", "price": 96,
             "close_percent": 100, "close_remaining": True},
            {"type": "take_profit", "level_id": "tp-i", "price": 102,
             "close_percent": 30},
        ]
        action = PositionManager().evaluate(
            {"management_rules": []},
            {"direction": "buy", "entry_price": 100, "stop_loss": 95,
             "initial_risk": 5, "volume": 1, "initial_volume": 1,
             "remaining_volume": 1, "exit_levels": levels,
             "partial_levels_done": []},
            {"price": 97.5},
        )
        self.assertEqual(action.action, "partial_close")
        self.assertAlmostEqual(action.close_volume, 0.7)
        self.assertEqual(action.level_ids, ["sl-i", "sl-s"])

        no_repeat = PositionManager().evaluate(
            {"management_rules": []},
            {"direction": "buy", "entry_price": 100, "stop_loss": 95,
             "initial_risk": 5, "volume": 1, "initial_volume": 1,
             "remaining_volume": 0.3, "exit_levels": levels,
             "partial_levels_done": ["sl-i", "sl-s"]},
            {"price": 97.5},
        )
        self.assertEqual(no_repeat.action, "none")

    def test_multi_level_exit_is_symmetric_for_sell_targets(self):
        action = PositionManager().evaluate(
            {"management_mode": "multi_level_exit", "management_rules": []},
            {"direction": "sell", "entry_price": 100, "stop_loss": 106,
             "initial_risk": 6, "volume": 1, "initial_volume": 1,
             "remaining_volume": 1, "partial_levels_done": [],
             "exit_levels": [
                 {"type": "take_profit", "level_id": "tp-i", "price": 98,
                  "close_percent": 30},
                 {"type": "take_profit", "level_id": "tp-s", "price": 96,
                  "close_percent": 40},
             ]},
            {"price": 95.5},
        )
        self.assertEqual(action.action, "partial_close")
        self.assertAlmostEqual(action.close_volume, 0.7)
        self.assertEqual(action.reason, "multi_level_take_profit")

    def test_price_discovery_builds_r_targets_and_trailing_runner(self):
        plan = PositionManager().create_plan(
            policy({
                "management_mode": "multi_level_exit",
                "multi_level_exit": {
                    "disaster_stop_buffer_atr": 0.5,
                    "price_discovery_take_profit_levels": [
                        {"risk_reward": 1, "close_percent": 30},
                        {"risk_reward": 2, "close_percent": 30},
                    ],
                    "runner_trailing_enabled": True,
                    "runner_trailing_activation_r": 1,
                    "runner_trailing_distance_r": 0.8,
                },
                "initial_stop_rules": [{"type": "signal"}],
                "initial_take_profit_rules": [{"type": "signal"}],
                "management_rules": [], "min_risk_reward": 0.5,
                "min_stop_percent": 0, "max_stop_percent": 0,
            }),
            "buy", 100, signal_stop_loss=98, signal_take_profit=102,
            atr=2, setup_context={"signal_source": "structure_plan"},
            signal_stop_candidates=[{
                "level_id": "sl-i", "structure_layer": "internal", "price": 98,
            }],
            signal_target_candidates=[{
                "level_id": "projection", "structure_layer": "internal",
                "price": 102, "source_type": "risk_reward_projection",
            }],
        )
        targets = [item for item in plan.exit_levels if item["type"] == "take_profit"]
        runner = [item for item in plan.exit_levels if item["type"] == "runner"]
        self.assertEqual([item["price"] for item in targets], [102, 104])
        self.assertEqual([item["close_percent"] for item in targets], [30, 30])
        self.assertEqual(len(runner), 1)
        self.assertFalse(any(item.get("close_remaining") for item in targets))

    def test_price_discovery_runner_uses_favorable_price_trailing_stop(self):
        levels = [{
            "type": "runner", "level_id": "price_discovery_runner",
            "price": 0, "close_remaining": True,
        }]
        position = {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5, "volume": 0.4, "remaining_volume": 0.4,
            "exit_levels": levels, "partial_levels_done": [],
        }
        config = {
            "management_mode": "multi_level_exit", "management_rules": [],
            "multi_level_exit": {
                "runner_trailing_enabled": True,
                "runner_trailing_activation_r": 1,
                "runner_trailing_distance_r": 0.8,
            },
        }
        before_activation = PositionManager().evaluate(
            config, {**position, "favorable_price": 104}, {"price": 100}
        )
        self.assertEqual(before_activation.action, "none")
        trailing_hit = PositionManager().evaluate(
            config, {**position, "favorable_price": 110}, {"price": 105.5}
        )
        self.assertEqual(trailing_hit.action, "close")
        self.assertEqual(trailing_hit.reason, "multi_level_runner_trailing")
        self.assertEqual(trailing_hit.events[0]["trailing_stop"], 106)

    def test_price_discovery_runner_trailing_is_symmetric_for_sell(self):
        action = PositionManager().evaluate({
            "management_mode": "multi_level_exit", "management_rules": [],
            "multi_level_exit": {
                "runner_trailing_enabled": True,
                "runner_trailing_activation_r": 1,
                "runner_trailing_distance_r": 0.8,
            },
        }, {
            "direction": "sell", "entry_price": 100, "stop_loss": 105,
            "initial_risk": 5, "favorable_price": 90,
            "volume": 0.4, "remaining_volume": 0.4,
            "partial_levels_done": [], "exit_levels": [{
                "type": "runner", "level_id": "price_discovery_runner",
                "price": 0, "reference_risk": 5,
            }],
        }, {"price": 94.5})
        self.assertEqual(action.action, "close")
        self.assertEqual(action.events[0]["trailing_stop"], 94)

    def test_structure_trend_pullback_uses_signal_half_rr_threshold(self):
        config = {
            "initial_stop_rules": [{"type": "signal"}],
            "initial_take_profit_rules": [{"type": "signal"}],
            "management_rules": [],
            "min_risk_reward": 1.0,
        }
        plan = PositionManager().create_plan(
            policy(config), "buy", 100,
            signal_stop_loss=99.5, signal_take_profit=100.3,
            setup_context={
                "signal_source": "structure_plan",
                "setup_type": "structure_location_pullback",
                "signal_min_risk_reward": 0.5,
            },
        )
        self.assertAlmostEqual(plan.risk_reward, 0.6)

        with self.assertRaisesRegex(ValueError, "盈亏比"):
            PositionManager().create_plan(
                policy(config), "buy", 100,
                signal_stop_loss=99.5, signal_take_profit=100.3,
                setup_context={
                    "signal_source": "ai_entry",
                    "setup_type": "generic_entry",
                    "signal_min_risk_reward": 0.5,
                },
            )

    def test_setup_profile_uses_exact_then_family_then_source(self):
        config = {
            "initial_stop_rules": [{"type": "fixed_percent", "value": 0.01}],
            "initial_take_profit_rules": [{"type": "risk_reward", "value": 2}],
            "management_rules": [],
            "min_risk_reward": 1,
            "setup_profiles": [
                {
                    "name": "AI兜底", "priority": 999,
                    "match": {"signal_sources": ["ai_entry"]},
                    "overrides": {"initial_stop_rules": [
                        {"type": "fixed_percent", "value": 0.02}
                    ]},
                },
                {
                    "name": "突破族", "priority": 1,
                    "match": {"setup_families": ["breakout"]},
                    "overrides": {"initial_stop_rules": [
                        {"type": "fixed_percent", "value": 0.03}
                    ]},
                },
                {
                    "name": "箱体突破", "priority": 0,
                    "match": {"setup_types": ["range_breakout"]},
                    "overrides": {"initial_stop_rules": [
                        {"type": "fixed_percent", "value": 0.04}
                    ]},
                },
            ],
        }
        plan = PositionManager().create_plan(
            policy(config), "buy", 100,
            setup_context={
                "setup_type": "range_breakout",
                "setup_family": "breakout",
                "signal_source": "ai_entry",
            },
        )
        self.assertEqual(plan.stop_loss, 96)
        self.assertEqual(
            plan.policy_snapshot["applied_setup_profile"]["name"],
            "箱体突破",
        )
        self.assertEqual(
            plan.policy_snapshot["setup_context"]["setup_type"],
            "range_breakout",
        )

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

    def test_pivot_trailing_records_only_an_effective_stop_update(self):
        manager = PositionManager()
        config = {"management_rules": [{
            "type": "pivot_trailing", "period": "M5",
        }]}
        position = {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5,
        }

        no_change = manager.evaluate(config, position, {"price": 102}, pivots=[{
            "period": "M5", "direction": "low", "price": 94,
        }])
        self.assertEqual(no_change.action, "none")
        self.assertFalse(any(
            event["status"] == "triggered" for event in no_change.events
        ))

        update = manager.evaluate(config, position, {"price": 102}, pivots=[{
            "period": "M5", "direction": "low", "price": 98,
        }])
        self.assertEqual(update.action, "modify_sl")
        self.assertEqual(update.stop_loss, 98)
        self.assertFalse(any(
            event["rule_type"] == "pivot_trailing"
            and event["status"] == "triggered"
            for event in update.events
        ))
        self.assertTrue(any(
            event["rule_type"] == "stop_loss_update"
            and event["new_stop_loss"] == 98
            for event in update.events
        ))

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

    def test_partial_take_profit_can_move_stop_to_break_even(self):
        manager = PositionManager()
        action = manager.evaluate({
            "management_rules": [{
                "type": "partial_take_profit",
                "levels": [{
                    "level_id": "tp1",
                    "trigger_r": 1,
                    "close_percent": 30,
                    "move_sl": "break_even",
                }],
            }]
        }, {
            "direction": "sell",
            "entry_price": 100,
            "stop_loss": 105,
            "initial_risk": 5,
            "remaining_volume": 1,
            "favorable_price": 94,
        }, {"price": 95})
        self.assertEqual(action.action, "partial_close")
        self.assertEqual(action.close_volume, 0.3)
        self.assertEqual(action.stop_loss, 100)
        self.assertEqual(action.level_id, "tp1")

    def test_completed_break_even_is_not_triggered_again(self):
        action = PositionManager().evaluate({
            "management_rules": [{
                "type": "break_even", "activation_r": 1, "offset_r": 0,
            }],
        }, {
            "direction": "buy", "entry_price": 100, "stop_loss": 100,
            "initial_risk": 5, "favorable_price": 106,
            "break_even_done": True,
        }, {"price": 106})
        self.assertEqual(action.action, "none")
        self.assertEqual(action.events, [])

    def test_completed_partial_level_is_not_triggered_again(self):
        action = PositionManager().evaluate({
            "management_rules": [{
                "type": "partial_take_profit",
                "levels": [{
                    "level_id": "tp1", "trigger_r": 1,
                    "close_percent": 30, "move_sl": "break_even",
                }],
            }],
        }, {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5, "remaining_volume": 0.7,
            "favorable_price": 106, "partial_levels_done": ["tp1"],
        }, {"price": 106})
        self.assertEqual(action.action, "none")
        self.assertEqual(action.events, [])

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

    def test_structure_trailing_uses_swing_protected_low(self):
        action = PositionManager().evaluate({
            "management_rules": [{
                "type": "structure_trailing", "structure_layer": "swing",
                "buffer_type": "atr", "buffer_value": 0.15,
                "min_improvement_atr": 0.10,
            }],
        }, {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5, "favorable_price": 110,
        }, {
            "price": 109, "atr": 2,
            "structure_hierarchy": {
                "swing": {"protected_low": {"price": 103}},
            },
        })
        self.assertEqual(action.action, "modify_sl")
        self.assertAlmostEqual(action.stop_loss, 102.7)

    def test_structure_trailing_never_loosens_stop(self):
        action = PositionManager().evaluate({
            "management_rules": [{
                "type": "structure_trailing", "structure_layer": "swing",
                "buffer_type": "atr", "buffer_value": 0.15,
                "min_improvement_atr": 0.10,
            }],
        }, {
            "direction": "buy", "entry_price": 100, "stop_loss": 104,
            "initial_risk": 5, "favorable_price": 110,
        }, {
            "price": 109, "atr": 2,
            "structure_hierarchy": {
                "swing": {"protected_low": {"price": 103}},
            },
        })
        self.assertEqual(action.action, "none")

    def test_disabled_trailing_rule_preserves_config_without_execution(self):
        action = PositionManager().evaluate({
            "management_rules": [{
                "type": "pivot_trailing", "enabled": False,
                "period": "M1", "buffer": {"type": "fixed_points", "value": 0},
            }],
        }, {
            "direction": "buy", "entry_price": 100, "stop_loss": 95,
            "initial_risk": 5, "favorable_price": 110,
        }, {"price": 109, "atr": 2}, pivots=[{
            "period": "M1", "direction": "low", "price": 103,
        }])
        self.assertEqual(action.action, "none")
        self.assertEqual(action.events, [])


if __name__ == "__main__":
    unittest.main()
