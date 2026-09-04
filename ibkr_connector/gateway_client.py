"""Small adapter around the official IBKR ``ibapi`` callback client.

No business logic lives here: callbacks are converted to normalized event
dictionaries and handed to the process runner.  ``ibapi`` is an optional
dependency so the web application can be installed without a broker SDK.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised only on a host with IBKR's SDK installed
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.order import Order
    from ibapi.wrapper import EWrapper
except ImportError:  # keep imports usable for config/tests on the server
    EClient = None  # type: ignore
    EWrapper = object  # type: ignore
    Contract = None  # type: ignore
    Order = None  # type: ignore


class IBGatewayClient(EWrapper):
    """Translate Gateway callbacks into ``on_event(name, payload)`` calls."""

    def __init__(self, host: str, port: int, client_id: int,
                 on_event: Callable[[str, Dict], None]):
        if EClient is None:
            raise RuntimeError("未安装 IBKR 官方 ibapi，请执行 pip install ibapi")
        super().__init__()
        self.on_event = on_event
        self._client = EClient(self)
        self._host, self._port, self._client_id = host, port, client_id
        self._thread: Optional[threading.Thread] = None
        self._next_request_id = 1
        self._request_symbols: Dict[int, str] = {}
        self._quotes: Dict[int, Dict[str, float]] = {}
        self._account_summary_request_id: Optional[int] = None

    @property
    def connected(self) -> bool:
        return bool(self._client.isConnected())

    def connect_and_run(self) -> None:
        self._client.connect(self._host, self._port, self._client_id)
        self._thread = threading.Thread(target=self._client.run, name="ibkr-api", daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self.connected:
            self._client.disconnect()

    def subscribe_symbols(self, symbols: Iterable[str]) -> None:
        """Subscribe to US stock/forex/future symbols supplied as ``SYM[:SEC]``.

        Contract qualification is intentionally explicit in phase one.  The
        server can later provide a contract snapshot instead of guessing an
        exchange or expiry from a display symbol.
        """
        for item in symbols:
            if isinstance(item, Mapping):
                symbol = str(item.get("symbol") or "").strip()
                sec_type = str(item.get("sec_type") or "STK")
                con_id = int(item.get("con_id") or 0)
                exchange = str(item.get("exchange") or "SMART")
                currency = str(item.get("currency") or "USD")
                expiry = str(item.get("expiry") or "")
            else:
                symbol, _, sec_type = str(item).partition(":")
                con_id, exchange, currency, expiry = 0, "SMART", "USD", ""
            if not symbol:
                continue
            contract = Contract()
            contract.symbol = symbol
            contract.secType = sec_type.upper() or "STK"
            contract.exchange = exchange
            contract.currency = currency
            if con_id > 0:
                contract.conId = con_id
            if expiry:
                contract.lastTradeDateOrContractMonth = expiry
            request_id = self._next_request_id
            self._next_request_id += 1
            self._request_symbols[request_id] = symbol
            self._client.reqMktData(request_id, contract, "", False, False, [])

    def place_market_order(self, command: Dict) -> int:
        if not self.connected or Order is None:
            raise RuntimeError("IBKR Gateway 尚未连接或 ibapi 未安装")
        symbol = str(command.get("symbol") or "").strip()
        action = str(command.get("action") or "").upper()
        quantity = float(command.get("quantity") or 0)
        if not symbol or action not in {"BUY", "SELL"} or quantity <= 0:
            raise ValueError("订单必须包含合法 symbol、BUY/SELL 和正数量")
        order_id = int(command.get("ibkr_order_id") or self._next_request_id)
        self._next_request_id = max(self._next_request_id + 1, order_id + 1)
        contract = Contract()
        contract.symbol = symbol
        contract.secType = str(command.get("sec_type") or "STK").upper()
        contract.exchange = str(command.get("exchange") or "SMART")
        contract.currency = str(command.get("currency") or "USD")
        order = Order()
        order.action, order.orderType = action, "MKT"
        order.totalQuantity = quantity
        order.tif = str(command.get("tif") or "DAY").upper()
        self._client.placeOrder(order_id, contract, order)
        return order_id

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        self.on_event("error", {"request_id": reqId, "code": errorCode,
                                 "symbol": self._request_symbols.get(reqId, ""),
                                 "message": errorString,
                                 "advanced_reject": advancedOrderRejectJson or None})

    def tickPrice(self, reqId, tickType, price, attrib):
        if price is None or price <= 0:
            return
        quote = self._quotes.setdefault(reqId, {})
        # IBKR tick types: 1=bid, 2=ask, 4=last.  Keep the last known side
        # because IBKR streams changes independently for bid and ask.
        if tickType == 1:
            quote["bid"] = float(price)
        elif tickType == 2:
            quote["ask"] = float(price)
        elif tickType == 4:
            quote["last"] = float(price)
        else:
            return
        bid = quote.get("bid") or quote.get("last")
        ask = quote.get("ask") or quote.get("last")
        if not bid or not ask:
            return
        self.on_event("quote", {"request_id": reqId, "tick_type": tickType,
                                 "symbol": self._request_symbols.get(reqId, ""),
                                 "bid": bid, "ask": ask,
                                 "price": (bid + ask) / 2.0})

    def nextValidId(self, orderId):
        self._next_request_id = max(self._next_request_id, int(orderId))
        self.on_event("gateway_ready", {"next_order_id": int(orderId)})
        # Keep a live account summary subscription so the server can expose
        # current equity/cash/margin instead of only the managed account id.
        if self._account_summary_request_id is None:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._account_summary_request_id = request_id
            self._client.reqAccountSummary(
                request_id,
                "All",
                "NetLiquidation,TotalCashValue,AvailableFunds,BuyingPower,MaintMarginReq,InitMarginReq,GrossPositionValue",
            )
            self.on_event("account_summary_requested", {"request_id": request_id})

    def managedAccounts(self, accountsList):
        self.on_event("accounts", {"accounts": [x for x in accountsList.split(",") if x]})

    def accountSummary(self, reqId, account, tag, value, currency):
        self.on_event("account_summary", {"request_id": reqId, "account": account,
                                           "tag": tag, "value": value,
                                           "currency": currency})

    def accountSummaryEnd(self, reqId):
        self.on_event("account_summary_end", {"request_id": int(reqId)})

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld,
                    mktCapPrice=0.0):
        """Normalize IBKR order status for the server execution receipt path."""
        self.on_event("order_status", {
            "order_id": int(orderId), "status": str(status),
            "filled": float(filled), "remaining": float(remaining),
            "avg_fill_price": float(avgFillPrice), "perm_id": int(permId),
            "parent_id": int(parentId), "last_fill_price": float(lastFillPrice),
            "client_id": int(clientId), "why_held": whyHeld or None,
            "market_cap_price": float(mktCapPrice or 0),
        })

    def execDetails(self, reqId, contract, execution):
        self.on_event("execution", {
            "request_id": int(reqId), "symbol": contract.symbol,
            "side": execution.side, "shares": float(execution.shares),
            "price": float(execution.price), "exec_id": execution.execId,
            "order_id": int(execution.orderId), "perm_id": int(execution.permId),
        })
