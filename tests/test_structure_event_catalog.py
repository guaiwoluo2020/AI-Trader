import unittest

from market.services.structure_events import collect_structure_events, latest_event_for, event_is_recent


class StructureEventCatalogTests(unittest.TestCase):
    def test_keeps_same_timestamp_events_separate_by_layer(self):
        structure = {
            "internal_events": [{"type": "bos", "direction": "up", "level": 101, "confirmed_at": 10}],
            "major_events": [{"type": "bos", "direction": "up", "level": 100, "confirmed_at": 10}],
            "external_events": [{"type": "liquidity_sweep", "direction": "down", "level": 98, "confirmed_at": 10}],
        }
        events = collect_structure_events(structure, "GOLD#", "M1")
        self.assertEqual(len(events), 3)
        self.assertEqual({item["layer"] for item in events}, {"internal", "swing", "external"})
        self.assertEqual(len({item["event_id"] for item in events}), 3)
        self.assertEqual(latest_event_for(events, layer="internal", event_type="bos")["level"], 101)

    def test_event_id_stable_when_window_slides(self):
        rows_a = [
            {"timestamp": 1_700_000_000 + i * 60, "open": 1, "high": 2, "low": 0, "close": 1}
            for i in range(10)
        ]
        rows_b = [
            {"timestamp": 1_700_000_000 + i * 60, "open": 1, "high": 2, "low": 0, "close": 1}
            for i in range(1, 11)
        ]
        first = collect_structure_events(
            {"major_events": [{"type": "choch", "direction": "up", "level": 100.0,
                               "confirmed_at": 8, "swing_index": 5}]},
            "GOLD#", "M1", rows=rows_a,
        )
        second = collect_structure_events(
            {"major_events": [{"type": "choch", "direction": "up", "level": 100.0,
                               "confirmed_at": 7, "swing_index": 4}]},
            "GOLD#", "M1", rows=rows_b,
        )
        self.assertEqual(first[0]["event_id"], second[0]["event_id"])
        self.assertEqual(first[0]["confirmed_at"], 1_700_000_000 + 8 * 60)
        self.assertEqual(second[0]["confirmed_at"], 1_700_000_000 + 8 * 60)

    def test_event_is_recent_for_unix_and_index(self):
        self.assertTrue(event_is_recent(
            {"confirmed_at": 1_700_000_120}, bar_time=1_700_000_120, seconds=60, max_bars=2,
        ))
        self.assertFalse(event_is_recent(
            {"confirmed_at": 1_700_000_000}, bar_time=1_700_000_300, seconds=60, max_bars=2,
        ))
        self.assertTrue(event_is_recent({"confirmed_at": 38}, last_index=39, max_bars=2))
        self.assertFalse(event_is_recent({"confirmed_at": 10}, last_index=39, max_bars=2))


if __name__ == "__main__":
    unittest.main()
