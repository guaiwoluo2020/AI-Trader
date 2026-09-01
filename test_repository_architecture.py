import unittest

from repositories.container import RepositoryContainer


class RepositoryArchitectureTest(unittest.TestCase):
    def test_container_exposes_all_domain_repositories(self):
        expected = (
            "accounts", "deployments", "strategies", "trade_config",
            "ai_sources", "ai_config", "ai_access", "ai_suggestions",
            "ai_runtime", "trade_execution", "position_events",
            "position_policies", "platform_mappings",
        )
        class FakeStorage:
            pass

        container = RepositoryContainer(FakeStorage())
        for name in expected:
            self.assertTrue(hasattr(container, name), name)
            self.assertIs(getattr(container, name).storage, container.storage, name)


if __name__ == "__main__":
    unittest.main()
