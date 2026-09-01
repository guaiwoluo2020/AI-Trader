#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EA 账户绑定测试。"""

import os
import tempfile
import unittest

from fastapi import HTTPException

from auth import AuthManager, reset_auth_manager
from ea_auth import require_ea_auth
from mysql_repositories import TradingAccountRepository, reset_storage


class _RequestState:
    pass


class _Request:
    def __init__(self):
        self.state = _RequestState()


class EAAuthTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_AUTH_FILE"] = os.path.join(
            self.temp_dir.name,
            "auth-users.json",
        )
        os.environ["AI_TRADER_DB_FILE"] = os.path.join(
            self.temp_dir.name,
            "ai-trader.db",
        )
        reset_storage()
        reset_auth_manager()
        self.auth_manager = AuthManager()
        self.user = self.auth_manager.authenticate("admin", "admin123456")

    def tearDown(self):
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_AUTH_FILE", None)
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    def test_binding_authenticates_and_sets_request_identity(self):
        account, token = TradingAccountRepository().create_or_rotate_default(
            self.user.user_id
        )
        request = _Request()

        identity = require_ea_auth(
            request=request,
            user_id=self.user.user_id,
            ea_token=token,
            x_ea_version="2.0.7",
        )

        self.assertEqual(identity.user_id, self.user.user_id)
        self.assertEqual(identity.account_id, account.account_id)
        self.assertEqual(request.state.ea_identity, identity)

    def test_raw_user_id_without_token_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            require_ea_auth(
                request=_Request(),
                user_id=self.user.user_id,
                ea_token=None,
                x_ea_version="2.0.7",
            )

        self.assertEqual(context.exception.status_code, 401)

    def test_rotating_token_revokes_previous_token(self):
        repo = TradingAccountRepository()
        _, old_token = repo.create_or_rotate_default(self.user.user_id)
        _, new_token = repo.create_or_rotate_default(self.user.user_id)

        self.assertIsNone(repo.authenticate(self.user.user_id, old_token))
        self.assertIsNotNone(repo.authenticate(self.user.user_id, new_token))


if __name__ == "__main__":
    unittest.main()
