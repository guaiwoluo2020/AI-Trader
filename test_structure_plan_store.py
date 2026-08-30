import sqlite3
import unittest

from market.store.structure_plan_store import StructureTradePlanRepository


class _Storage:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """
            CREATE TABLE structure_plan_executions (
                execution_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                deployment_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                plan_group_id TEXT NOT NULL,
                status TEXT NOT NULL,
                order_id TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(user_id,account_id,deployment_id,plan_id)
            )
            """
        )

    def execute(self, sql, params=()):
        self.connection.execute(sql, params)
        self.connection.commit()

    def fetchone(self, sql, params=()):
        return self.connection.execute(sql, params).fetchone()

    def fetchall(self, sql, params=()):
        return self.connection.execute(sql, params).fetchall()


class StructurePlanExecutionStoreTests(unittest.TestCase):
    def setUp(self):
        self.repo = StructureTradePlanRepository(_Storage())

    def test_same_plan_can_only_be_claimed_once_per_deployment(self):
        first = self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "plan-a", "group-a"
        )
        second = self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "plan-a", "group-a"
        )
        other_deployment = self.repo.claim_execution(
            1, 11, "deployment-b", "strategy-a", "plan-a", "group-a"
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(other_deployment)

    def test_sibling_plans_in_same_group_are_mutually_exclusive(self):
        self.assertTrue(self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "buy-plan", "group-a"
        ))
        self.assertFalse(self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "sell-plan", "group-a"
        ))

    def test_claim_can_be_released_after_technical_failure(self):
        self.assertTrue(self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "plan-a", "group-a"
        ))
        self.repo.release_claim(1, 10, "deployment-a", "plan-a")
        self.assertFalse(self.repo.is_consumed(
            1, 10, "deployment-a", "plan-a"
        ))

    def test_record_execution_updates_claim(self):
        self.assertTrue(self.repo.claim_execution(
            1, 10, "deployment-a", "strategy-a", "plan-a", "group-a"
        ))
        self.repo.record_execution(
            1, 10, "deployment-a", "strategy-a", "plan-a", "group-a",
            "ordered", order_id="order-a",
        )
        rows = self.repo.list_executions(1, ["plan-a"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "ordered")
        self.assertEqual(rows[0]["order_id"], "order-a")


if __name__ == "__main__":
    unittest.main()
