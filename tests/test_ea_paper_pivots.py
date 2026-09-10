import unittest

from routes_ea import _paper_pivots_for_symbol


class _Pivot:
    def __init__(self, price, direction, period="M5"):
        self.price = price
        self.direction = direction
        self.period = period

    def to_dict(self):
        return {
            "price": self.price,
            "direction": self.direction,
            "period": self.period,
        }


class _PivotStore:
    def get_all_periods(self, symbol):
        self.symbol = symbol
        return ["M1", "M5"]

    def get_pivot_objects(self, symbol, period):
        return [_Pivot(7602.97, "high", period)]


class _Server:
    pivot_store = _PivotStore()


class EAPaperPivotInputTests(unittest.TestCase):
    def test_paper_tick_receives_all_current_symbol_pivots(self):
        pivots = _paper_pivots_for_symbol(_Server(), "US500Cash#")

        self.assertEqual(2, len(pivots))
        self.assertEqual({"M1", "M5"}, {item["period"] for item in pivots})
        self.assertEqual(7602.97, pivots[1]["price"])


if __name__ == "__main__":
    unittest.main()
