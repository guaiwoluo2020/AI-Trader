import unittest

from market.services.market_structure_engine_v2 import (
    ENGINE_VERSION,
    _atr_series,
    _anchor_confirmed_segments,
    _event_stream,
    _range,
    _segments,
    analyze,
    analyze_incremental,
    restore_snapshot,
)


def bars(closes):
    return [{"timestamp": i, "open": p, "high": p + 1, "low": p - 1, "close": p} for i, p in enumerate(closes)]


class MarketStructureEngineTests(unittest.TestCase):
    def test_breakout_is_close_confirmed(self):
        rows = bars([100 + i * .2 for i in range(40)] + [108, 109, 110, 111, 112, 113])
        result = analyze("TEST", "M5", rows, {"break_confirm_bars": 2})
        self.assertIn(result["current_state"], {"bullish", "bearish", "range", "undetermined"})
        self.assertTrue(all(e.get("confirmation") != "wick_only" for e in result["events"] if e["type"] in {"bos", "choch"}))

    def test_liquidity_sweep_does_not_create_structure_segment(self):
        rows = bars([100, 101, 100, 101, 100, 101, 100, 101, 100, 101] * 4)
        result = analyze("TEST", "M5", rows)
        self.assertTrue(all(e["type"] != "liquidity_sweep" or e.get("confirmation") == "wick_only" for e in result["events"]))
        self.assertLessEqual(len(result["segments"]), 5)

    def test_confirmed_down_segment_starts_at_reversal_high_not_break_candle(self):
        rows = bars([100, 102, 104, 107, 110, 109, 107, 104, 101, 98])
        event = {
            "index": 8,
            "type": "choch",
            "direction": "down",
            "confirmation": "close_confirmed",
        }
        segments = [
            {"start_index": 0, "end_index": 8, "type": "up", "event": None},
            {"start_index": 8, "end_index": 9, "type": "down", "event": event},
        ]

        anchored = _anchor_confirmed_segments(rows, segments)

        self.assertEqual(anchored[0]["end_index"], 3)
        self.assertEqual(anchored[1]["start_index"], 4)
        self.assertEqual(event["segment_anchor_index"], 4)
        self.assertEqual(event["confirmation_index"], 8)

    def test_old_snapshot_is_not_reused_after_engine_upgrade(self):
        rows = bars([100 + i * 0.1 for i in range(40)])
        restore_snapshot({
            "symbol": "OLD",
            "period": "M5",
            "engine_version": "older-engine",
            "last_bar_time": rows[-1]["timestamp"],
            "segments": [{"type": "down"}],
        })

        result = analyze_incremental("OLD", "M5", rows)

        self.assertEqual(result["engine_version"], ENGINE_VERSION)
        self.assertNotEqual(result["segments"], [{"type": "down"}])

    def test_locked_segments_are_rebuilt_after_config_change(self):
        rows = bars([100 + i * 0.1 for i in range(40)])
        restore_snapshot({
            "symbol": "CONFIG_CHANGE",
            "period": "M5",
            "engine_version": ENGINE_VERSION,
            "config_signature": "old-config",
            "last_bar_time": rows[-1]["timestamp"],
            "segment_history": [{
                "type": "down",
                "locked": True,
                "start_time": rows[0]["timestamp"],
                "end_time": rows[20]["timestamp"],
            }],
            "segments": [{
                "type": "down",
                "locked": True,
                "start_time": rows[0]["timestamp"],
                "end_time": rows[20]["timestamp"],
            }],
        })

        result = analyze_incremental(
            "CONFIG_CHANGE", "M5", rows, {"trend_min_direction_ratio": 0.70}
        )

        self.assertEqual(result["engine_version"], ENGINE_VERSION)
        self.assertFalse(result.get("locked_segment_count"))

    def test_internal_break_does_not_change_major_state(self):
        rows = bars([100, 102, 105, 103, 101, 104, 108, 106, 104, 107, 110, 109, 108, 106, 105])
        atrs = _atr_series(rows)
        small = [
            {"index": 2, "confirmed_at": 3, "kind": "high", "price": 106, "level": "small"},
            {"index": 4, "confirmed_at": 5, "kind": "low", "price": 100, "level": "small"},
            {"index": 10, "confirmed_at": 11, "kind": "high", "price": 111, "level": "small"},
            {"index": 9, "confirmed_at": 10, "kind": "low", "price": 107, "level": "small"},
        ]
        major = [
            {"index": 2, "confirmed_at": 4, "kind": "high", "price": 106, "level": "medium"},
            {"index": 4, "confirmed_at": 6, "kind": "low", "price": 99, "level": "medium"},
        ]
        config = {"break_confirm_bars": 1, "break_buffer_atr": 0, "retest_bars": 0, "displacement_atr": 99}
        _, _, internal_state = _event_stream(rows, small, atrs, config, "internal")
        _, _, major_state = _event_stream(rows, major, atrs, config, "major")
        self.assertEqual(internal_state, "down")
        self.assertEqual(major_state, "up")

    def test_major_reversal_requires_real_retest_and_hold(self):
        rows = bars([100, 99, 98, 97, 96, 95, 94, 95, 96, 101, 100, 102])
        pivots = [
            {"index": 0, "confirmed_at": 1, "kind": "high", "price": 100, "level": "medium"},
            {"index": 2, "confirmed_at": 3, "kind": "low", "price": 97, "level": "medium"},
            {"index": 4, "confirmed_at": 5, "kind": "high", "price": 99, "level": "medium"},
            {"index": 6, "confirmed_at": 7, "kind": "low", "price": 93, "level": "medium"},
        ]
        events, _, state = _event_stream(rows, pivots, _atr_series(rows), {
            "break_confirm_bars": 1, "break_buffer_atr": 0,
            "retest_bars": 2, "displacement_atr": 99,
        }, "major")
        bullish = [event for event in events if event["type"] == "choch" and event["direction"] == "up"]
        self.assertEqual(state, "up")
        self.assertEqual(bullish[-1]["confirmation"], "retest_confirmed")
        self.assertGreater(bullish[-1]["confirmed_at"], bullish[-1]["break_confirmed_at"])

    def test_small_pivots_never_fallback_to_main_structure(self):
        rows = bars([100, 104, 99, 105, 98, 106, 97, 107, 96, 108] * 3)
        result = analyze("NO_SMALL_FALLBACK", "M5", rows, {
            "pivot_legs": 2, "medium_pivot_legs": 50, "large_pivot_legs": 60,
        })
        self.assertEqual(result["major_state"], "undetermined")
        self.assertNotIn(result["current_state"], {"up", "down"})

    def test_mixed_major_evidence_downgrades_long_trend_segment(self):
        rows = bars([100 + (i % 5) * 0.2 for i in range(80)])
        rows[-1]["close"] = 101
        major = [
            {"index": 15, "kind": "high", "price": 105, "label": "HH"},
            {"index": 22, "kind": "low", "price": 99, "label": "HL"},
            {"index": 32, "kind": "high", "price": 104, "label": "LH"},
            {"index": 40, "kind": "low", "price": 98, "label": "LL"},
            {"index": 52, "kind": "high", "price": 103, "label": "LH"},
            {"index": 60, "kind": "low", "price": 97, "label": "LL"},
        ]
        events = [{"type": "bos", "direction": "down", "confirmed_at": 10,
                   "confirmation": "close_confirmed", "scope": "major"}]
        result = _segments(rows, events, None, [], major, 1.0, {
            "trend_min_direction_ratio": 0.62,
            "trend_min_efficiency": 0.30,
            "range_min_bars": 24,
            "trend_max_anchor_bars": 48,
        })
        self.assertEqual(result[-1]["type"], "sideways")
        self.assertIn("LH/LL", result[-1]["reason"])
        self.assertIn("方向效率", result[-1]["reason"])

    def test_dominant_lower_structure_with_large_displacement_is_downtrend(self):
        rows = bars([110 - i * 0.35 for i in range(80)])
        major = [
            {"index": 15, "kind": "high", "price": 106, "label": "HH"},
            {"index": 22, "kind": "low", "price": 101, "label": "HL"},
            {"index": 32, "kind": "high", "price": 102, "label": "LH"},
            {"index": 40, "kind": "low", "price": 96, "label": "LL"},
            {"index": 52, "kind": "high", "price": 98, "label": "LH"},
            {"index": 60, "kind": "low", "price": 90, "label": "LL"},
        ]
        events = [{"type": "bos", "direction": "down", "confirmed_at": 10,
                   "confirmation": "close_confirmed", "scope": "major"}]
        result = _segments(rows, events, None, [], major, 1.0, {
            "trend_min_direction_ratio": 0.62,
            "trend_min_efficiency": 0.30,
            "trend_min_net_change_atr": 1.5,
            "trend_min_slope_consistency": 0.60,
            "trend_max_retrace_atr": 4.0,
            "range_min_bars": 24,
            "trend_max_anchor_bars": 48,
        })
        self.assertEqual(result[-1]["type"], "down")
        self.assertIn("主结构偏向下跌", result[-1]["reason"])

    def test_local_pattern_does_not_override_swing_bias(self):
        rows = bars([110 - i * 0.25 for i in range(100)])
        result = analyze("PARALLEL", "M5", rows)
        self.assertIn("structure_hierarchy", result)
        self.assertIn("local_patterns", result)
        self.assertEqual(result["current_state"], result["major_state"])

    def test_triangle_is_a_confirmed_range_lifecycle(self):
        closes = [100 + ((i % 6) - 3) * 0.25 * (1 - i / 100) for i in range(72)]
        rows = bars(closes)
        pivots = []
        for index, price in ((12, 104), (24, 103.5), (36, 103), (48, 102.5)):
            pivots.append({"index": index, "kind": "high", "price": price})
        for index, price in ((15, 96), (27, 96.5), (39, 97), (51, 97.5)):
            pivots.append({"index": index, "kind": "low", "price": price})
        result = _range(rows, sorted(pivots, key=lambda item: item["index"]), 1.0, {
            "range_min_bars": 24, "range_touch_tolerance": 0.003, "range_touch_atr": 0.5,
            "range_min_touches": 2, "range_min_inside_ratio": 0.55, "range_max_atr": 10,
            "break_confirm_bars": 2, "break_buffer_atr": 0.1,
        })
        self.assertIsNotNone(result)
        self.assertEqual(result["pattern"], "triangle")
        self.assertTrue(result["active"])

    def test_range_failed_breakout_returns_to_confirmed_range(self):
        rows = bars([100 + ((i % 6) - 3) * 0.2 for i in range(72)])
        rows[-2].update({"open": 105, "high": 107, "low": 104, "close": 106})
        rows[-1].update({"open": 100, "high": 101, "low": 99, "close": 100})
        pivots = []
        for index, price in ((12, 104), (24, 103.5), (36, 103), (48, 102.5)):
            pivots.append({"index": index, "kind": "high", "price": price})
        for index, price in ((15, 96), (27, 96.5), (39, 97), (51, 97.5)):
            pivots.append({"index": index, "kind": "low", "price": price})
        result = _range(rows, sorted(pivots, key=lambda item: item["index"]), 1.0, {
            "range_min_bars": 24, "range_touch_tolerance": 0.003, "range_touch_atr": 0.5,
            "range_min_touches": 2, "range_min_inside_ratio": 0.55, "range_max_atr": 10,
            "break_confirm_bars": 2, "break_buffer_atr": 0.1,
        })
        self.assertEqual(result["status"], "failed_breakout")
        self.assertTrue(result["active"])

    def test_batch_and_incremental_results_have_same_confirmed_events(self):
        rows = bars([100, 102, 104, 101, 98, 100, 105, 108, 104, 99, 96, 100, 106, 110] * 8)
        batch = analyze("EQUIV", "M5", rows)
        incremental = analyze_incremental("EQUIV", "M5", rows)
        event_key = lambda event: (event["type"], event["direction"], event["confirmed_at"], event["scope"])
        self.assertEqual([event_key(e) for e in batch["major_events"]],
                         [event_key(e) for e in incremental["major_events"]])

    def test_confirmed_event_prefix_does_not_repaint_when_bars_are_appended(self):
        rows = bars([100, 103, 106, 102, 98, 101, 107, 111, 106, 100, 95, 99, 105, 109] * 10)
        prefix = analyze("PREFIX", "M5", rows[:112])
        full = analyze("PREFIX", "M5", rows)
        cutoff = 112 - 25
        key = lambda event: (event["type"], event["direction"], event["confirmed_at"], event["level"])
        old = [key(e) for e in prefix["major_events"] if e["confirmed_at"] <= cutoff]
        new = [key(e) for e in full["major_events"] if e["confirmed_at"] <= cutoff]
        self.assertEqual(old, new)

    def test_locked_boundaries_survive_window_length_change(self):
        rows = bars([100, 103, 106, 102, 98, 101, 107, 111, 106, 100, 95, 99, 105, 109] * 50)
        short = analyze_incremental("WINDOW_STABLE", "M5", rows[-300:])
        locked_before = [(s.get("start_time"), s.get("end_time"), s["type"])
                         for s in short["segment_history"] if s.get("locked")]
        long = analyze_incremental("WINDOW_STABLE", "M5", rows[-600:])
        locked_after = [(s.get("start_time"), s.get("end_time"), s["type"])
                        for s in long["segment_history"] if s.get("locked")]
        self.assertEqual(locked_before, locked_after[:len(locked_before)])


if __name__ == "__main__":
    unittest.main()
