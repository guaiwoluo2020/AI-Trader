import unittest

from repositories.instrument_specs import normalize_volume


class InstrumentSpecTests(unittest.TestCase):
    def test_open_volume_uses_step_without_exceeding_risk(self):
        spec = {"min_volume": 0.01, "volume_step": 0.01, "max_volume": 10, "volume_digits": 2}
        self.assertEqual(0.01, normalize_volume(0.0147, spec, opening=True))

    def test_partial_volume_closes_remaining_when_level_is_dust(self):
        spec = {"min_volume": 0.01, "volume_step": 0.01, "max_volume": 10, "volume_digits": 2}
        self.assertEqual(0.02, normalize_volume(0.0063, spec, opening=False, current_volume=0.02))

    def test_fractional_symbol_is_supported(self):
        spec = {"min_volume": 0.001, "volume_step": 0.001, "max_volume": 100, "volume_digits": 3}
        self.assertEqual(0.014, normalize_volume(0.0147, spec, opening=True))


if __name__ == "__main__":
    unittest.main()
