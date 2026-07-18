#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证功能测试
"""

import os
import tempfile
import unittest

from fastapi import HTTPException

from auth import AuthManager, require_auth, reset_auth_manager
from main import create_app


class AuthRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.auth_file = os.path.join(self.temp_dir.name, "auth-users.json")

        os.environ["AI_TRADER_AUTH_FILE"] = self.auth_file
        os.environ["AI_TRADER_DEFAULT_ADMIN_USERNAME"] = "admin"
        os.environ["AI_TRADER_DEFAULT_ADMIN_PASSWORD"] = "admin123456"
        reset_auth_manager()
        self.auth_manager = AuthManager(self.auth_file)

    def tearDown(self):
        reset_auth_manager()
        os.environ.pop("AI_TRADER_AUTH_FILE", None)
        os.environ.pop("AI_TRADER_DEFAULT_ADMIN_USERNAME", None)
        os.environ.pop("AI_TRADER_DEFAULT_ADMIN_PASSWORD", None)
        self.temp_dir.cleanup()

    def test_health_is_public(self):
        app = create_app()
        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/auth/login", paths)
        self.assertIn("/auth/me", paths)

    def test_login_and_fetch_current_user(self):
        user = self.auth_manager.authenticate("admin", "admin123456")
        self.assertIsNotNone(user)

        token = self.auth_manager.create_token(user)
        verified = self.auth_manager.verify_token(token)
        self.assertEqual(verified.username, "admin")

    def test_trader_routes_require_auth(self):
        with self.assertRaises(HTTPException) as context:
            require_auth(None)

        self.assertEqual(context.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
