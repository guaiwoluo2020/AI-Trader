import tempfile
import unittest
from pathlib import Path

from sqlite_storage import (
    EAActivationRepository,
    SQLiteStorage,
    TradingAccountRepository,
    UserRepository,
)


class EAActivationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = SQLiteStorage(
            str(Path(self.temp_dir.name) / "activation-test.db")
        )
        self.user = UserRepository(self.storage).create_user(
            "activation-user",
            "password-hash",
            "salt",
        )
        self.accounts = TradingAccountRepository(self.storage)
        self.activations = EAActivationRepository(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_download_code_does_not_rotate_existing_credentials(self):
        account, old_token = self.accounts.create_or_rotate_default(self.user.user_id)
        self.assertFalse(self.activations.has_downloaded(self.user.user_id))

        code, _ = self.activations.create(self.user.user_id)

        self.assertEqual(12, len(code))
        self.assertTrue(self.activations.has_downloaded(self.user.user_id))
        self.assertEqual(
            account.account_id,
            self.accounts.authenticate(self.user.user_id, old_token).account_id,
        )

    def test_activation_is_one_time_and_rotates_credentials(self):
        _, old_token = self.accounts.create_or_rotate_default(self.user.user_id)
        code, _ = self.activations.create(self.user.user_id)

        activated = self.activations.consume(
            code,
            mt5_login="900001",
            mt5_server="Broker-Demo",
            ea_version="2.03",
            program_name=f"mt5TerminalEA_{code}",
        )

        self.assertIsNotNone(activated)
        account, new_token = activated
        self.assertEqual("900001", account.mt5_login)
        self.assertEqual("Broker-Demo", account.mt5_server)
        self.assertEqual("2.03", account.ea_version)
        self.assertIsNone(self.accounts.authenticate(self.user.user_id, old_token))
        self.assertIsNotNone(
            self.accounts.authenticate(self.user.user_id, new_token)
        )
        self.assertIsNone(self.activations.consume(code))

    def test_new_download_invalidates_previous_unused_code(self):
        first_code, _ = self.activations.create(self.user.user_id)
        second_code, _ = self.activations.create(self.user.user_id)

        self.assertIsNone(self.activations.consume(first_code))
        self.assertIsNotNone(self.activations.consume(second_code))


if __name__ == "__main__":
    unittest.main()
