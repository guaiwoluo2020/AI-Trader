#!/usr/bin/env python3
"""Tests for persistent tenant-scoped system events."""

import tempfile
import time
import unittest
from pathlib import Path

from sqlite_storage import SQLiteStorage, TradingAccountRepository, UserRepository
from system_event_log import SystemEventLogRepository


class SystemEventLogRepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "events.db"))
        self.storage.initialize()
        users = UserRepository(self.storage)
        self.user_a = users.create_user("event-a", "hash", "salt")
        self.user_b = users.create_user("event-b", "hash", "salt")
        accounts = TradingAccountRepository(self.storage)
        self.account_a = accounts.ensure_default(self.user_a.user_id)
        self.account_b = accounts.ensure_default(self.user_b.user_id)
        self.repository = SystemEventLogRepository(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def add(self, user_id, account_id, **values):
        return self.repository.add({
            "user_id": user_id, "account_id": account_id,
            "level": values.get("level", "info"),
            "category": values.get("category", "trading"),
            "event_type": values.get("event_type", "order_generated"),
            "event_name": values.get("event_name", "交易指令生成"),
            "message": values.get("message", "测试事件"),
            "symbol": values.get("symbol", "GOLD_"),
            "correlation_id": values.get("correlation_id", "flow-1"),
            "occurred_at": values.get("occurred_at", int(time.time())),
            "detail": {"order_id": "order-1"},
        })

    def test_events_are_persistent_and_tenant_scoped(self):
        self.add(self.user_a.user_id, self.account_a.account_id)
        self.add(self.user_b.user_id, self.account_b.account_id)

        result = self.repository.list({"user_id": self.user_a.user_id})

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["username"], "event-a")
        self.assertEqual(result["items"][0]["detail"]["order_id"], "order-1")

    def test_summary_groups_warning_error_and_trading_events(self):
        self.add(self.user_a.user_id, self.account_a.account_id, level="warning")
        self.add(
            self.user_a.user_id, self.account_a.account_id,
            level="error", category="integration",
        )

        summary = self.repository.summary({"user_id": self.user_a.user_id})

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["trading"], 1)

    def test_retention_cleanup_never_deletes_audit_events(self):
        old = int(time.time()) - 1000
        self.add(self.user_a.user_id, self.account_a.account_id, occurred_at=old)
        self.add(
            self.user_a.user_id, self.account_a.account_id,
            occurred_at=old, category="audit", event_type="config_changed",
        )

        deleted = self.repository.purge_operational(int(time.time()) - 100)

        self.assertEqual(deleted, 1)
        remaining = self.repository.list({"user_id": self.user_a.user_id})
        self.assertEqual(remaining["total"], 1)
        self.assertEqual(remaining["items"][0]["category"], "audit")

    def test_search_and_correlation_filters_are_supported(self):
        self.add(
            self.user_a.user_id, self.account_a.account_id,
            message="风控拦截同向持仓", correlation_id="decision-88",
        )
        self.assertEqual(self.repository.list({"search": "同向持仓"})["total"], 1)
        self.assertEqual(
            self.repository.list({"correlation_id": "decision-88"})["total"], 1
        )


if __name__ == "__main__":
    unittest.main()
