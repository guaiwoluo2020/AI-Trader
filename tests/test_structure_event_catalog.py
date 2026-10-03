import unittest

from market.services.structure_events import collect_structure_events, latest_event_for


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


if __name__ == "__main__":
    unittest.main()
