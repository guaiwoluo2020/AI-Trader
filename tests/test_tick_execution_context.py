import unittest
from types import SimpleNamespace

from market.services.tick_execution_context import TickExecutionContext


class TickExecutionContextTests(unittest.TestCase):
    def test_context_returns_defensive_signal_copies_with_one_stable_tick_id(self):
        original = SimpleNamespace(signal_id="signal-1", action="buy")
        context = TickExecutionContext.create(
            user_id=7,
            source_account_id=21,
            symbol="BTCUSD#",
            price=113500.0,
            captured_at=1770000000.25,
            signals_by_strategy={"strategy-1": [original]},
        )

        first = context.signals_for("strategy-1")
        second = context.signals_for("strategy-1")
        first[0].action = "sell"

        self.assertEqual(second[0].action, "buy")
        self.assertEqual(context.tick_id, context.to_audit_dict()["tick_id"])
        self.assertEqual(context.symbol, "BTCUSD#")
        self.assertFalse(context.has_strategy("missing"))


if __name__ == "__main__":
    unittest.main()
