import unittest

from market.services.live_strategy_promotion import promotion_candidate_accounts


class LiveStrategyPromotionTests(unittest.TestCase):
    def test_filters_same_symbol_live_accounts_and_excludes_existing_deployments(self):
        accounts = [
            {
                "account_id": 1, "account_name": "MT5 BTC",
                "account_type": "mt5", "symbol": "BTCUSD",
                "status": "active", "connected": True,
                "enabled": True, "trading_enabled": True,
                "auto_trading_enabled": True,
            },
            {
                "account_id": 2, "account_name": "MT5 GOLD",
                "account_type": "mt5", "symbol": "GOLD",
                "status": "active", "connected": True,
                "enabled": True, "trading_enabled": True,
                "auto_trading_enabled": True,
            },
            {
                "account_id": 3, "account_name": "IBKR BTC",
                "account_type": "ibkr", "symbol": "BTCUSD",
                "status": "active", "connected": True,
                "enabled": True, "trading_enabled": True,
                "auto_trading_enabled": True,
            },
        ]
        result = promotion_candidate_accounts(
            accounts, strategy_symbol="BTCUSD", deployed_account_ids={3},
        )
        self.assertEqual([item["account_id"] for item in result], [1])

    def test_excludes_offline_or_disabled_accounts(self):
        accounts = [
            {
                "account_id": 1, "account_name": "offline",
                "account_type": "mt5", "symbol": "BTCUSD",
                "status": "active", "connected": False,
                "enabled": True, "trading_enabled": True,
                "auto_trading_enabled": True,
            },
            {
                "account_id": 2, "account_name": "manual",
                "account_type": "mt5", "symbol": "BTCUSD",
                "status": "active", "connected": True,
                "enabled": True, "trading_enabled": True,
                "auto_trading_enabled": False,
            },
        ]
        self.assertEqual(
            promotion_candidate_accounts(accounts, "BTCUSD", set()), []
        )


if __name__ == "__main__":
    unittest.main()
