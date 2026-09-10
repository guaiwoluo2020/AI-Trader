import unittest

from market.services.strategy.risk_manager import RiskManager


class AggregatePositionRiskTests(unittest.TestCase):
    def manager(self):
        manager = RiskManager()
        manager._account_balance = 1000.0
        manager._daily_risk_limit = 5.0
        manager._symbol_config["GOLD"] = {"point_value": 1.0}
        return manager

    def test_existing_and_new_stop_risk_are_combined(self):
        result = self.manager().check_aggregate_position_risk(
            "GOLD", [{"volume": 3.0, "price_open": 100, "sl": 90}],
            3.0, 100, 90,
        )
        self.assertFalse(result["allowed"])
        self.assertAlmostEqual(result["total_risk_percent"], 6.0)

    def test_missing_balance_is_rejected(self):
        manager = RiskManager()
        result = manager.check_aggregate_position_risk(
            "GOLD", [], 0.01, 100, 99,
        )
        self.assertFalse(result["allowed"])


if __name__ == "__main__":
    unittest.main()
