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

    def test_ea_supports_historical_dataset_tasks(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertIn('#property version   "2.04"', source)
        self.assertIn("CheckHistoricalDataTask();", source)
        self.assertIn("CopyRates(\n      _Symbol, PERIOD_M1", source)
        self.assertIn("/ea/backtest-data/tasks/next?symbol=", source)
        self.assertIn("/chunks", source)

    def test_risk_threshold_uses_larger_account_value(self):
        source = EA_SOURCE.read_text(encoding="utf-8")

        self.assertIn(
            "MathMax(g_accountBalance, g_accountEquity)",
            source,
        )
        self.assertNotIn(
            "g_accountBalance > 0 ? g_accountBalance : g_accountEquity",
            source,
        )
        self.assertIn("if(riskBase <= 0)", source)
        self.assertIn(
            "double riskThreshold = riskBase * (g_riskLimitPercent / 100.0)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
