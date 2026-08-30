import json
import unittest

from market.services.daily_review_service import DailyReviewCoordinator


class _MemoryStorage:
    def __init__(self):
        self.entities = {}

    def fetchone(self, sql, params=()):
        if "runtime_entities" not in sql:
            return None
        payload = self.entities.get((params[0], params[1]))
        return {"payload_json": payload} if payload is not None else None

    def execute(self, sql, params=()):
        if "runtime_entities" in sql and len(params) >= 7:
            self.entities[(params[2], params[3])] = params[6]


class DailyReviewCoordinatorTestCase(unittest.TestCase):
    def test_buy_plan_outcome_records_target_hit(self):
        plan = {
            "plan_id": "p1", "setup_type": "range_boundary", "direction": "buy",
            "entry_price": 100, "entry_zone": {"lower": 99, "upper": 101},
            "stop_loss": 95, "take_profit": 110, "valid_from": 1000,
            "expires_at": 1300,
        }
        bars = [
            {"time": 1000, "low": 98, "high": 102},
            {"time": 1060, "low": 101, "high": 111},
        ]
        result = DailyReviewCoordinator._plan_outcome(plan, bars, 1300)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["outcome"], "target_hit")
        self.assertEqual(result["resolved_at"], 1060)

    def test_stale_structure_scope_is_skipped_without_llm(self):
        now = 2_000_000
        coordinator = DailyReviewCoordinator(lambda _: None, storage=_MemoryStorage())
        saved = []
        coordinator._save_structure = lambda user_id, entity_id, payload: saved.append(payload)
        coordinator._call_llm = lambda *args, **kwargs: self.fail("stale scope must not call LLM")

        status = coordinator._review_structure_scope(
            {"user_id": 7, "symbol": "BTCUSD", "period": "M5", "latest_market_at": now - 43201},
            "2026-08-30", now,
        )

        self.assertEqual(status, "skipped")
        self.assertIn("过去12小时没有收到行情", saved[0]["skip_reason"])

    def test_family_failure_does_not_abort_following_scope(self):
        result = {"structure": {"completed": 0, "skipped": 0, "failed": 0}}

        def review(scope):
            if scope == 1:
                raise RuntimeError("broken scope")
            return "completed"

        DailyReviewCoordinator._run_family(result, "structure", lambda: [1, 2], review)
        self.assertEqual(result["structure"]["failed"], 1)
        self.assertEqual(result["structure"]["completed"], 1)

    def test_daily_batch_is_idempotent_after_completion(self):
        storage = _MemoryStorage()

        class Coordinator(DailyReviewCoordinator):
            def __init__(self):
                super().__init__(lambda _: None, storage=storage, now_provider=lambda: 2_000_000)
                self.scope_calls = 0

            def _structure_scopes(self, now):
                self.scope_calls += 1
                return []

            def _strategy_scopes(self):
                return []

        coordinator = Coordinator()
        first = coordinator.run_once("2026-08-30")
        second = coordinator.run_once("2026-08-30")

        self.assertEqual(first["status"], "completed")
        self.assertTrue(second["already_completed"])
        self.assertEqual(coordinator.scope_calls, 1)
        saved = json.loads(storage.entities[(coordinator.BATCH_ENTITY, "2026-08-30")])
        self.assertEqual(saved["status"], "completed")

    def test_strategy_review_includes_unconsumed_structure_plans(self):
        class Storage(_MemoryStorage):
            def fetchall(self, sql, params=()):
                if "FROM structure_trade_plans" in sql:
                    return [{
                        "plan_id": "p1", "plan_group_id": "g1", "period": "M5",
                        "setup_type": "range_boundary", "direction": "buy",
                        "entry_mode": "touch_or_near", "status": "active",
                        "structure_bar_time": 1900, "valid_from": 1900,
                        "expires_at": 2500, "created_at": 1900, "updated_at": 1900,
                        "payload_json": json.dumps({
                            "entry_price": 100, "stop_loss": 95, "take_profit": 110,
                            "risk_reward_ratio": 2, "reason": "箱体下沿买入",
                        }),
                    }]
                if "FROM structure_plan_executions" in sql:
                    return []
                return []

        class Strategy:
            @staticmethod
            def get_signal_sources(source_type, enabled_only=True):
                return [{"type": source_type, "period": "M5", "enabled": True}]

        coordinator = DailyReviewCoordinator(lambda _: None, storage=Storage())
        context = coordinator._deployment_structure_context(
            7, 17, "deployment-1", "BTCUSD", Strategy(), 1000,
        )

        self.assertTrue(context["enabled"])
        self.assertEqual(context["metrics"]["structure_plan_count"], 1)
        self.assertEqual(context["metrics"]["structure_plan_unconsumed_count"], 1)
        self.assertEqual(context["plans"][0]["execution_status"], "unconsumed")


if __name__ == "__main__":
    unittest.main()
