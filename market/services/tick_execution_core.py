"""Shared quote-level execution semantics.

This module deliberately has no database or MT5 dependency.  Live/Paper and
historical adapters own persistence and broker transport, while this core owns
the timing and price rules that must never diverge between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Mapping, Protocol, TypeVar


@dataclass(frozen=True)
class TickQuote:
    bid: float
    ask: float
    timestamp: int

    @classmethod
    def create(cls, bid: float, ask: float | None, timestamp: int) -> "TickQuote":
        bid_value = float(bid)
        ask_value = float(ask if ask is not None else bid_value)
        if bid_value <= 0 or ask_value <= 0:
            raise ValueError("Tick 报价必须大于零")
        return cls(min(bid_value, ask_value), max(bid_value, ask_value), int(timestamp))

    def entry_price(self, direction: str) -> float:
        return self.ask if str(direction).lower() == "buy" else self.bid

    def close_price(self, direction: str) -> float:
        return self.bid if str(direction).lower() == "buy" else self.ask


@dataclass(frozen=True)
class PendingTickResult:
    status: str  # wait / eligible / timeout
    reason: str = ""


@dataclass(frozen=True)
class ExitTickResult:
    status: str  # hold / stop_loss / take_profit
    price: float = 0.0


TOrder = TypeVar("TOrder")


@dataclass(frozen=True)
class PendingTickBatch:
    """A deterministic pending-order transition for one quote."""

    waiting: List[Any]
    eligible: List[Any]
    timed_out: List[Any]


class PendingOrderExecutionAdapter(Protocol[TOrder]):
    """Persistence/transport boundary for the common Pending state machine.

    Paper persists fills in MySQL, historical replay mutates its in-memory
    ledger, and MT5 only submits an instruction then waits for an EA receipt.
    None of those boundaries is allowed to redefine quote timing.
    """

    timeout_seconds: int

    def requested_at(self, order: TOrder) -> int: ...

    def on_timeout(self, order: TOrder, result: PendingTickResult) -> None: ...

    def on_eligible(self, order: TOrder, quote: TickQuote) -> None: ...


class TickExecutionCore:
    """Canonical plan-to-pending and quote-to-exit state transitions."""

    @staticmethod
    def pending_state(
        requested_at: int,
        quote: TickQuote,
        timeout_seconds: int = 60,
    ) -> PendingTickResult:
        requested_at = int(requested_at or 0)
        timeout = max(1, int(timeout_seconds or 60))
        # An order created while processing a quote is never allowed to consume
        # that same quote.  The next quote is the first executable one.
        if quote.timestamp <= requested_at:
            return PendingTickResult("wait")
        if quote.timestamp > requested_at + timeout:
            return PendingTickResult("timeout", "等待下一次行情撮合超时，订单已自动取消")
        return PendingTickResult("eligible")

    @classmethod
    def classify_pending(
        cls,
        orders: Iterable[TOrder],
        quote: TickQuote,
        requested_at: Callable[[TOrder], int],
        timeout_seconds: int = 60,
    ) -> PendingTickBatch:
        """Classify a Pending queue once, without touching storage.

        This is deliberately the only place deciding whether an order can use
        a quote.  Adapters own persistence, order validation and broker I/O;
        they receive the same waiting/eligible/timeout partition in live and
        replay execution.
        """
        waiting: List[TOrder] = []
        eligible: List[TOrder] = []
        timed_out: List[TOrder] = []
        for order in orders:
            state = cls.pending_state(requested_at(order), quote, timeout_seconds)
            if state.status == "wait":
                waiting.append(order)
            elif state.status == "timeout":
                timed_out.append(order)
            else:
                eligible.append(order)
        return PendingTickBatch(waiting, eligible, timed_out)

    @classmethod
    def advance_pending(
        cls,
        adapter: PendingOrderExecutionAdapter[TOrder],
        orders: Iterable[TOrder],
        quote: TickQuote,
    ) -> PendingTickBatch:
        """Advance Pending state and delegate side effects to one adapter.

        The returned batch is useful to adapters that keep an in-memory queue;
        persistent adapters can ignore it after recording their own outcomes.
        """
        batch = cls.classify_pending(
            orders, quote, adapter.requested_at, adapter.timeout_seconds,
        )
        timeout = PendingTickResult("timeout", "等待下一次行情撮合超时，订单已自动取消")
        for order in batch.timed_out:
            adapter.on_timeout(order, timeout)
        for order in batch.eligible:
            adapter.on_eligible(order, quote)
        return batch

    @staticmethod
    def exit_state(position: Mapping[str, Any] | Any, quote: TickQuote) -> ExitTickResult:
        def value(name: str, default: float = 0.0) -> float:
            item = position.get(name, default) if isinstance(position, Mapping) else getattr(position, name, default)
            return float(item or default)

        direction = str(
            position.get("direction", "") if isinstance(position, Mapping)
            else getattr(position, "direction", "")
        ).lower()
        price = quote.close_price(direction)
        stop_loss = value("stop_loss")
        take_profit = value("take_profit")
        if direction == "buy":
            if stop_loss > 0 and price <= stop_loss:
                return ExitTickResult("stop_loss", price)
            if take_profit > 0 and price >= take_profit:
                return ExitTickResult("take_profit", price)
        elif direction == "sell":
            if stop_loss > 0 and price >= stop_loss:
                return ExitTickResult("stop_loss", price)
            if take_profit > 0 and price <= take_profit:
                return ExitTickResult("take_profit", price)
        return ExitTickResult("hold", price)
