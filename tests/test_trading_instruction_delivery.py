from datetime import datetime, timedelta
import unittest

from market.models.trading_instruction import TradingInstruction
from market.store.trading_instruction_store import TradingInstructionStore
from mysql_repositories import TradeExecutionRepository


def _instruction(instruction_id="inst-1"):
    return TradingInstruction(
        instruction_id=instruction_id,
        symbol="BTCUSD#",
        action="b",
        price=100.0,
        mount=0.01,
    )


class TradingInstructionDeliveryTest(unittest.TestCase):
    def test_delivery_retains_same_instruction_until_execution_receipt(self):
        store = TradingInstructionStore()
        instruction = _instruction()
        store.add_instruction(instruction)

        first = store.fetch_and_remove_by_symbol("BTCUSD#", 100.0)

        self.assertEqual([item["instruction_id"] for item in first], ["inst-1"])
        self.assertEqual(store.get_instruction_by_id("inst-1").status, "delivered")
        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 100.0), [])

        instruction.last_delivery_at = datetime.now() - timedelta(seconds=16)
        retry = store.fetch_and_remove_by_symbol("BTCUSD#", 95.0)

        self.assertEqual([item["instruction_id"] for item in retry], ["inst-1"])
        self.assertEqual(instruction.delivery_attempts, 2)

        self.assertTrue(store.mark_execution_report("inst-1", True))
        self.assertIsNone(store.get_instruction_by_id("inst-1"))
        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 110.0), [])


    def test_delivery_times_out_after_bounded_retries(self):
        store = TradingInstructionStore()
        instruction = _instruction("inst-timeout")
        instruction.status = "delivered"
        instruction.delivery_attempts = store.MAX_DELIVERY_ATTEMPTS
        instruction.last_delivery_at = datetime.now() - timedelta(seconds=16)
        store.add_instruction(instruction)

        self.assertEqual(store.fetch_and_remove_by_symbol("BTCUSD#", 100.0), [])
        self.assertEqual(store.get_instruction_by_id("inst-timeout").status, "timeout")


    def test_delivery_metadata_survives_model_round_trip(self):
        instruction = _instruction()
        instruction.status = "delivered"
        instruction.delivery_attempts = 2
        instruction.last_delivery_at = datetime.now()

        restored = TradingInstruction.from_dict(instruction.to_full_dict())

        self.assertEqual(restored.status, "delivered")
        self.assertEqual(restored.delivery_attempts, 2)
        self.assertIsNotNone(restored.last_delivery_at)


class _ExistingExecutionStorage:
    def __init__(self):
        self.execute_called = False

    def fetchone(self, _sql, _params=()):
        return {
            "instruction_id": "inst-duplicate",
            "execution_status": "filled",
            "success": 1,
            "payload_json": "{}",
            "position_attribution_json": "{}",
        }

    def execute(self, _sql, _params=()):
        self.execute_called = True


class TradeExecutionReceiptIdempotencyTest(unittest.TestCase):
    def test_duplicate_receipt_does_not_write_a_second_execution(self):
        storage = _ExistingExecutionStorage()
        result = TradeExecutionRepository(storage).record(
            1, 2, {"instruction_id": "inst-duplicate", "success": True}
        )

        self.assertTrue(result["duplicate"])
        self.assertEqual(result["status"], "filled")
        self.assertFalse(storage.execute_called)
