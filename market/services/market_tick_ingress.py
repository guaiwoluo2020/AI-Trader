"""Unified market Tick ingress for MT5 polling and IBKR streaming transports."""
from __future__ import annotations

from typing import Iterable, Optional


class MarketTickIngress:
    """Route an authoritative quote into the existing account engines.

    The caller supplies the execution accounts that should be driven by this
    market source.  Transport-specific routes never call ``TradingServer``
    directly, which keeps GET /get_trades and IBKR WebSocket semantically
    identical.
    """

    def __init__(self, engine_manager):
        self.engine_manager = engine_manager

    def ingest(
        self,
        *,
        user_id: int,
        symbol: str,
        price: float,
        account_ids: Iterable[int] = (),
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        source: str = "unknown",
    ):
        value = float(price)
        if value <= 0:
            return {}
        accounts = tuple(dict.fromkeys(int(item) for item in account_ids if int(item) > 0))
        # Keep the existing account-driven path untouched when execution
        # accounts are supplied.  With no mapping, advance only the shared
        # user market engine; this avoids processing one Tick twice.
        if accounts:
            market_result = None
            result = self.engine_manager.process_user_market_tick(
                int(user_id), accounts, str(symbol), value,
            )
        else:
            market_result = self.engine_manager.get_market_engine(int(user_id)).process_price(
                str(symbol), value,
            )
            result = {}
        return {
            "source": str(source),
            "symbol": str(symbol),
            "price": value,
            "bid": float(bid if bid and bid > 0 else value),
            "ask": float(ask if ask and ask > 0 else value),
            "accounts": list(accounts),
            "results": result,
            "market_result": market_result,
        }
