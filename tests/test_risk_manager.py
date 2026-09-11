import unittest

from market.models.trading_strategy import TradingStrategy
from market.services.strategy.risk_manager import RiskManager


class RiskManagerVolumeTest(unittest.TestCase):
    def test_fixed_volume_is_not_blocked_by_legacy_max_risk_points(self):
        manager = RiskManager()
        strategy = TradingStrategy(
            strategy_id="btc-live",
            strategy_name="BTC",
            symbol="BTCUSDm",
            fixed_volume=0.01,
            volume_mode="fixed",
        )

        # BTC 按持仓管理方案的 0.1% 最小止损约为 77 点。策略层旧的
        # 50 点限制不得再使实盘手数静默变成 0。
        self.assertEqual(
            manager.calculate_volume("BTCUSDm", 77.7, strategy),
            0.01,
        )

    def test_account_daily_risk_limit_overrides_default(self):
        manager = RiskManager()
        manager.set_account_limits(
            max_positions=10,
            max_single_volume=10,
            daily_loss_limit=5,
            daily_order_limit=100,
            daily_risk_limit=12.5,
        )
        manager.update_account_info(1000, 1000, 1000)
        result = manager.check_risk("X", 1, 100)
        self.assertEqual(result["daily_risk_limit"], 12.5)

    def test_daily_risk_limit_defaults_to_five_percent(self):
        manager = RiskManager()
        self.assertEqual(manager.get_status()["daily_risk_limit"], 5.0)


if __name__ == "__main__":
    unittest.main()
