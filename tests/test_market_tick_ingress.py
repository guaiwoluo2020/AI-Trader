import unittest

from market.services.market_tick_ingress import MarketTickIngress


class _Manager:
    def __init__(self):
        self.calls = []

    def process_user_market_tick(self, user_id, account_ids, symbol, price):
        self.calls.append((user_id, tuple(account_ids), symbol, price))
        return {account_ids[0]: {"ok": True}}

    def get_market_engine(self, user_id):
        return self

    def process_price(self, symbol, price):
        return {"market": True}


class MarketTickIngressTest(unittest.TestCase):
    def test_routes_quote_to_shared_account_path(self):
        manager = _Manager()
        result = MarketTickIngress(manager).ingest(
            user_id=7, account_ids=(12, 12, 0), symbol="AAPL", price=100,
            source="ibkr", bid=99.9, ask=100.1,
        )
        self.assertEqual(manager.calls, [(7, (12,), "AAPL", 100.0)])
        self.assertEqual(result["source"], "ibkr")
        self.assertEqual(result["accounts"], [12])

    def test_market_state_advances_without_account_mapping(self):
        manager = _Manager()
        result = MarketTickIngress(manager).ingest(
            user_id=7, account_ids=(), symbol="AAPL", price=100, source="ibkr",
        )
        self.assertEqual(result["accounts"], [])
        self.assertEqual(result["market_result"], {"market": True})


if __name__ == "__main__":
    unittest.main()
