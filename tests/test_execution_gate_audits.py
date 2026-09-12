import json
import unittest

from repositories.execution_gate_audits import ExecutionGateAuditRepository


class _Storage:
    def __init__(self):
        self.rows = {}

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if not normalized.startswith("INSERT INTO execution_gate_audits"):
            raise AssertionError(normalized)
        columns = [item.strip() for item in normalized.split("(", 1)[1].split(")", 1)[0].split(",")]
        row = dict(zip(columns, params))
        previous = self.rows.get(row["audit_id"])
        if previous is not None and "occurrence_count=execution_gate_audits.occurrence_count + 1" in normalized:
            row["tick_id"] = previous["tick_id"]
            row["first_seen_at"] = previous["first_seen_at"]
            row["occurrence_count"] = previous["occurrence_count"] + 1
            row["created_at"] = previous["created_at"]
        self.rows[row["audit_id"]] = row

    def fetchall(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM execution_gate_audits" not in normalized:
            raise AssertionError(normalized)
        user_id, *plan_ids = params
        return [
            dict(row) for row in self.rows.values()
            if row["user_id"] == user_id and row["plan_id"] in plan_ids
        ]


class ExecutionGateAuditRepositoryTests(unittest.TestCase):
    def test_different_plan_stages_have_distinct_audit_rows_on_same_tick(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)
        common = dict(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", tick_id="tick-1", execution_mode="paper",
            symbol="BTCUSD#", plan_id="plan-1", direction="buy",
            status="blocked", reason_code="trading_disabled",
        )

        initial = repo.record(plan_stage="initial", **common)
        breakout = repo.record(plan_stage="breakout", **common)

        self.assertNotEqual(initial, breakout)
        self.assertEqual(len(storage.rows), 2)

    def test_record_is_idempotent_for_the_same_execution_event(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)
        common = dict(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", tick_id="tick-1", execution_mode="paper",
            symbol="BTCUSD#", plan_id="plan-1", plan_stage="initial",
            direction="buy", gate_trace=[{"reason_code": "eligible"}],
            account_snapshot={"open_positions": 0},
        )

        first = repo.record(status="eligible", reason_code="eligible", **common)
        second = repo.record(status="eligible", reason_code="eligible", **common)

        self.assertEqual(first, second)
        self.assertEqual(len(storage.rows), 1)
        row = next(iter(storage.rows.values()))
        self.assertEqual(row["reason_code"], "eligible")
        self.assertEqual(json.loads(row["gate_trace_json"])[0]["reason_code"], "eligible")

    def test_ordinary_inactive_tick_is_not_persisted(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)

        audit_id = repo.record(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", tick_id="tick-1", execution_mode="paper",
            symbol="BTCUSD#", status="no_action", reason_code="no_new_trigger",
        )

        self.assertIsNone(audit_id)
        self.assertEqual(storage.rows, {})

    def test_no_direction_tick_is_not_persisted(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)

        audit_id = repo.record(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", tick_id="tick-1", execution_mode="live",
            symbol="BTCUSD#", status="no_action", reason_code="no_direction",
        )

        self.assertIsNone(audit_id)
        self.assertEqual(storage.rows, {})

    def test_repeated_identical_block_is_aggregated_across_ticks(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)
        common = dict(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", execution_mode="paper", symbol="BTCUSD#",
            plan_id="plan-1", plan_stage="initial", direction="buy",
            status="blocked", reason_code="risk_limit",
        )

        first = repo.record(tick_id="tick-1", **common)
        second = repo.record(tick_id="tick-2", **common)

        self.assertEqual(first, second)
        self.assertEqual(len(storage.rows), 1)
        row = next(iter(storage.rows.values()))
        self.assertEqual(row["tick_id"], "tick-1")
        self.assertEqual(row["last_tick_id"], "tick-2")
        self.assertEqual(row["occurrence_count"], 2)
        self.assertLessEqual(row["first_seen_at"], row["last_seen_at"])

    def test_different_block_reasons_are_separate_audits(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)
        common = dict(
            user_id=7, account_id=22, deployment_id="dep-1",
            strategy_id="strategy-1", execution_mode="paper", symbol="BTCUSD#",
            plan_id="plan-1", plan_stage="initial", direction="buy", status="blocked",
        )

        risk = repo.record(tick_id="tick-1", reason_code="risk_limit", **common)
        position = repo.record(tick_id="tick-2", reason_code="position_limit", **common)

        self.assertNotEqual(risk, position)
        self.assertEqual(len(storage.rows), 2)

    def test_list_for_plans_returns_all_account_outcomes_in_one_query(self):
        storage = _Storage()
        repo = ExecutionGateAuditRepository(storage)
        for account_id, mode in ((21, "live"), (22, "paper")):
            repo.record(
                user_id=7, account_id=account_id, deployment_id=f"dep-{account_id}",
                strategy_id="strategy-1", tick_id="tick-1", execution_mode=mode,
                symbol="BTCUSD#", plan_id="plan-1", plan_stage="initial",
                direction="buy", status="eligible", reason_code="eligible",
            )

        rows = repo.list_for_plans(7, ["plan-1"])

        self.assertEqual({row["execution_mode"] for row in rows}, {"live", "paper"})


if __name__ == "__main__":
    unittest.main()
