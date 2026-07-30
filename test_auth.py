#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证功能测试
"""

import os
import tempfile
import unittest

from fastapi import HTTPException

from auth import (
    AuthManager,
    UsernameAlreadyExistsError,
    require_auth,
    reset_auth_manager,
)
from main import create_app
from sqlite_storage import reset_storage


class AuthRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_file = os.path.join(self.temp_dir.name, "auth-users.json")
        self.db_file = os.path.join(self.temp_dir.name, "ai-trader.db")

        os.environ["AI_TRADER_AUTH_FILE"] = self.auth_file
        os.environ["AI_TRADER_DB_FILE"] = self.db_file
        os.environ["AI_TRADER_DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["AI_TRADER_DEFAULT_ADMIN_PASSWORD"] = "admin123456"
        reset_storage()
        reset_auth_manager()
        self.auth_manager = AuthManager(self.auth_file)

    def tearDown(self):
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_AUTH_FILE", None)
        os.environ.pop("AI_TRADER_DB_FILE", None)
        os.environ.pop("AI_TRADER_DEFAULT_ADMIN_USERNAME", None)
        os.environ.pop("AI_TRADER_DEFAULT_ADMIN_PASSWORD", None)
        self.temp_dir.cleanup()

    def test_health_is_public(self):
        app = create_app()
        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/auth/login", paths)
        self.assertIn("/auth/register", paths)
        self.assertIn("/auth/me", paths)
        self.assertIn("/auth/change-password", paths)

    def test_login_and_fetch_current_user(self):
        user = self.auth_manager.authenticate("admin", "admin123456")
        self.assertIsNotNone(user)

        token = self.auth_manager.create_token(user)
        verified = self.auth_manager.verify_token(token)
        self.assertGreater(verified.user_id, 0)
        self.assertEqual(verified.username, "admin")
        self.assertEqual(verified.role, "admin")

    def test_register_creates_login_ready_user(self):
        user = self.auth_manager.register(" New_Trader ", "TradePass2026")

        self.assertEqual("new_trader", user.username)
        self.assertEqual("user", user.role)
        self.assertIsNotNone(
            self.auth_manager.authenticate("NEW_TRADER", "TradePass2026")
        )

    def test_change_password_revokes_old_token_and_preserves_role(self):
        user = self.auth_manager.register("new_trader", "TradePass2026")
        old_token = self.auth_manager.create_token(user)

        updated = self.auth_manager.change_password(
            user.user_id,
            "TradePass2026",
            "NewTradePass2027",
        )

        self.assertEqual(updated.role, "user")
        self.assertIsNone(
            self.auth_manager.authenticate("new_trader", "TradePass2026")
        )
        self.assertIsNotNone(
            self.auth_manager.authenticate("new_trader", "NewTradePass2027")
        )
        with self.assertRaises(HTTPException) as context:
            self.auth_manager.verify_token(old_token)
        self.assertEqual(context.exception.status_code, 401)

    def test_change_password_rejects_incorrect_current_password(self):
        user = self.auth_manager.register("new_trader", "TradePass2026")

        with self.assertRaisesRegex(ValueError, "当前密码不正确"):
            self.auth_manager.change_password(
                user.user_id,
                "WrongPass2026",
                "NewTradePass2027",
            )

    def test_register_rejects_duplicate_username(self):
        self.auth_manager.register("new_trader", "TradePass2026")

        with self.assertRaises(UsernameAlreadyExistsError):
            self.auth_manager.register("NEW_TRADER", "AnotherPass2026")

    def test_register_rejects_weak_password(self):
        with self.assertRaisesRegex(ValueError, "同时包含字母和数字"):
            self.auth_manager.register("new_trader", "passwordonly")

    def test_trader_routes_require_auth(self):
        with self.assertRaises(HTTPException) as context:
            require_auth(None)

        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
