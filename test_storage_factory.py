import unittest
from unittest.mock import patch

from infrastructure import storage_factory


class FakeStorage:
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.closed = False

    def fetchone(self, _sql, _params=()):
        if not self.healthy:
            raise RuntimeError("database unavailable")
        return {"ok": 1}

    def close(self):
        self.closed = True


class StorageFactoryTest(unittest.TestCase):
    def tearDown(self):
        storage_factory._storage = None

    def test_factory_reuses_single_storage_instance(self):
        fake = FakeStorage()
        with patch.object(storage_factory, "MySQLStorage", return_value=fake) as factory:
            self.assertIs(storage_factory.get_mysql_storage(), fake)
            self.assertIs(storage_factory.get_mysql_storage(), fake)
            factory.assert_called_once_with()

    def test_reset_closes_existing_storage(self):
        fake = FakeStorage()
        storage_factory._storage = fake

        storage_factory.reset_storage()

        self.assertTrue(fake.closed)
        self.assertIsNone(storage_factory._storage)

    def test_healthcheck_reports_database_failure(self):
        storage_factory._storage = FakeStorage(healthy=False)
        self.assertFalse(storage_factory.healthcheck_storage())


if __name__ == "__main__":
    unittest.main()
