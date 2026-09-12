import json
import threading
import unittest

from market.store.structure_plan_store import (
    StructureTradePlanRepository, opportunity_status_for_execution,
)


class _ExecutionStorage:
    """Minimal transactional model for Structure Plan execution rows."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO structure_plan_executions"):
            columns = normalized.split("(", 1)[1].split(")", 1)[0].split(",")
            row = dict(zip((item.strip() for item in columns), params))
            with self.lock:
                self.rows.setdefault(row["execution_id"], row)
            return
        if normalized.startswith("DELETE FROM structure_plan_executions"):
            user_id, account_id, deployment_id, plan_id, plan_stage, direction = params
            with self.lock:
                for key, row in list(self.rows.items()):
                    if (
                        row["user_id"], row["account_id"], row["deployment_id"],
                        row["plan_id"], row["plan_stage"], row["direction"],
                    ) == (
                        user_id, account_id, deployment_id, plan_id,
                        plan_stage, direction,
                    ) and row["status"] == "claimed":
                        del self.rows[key]
            return
        if normalized.startswith("UPDATE structure_plan_executions"):
            with self.lock:
                if "WHERE user_id=? AND account_id=?" in normalized:
                    user_id, account_id, deployment_id, plan_id, stage, direction = params[-6:]
                    row = next((item for item in self.rows.values() if (
                        item["user_id"], item["account_id"], item["deployment_id"],
                        item["plan_id"], item["plan_stage"], item["direction"],
                    ) == (user_id, account_id, deployment_id, plan_id, stage, direction)), None)
                    if row:
                        assignments = normalized.split(" SET ", 1)[1].split(" WHERE ", 1)[0].split(",")
                        for assignment, value in zip(assignments, params[:-6]):
                            row[assignment.split("=", 1)[0]] = value
            return
        if normalized.startswith("UPDATE structure_trade_plans"):
            return
        raise AssertionError(normalized)

    def fetchone(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM structure_plan_executions" in normalized:
            with self.lock:
                rows = list(self.rows.values())
            if "execution_id=?" in normalized:
                row = next((item for item in rows if item["execution_id"] == params[0]), None)
            elif "plan_group_id=?" in normalized:
                user_id, account_id, deployment_id, group_id, stage = params
                row = next((item for item in rows if (
                    item["user_id"], item["account_id"], item["deployment_id"],
                    item["plan_group_id"], item["plan_stage"],
                ) == (user_id, account_id, deployment_id, group_id, stage)
                    and item["status"] != "released"), None)
            else:
                user_id, account_id, deployment_id, plan_id, stage, direction = params
                row = next((item for item in rows if (
                    item["user_id"], item["account_id"], item["deployment_id"],
                    item["plan_id"], item["plan_stage"], item["direction"],
                ) == (user_id, account_id, deployment_id, plan_id, stage, direction)
                    and item["status"] != "released"), None)
            return dict(row) if row else None
        if "FROM structure_trade_plans" in normalized:
            return None
        raise AssertionError(normalized)

    def fetchall(self, sql, params=()):
        raise AssertionError("fetchall not expected")


class StructurePlanExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.storage = _ExecutionStorage()
        self.repository = StructureTradePlanRepository(self.storage)

    def claim(self, plan_id="plan-1", group_id="group-1", stage="initial", direction="buy"):
        return self.repository.claim_execution(
            7, 22, "deployment-1", "strategy-1", plan_id, group_id,
            plan_stage=stage, direction=direction, tick_id="tick-1",
            execution_mode="paper", payload={"plan_stage": stage, "direction": direction},
        )

    def test_same_plan_stage_and_direction_can_only_be_claimed_once(self):
        self.assertTrue(self.claim())
        self.assertFalse(self.claim())

    def test_initial_and_breakout_stages_are_independently_consumable(self):
        self.assertTrue(self.claim(stage="initial"))
        self.assertTrue(self.claim(stage="breakout"))

    def test_group_alternatives_are_mutually_exclusive_only_within_stage(self):
        self.assertTrue(self.claim(plan_id="buy-plan", stage="initial", direction="buy"))
        self.assertFalse(self.claim(plan_id="sell-plan", stage="initial", direction="sell"))
        self.assertTrue(self.claim(plan_id="sell-plan", stage="breakout", direction="sell"))

    def test_release_only_removes_selected_stage_and_direction(self):
        self.assertTrue(self.claim(stage="initial"))
        self.assertTrue(self.claim(stage="breakout"))
        self.repository.release_claim(
            7, 22, "deployment-1", "plan-1",
            plan_stage="initial", direction="buy",
        )
        self.assertTrue(self.claim(stage="initial"))
        self.assertFalse(self.claim(stage="breakout"))

    def test_record_and_status_update_only_touch_selected_stage(self):
        self.assertTrue(self.claim(stage="initial"))
        self.assertTrue(self.claim(stage="breakout"))
        self.repository.record_execution(
            7, 22, "deployment-1", "strategy-1", "plan-1", "group-1",
            "ordered", order_id="order-initial",
            payload={"trade_opportunity_stage": "initial", "direction": "buy"},
            plan_stage="initial", direction="buy",
        )
        updated = self.repository.update_execution_status(
            7, 22, "deployment-1", "plan-1", "filled",
            order_id="order-initial", plan_stage="initial", direction="buy",
        )

        self.assertTrue(updated)
        rows = list(self.storage.rows.values())
        initial = next(row for row in rows if row["plan_stage"] == "initial")
        breakout = next(row for row in rows if row["plan_stage"] == "breakout")
        self.assertEqual(initial["status"], "filled")
        self.assertEqual(initial["order_id"], "order-initial")
        self.assertEqual(breakout["status"], "claimed")

    def test_execution_receipts_map_to_stage_scoped_opportunity_states(self):
        self.assertEqual(
            opportunity_status_for_execution("initial", "filled"),
            "initial_filled",
        )
        self.assertEqual(
            opportunity_status_for_execution("breakout", "pending"),
            "breakout_ordered",
        )
        self.assertEqual(
            opportunity_status_for_execution("breakout", "timeout"),
            "breakout_failed",
        )
        self.assertEqual(opportunity_status_for_execution("initial", "unknown"), "")

    def test_opportunity_aggregate_prefers_protection_then_breakout(self):
        self.assertEqual(
            self.repository._aggregate_opportunity_status({
                "initial_execution_status": "filled",
            }),
            "protection_pending",
        )
        self.assertEqual(
            self.repository._aggregate_opportunity_status({
                "initial_execution_status": "filled",
                "initial_protection_confirmed": True,
            }),
            "breakout_eligible",
        )
        self.assertEqual(
            self.repository._aggregate_opportunity_status({
                "initial_protection_confirmed": True,
                "breakout_execution_status": "filled",
            }),
            "breakout_filled",
        )


if __name__ == "__main__":
    unittest.main()
