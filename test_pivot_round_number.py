import unittest

from market.services.signal.signal_rules import (
    build_pivot_signal,
    constrain_pivot_levels_to_hundred_band,
)


class PivotRoundNumberTest(unittest.TestCase):
    def test_buy_target_crossing_hundred_is_capped_inside_band(self):
        sl, tp = constrain_pivot_levels_to_hundred_band(4580, "buy", 4560, 4610)
        self.assertEqual(sl, 4560)
        self.assertEqual(tp, 4599)

    def test_sell_target_and_stop_crossing_hundred_are_capped_inside_band(self):
        sl, tp = constrain_pivot_levels_to_hundred_band(4580, "sell", 4610, 4470)
        self.assertEqual(sl, 4599)
        self.assertEqual(tp, 4501)

    def test_pivot_signal_applies_rule_before_returning_signal(self):
        signal = build_pivot_signal(
            "GOLD_", 4580, "M5", 4570, "low", 4610,
            stop_buffer_ratio=0.0001, risk_reward_ratio=1,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal.suggested_tp, 4599)


if __name__ == "__main__":
    unittest.main()
