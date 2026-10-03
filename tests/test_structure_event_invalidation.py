import unittest

from market.services.structure_events import event_conflict_reason


def _plan(**kwargs):
    item = {
        "direction": "buy",
        "setup_type": "trend_continuation",
        "plan_type": "trend_continuation",
        "event_layer": "swing",
        "event_type": "bos",
        "source_event": {"confirmed_at": 10},
    }
    item.update(kwargs)
    return item


class EventConflictTests(unittest.TestCase):
    def test_choch_retires_same_layer_bos_plan(self):
        reason = event_conflict_reason(_plan(), [
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 10},
            {"layer": "swing", "event_type": "choch", "direction": "down", "confirmed_at": 20},
        ])
        self.assertIn("CHOCH", reason)

    def test_bos_retires_same_layer_choch_plan(self):
        reason = event_conflict_reason(_plan(
            setup_type="structure_reversal", plan_type="structure_reversal",
            direction="sell", event_type="choch",
            source_event={"confirmed_at": 10},
        ), [
            {"layer": "swing", "event_type": "choch", "direction": "down", "confirmed_at": 10},
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 20},
        ])
        self.assertIn("BOS", reason)

    def test_same_bar_choch_beats_bos(self):
        reason = event_conflict_reason(_plan(), [
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 20},
            {"layer": "swing", "event_type": "choch", "direction": "up", "confirmed_at": 20},
        ])
        self.assertIn("CHOCH", reason)

    def test_choch_keeps_same_direction_reversal(self):
        reason = event_conflict_reason(_plan(
            setup_type="structure_reversal", plan_type="structure_reversal",
            direction="sell", event_type="choch",
            source_event={"confirmed_at": 20},
        ), [
            {"layer": "swing", "event_type": "choch", "direction": "down", "confirmed_at": 20},
        ])
        self.assertEqual(reason, "")

    def test_internal_choch_does_not_kill_swing_bos(self):
        reason = event_conflict_reason(_plan(), [
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 20},
            {"layer": "internal", "event_type": "choch", "direction": "down", "confirmed_at": 30},
        ])
        self.assertEqual(reason, "")

    def test_reclaim_retires_range_breakout(self):
        reason = event_conflict_reason(_plan(
            setup_type="swing_range_breakout", plan_type="swing_range_breakout",
        ), [
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 10},
            {"layer": "swing", "event_type": "reclaim", "direction": "down", "confirmed_at": 20},
        ])
        self.assertIn("收回", reason)

    def test_external_choch_retires_opposite_swing_continuation(self):
        reason = event_conflict_reason(_plan(), [
            {"layer": "swing", "event_type": "bos", "direction": "up", "confirmed_at": 10},
            {"layer": "external", "event_type": "choch", "direction": "down", "confirmed_at": 20},
        ])
        self.assertIn("EXTERNAL", reason)
