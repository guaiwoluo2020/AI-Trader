import unittest
from datetime import datetime

from market.risk_clock import BEIJING, risk_day_key, risk_day_start_timestamp


class RiskClockTest(unittest.TestCase):
    def test_business_day_changes_at_beijing_seven(self):
        before = datetime(2026, 8, 30, 6, 59, 59, tzinfo=BEIJING).timestamp()
        after = datetime(2026, 8, 30, 7, 0, 0, tzinfo=BEIJING).timestamp()

        self.assertEqual(risk_day_key(before), "2026-08-29")
        self.assertEqual(risk_day_key(after), "2026-08-30")
        self.assertEqual(
            risk_day_start_timestamp(before),
            int(datetime(2026, 8, 29, 7, 0, tzinfo=BEIJING).timestamp()),
        )
        self.assertEqual(risk_day_start_timestamp(after), int(after))


if __name__ == "__main__":
    unittest.main()
