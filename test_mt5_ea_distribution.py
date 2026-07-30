import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
EA_SOURCE = PROJECT_ROOT / "mt5TerminalEA.mq5"
EA_ARTIFACT = PROJECT_ROOT / "dist" / "mt5TerminalEA.ex5"


class MT5EADistributionTest(unittest.TestCase):
    def test_ea_defaults_to_public_api(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            'input string InpServerUrl = "http://182.92.119.121/api"',
            source,
        )
        self.assertNotIn(
            'input string InpServerUrl = "http://127.0.0.1',
            source,
        )

    def test_compiled_artifact_is_available(self):
        self.assertTrue(EA_ARTIFACT.is_file())
        self.assertGreater(EA_ARTIFACT.stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
