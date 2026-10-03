import unittest

from market.services.signal.structure_plan_signal import StructurePlanSignalGenerator


class _Repo:
    def __init__(self):
        self.payloads = []

    def update_payload(self, plan_id, payload):
        self.payloads.append((plan_id, dict(payload)))


class ChochRetestConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.gen = StructurePlanSignalGenerator.__new__(StructurePlanSignalGenerator)
        self.gen.repository = _Repo()

    def test_buy_retest_needs_two_closes_and_higher_lows(self):
        plan = {
            "plan_id": "choch-buy",
            "entry_price": 100.0,
            "direction": "buy",
            "structure_snapshot": {"atr": 2.0},
        }
        rows = [
            {"timestamp": 1000, "open": 99.0, "high": 101.0, "low": 99.0, "close": 100.3},
            {"timestamp": 1060, "open": 100.2, "high": 101.2, "low": 99.4, "close": 100.6},
        ]
        config = {
            "choch_retest_confirmation_bars": 2,
            "choch_retest_min_body_atr": 0.2,
            "choch_retest_min_close_extension_atr": 0.05,
        }
        self.assertTrue(self.gen._choch_entry_retest_confirmed(plan, rows, config))
        self.assertTrue(plan["choch_retest_confirmed"])
        rows[-1] = dict(rows[-1], low=99.0)
        plan["choch_retest_confirmation_bar"] = 0
        self.assertFalse(self.gen._choch_entry_retest_confirmed(plan, rows, config))

    def test_sell_retest_fails_when_close_crosses_back(self):
        plan = {
            "plan_id": "choch-sell",
            "entry_price": 100.0,
            "direction": "sell",
            "structure_snapshot": {"atr": 2.0},
        }
        rows = [
            {"timestamp": 1000, "open": 101.0, "high": 101.0, "low": 99.0, "close": 99.7},
            {"timestamp": 1060, "open": 99.8, "high": 100.6, "low": 98.8, "close": 100.1},
        ]
        self.assertFalse(self.gen._choch_entry_retest_confirmed(
            plan, rows, {"choch_retest_confirmation_bars": 2},
        ))


if __name__ == "__main__":
    unittest.main()
