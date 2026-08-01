#!/usr/bin/env python3
"""历史回测行情数据集测试。"""

import tempfile
import unittest
from pathlib import Path

from backtest_data import (
    BacktestDatasetRepository,
    BacktestDatasetService,
    DatasetStatus,
)
from sqlite_storage import (
    SQLiteStorage,
    TradingAccountRepository,
    UserRepository,
)


class BacktestDatasetTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        storage = SQLiteStorage(str(Path(self.temp_dir.name) / "test.db"))
        storage.initialize()
        self.user = UserRepository(storage).create_user(
            "dataset-user", "hash", "salt"
        )
        self.account, _ = TradingAccountRepository(
            storage
        ).create_or_rotate_default(self.user.user_id)
        self.repository = BacktestDatasetRepository(storage)
        self.service = BacktestDatasetService(
            self.repository, Path(self.temp_dir.name) / "market-data"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_dataset(self):
        start = 1767225600
        end = start + 60 * 10
        dataset = self.service.create_dataset(
            self.user.user_id,
            self.account.account_id,
            "Gold January",
            "GOLD_",
            start,
            end,
            warmup_days=0,
        )
        return dataset, start, end

    @staticmethod
    def _bars(start, end):
        return [
            {
                "time": timestamp,
                "open": 4000.0,
                "high": 4002.0,
                "low": 3999.0,
                "close": 4001.0,
                "tick_volume": 100,
                "real_volume": 0,
                "spread": 20,
            }
            for timestamp in range(start, end + 1, 60)
        ]

    def test_ea_upload_builds_ready_dataset(self):
        dataset, start, end = self._create_dataset()
        task = self.service.get_next_task(self.account.account_id, "GOLD_")

        result = self.service.accept_chunk(
            self.account.account_id,
            dataset["dataset_id"],
            {
                "chunk_index": task["chunk_index"],
                "range_start": task["range_start"],
                "range_end": task["range_end"],
                "bars": self._bars(start, end),
                "broker_server": "DemoBroker",
                "ea_version": "2.04",
            },
        )

        ready = result["dataset"]
        self.assertEqual(ready["status"], DatasetStatus.READY)
        self.assertEqual(ready["received_bars"], 11)
        self.assertEqual(ready["quality_score"], 100.0)
        self.assertEqual(ready["broker_server"], "DemoBroker")
        self.assertTrue(Path(ready["file_path"]).exists())
        self.assertTrue(ready["data_hash"])

    def test_duplicate_chunk_is_idempotent_after_completion(self):
        dataset, start, end = self._create_dataset()
        task = self.service.get_next_task(self.account.account_id, "GOLD_")
        payload = {
            "chunk_index": task["chunk_index"],
            "range_start": task["range_start"],
            "range_end": task["range_end"],
            "bars": self._bars(start, end),
        }

        self.service.accept_chunk(
            self.account.account_id, dataset["dataset_id"], payload
        )
        duplicate = self.service.accept_chunk(
            self.account.account_id, dataset["dataset_id"], payload
        )

        self.assertEqual(duplicate["result"], "duplicate")
        self.assertEqual(
            duplicate["dataset"]["received_bars"], 11
        )

    def test_task_is_scoped_to_mt5_account_and_symbol(self):
        self._create_dataset()
        second_user = UserRepository(self.repository.storage).create_user(
            "second-user", "hash", "salt"
        )
        second_account, _ = TradingAccountRepository(
            self.repository.storage
        ).create_or_rotate_default(second_user.user_id)

        self.assertIsNone(
            self.service.get_next_task(second_account.account_id, "GOLD_")
        )
        self.assertIsNone(
            self.service.get_next_task(self.account.account_id, "EURUSD")
        )

    def test_invalid_ohlc_is_counted_and_dataset_fails_when_empty(self):
        dataset, start, end = self._create_dataset()
        task = self.service.get_next_task(self.account.account_id, "GOLD_")

        result = self.service.accept_chunk(
            self.account.account_id,
            dataset["dataset_id"],
            {
                "chunk_index": task["chunk_index"],
                "range_start": task["range_start"],
                "range_end": task["range_end"],
                "bars": [{
                    "time": start,
                    "open": 4000,
                    "high": 3990,
                    "low": 4010,
                    "close": 4001,
                }],
            },
        )

        self.assertEqual(result["dataset"]["status"], DatasetStatus.FAILED)
        self.assertEqual(result["dataset"]["invalid_count"], 1)

    def test_shared_dataset_is_visible_but_not_manageable_by_other_user(self):
        dataset, _, _ = self._create_dataset()
        second_user = UserRepository(self.repository.storage).create_user(
            "catalog-user", "hash", "salt"
        )

        visible = self.repository.list_for_user(second_user.user_id)

        self.assertEqual([item["dataset_id"] for item in visible], [dataset["dataset_id"]])
        self.assertFalse(visible[0]["is_owner"])
        self.assertFalse(visible[0]["can_manage"])
        self.assertNotIn("account_id", visible[0])
        self.assertFalse(self.repository.cancel(second_user.user_id, dataset["dataset_id"]))
        self.assertFalse(self.repository.delete(second_user.user_id, dataset["dataset_id"]))
        self.assertIsNone(
            self.repository.update_visibility(
                second_user.user_id, dataset["dataset_id"], "private"
            )
        )

    def test_owner_can_make_dataset_private(self):
        dataset, _, _ = self._create_dataset()
        second_user = UserRepository(self.repository.storage).create_user(
            "private-user", "hash", "salt"
        )

        updated = self.repository.update_visibility(
            self.user.user_id, dataset["dataset_id"], "private"
        )

        self.assertEqual(updated["visibility"], "private")
        self.assertEqual(self.repository.list_for_user(second_user.user_id), [])
        self.assertIsNone(
            self.repository.get_visible(second_user.user_id, dataset["dataset_id"])
        )
        self.assertIsNotNone(
            self.repository.get_visible(self.user.user_id, dataset["dataset_id"])
        )


if __name__ == "__main__":
    unittest.main()
