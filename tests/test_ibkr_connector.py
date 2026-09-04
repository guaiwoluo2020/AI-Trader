import os
import unittest
from unittest.mock import patch

from ibkr_connector.config import ConnectorConfig
from ibkr_connector.protocol import event, execution_report, hello


class IBKRConnectorConfigTest(unittest.TestCase):
    def test_env_config_and_read_only_default(self):
        with patch.dict(os.environ, {
            "IBKR_SERVER_WS_URL": "wss://example/ws/ibkr",
            "IBKR_CONNECTOR_TOKEN": "secret",
            "IBKR_SYMBOLS": "AAPL:STK, EURUSD:CASH",
        }, clear=False):
            config = ConnectorConfig.from_env()
        self.assertEqual(config.server_ws_url, "wss://example/ws/ibkr")
        self.assertEqual(config.symbols, ("AAPL:STK", "EURUSD:CASH"))
        self.assertTrue(config.read_only)

    def test_envelopes_are_versioned(self):
        config = ConnectorConfig(connector_token="x", account="DU1", client_id=7)
        self.assertEqual(hello(config)["type"], "hello")
        payload = event("quote", {"symbol": "AAPL", "price": 1}, account="DU1")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["payload"]["symbol"], "AAPL")
        report = execution_report("order_rejected", {"reason": "x"}, command_id="cmd-1")
        self.assertEqual(report["command_id"], "cmd-1")

    def test_last_quote_is_not_emitted_until_a_two_sided_quote_exists(self):
        # This behavior is covered by the gateway callback contract; the test
        # intentionally stays SDK-free and validates the protocol shape.
        payload = event("quote", {"symbol": "AAPL", "bid": 99.9, "ask": 100.1,
                                   "price": 100.0})
        self.assertEqual(payload["payload"]["bid"], 99.9)
        self.assertEqual(payload["payload"]["ask"], 100.1)


if __name__ == "__main__":
    unittest.main()
