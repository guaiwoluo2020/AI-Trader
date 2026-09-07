"""Small adapter around the official IBKR ``ibapi`` callback client.

No business logic lives here: callbacks are converted to normalized event
dictionaries and handed to the process runner.  ``ibapi`` is an optional
dependency so the web application can be installed without a broker SDK.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
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
        self._bars: Dict[tuple, Dict] = {}
        self._history_requests: Dict[int, Dict] = {}
        self._history_rows: Dict[int, list] = {}
        self._account_summary_request_id: Optional[int] = None
        self._positions: Dict[int, Dict] = {}
        self._positions_requested = False
        # ``isConnected`` only means the TCP socket is open.  IBKR requests
        # must wait until nextValidId, otherwise Gateway can immediately drop
        # a client that starts sending subscriptions during its handshake.
        self._ready = threading.Event()

    @property
    def connected(self) -> bool:
        return bool(self._client.isConnected())

    @property
    def ready(self) -> bool:
        return self.connected and self._ready.is_set()

    def connect_and_run(self, ready_timeout: float = 20.0) -> None:
        self._client.connect(self._host, self._port, self._client_id)
        self._thread = threading.Thread(target=self._client.run, name="ibkr-api", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=max(1.0, float(ready_timeout))):
            self.close()
            raise TimeoutError("IBKR Gateway API 握手超时，未收到 nextValidId")

    def close(self) -> None:
        self._ready.clear()
        if self.connected:
            self._client.disconnect()

    def request_positions(self) -> bool:
        """Request a fresh complete broker position snapshot.

        Gateway sends ``positionEnd`` even when the account is flat, so the
        server can clear stale positions.  The guard prevents overlapping
        requests while callbacks for the previous snapshot are still active.
        """
        if not self.ready or self._positions_requested:
            return False
        self._positions_requested = True
        self._positions.clear()
        self._client.reqPositions()
        self.on_event("positions_requested", {})
        return True

    def connectionClosed(self):
        self._ready.clear()
        self.on_event("gateway_disconnected", {})

    def subscribe_symbols(self, symbols: Iterable[str], kline_cursors: Optional[Dict] = None) -> None:
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
            cursor = (kline_cursors or {}).get(symbol, {}) if isinstance(kline_cursors, dict) else {}
            required_periods = ("M1", "M5", "M15", "H1", "H4")
            # A cursor is tracked per period.  For example, a backend that
            # already has M1 but was just upgraded to H4 must still receive a
            # bounded history request to initialize the missing periods.
            needs_history = not all(
                int(cursor.get(period) or 0) > 0 for period in required_periods
            ) if isinstance(cursor, dict) else True
            if needs_history:
                history_id = self._next_request_id
                self._next_request_id += 1
                self._history_requests[history_id] = {"symbol": symbol, "cursor": {}}
                self._history_rows[history_id] = []
                self._client.reqHistoricalData(
                    history_id, contract, "", "2 D", "1 min", "TRADES",
                    0, 2, False, [],
                )

    def historicalData(self, reqId, bar):
        request = self._history_requests.get(reqId)
        if request is None:
            return
        raw_date = str(getattr(bar, "date", "")).strip()
        try:
            timestamp = int(float(raw_date))
        except (TypeError, ValueError):
            # IBKR may return a UTC epoch or a wall-clock string depending on
            # the Gateway/API version.  Historical bars are requested in UTC.
            parsed = None
            for fmt in ("%Y%m%d %H:%M:%S", "%Y%m%d"):
                try:
                    parsed = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            if parsed is None:
                logger.warning("Ignoring IBKR historical bar with invalid date: %r", raw_date)
                return
            timestamp = int(parsed.timestamp())
        if timestamp <= 0:
            return
        self._history_rows.setdefault(reqId, []).append({
            "timestamp": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
            "open": float(bar.open), "high": float(bar.high),
            "low": float(bar.low), "close": float(bar.close),
            "volume": float(getattr(bar, "volume", 0) or 0),
        })

    def historicalDataEnd(self, reqId, start, end):
        request = self._history_requests.pop(reqId, None)
        rows = self._history_rows.pop(reqId, [])
        if not request or not rows:
            return
        rows.sort(key=lambda item: item["timestamp"])
        for period, seconds in (("M1", 60), ("M5", 300), ("M15", 900), ("H1", 3600), ("H4", 14400)):
            grouped = {}
            for row in rows:
                ts = int(datetime.fromisoformat(row["timestamp"]).timestamp())
                bucket = ts - ts % seconds
                item = grouped.setdefault(bucket, {
                    "timestamp": datetime.fromtimestamp(bucket, timezone.utc).isoformat(),
                    "open": row["open"], "high": row["high"], "low": row["low"],
                    "close": row["close"], "volume": row.get("volume", 0),
                })
                item["high"] = max(item["high"], row["high"])
                item["low"] = min(item["low"], row["low"])
                item["close"] = row["close"]
                item["volume"] += row.get("volume", 0)
            self.on_event("kline", {
                "symbol": request["symbol"], "period": period,
                "is_full": True, "klines": list(grouped.values()),
            })

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
        self._update_bars(self._request_symbols.get(reqId, ""), (bid + ask) / 2.0)

    def _update_bars(self, symbol: str, price: float) -> None:
        """Build completed M1/M5/M15 bars in memory; never persist raw ticks."""
        if not symbol or price <= 0:
            return
        now = int(time.time())
        for period, seconds in (
            ("M1", 60), ("M5", 300), ("M15", 900),
            ("H1", 3600), ("H4", 14400),
        ):
            bucket = now - (now % seconds)
            key = (symbol, period)
            current = self._bars.get(key)
            if current is not None and current["timestamp"] != bucket:
                self.on_event("kline", {
                    "symbol": symbol, "period": period, "is_full": False,
                    "klines": [current],
                })
                current = None
            if current is None:
                current = {
                    "timestamp": datetime.fromtimestamp(
                        bucket, timezone.utc
                    ).isoformat(),
                    "open": price, "high": price, "low": price,
                    "close": price, "volume": 0,
                }
                self._bars[key] = current
            else:
                current["high"] = max(float(current["high"]), price)
                current["low"] = min(float(current["low"]), price)
                current["close"] = price

    def nextValidId(self, orderId):
        self._next_request_id = max(self._next_request_id, int(orderId))
        self._ready.set()
        self.on_event("gateway_ready", {"next_order_id": int(orderId)})
        # Request a complete broker position snapshot after the API handshake.
        # The snapshot is finalized by positionEnd(), including an empty list
        # when the account genuinely has no open positions.
        self.request_positions()
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

    def position(self, account, contract, pos, avgCost):
        """Collect one IBKR position callback for the current full snapshot."""
        try:
            quantity = float(pos or 0)
        except (TypeError, ValueError):
            return
        symbol = str(getattr(contract, "symbol", "") or "").strip()
        if not symbol or quantity == 0:
            return
        con_id = int(getattr(contract, "conId", 0) or 0)
        # conId is stable and unique within an IBKR account.  Fall back to a
        # deterministic symbol key for mocked/older Gateway callbacks.
        key = con_id if con_id > 0 else abs(hash(symbol))
        self._positions[key] = {
            "ticket": key,
            "symbol": symbol,
            "volume": abs(quantity),
            "priceOpen": float(avgCost or 0),
            "type": "BUY" if quantity > 0 else "SELL",
            "profit": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "comment": "IBKR",
            "con_id": con_id,
            "account": str(account or "").strip().upper(),
            "sec_type": str(getattr(contract, "secType", "") or ""),
        }
        logger.info("IBKR position callback account=%s symbol=%s quantity=%s",
                    account, symbol, quantity)

    def positionEnd(self):
        """Publish the complete position snapshot, including an empty one."""
        positions = list(self._positions.values())
        self.on_event("positions_snapshot", {
            "account": str(positions[0].get("account") if positions else "").upper(),
            "positions": positions,
        })
        logger.info("IBKR position snapshot complete count=%s", len(positions))
        self._positions.clear()
        self._positions_requested = False

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
