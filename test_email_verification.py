#!/usr/bin/env python3
"""Registration email verification tests."""

import os
import tempfile
import unittest
from unittest.mock import patch

from auth import AuthManager, reset_auth_manager
from email_verification import (
    EmailDomainPolicy,
    EmailVerificationError,
    EmailVerificationService,
    SystemEmailConfigRepository,
)
from sqlite_storage import get_storage, reset_storage


class EmailVerificationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_DB_FILE"] = os.path.join(
            self.temp_dir.name, "ai-trader.db"
        )
        os.environ["AI_TRADER_AUTH_FILE"] = os.path.join(
            self.temp_dir.name, "auth-users.json"
        )
        reset_storage()
        reset_auth_manager()
        self.auth = AuthManager()
        self.config = SystemEmailConfigRepository()
        self.config.save({
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "use_ssl": True,
            "sender_email": "service@example.com",
            "sender_name": "AI Trader",
            "password": "NeverStorePlaintext123",
            "enabled": True,
        }, updated_by=1)

    def tearDown(self):
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_DB_FILE", None)
        os.environ.pop("AI_TRADER_AUTH_FILE", None)
        self.temp_dir.cleanup()

    def test_disposable_email_domain_is_rejected(self):
        policy = EmailDomainPolicy()
        with self.assertRaisesRegex(EmailVerificationError, "临时邮箱"):
            policy.normalize("trader@mailinator.com")

    def test_smtp_password_is_encrypted_at_rest(self):
        row = get_storage().fetchone(
            "SELECT encrypted_password FROM system_email_config WHERE id = 1"
        )
        self.assertNotIn("NeverStorePlaintext123", row["encrypted_password"])
        self.assertEqual(
            self.config.get(include_password=True)["password"],
            "NeverStorePlaintext123",
        )
        self.assertIsNone(self.config.get()["password"])

    def test_code_is_hashed_rate_limited_and_required_for_registration(self):
        service = EmailVerificationService()
        captured = {}

        def capture_message(config, target, code):
            captured.update({"target": target, "code": code})

        with patch.object(service, "_send_message", side_effect=capture_message):
            result = service.send_code("Trader@Example.com")

        self.assertEqual(result["email"], "trader@example.com")
        row = service.repository.get("trader@example.com")
        self.assertNotEqual(row["code_hash"], captured["code"])
        with self.assertRaisesRegex(EmailVerificationError, "发送过于频繁"):
            service.send_code("trader@example.com")
        with self.assertRaisesRegex(EmailVerificationError, "不正确"):
            service.assert_valid_code("trader@example.com", "000000")

        email = service.assert_valid_code(
            "trader@example.com", captured["code"]
        )
        user = self.auth.register("verified", "TradePass2026", email)
        service.consume(email)

        self.assertEqual(user.email, "trader@example.com")
        self.assertIsNone(service.repository.get(email))


if __name__ == "__main__":
    unittest.main()
