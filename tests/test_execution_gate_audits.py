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

    def test_record_is_idempotent_per_tick_deployment_and_strategy(self):
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
        second = repo.record(status="blocked", reason_code="risk_limit", **common)

        self.assertEqual(first, second)
        self.assertEqual(len(storage.rows), 1)
        row = next(iter(storage.rows.values()))
        self.assertEqual(row["reason_code"], "risk_limit")
        self.assertEqual(json.loads(row["gate_trace_json"])[0]["reason_code"], "eligible")

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
