"""IBKR Gateway connector.

The connector is deliberately a separate process.  It talks to IB Gateway on
localhost via the official ``ibapi`` socket client and publishes normalized
events to AI-Trader over an outbound WebSocket connection.
"""

__all__ = ["config", "gateway_client", "protocol"]
