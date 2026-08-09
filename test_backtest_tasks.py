#!/usr/bin/env python3
"""回测模板、批次和任务管理测试。"""

import tempfile
import unittest
from pathlib import Path

from backtest_data import (
    BacktestDatasetRepository,
    BacktestDatasetService,
    DatasetReferencedError,
)
from backtest_tasks import BacktestTemplateService
from market.models import PositionManagementPolicy
from market.models.trading_strategy import TradingStrategy
from sqlite_storage import (
    PositionManagementPolicyRepository,
    SQLiteStorage,
    StrategyConfigRepository,
    TradingAccountRepository,
    UserRepository,
)


class BacktestTemplateTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "test.db"))
        self.storage.initialize()
        self.users = UserRepository(self.storage)
        self.user = self.users.create_user("template-user", "hash", "salt")
        self.account, _ = TradingAccountRepository(
            self.storage
        ).create_or_rotate_default(self.user.user_id)
        self.strategy_repository = StrategyConfigRepository(self.storage)
        PositionManagementPolicyRepository(self.storage).save(
            PositionManagementPolicy(
                policy_id="policy-1", user_id=self.user.user_id,
                name="Test", config={
                    "initial_stop_rules": [{"type": "signal"}],
                    "initial_take_profit_rules": [{"type": "signal"}],
                    "management_rules": [],
                },
            )
        )
        self.strategy = TradingStrategy(
            symbol="GOLD_", strategy_name="Gold Pivot", enabled=False,
            position_management_policy_id="policy-1",
        )
        self.strategy_repository.save_strategy(self.user.user_id, self.strategy)
        self.dataset_repository = BacktestDatasetRepository(self.storage)
        self.dataset_service = BacktestDatasetService(
            self.dataset_repository, Path(self.temp_dir.name) / "data"
        )
        self.service = BacktestTemplateService(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _ready_dataset(self, name, start=1767225600):
        dataset = self.dataset_service.create_dataset(
            self.user.user_id,
            self.account.account_id,
            name,
            "GOLD_",
            start,
            start + 3600,
            warmup_days=0,
        )
        self.dataset_repository.mark_ready(
            dataset["dataset_id"],
            received_bars=61,
            duplicate_count=0,
            gap_count=0,
            invalid_count=0,
            quality_score=100,
            data_format="csv.gz",
            file_path=str(Path(self.temp_dir.name) / f"{dataset['dataset_id']}.csv.gz"),
            data_hash=f"hash-{dataset['dataset_id']}",
        )
        return self.dataset_repository.get_for_user(
            self.user.user_id, dataset["dataset_id"]
        )

    def _payload(self, dataset_ids):
        return {
            "template_name": "Gold regression",
            "strategy_id": self.strategy.strategy_id,
            "dataset_ids": dataset_ids,
            "description": "standard assumptions",
            "initial_capital": 50000,
            "position_sizing_mode": "risk_percent",
            "fixed_volume": 0.02,
            "risk_percent": 1.5,
            "spread_points": 15,
            "slippage_points": 2,
            "commission_per_lot": 7,
            "max_positions": 2,
            "max_same_direction": 2,
            "use_strategy_exits": True,
        }

    def test_template_run_creates_one_task_per_dataset(self):
        first = self._ready_dataset("Gold H1")
        second = self._ready_dataset("Gold H2", 1767312000)
        template = self.service.create_template(
            self.user.user_id,
            self._payload([first["dataset_id"], second["dataset_id"]]),
        )

        batch = self.service.run_template(
            self.user.user_id, template["template_id"]
        )

        self.assertEqual(batch["status"], "queued")
        self.assertEqual(template["visibility"], "shared")
        self.assertEqual(batch["task_count"], 2)
        self.assertEqual(len(batch["tasks"]), 2)
        self.assertEqual(
            {task["dataset_id"] for task in batch["tasks"]},
            {first["dataset_id"], second["dataset_id"]},
        )
        self.assertTrue(all(task["status"] == "queued" for task in batch["tasks"]))
        self.assertEqual(batch["template_snapshot"]["max_same_direction"], 2)

    def test_strategy_context_exposes_template_risk_defaults(self):
        context = self.service.get_context(self.user.user_id)

        strategy = next(
            item for item in context["strategies"]
            if item["strategy_id"] == self.strategy.strategy_id
        )
        self.assertEqual(strategy["risk_percent"], self.strategy.risk_percent)
        self.assertEqual(strategy["max_positions"], self.strategy.max_positions)
        self.assertEqual(
            strategy["max_same_direction"], self.strategy.max_same_direction
        )

    def test_missing_template_limits_default_to_latest_strategy(self):
        self.strategy.risk_percent = 2.5
        self.strategy.max_positions = 8
        self.strategy.max_same_direction = 5
        self.strategy_repository.save_strategy(self.user.user_id, self.strategy)
        dataset = self._ready_dataset("Gold defaults")
        payload = self._payload([dataset["dataset_id"]])
        for key in ("risk_percent", "max_positions", "max_same_direction"):
            payload.pop(key)

        template = self.service.create_template(self.user.user_id, payload)

        self.assertEqual(template["risk_percent"], 2.5)
        self.assertEqual(template["max_positions"], 8)
        self.assertEqual(template["max_same_direction"], 5)

    def test_same_direction_limit_cannot_exceed_total_limit(self):
        dataset = self._ready_dataset("Gold limits")
        payload = self._payload([dataset["dataset_id"]])
        payload["max_same_direction"] = 3

        with self.assertRaisesRegex(ValueError, "同向最大持仓"):
            self.service.create_template(self.user.user_id, payload)

    def test_each_run_keeps_an_immutable_strategy_snapshot(self):
        dataset = self._ready_dataset("Gold snapshot")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )
        first = self.service.run_template(self.user.user_id, template["template_id"])

        self.strategy.min_confidence = 83
        self.strategy_repository.save_strategy(self.user.user_id, self.strategy)
        second = self.service.run_template(self.user.user_id, template["template_id"])

        self.assertNotEqual(
            first["strategy_snapshot_hash"], second["strategy_snapshot_hash"]
        )
        self.assertNotEqual(
            first["strategy_snapshot"]["min_confidence"],
            second["strategy_snapshot"]["min_confidence"],
        )
        reloaded = self.service.get_batch(self.user.user_id, first["batch_id"])
        self.assertEqual(reloaded["strategy_snapshot"]["min_confidence"], 50)

    def test_other_user_can_use_shared_but_not_private_dataset(self):
        dataset = self._ready_dataset("Shared Gold")
        other = self.users.create_user("shared-runner", "hash", "salt")
        PositionManagementPolicyRepository(self.storage).save(
            PositionManagementPolicy(
                policy_id="other-policy", user_id=other.user_id,
                name="Other exits", config={
                    "initial_stop_rules": [{"type": "signal"}],
                    "initial_take_profit_rules": [{"type": "signal"}],
                    "management_rules": [],
                },
            )
        )
        other_strategy = TradingStrategy(
            symbol="GOLD_", strategy_name="Other Gold", enabled=False,
            position_management_policy_id="other-policy",
        )
        self.strategy_repository.save_strategy(other.user_id, other_strategy)
        other_service = BacktestTemplateService(self.storage)
        payload = self._payload([dataset["dataset_id"]])
        payload["strategy_id"] = other_strategy.strategy_id

        template = other_service.create_template(other.user_id, payload)
        self.assertEqual(
            other_service.run_template(other.user_id, template["template_id"])[
                "task_count"
            ],
            1,
        )

        self.dataset_repository.update_visibility(
            self.user.user_id, dataset["dataset_id"], "private"
        )
        with self.assertRaisesRegex(ValueError, "不可见"):
            other_service.run_template(other.user_id, template["template_id"])

    def test_template_management_is_scoped_to_owner(self):
        dataset = self._ready_dataset("Owned Gold")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )
        other = self.users.create_user("template-intruder", "hash", "salt")

        self.assertIsNone(
            self.service.get_template(other.user_id, template["template_id"])
        )
        self.assertIsNone(
            self.service.update_template(
                other.user_id, template["template_id"], self._payload([])
            )
        )
        self.assertFalse(
            self.service.delete_template(other.user_id, template["template_id"])
        )

    def test_template_rejects_dataset_with_different_symbol(self):
        dataset = self._ready_dataset("Gold mismatch")
        strategy = TradingStrategy(
            symbol="EURUSD", strategy_name="Euro", enabled=False
        )
        self.strategy_repository.save_strategy(self.user.user_id, strategy)
        payload = self._payload([dataset["dataset_id"]])
        payload["strategy_id"] = strategy.strategy_id

        with self.assertRaisesRegex(ValueError, "品种"):
            self.service.create_template(self.user.user_id, payload)

    def test_deleting_template_keeps_historical_batch(self):
        dataset = self._ready_dataset("Gold retained")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )
        batch = self.service.run_template(self.user.user_id, template["template_id"])

        self.assertTrue(
            self.service.delete_template(self.user.user_id, template["template_id"])
        )
        retained = self.service.get_batch(self.user.user_id, batch["batch_id"])
        self.assertIsNone(retained["template_id"])
        self.assertEqual(retained["task_count"], 1)

    def test_shared_template_can_be_run_but_not_managed_by_other_user(self):
        dataset = self._ready_dataset("Shared template Gold")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )
        other = self.users.create_user("template-runner", "hash", "salt")

        visible = self.service.list_templates(other.user_id)
        batch = self.service.run_template(other.user_id, template["template_id"])

        self.assertEqual(len(visible), 1)
        self.assertFalse(visible[0]["is_owner"])
        self.assertFalse(visible[0]["can_manage"])
        self.assertEqual(visible[0]["creator_username"], "template-user")
        self.assertEqual(batch["strategy_name"], self.strategy.strategy_name)
        self.assertEqual(batch["task_count"], 1)
        owner = self.storage.fetchone(
            "SELECT user_id FROM backtest_batches WHERE batch_id = ?",
            (batch["batch_id"],),
        )
        self.assertEqual(int(owner["user_id"]), other.user_id)
        self.assertIsNone(
            self.service.update_template(
                other.user_id,
                template["template_id"],
                self._payload([dataset["dataset_id"]]),
            )
        )
        self.assertFalse(
            self.service.delete_template(other.user_id, template["template_id"])
        )

    def test_private_template_is_hidden_from_other_users(self):
        dataset = self._ready_dataset("Private template Gold")
        payload = self._payload([dataset["dataset_id"]])
        payload["visibility"] = "private"
        template = self.service.create_template(self.user.user_id, payload)
        other = self.users.create_user("private-template-user", "hash", "salt")

        self.assertEqual(self.service.list_templates(other.user_id), [])
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.service.run_template(other.user_id, template["template_id"])

    def test_shared_template_rejects_private_dataset(self):
        dataset = self._ready_dataset("Private source Gold")
        self.dataset_repository.update_visibility(
            self.user.user_id, dataset["dataset_id"], "private"
        )

        with self.assertRaisesRegex(ValueError, "共享模板"):
            self.service.create_template(
                self.user.user_id, self._payload([dataset["dataset_id"]])
            )

        payload = self._payload([dataset["dataset_id"]])
        payload["visibility"] = "private"
        private_template = self.service.create_template(self.user.user_id, payload)
        self.assertEqual(private_template["visibility"], "private")

    def test_dataset_referenced_by_template_cannot_be_deleted(self):
        dataset = self._ready_dataset("Template protected Gold")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )

        referenced = self.dataset_repository.get_for_user(
            self.user.user_id, dataset["dataset_id"]
        )
        self.assertEqual(referenced["template_reference_count"], 1)
        self.assertEqual(referenced["task_reference_count"], 0)
        self.assertFalse(referenced["can_delete"])
        with self.assertRaisesRegex(DatasetReferencedError, "1 个模板"):
            self.dataset_service.delete_dataset(
                self.user.user_id, dataset["dataset_id"]
            )

        self.service.delete_template(self.user.user_id, template["template_id"])
        self.assertTrue(
            self.dataset_service.delete_dataset(
                self.user.user_id, dataset["dataset_id"]
            )
        )

    def test_dataset_referenced_by_task_remains_protected_after_template_deleted(self):
        dataset = self._ready_dataset("Task protected Gold")
        template = self.service.create_template(
            self.user.user_id, self._payload([dataset["dataset_id"]])
        )
        self.service.run_template(self.user.user_id, template["template_id"])
        self.service.delete_template(self.user.user_id, template["template_id"])

        referenced = self.dataset_repository.get_for_user(
            self.user.user_id, dataset["dataset_id"]
        )
        self.assertEqual(referenced["template_reference_count"], 0)
        self.assertEqual(referenced["task_reference_count"], 1)
        with self.assertRaisesRegex(DatasetReferencedError, "1 个回测任务"):
            self.dataset_service.delete_dataset(
                self.user.user_id, dataset["dataset_id"]
            )


if __name__ == "__main__":
    unittest.main()
