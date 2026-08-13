"""Invitation-only registration tests."""

import os
import tempfile
import time
import unittest

from auth import AuthManager, reset_auth_manager
from invitations import InvitationError, InvitationService
from sqlite_storage import reset_storage


class InvitationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_DB_FILE"] = os.path.join(
            self.temp_dir.name, "ai-trader.db"
        )
        reset_storage()
        reset_auth_manager()
        self.admin = AuthManager().user_repo.get_by_username("admin")
        self.service = InvitationService()

    def tearDown(self):
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    def test_create_claim_exhaust_and_release(self):
        created = self.service.create(self.admin.user_id, "伙伴", 1, 7)
        self.assertIn("code", created)
        self.assertEqual(created["used_count"], 0)

        invitation_id = self.service.claim(created["code"])
        with self.assertRaisesRegex(InvitationError, "上限"):
            self.service.assert_available(created["code"])

        self.service.release(invitation_id)
        self.assertTrue(self.service.assert_available(created["code"])["available"])

    def test_disabled_and_expired_codes_are_rejected(self):
        created = self.service.create(self.admin.user_id, max_uses=2)
        self.service.set_active(created["invitation_id"], False)
        with self.assertRaisesRegex(InvitationError, "停用"):
            self.service.assert_available(created["code"])

        self.service.storage.execute(
            "UPDATE invitation_codes SET active = 1, expires_at = ? WHERE invitation_id = ?",
            (int(time.time()) - 1, created["invitation_id"]),
        )
        with self.assertRaisesRegex(InvitationError, "过期"):
            self.service.assert_available(created["code"])

    def test_plaintext_code_is_not_returned_by_list(self):
        created = self.service.create(self.admin.user_id)
        listed = self.service.list_all()[0]
        self.assertNotIn("code", listed)
        self.assertEqual(listed["code_prefix"], created["code"][:4])


if __name__ == "__main__":
    unittest.main()
