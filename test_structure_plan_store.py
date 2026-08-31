import sqlite3
import unittest
from unittest.mock import patch

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
        self.connection.execute(
            """
            CREATE TABLE structure_trade_plans (
                plan_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                strategy_id TEXT NOT NULL,
                signal_source_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                period TEXT NOT NULL,
                plan_group_id TEXT NOT NULL,
                setup_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_mode TEXT NOT NULL,
                status TEXT NOT NULL,
                structure_bar_time INTEGER NOT NULL,
                valid_from INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
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

    @staticmethod
    def _plan(plan_id, *, status="active", direction="buy", entry=100,
              valid_from=1000, expires_at=1360, setup="range_breakout"):
        return {
            "plan_id": plan_id,
            "plan_group_id": f"group-{plan_id}",
            "setup_type": setup,
            "direction": direction,
            "entry_mode": "breakout_retest" if entry else "watch",
            "status": status,
            "entry_price": entry,
            "entry_zone": {
                "lower": entry - 1 if entry else 0,
                "upper": entry + 1 if entry else 0,
            },
            "valid_from": valid_from,
            "expires_at": expires_at,
            "fingerprint": f"fp-{plan_id}-{valid_from}",
            "generated_at": valid_from,
        }

    def _replace(self, plans, bar_time):
        return self.repo.replace_scope(
            1, 0, "", "market_structure", "XAUUSDm", "M1",
            plans, bar_time,
        )

    @patch("market.store.structure_plan_store.time.time", return_value=1100)
    def test_no_trade_does_not_invalidate_unexpired_actionable_plan(self, _):
        active = self._plan("active-plan")
        self._replace([active], 1000)
        observation = self._plan(
            "no-trade", status="watching", direction="none", entry=0,
            valid_from=1060, expires_at=1120, setup="no_trade",
        )

        current = self._replace([observation], 1060)

        by_id = {item["plan_id"]: item for item in current}
        self.assertEqual(by_id["active-plan"]["status"], "active")
        self.assertEqual(by_id["no-trade"]["status"], "watching")

    @patch("market.store.structure_plan_store.time.time", return_value=1100)
    def test_new_actionable_plan_replaces_previous_opportunity(self, _):
        self._replace([self._plan("old-plan")], 1000)

        current = self._replace([
            self._plan("new-plan", direction="sell", entry=99, valid_from=1060)
        ], 1060)

        self.assertEqual([item["plan_id"] for item in current], ["new-plan"])
        old = self.repo.storage.fetchone(
            "SELECT status FROM structure_trade_plans WHERE plan_id=?",
            ("old-plan",),
        )
        self.assertEqual(old["status"], "invalidated")

    @patch("market.store.structure_plan_store.time.time", return_value=1100)
    def test_same_live_plan_keeps_original_boundary_and_expiry(self, _):
        self._replace([self._plan("same-plan", entry=100, expires_at=1360)], 1000)
        changed = self._plan(
            "same-plan", entry=105, valid_from=1060, expires_at=1420,
        )

        current = self._replace([changed], 1060)

        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["entry_price"], 100)
        self.assertEqual(current[0]["valid_from"], 1000)
        self.assertEqual(current[0]["expires_at"], 1360)

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
