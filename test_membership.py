#!/usr/bin/env python3
"""会员等级、资源权益与实盘授权测试。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from membership import MembershipError, MembershipService
from server import TradingServer
from sqlite_storage import SQLiteStorage, UserRepository
from user_quotas import UserQuotaService


class MembershipServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(str(Path(self.temp_dir.name) / "membership.db"))
        self.storage.initialize()
        self.users = UserRepository(self.storage)
        self.user = self.users.create_user("member", "hash", "salt")
        self.admin = self.users.create_user("admin-member", "hash", "salt", role="admin")
        self.service = MembershipService(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_new_user_defaults_to_silver_without_live_permission(self):
        access = self.service.get_access(self.user.user_id)

        self.assertEqual("silver", access["membership_level"])
        self.assertFalse(access["can_live_trade"])
        self.assertEqual(2, access["limits"]["paper_accounts"])
        self.assertEqual(0, access["limits"]["live_accounts"])

    def test_admin_can_enable_gold_live_trading(self):
        access = self.service.update_user(
            self.user.user_id, "gold", True, self.admin.user_id
        )

        self.assertTrue(access["can_live_trade"])
        self.assertEqual(1, access["limits"]["live_accounts"])
        self.service.assert_live_trading(self.user.user_id)

    def test_silver_cannot_receive_live_permission(self):
        with self.assertRaisesRegex(MembershipError, "黄金或钻石"):
            self.service.update_user(
                self.user.user_id, "silver", True, self.admin.user_id
            )

    def test_trading_server_observes_membership_changes_immediately(self):
        server = SimpleNamespace(
            user_id=self.user.user_id,
            account_id=10,
            memberships=self.service,
        )
        self.assertFalse(TradingServer._live_entries_allowed(server))

        self.service.update_user(
            self.user.user_id, "gold", True, self.admin.user_id
        )

        self.assertTrue(TradingServer._live_entries_allowed(server))

    def test_membership_controls_default_resource_quotas(self):
        quotas = UserQuotaService(self.storage)
        self.service.update_user(
            self.user.user_id, "diamond", False, self.admin.user_id
        )

        summary = quotas.get_summary(self.user.user_id, "user")

        self.assertEqual(100, summary["limits"]["datasets"])
        self.assertEqual(100, summary["limits"]["strategies"])
        self.assertEqual(300, summary["limits"]["signal_sources"])

    def test_revoking_live_permission_removes_queued_entry_entities(self):
        self.service.update_user(
            self.user.user_id, "gold", True, self.admin.user_id
        )
        self.storage.execute(
            """
            INSERT INTO runtime_entities(
                user_id, account_id, entity_type, entity_id, payload_json,
                created_at, updated_at
            ) VALUES(?, 10, 'trading_instruction', 'queued', '{}', 1, 1)
            """,
            (self.user.user_id,),
        )

        access = self.service.update_user(
            self.user.user_id, "silver", False, self.admin.user_id
        )

        self.assertFalse(access["can_live_trade"])
        remaining = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM runtime_entities WHERE user_id = ?",
            (self.user.user_id,),
        )
        self.assertEqual(0, int(remaining["total"]))


if __name__ == "__main__":
    unittest.main()
