import unittest

from market.services.tick_execution_core import (
    PendingTickResult, TickExecutionCore, TickQuote,
)


class TickExecutionCoreTests(unittest.TestCase):
    def test_pending_requires_next_quote_and_then_times_out(self):
        quote = TickQuote.create(100, 101, 1_000)
        self.assertEqual(TickExecutionCore.pending_state(1_000, quote).status, "wait")
        self.assertEqual(
            TickExecutionCore.pending_state(1_000, TickQuote.create(100, 101, 1_001)).status,
            "eligible",
        )
        self.assertEqual(
            TickExecutionCore.pending_state(1_000, TickQuote.create(100, 101, 1_061)).status,
            "timeout",
        )

    def test_exit_uses_executable_side_of_quote(self):
        buy = {"direction": "buy", "stop_loss": 99.5, "take_profit": 102}
        self.assertEqual(
            TickExecutionCore.exit_state(buy, TickQuote.create(99.5, 100, 2)).status,
            "stop_loss",
        )
        sell = {"direction": "sell", "stop_loss": 102, "take_profit": 99}
        self.assertEqual(
            TickExecutionCore.exit_state(sell, TickQuote.create(100, 102, 2)).status,
            "stop_loss",
        )

    def test_classify_pending_has_one_canonical_partition(self):
        quote = TickQuote.create(100, 101, 1_061)
        orders = [
            {"requested_at": 1_000, "id": "timeout"},
            {"requested_at": 1_001, "id": "eligible"},
            {"requested_at": 1_061, "id": "same_tick"},
        ]
        batch = TickExecutionCore.classify_pending(
            orders, quote, lambda order: order["requested_at"], 60,
        )
        self.assertEqual([item["id"] for item in batch.timed_out], ["timeout"])
        self.assertEqual([item["id"] for item in batch.eligible], ["eligible"])
        self.assertEqual([item["id"] for item in batch.waiting], ["same_tick"])

    def test_adapter_receives_only_timeout_and_eligible_transitions(self):
        class Adapter:
            timeout_seconds = 60

            def __init__(self):
                self.events = []

            @staticmethod
            def requested_at(order):
                return order["requested_at"]

            def on_timeout(self, order, result: PendingTickResult):
                self.events.append((order["id"], result.status))

            def on_eligible(self, order, quote):
                self.events.append((order["id"], "eligible"))

        adapter = Adapter()
        batch = TickExecutionCore.advance_pending(
            adapter,
            [
                {"requested_at": 1_000, "id": "timeout"},
                {"requested_at": 1_001, "id": "eligible"},
                {"requested_at": 1_061, "id": "same_tick"},
            ],
            TickQuote.create(100, 101, 1_061),
        )
        self.assertEqual(adapter.events, [("timeout", "timeout"), ("eligible", "eligible")])
        self.assertEqual([item["id"] for item in batch.waiting], ["same_tick"])


if __name__ == "__main__":
    unittest.main()
