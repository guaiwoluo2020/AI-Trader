import unittest

from market.services.market_structure_evaluation import evaluate_segments


class MarketStructureEvaluationTests(unittest.TestCase):
    def test_metrics_report_direction_boundary_and_delay(self):
        predicted = [{"type": "down", "start_time": 0, "end_time": 600,
                      "confirmation_time": 120}]
        labels = [{"expected_type": "down", "start": 0, "end": 660}]
        metrics = evaluate_segments(predicted, labels, 60)
        self.assertEqual(metrics["direction_accuracy"], 1)
        self.assertEqual(metrics["mean_boundary_error_bars"], 0.5)
        self.assertEqual(metrics["mean_confirmation_delay_bars"], 2)


if __name__ == "__main__":
    unittest.main()
