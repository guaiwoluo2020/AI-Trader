import unittest

from market.services.signal.structure_plan.price_calculator import location_reclaim_confirmation


class LocationReclaimConfirmationTests(unittest.TestCase):
    def test_buy_requires_three_closes_and_rising_lows(self):
        rows = [
            {"open": 99.0, "high": 100.7, "low": 98.8, "close": 100.5},
            {"open": 100.3, "high": 100.9, "low": 99.0, "close": 100.6},
            {"open": 100.4, "high": 101.0, "low": 99.2, "close": 100.7},
        ]
        check = lambda bars: location_reclaim_confirmation(
            bars, 100, "buy", 2, confirmation_bars=3,
        )
        self.assertFalse(check(rows[:2])[0])
        self.assertTrue(check(rows)[0])
        self.assertIn("低点未抬高", check([rows[0], rows[1], {**rows[2], "low": 99.0}])[2])
        self.assertIn("收盘重新穿过", check([rows[0], rows[1], {**rows[2], "close": 99.9}])[2])

    def test_sell_requires_three_closes_and_falling_highs(self):
        rows = [
            {"open": 101.0, "high": 101.2, "low": 99.3, "close": 99.5},
            {"open": 99.7, "high": 101.0, "low": 99.1, "close": 99.4},
            {"open": 99.6, "high": 100.8, "low": 99.0, "close": 99.3},
        ]
        check = lambda bars: location_reclaim_confirmation(
            bars, 100, "sell", 2, confirmation_bars=3,
        )
        self.assertTrue(check(rows)[0])
        self.assertIn("高点未降低", check([rows[0], rows[1], {**rows[2], "high": 101.0}])[2])
        self.assertIn("收盘重新穿过", check([rows[0], rows[1], {**rows[2], "close": 100.1}])[2])

    def test_rolling_window_can_confirm_after_an_earlier_sequence_failed(self):
        rows = [
            {"open": 99.0, "high": 100.7, "low": 98.8, "close": 100.6},
            {"open": 100.3, "high": 100.9, "low": 99.2, "close": 100.9},
            {"open": 100.4, "high": 100.8, "low": 99.1, "close": 100.8},
            {"open": 100.6, "high": 101.0, "low": 99.3, "close": 101.0},
            {"open": 100.8, "high": 101.2, "low": 99.5, "close": 101.2},
            {"open": 101.0, "high": 101.4, "low": 99.8, "close": 101.4},
        ]
        accepted, evidence, rejection = location_reclaim_confirmation(
            rows[-3:], 100, "buy", 2, confirmation_bars=3, require_touch=False,
        )
        self.assertTrue(accepted, rejection)
        self.assertEqual(evidence["confirmation_extremes"], [99.3, 99.5, 99.8])


if __name__ == "__main__":
    unittest.main()
