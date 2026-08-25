import unittest

from market.models.pending_order import PendingOrder
from market.models.trading_instruction import TradingInstruction
from market.services.position_attribution import (
    build_position_attribution,
    close_position_attribution,
)


class PositionAttributionTest(unittest.TestCase):
    def setUp(self):
        self.summary = {
            "selected_signal_source": "ai_entry",
            "selected_signal_source_id": "gold_m1",
            "selected_ai_plan_id": "plan-100",
            "selected_ai_plan_valid_from": 100,
            "selected_ai_plan_expires_at": 700,
            "position_management": {
                "initial_risk": 3.5,
                "setup_context": {
                    "signal_source": "ai_entry",
                    "setup_type": "range_breakout",
                    "setup_family": "breakout",
                    "entry_mode": "close_confirmed_breakout",
                },
                "applied_setup_profile": {
                    "profile_id": "breakout_profile",
                    "name": "突破跟随",
                },
                "policy_snapshot": {
                    "policy_id": "policy_1",
                    "name": "黄金持仓管理",
                    "version": 4,
                    "config": {"stop_loss": {"type": "atr", "value": 1.2}},
                },
            },
        }

    def test_build_and_close_attribution(self):
        attribution = build_position_attribution(
            self.summary,
            decision_id="decision_1",
            entry_reason="箱体收盘突破后入场",
            initial_stop_loss=4638.0,
            initial_take_profit=4650.0,
        )
        self.assertEqual(attribution["setup_type"], "range_breakout")
        self.assertEqual(attribution["setup_profile_id"], "breakout_profile")
        self.assertEqual(attribution["position_policy_id"], "policy_1")
        self.assertEqual(attribution["position_policy_version"], 4)
        self.assertEqual(attribution["initial_risk"], 3.5)
        self.assertEqual(attribution["ai_plan_id"], "plan-100")
        self.assertEqual(attribution["ai_plan_instance_id"], "plan-100:100")

        closed = close_position_attribution(attribution, "take_profit", 1.75)
        self.assertEqual(closed["exit_reason"], "take_profit")
        self.assertEqual(closed["realized_r"], 1.75)
        self.assertEqual(attribution["exit_reason"], "")

    def test_pending_order_to_instruction_preserves_attribution(self):
        attribution = build_position_attribution(
            self.summary,
            decision_id="decision_2",
            entry_reason="回踩确认",
            initial_stop_loss=100,
            initial_take_profit=120,
        )
        order = PendingOrder(
            symbol="GOLD_",
            action="b",
            price=110,
            mount=0.1,
            sl=100,
            tp=120,
            decision_id="decision_2",
            position_attribution=attribution,
        )
        restored = PendingOrder.from_dict(order.to_dict())
        instruction = TradingInstruction.from_pending_order(restored)

        self.assertEqual(instruction.decision_id, "decision_2")
        self.assertEqual(
            instruction.position_attribution["setup_profile_name"], "突破跟随"
        )
        self.assertEqual(
            instruction.to_full_dict()["position_attribution"]["setup_type"],
            "range_breakout",
        )


if __name__ == "__main__":
    unittest.main()
