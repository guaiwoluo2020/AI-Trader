import os
import unittest
from unittest.mock import patch

import market_tick_store


class TickPersistenceSwitchTest(unittest.TestCase):
    def tearDown(self):
        # The switch is process-local; never leak an override into other tests.
        market_tick_store._enabled_override = None

    def test_defaults_to_disabled_when_environment_is_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI_TRADER_TICK_PERSISTENCE_ENABLED", None)
            self.assertFalse(market_tick_store.tick_persistence_enabled())
            self.assertFalse(market_tick_store.tick_persistence_config()["enabled"])
            self.assertFalse(market_tick_store.tick_persistence_config()["default_enabled"])

    def test_environment_can_enable_default(self):
        with patch.dict(os.environ, {"AI_TRADER_TICK_PERSISTENCE_ENABLED": "1"}):
            self.assertTrue(market_tick_store.tick_persistence_enabled())
            config = market_tick_store.tick_persistence_config()
            self.assertTrue(config["enabled"])
            self.assertTrue(config["default_enabled"])

    def test_runtime_switch_overrides_environment(self):
        with patch.dict(os.environ, {"AI_TRADER_TICK_PERSISTENCE_ENABLED": "1"}):
            self.assertFalse(market_tick_store.set_tick_persistence_enabled(False))
            self.assertFalse(market_tick_store.tick_persistence_enabled())
            self.assertTrue(market_tick_store.set_tick_persistence_enabled(True))
            self.assertTrue(market_tick_store.tick_persistence_enabled())


if __name__ == "__main__":
    unittest.main()
