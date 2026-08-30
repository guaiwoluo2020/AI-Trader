"""Hierarchical, close-confirmed market-structure engine."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Dict, List, Optional, Tuple

_CACHE: Dict[str, Dict] = {}
ENGINE_VERSION = "hierarchical-structure-v8"
DEFAULT_CONFIG = {
    "pivot_legs": 3, "medium_pivot_legs": 8, "large_pivot_legs": 25,
    "min_reversal_atr": 0.5, "break_buffer_atr": 0.10,
    "break_confirm_bars": 2, "retest_bars": 2, "displacement_atr": 0.8,
    "range_touch_tolerance": 0.003, "range_touch_atr": 0.45,
    "range_min_touches": 2, "range_min_inside_ratio": 0.65,
    "range_max_atr": 8.0, "range_min_bars": 24, "min_segment_bars": 12,
    "trendline_touch_atr": 0.5, "trendline_min_touches": 2,
    "trendline_min_bars": 18,
    "trend_min_direction_ratio": 0.62,
    "trend_relaxed_direction_ratio": 0.55,
    "trend_min_efficiency": 0.30,
    "trend_min_net_change_atr": 1.5,
    "trend_min_slope_consistency": 0.60,
    "trend_max_retrace_atr": 4.0,
    "candidate_timeout_bars": 12,
    "trend_max_anchor_bars": 48,
}


def _v(row: Dict, key: str) -> float:
    return float(row.get(key) or row.get(f"{key}_price") or 0)


def _time(row: Dict):
    return row.get("timestamp_utc") or row.get("timestamp") or row.get("time")


def _atr_series(rows: List[Dict], length: int = 14) -> List[float]:
    values, true_ranges, previous = [], [], 0.0
    for row in rows:
        high, low, close = _v(row, "high"), _v(row, "low"), _v(row, "close")
        true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)) if previous else high - low)
        values.append(sum(true_ranges[-length:]) / max(1, min(length, len(true_ranges))))
        previous = close
    return values


def _atr(rows: List[Dict], length: int = 14) -> float:
    values = _atr_series(rows, length)
    return values[-1] if values else 0.0


def _pivots(rows: List[Dict], legs: int, level: str, atrs: List[float], min_atr: float) -> List[Dict]:
    """Symmetric fractal pivots: occurrence and later detection are explicit."""
    legs, raw = max(2, int(legs)), []
    for index in range(legs, len(rows) - legs):
        neighbours = rows[index - legs:index] + rows[index + 1:index + legs + 1]
        high, low = _v(rows[index], "high"), _v(rows[index], "low")
        if high > max(_v(item, "high") for item in neighbours):
            raw.append({"index": index, "kind": "high", "price": high,
                        "detected_at": index + legs, "confirmed_at": index + legs, "level": level})
        if low < min(_v(item, "low") for item in neighbours):
            raw.append({"index": index, "kind": "low", "price": low,
                        "detected_at": index + legs, "confirmed_at": index + legs, "level": level})
    result = []
    multiplier = {"small": 0.45, "medium": 0.8, "large": 1.15}.get(level, 1.0)
    for pivot in sorted(raw, key=lambda item: (item["index"], item["kind"] == "low")):
        if result and result[-1]["kind"] == pivot["kind"]:
            extreme = (pivot["kind"] == "high" and pivot["price"] > result[-1]["price"]) or (
                pivot["kind"] == "low" and pivot["price"] < result[-1]["price"])
            if extreme:
                result[-1] = pivot
            continue
        if result:
            local_atr = atrs[min(pivot["index"], len(atrs) - 1)] if atrs else 0
            if abs(pivot["price"] - result[-1]["price"]) < local_atr * min_atr * multiplier:
                continue
        result.append(pivot)
    previous = {"high": None, "low": None}
    for pivot in result:
        old = previous[pivot["kind"]]
        pivot["label"] = None if old is None else (
            "HH" if pivot["kind"] == "high" and pivot["price"] > old else
            "LH" if pivot["kind"] == "high" else "LL" if pivot["price"] < old else "HL")
        previous[pivot["kind"]] = pivot["price"]
    return result


def _event_stream(rows: List[Dict], pivots: List[Dict], atrs: List[float], config: Dict,
                  scope: str) -> Tuple[List[Dict], List[Dict], str]:
    """Independent state machine for internal or major structure.

    A major counter-trend break has a real lifecycle: candidate -> confirmed
    close break -> retest/continuation hold -> CHoCH, or failed break.  Merely
    waiting N bars is not treated as a retest.
    """
    events, candidates, state, consumed = [], [], "undetermined", set()
    pending = {"up": None, "down": None}
    confirm_bars = max(1, int(config["break_confirm_bars"]))
    for index, row in enumerate(rows):
        available = [p for p in pivots if p["confirmed_at"] <= index]
        high = next((p for p in reversed(available) if p["kind"] == "high"), None)
        low = next((p for p in reversed(available) if p["kind"] == "low"), None)
        close = _v(row, "close")
        atr = atrs[index] if index < len(atrs) else (atrs[-1] if atrs else 0)
        buffer = atr * float(config["break_buffer_atr"])
        for direction, pivot, broken in (
            ("up", high, bool(high and close > high["price"] + buffer)),
            ("down", low, bool(low and close < low["price"] - buffer)),
        ):
            key = (pivot["kind"], pivot["index"]) if pivot else None
            current = pending[direction]

            # A confirmed counter-trend break is waiting for an actual touch of
            # the broken level and a close that holds on the new side.
            if current and current.get("stage") == "retest":
                current["bars_after_break"] += 1
                if current["bars_after_break"] > int(config.get("candidate_timeout_bars", 12)):
                    events.append({**current, "index": index, "confirmed_at": index,
                                   "type": "breakout_failed", "confirmation": "candidate_timeout",
                                   "scope": scope})
                    pending[direction] = None
                    continue
                invalid = close < current["level"] - buffer if direction == "up" else close > current["level"] + buffer
                touched = (_v(row, "low") <= current["level"] + buffer and close >= current["level"]) if direction == "up" else (
                    _v(row, "high") >= current["level"] - buffer and close <= current["level"])
                held = close >= current["level"] if direction == "up" else close <= current["level"]
                if invalid:
                    events.append({**current, "index": index, "confirmed_at": index,
                                   "type": "breakout_failed", "confirmation": "closed_back_inside",
                                   "scope": scope})
                    pending[direction] = None
                    continue
                if touched or (current["bars_after_break"] >= int(config.get("retest_bars", 0)) and held):
                    events.append({**current, "index": index, "confirmed_at": index,
                                   "confirmation_index": index, "type": current["event_type"],
                                   "confirmation": "retest_confirmed" if touched else "continuation_confirmed",
                                   "scope": scope, "retest_status": "touched_and_held" if touched else "held_without_touch"})
                    consumed.add((current["pivot_kind"], current["swing_index"]))
                    state = direction
                    pending = {"up": None, "down": None}
                continue
            if not broken or key in consumed:
                pending[direction] = None
                continue
            if not current or current["swing_index"] != pivot["index"]:
                current = {"index": index, "detected_at": index, "type": "breakout_candidate",
                           "direction": direction, "level": pivot["price"], "swing_index": pivot["index"],
                           "pivot_at": pivot["index"], "pivot_detected_at": pivot["confirmed_at"],
                           "scope": scope, "pivot_kind": pivot["kind"], "count": 0, "stage": "candidate"}
                candidates.append(current.copy())
            current["count"] += 1
            pending[direction] = current
            body = abs(close - _v(row, "open"))
            displacement = body / max(atr, 1e-9)
            event_type = "choch" if state not in ("undetermined", direction) else "bos"
            if current["count"] < confirm_bars:
                continue
            needs_retest = (event_type == "choch" and scope == "major"
                            and displacement < float(config.get("displacement_atr", 0.8))
                            and int(config.get("retest_bars", 0)) > 0)
            if needs_retest:
                current.update({"stage": "retest", "event_type": event_type,
                                "break_confirmed_at": index, "bars_after_break": 0,
                                "displacement_atr": round(displacement, 3), "status": "break_confirmed"})
                candidates.append(current.copy())
                pending[direction] = current
                continue
            events.append({**current, "index": index, "confirmed_at": index,
                           "confirmation_index": index, "type": event_type,
                           "confirmation": "close_confirmed", "scope": scope,
                           "displacement_atr": round(displacement, 3),
                           "retest_status": "displacement_confirmed" if displacement >= float(config.get("displacement_atr", 0.8)) else "not_required",
                           "retest_required": max(0, int(config.get("retest_bars", 0)))})
            consumed.add(key)
            state = direction
            pending = {"up": None, "down": None}
        # Wick rejection is evidence only and never changes the state.
        if high and _v(row, "high") > high["price"] and close <= high["price"]:
            events.append({"index": index, "confirmed_at": index, "type": "liquidity_sweep",
                           "direction": "up", "level": high["price"], "swing_index": high["index"],
                           "scope": scope, "confirmation": "wick_rejected"})
        if low and _v(row, "low") < low["price"] and close >= low["price"]:
            events.append({"index": index, "confirmed_at": index, "type": "liquidity_sweep",
                           "direction": "down", "level": low["price"], "swing_index": low["index"],
                           "scope": scope, "confirmation": "wick_rejected"})
    active = next((item for item in pending.values() if item), None)
    if active:
        candidates.append({**active, "status": "candidate"})
    return events, candidates[-30:], state


def _linear(points: List[Dict]) -> Tuple[float, float]:
    if len(points) < 2:
        return 0.0, points[-1]["price"] if points else 0.0
    xs, ys = [float(p["index"]) for p in points], [float(p["price"]) for p in points]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - xm) ** 2 for x in xs)
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / denominator if denominator else 0.0
    return slope, ym - slope * xm


def _range(rows: List[Dict], pivots: List[Dict], atr: float, config: Dict) -> Optional[Dict]:
    """Select a robust recent rectangle/triangle and expose its lifecycle."""
    if len(rows) < int(config["range_min_bars"]):
        return None
    best = None
    for length in (24, 36, 48, 72, 96, 120):
        if length > len(rows):
            continue
        start = len(rows) - length
        relevant = [p for p in pivots if p["index"] >= start]
        highs = [p for p in relevant if p["kind"] == "high"][-5:]
        lows = [p for p in relevant if p["kind"] == "low"][-5:]
        if len(highs) < 2 or len(lows) < 2:
            continue
        hs, hi = _linear(highs)
        ls, li = _linear(lows)
        end = len(rows) - 1
        top, bottom = hi + hs * end, li + ls * end
        if top <= bottom:
            continue
        epsilon = atr * 0.025
        hf, lf = abs(hs) <= epsilon, abs(ls) <= epsilon
        if hf and lf:
            pattern = "range"
        elif hs < -epsilon and ls > epsilon:
            pattern = "triangle"
        elif hf and ls > epsilon:
            pattern = "ascending_triangle"
        elif hs < -epsilon and lf:
            pattern = "descending_triangle"
        elif hs > epsilon and ls < -epsilon:
            pattern = "broadening"
        else:
            continue
        tolerance = max(abs(top) * float(config["range_touch_tolerance"]),
                        atr * float(config["range_touch_atr"]))
        ht = sum(abs(p["price"] - (hi + hs * p["index"])) <= tolerance for p in highs)
        lt = sum(abs(p["price"] - (li + ls * p["index"])) <= tolerance for p in lows)
        inside = sum((li + ls * i - tolerance) <= _v(rows[i], "close") <= (hi + hs * i + tolerance)
                     for i in range(start, len(rows))) / length
        width_atr = (top - bottom) / max(atr, 1e-9)
        active = (ht >= int(config["range_min_touches"]) and lt >= int(config["range_min_touches"])
                  and inside >= float(config["range_min_inside_ratio"])
                  and width_atr <= float(config["range_max_atr"]))
        score = inside * 50 + min(20, (ht + lt) * 4) + min(15, length / 8) + (30 if active else 0)
        item = {"active": active, "status": "confirmed" if active else "candidate", "pattern": pattern,
                "start_index": start, "end_index": end, "top": top, "bottom": bottom,
                "high_slope": hs, "low_slope": ls, "high_intercept": hi, "low_intercept": li,
                "high_touches": ht, "low_touches": lt, "inside_ratio": round(inside, 3),
                "width_atr": round(width_atr, 2), "score": round(score, 2)}
        if best is None or item["score"] > best["score"]:
            best = item
    if not best:
        return None
    count = max(1, int(config["break_confirm_bars"]))
    buffer = atr * float(config["break_buffer_atr"])
    inspect_from = max(best["start_index"], len(rows) - count - 3)
    samples = []
    for index in range(inspect_from, len(rows)):
        close = _v(rows[index], "close")
        upper = best["high_intercept"] + best["high_slope"] * index
        lower = best["low_intercept"] + best["low_slope"] * index
        samples.append({"index": index, "up": close > upper + buffer,
                        "down": close < lower - buffer,
                        "inside": lower <= close <= upper})
    if samples and len(samples) >= count and all(item["up"] for item in samples[-count:]):
        confirmed_at = samples[-count]["index"]
        best.update({"active": False, "status": "breakout_confirmed", "breakout_direction": "up",
                     "lifecycle_event": {"type": "range_breakout_confirmed", "direction": "up",
                                         "index": confirmed_at, "confirmed_at": confirmed_at}})
    elif samples and len(samples) >= count and all(item["down"] for item in samples[-count:]):
        confirmed_at = samples[-count]["index"]
        best.update({"active": False, "status": "breakout_confirmed", "breakout_direction": "down",
                     "lifecycle_event": {"type": "range_breakout_confirmed", "direction": "down",
                                         "index": confirmed_at, "confirmed_at": confirmed_at}})
    elif samples and (samples[-1]["up"] or samples[-1]["down"]):
        direction = "up" if samples[-1]["up"] else "down"
        best.update({"status": "breakout_candidate", "breakout_direction": direction,
                     "lifecycle_event": {"type": "range_breakout_candidate", "direction": direction, "index": len(rows) - 1}})
    elif samples and samples[-1]["inside"]:
        prior_up = any(item["up"] for item in samples[:-1])
        prior_down = any(item["down"] for item in samples[:-1])
        if prior_up or prior_down:
            direction = "up" if prior_up else "down"
            best.update({"active": True, "status": "failed_breakout", "breakout_direction": direction,
                         "lifecycle_event": {"type": "range_failed_breakout", "direction": direction,
                                             "index": len(rows) - 1, "returned_inside": True}})
    return best


def _trendlines(rows: List[Dict], levels: Dict[str, List[Dict]], atr: float, config: Dict) -> List[Dict]:
    result, zone = [], atr * float(config["trendline_touch_atr"])
    for level, pivots in levels.items():
        for kind in ("high", "low"):
            points = [p for p in pivots if p["kind"] == kind]
            for first, second in zip(points[-4:-1], points[-3:]):
                span = second["index"] - first["index"]
                if span < int(config["trendline_min_bars"]):
                    continue
                slope = (second["price"] - first["price"]) / span
                touches, broken_at, consecutive = 0, None, 0
                for index in range(second["index"], len(rows)):
                    line = first["price"] + slope * (index - first["index"])
                    if _v(rows[index], "low") - zone <= line <= _v(rows[index], "high") + zone:
                        touches += 1
                    crossed = _v(rows[index], "close") > line + zone if kind == "high" else _v(rows[index], "close") < line - zone
                    consecutive = consecutive + 1 if crossed else 0
                    if consecutive >= int(config["break_confirm_bars"]):
                        broken_at = index
                        break
                if touches >= int(config["trendline_min_touches"]):
                    result.append({"kind": "resistance" if kind == "high" else "support", "level": level,
                                   "start_index": first["index"], "anchor_index": second["index"],
                                   "end_index": broken_at if broken_at is not None else len(rows) - 1,
                                   "start_price": first["price"], "anchor_price": second["price"],
                                   "slope": slope, "touches": touches, "broken_at": broken_at,
                                   "score": round(touches * 10 + span / 10 + {"small": 1, "medium": 4, "large": 8}[level], 2)})
    return sorted(result, key=lambda item: item["score"], reverse=True)[:6]


def _attach_pivot_parents(levels: Dict[str, List[Dict]]) -> None:
    """Annotate higher pivots with the lower-level pivots that formed them."""
    for parent_name, child_name in (("medium", "small"), ("large", "medium")):
        children = levels.get(child_name) or []
        for parent in levels.get(parent_name) or []:
            source = sorted(children,
                            key=lambda child: abs(int(child.get("index", -1)) - int(parent.get("index", -1))))[:3]
            parent["source_pivot_indexes"] = [int(item["index"]) for item in source[:3]]
            parent["source_level"] = child_name


def _protected_levels(pivots: List[Dict], bias: str) -> Dict:
    """Return protected/weak swing levels for the current directional bias.

    In a bearish structure the latest meaningful high before the latest lower
    low protects the trend. In a bullish structure the symmetric low protects
    it. These levels are deliberately derived from confirmed pivots only.
    """
    highs = [p for p in pivots if p.get("kind") == "high"]
    lows = [p for p in pivots if p.get("kind") == "low"]
    if not highs and not lows:
        return {"protected_high": None, "protected_low": None, "weak_high": None, "weak_low": None}
    latest_high, latest_low = (highs[-1] if highs else None), (lows[-1] if lows else None)
    if bias == "down" and latest_low:
        candidates = [p for p in highs if p.get("index", -1) <= latest_low.get("index", -1)]
        protected_high = candidates[-1] if candidates else latest_high
        protected_low = latest_low
        weak_high = latest_high
        weak_low = lows[-2] if len(lows) > 1 else None
    elif bias == "up" and latest_high:
        candidates = [p for p in lows if p.get("index", -1) <= latest_high.get("index", -1)]
        protected_low = candidates[-1] if candidates else latest_low
        protected_high = latest_high
        weak_low = latest_low
        weak_high = highs[-2] if len(highs) > 1 else None
    else:
        protected_high, protected_low = latest_high, latest_low
        weak_high, weak_low = None, None
    def point(item):
        if not item:
            return None
        return {"index": item.get("index"), "price": item.get("price"),
                "label": item.get("label"), "confirmed_at": item.get("confirmed_at")}
    return {"protected_high": point(protected_high), "protected_low": point(protected_low),
            "weak_high": point(weak_high), "weak_low": point(weak_low)}


def _hierarchy(levels: Dict[str, List[Dict]], states: Dict[str, str], events: Dict[str, List[Dict]]) -> Dict:
    """Build the public Internal -> Swing -> External structure hierarchy."""
    _attach_pivot_parents(levels)
    output = {}
    for name, scope in (("internal", "small"), ("swing", "medium"), ("external", "large")):
        bias = states.get(name, "undetermined")
        event_list = events.get(name) or []
        last_event = event_list[-1] if event_list else None
        opposite = ("down" if bias == "up" else "up") if bias in {"up", "down"} else None
        phase = "continuation" if bias in {"up", "down"} else "forming"
        if last_event and last_event.get("type") == "choch":
            phase = "reversal_confirmed"
        levels_for_scope = levels.get(scope) or []
        if name == "internal" and bias in {"up", "down"} and states.get("swing") in {"up", "down"} and bias != states.get("swing"):
            phase = "pullback"
        protected = _protected_levels(levels_for_scope, bias)
        output[name] = {
            "bias": bias,
            "phase": phase,
            "pivot_count": len(levels_for_scope),
            "pivots": levels_for_scope,
            "protected_high": protected["protected_high"],
            "protected_low": protected["protected_low"],
            "weak_high": protected["weak_high"],
            "weak_low": protected["weak_low"],
            "last_event": last_event,
            "reversal_direction": opposite if phase == "reversal_candidate" else None,
        }
    return output


def _local_patterns(rows: List[Dict], box: Optional[Dict], trendlines: List[Dict]) -> List[Dict]:
    """Expose local geometry independently from the directional structure."""
    patterns = []
    if box:
        pattern_type = {
            "range": "range", "triangle": "converging_triangle",
            "ascending_triangle": "ascending_triangle", "descending_triangle": "descending_triangle",
            "broadening": "diverging_triangle",
        }.get(box.get("pattern"), box.get("pattern", "range"))
        item = {**box, "type": pattern_type, "scope": "internal",
                "status": box.get("status", "candidate"),
                "target": None, "breakout": box.get("lifecycle_event")}
        if box.get("top") is not None and box.get("bottom") is not None:
            item["target"] = {"up": box["top"] + (box["top"] - box["bottom"]),
                               "down": box["bottom"] - (box["top"] - box["bottom"])}
        patterns.append(item)
    for line in trendlines:
        patterns.append({"type": "trendline", "scope": line.get("level", "internal"),
                         "status": "broken" if line.get("broken_at") is not None else "active",
                         **line})
    return patterns


def _anchor_confirmed_segments(rows: List[Dict], segments: List[Dict],
                               pivots: Optional[List[Dict]] = None,
                               max_backdate_bars: int = 48) -> List[Dict]:
    """Backdate visuals to the reversal extreme while retaining confirmation time."""
    for position in range(1, len(segments)):
        previous, current = segments[position - 1], segments[position]
        event = current.get("event") or {}
        if current.get("type") not in ("up", "down") or not event:
            continue
        search_start = max(previous["start_index"], 0)
        search_end = min(int(event.get("confirmed_at", event.get("index", search_start))), len(rows) - 1)
        if search_end <= search_start:
            continue
        opposite = "high" if current["type"] == "down" else "low"
        eligible = [p for p in (pivots or []) if p.get("kind") == opposite
                    and search_start <= p.get("index", -1) <= search_end]
        if eligible:
            # Use the latest confirmed structural pivot, not the most extreme
            # price anywhere in the preceding regime.
            anchor = eligible[-1]["index"]
        else:
            bounded_start = max(search_start, search_end - max(1, int(max_backdate_bars)))
            indexes = range(bounded_start, search_end + 1)
            anchor = max(indexes, key=lambda i: _v(rows[i], "high")) if current["type"] == "down" else min(indexes, key=lambda i: _v(rows[i], "low"))
        anchor = max(previous["start_index"] + 1, min(anchor, search_end))
        previous["end_index"], current["start_index"] = anchor - 1, anchor
        event["segment_anchor_index"], event["confirmation_index"] = anchor, search_end
    return [segment for segment in segments if segment["end_index"] >= segment["start_index"]]


def _segments(rows: List[Dict], events: List[Dict], box: Optional[Dict],
              small: List[Dict], major: List[Dict], atr: float,
              config: Dict) -> List[Dict]:
    changes = [event for event in events if event["type"] in ("bos", "choch")]
    points = [0] + [event["confirmed_at"] for event in changes] + [max(0, len(rows) - 1)]
    result = []
    for start, end in zip(points, points[1:]):
        event = next((item for item in changes if item["confirmed_at"] == start), None)
        result.append({"start_index": start, "end_index": end,
                       "type": event["direction"] if event else "transition", "event": event,
                       "status": "confirmed" if event else "transition"})
    result = _anchor_confirmed_segments(
        rows, result, major, int(config.get("trend_max_anchor_bars", 48))
    )
    if box and box.get("active"):
        start, kept = int(box["start_index"]), []
        for segment in result:
            if segment["end_index"] < start:
                kept.append(segment)
            elif segment["start_index"] < start:
                segment["end_index"] = start - 1
                kept.append(segment)
        kind = "triangle" if "triangle" in box["pattern"] else "sideways"
        kept.append({"start_index": start, "end_index": len(rows) - 1, "type": kind, "status": "confirmed",
                     "event": {"type": "range_confirmed", "confirmed_at": len(rows) - 1,
                               "pattern": box["pattern"], "direction": "neutral"}})
        result = kept
    elif box and box.get("status") == "breakout_confirmed":
        start = int(box["start_index"])
        breakout = int((box.get("lifecycle_event") or {}).get("confirmed_at", len(rows) - 1))
        kept = []
        for segment in result:
            if segment["end_index"] < start:
                kept.append(segment)
            elif segment["start_index"] < start:
                kept.append({**segment, "end_index": start - 1})
        kind = "triangle" if "triangle" in box["pattern"] else "sideways"
        if breakout > start:
            kept.append({"start_index": start, "end_index": breakout - 1,
                         "type": kind, "status": "confirmed",
                         "event": {"type": "range_confirmed", "confirmed_at": start,
                                   "pattern": box["pattern"], "direction": "neutral"}})
        kept.append({"start_index": breakout, "end_index": len(rows) - 1,
                     "type": box["breakout_direction"], "status": "confirmed",
                     "event": {**(box.get("lifecycle_event") or {}),
                               "confirmation_index": breakout,
                               "confirmation": "close_confirmed", "scope": "major"}})
        result = kept
    merged = []
    for segment in result:
        segment["bars"] = segment["end_index"] - segment["start_index"] + 1
        if merged and merged[-1]["type"] == segment["type"]:
            merged[-1]["end_index"] = segment["end_index"]
        elif merged and segment["type"] == "transition" and segment["bars"] < 3:
            merged[-1]["end_index"] = segment["end_index"]
        else:
            merged.append(segment)
    for position, segment in enumerate(merged):
        segment["bars"] = segment["end_index"] - segment["start_index"] + 1
        pivots = [p for p in major if segment["start_index"] <= p["index"] <= segment["end_index"]]
        counts = {label: sum(p.get("label") == label for p in pivots) for label in ("HH", "HL", "LH", "LL")}
        net = (_v(rows[segment["end_index"]], "close") - _v(rows[segment["start_index"]], "close")) / max(atr, 1e-9)
        event = segment.get("event") or {}
        closes = [_v(row, "close") for row in rows[segment["start_index"]:segment["end_index"] + 1]]
        price_span = max(closes) - min(closes) if closes else 0
        retracement = 0.0
        if price_span and segment["type"] == "up":
            retracement = (max(closes) - closes[-1]) / price_span
        elif price_span and segment["type"] == "down":
            retracement = (closes[-1] - min(closes)) / price_span
        evidence_count = counts["HH"] + counts["HL"] if segment["type"] == "up" else counts["LH"] + counts["LL"]
        opposing_count = counts["LH"] + counts["LL"] if segment["type"] == "up" else counts["HH"] + counts["HL"]
        labelled_count = evidence_count + opposing_count
        direction_ratio = evidence_count / labelled_count if labelled_count else 0.0
        pivot_path = sum(abs(b["price"] - a["price"]) for a, b in zip(pivots, pivots[1:]))
        efficiency = abs(_v(rows[segment["end_index"]], "close") - _v(rows[segment["start_index"]], "close")) / max(pivot_path, atr, 1e-9)
        running_high = running_low = None
        max_drawdown_atr = max_rally_atr = 0.0
        for close in closes:
            running_high = close if running_high is None else max(running_high, close)
            running_low = close if running_low is None else min(running_low, close)
            max_drawdown_atr = max(max_drawdown_atr, (running_high - close) / max(atr, 1e-9))
            max_rally_atr = max(max_rally_atr, (close - running_low) / max(atr, 1e-9))
        max_retrace_atr = max_drawdown_atr if segment["type"] != "down" else max_rally_atr
        slope_moves = []
        for kind in ("high", "low"):
            points = [p for p in pivots if p.get("kind") == kind]
            slope_moves.extend(b["price"] - a["price"] for a, b in zip(points, points[1:]))
        expected_sign = -1 if segment["type"] == "down" else 1
        slope_consistency = (sum((move * expected_sign) > 0 for move in slope_moves) / len(slope_moves)
                             if slope_moves else 1.0)
        displacement = float(event.get("displacement_atr") or 0)
        confirmation_bonus = {"retest_confirmed": 18, "continuation_confirmed": 12,
                              "close_confirmed": 10}.get(event.get("confirmation"), 0)
        level_bonus = {"major": 8, "external": 12}.get(event.get("scope"), 0)
        strength = min(95, 28 + evidence_count * 6 + min(20, abs(net) * 4)
                       + min(12, displacement * 6) + confirmation_bonus + level_bonus
                       - min(18, retracement * 20))
        if segment["type"] in ("sideways", "triangle"):
            strength = min(95, 45 + (box.get("score", 0) / 2 if box else 0))
        if (segment["type"] == "up" and net < -0.25) or (segment["type"] == "down" and net > 0.25):
            segment["type"], strength = "transition", min(strength, 40)
        breakout_segment = event.get("type") == "range_breakout_confirmed"
        mixed_structure = (labelled_count >= 4 and direction_ratio < float(config.get("trend_min_direction_ratio", 0.62)))
        inefficient = (len(pivots) >= 4 and efficiency < float(config.get("trend_min_efficiency", 0.30)))
        slope_inconsistent = (len(slope_moves) >= 2 and
                              slope_consistency < float(config.get("trend_min_slope_consistency", 0.60)))
        excessive_retrace = max_retrace_atr > max(float(config.get("trend_max_retrace_atr", 4.0)), abs(net) * 0.60)
        if segment["type"] in ("up", "down") and not breakout_segment and (mixed_structure or inefficient or slope_inconsistent or excessive_retrace):
            segment["type"] = "sideways" if segment["bars"] >= int(config.get("range_min_bars", 24)) else "transition"
            strength = min(strength, 55)
        # A segment can contain both HH/HL and LH/LL without being a neutral
        # range. If one side clearly dominates and price travelled materially
        # in that direction, retain the dominant directional structure.
        if segment["type"] == "sideways" and not (event.get("type") == "range_confirmed" and box):
            up_count = counts["HH"] + counts["HL"]
            down_count = counts["LH"] + counts["LL"]
            total = up_count + down_count
            dominant = max(up_count, down_count)
            dominant_ratio = dominant / total if total else 0.0
            min_net_atr = float(config.get("trend_min_net_change_atr", 1.5))
            strict_ratio = float(config.get("trend_min_direction_ratio", 0.62))
            relaxed_ratio = float(config.get("trend_relaxed_direction_ratio", 0.55))
            required_ratio = relaxed_ratio if abs(net) >= min_net_atr else strict_ratio
            if total >= 4 and dominant_ratio >= required_ratio:
                down_slope_ok = (sum(move < 0 for move in slope_moves) / len(slope_moves) >= float(config.get("trend_min_slope_consistency", 0.60))
                                 if slope_moves else True)
                down_retrace_ok = max_rally_atr <= max(float(config.get("trend_max_retrace_atr", 4.0)), abs(net) * 0.60)
                if down_count > up_count and net <= -min_net_atr and down_slope_ok and down_retrace_ok:
                    segment["type"] = "down"
                    strength = min(75, max(strength, 45) + min(20, abs(net) * 2))
                up_slope_ok = (sum(move > 0 for move in slope_moves) / len(slope_moves) >= float(config.get("trend_min_slope_consistency", 0.60))
                               if slope_moves else True)
                up_retrace_ok = max_drawdown_atr <= max(float(config.get("trend_max_retrace_atr", 4.0)), abs(net) * 0.60)
                if up_count > down_count and net >= min_net_atr and up_slope_ok and up_retrace_ok:
                    segment["type"] = "up"
                    strength = min(75, max(strength, 45) + min(20, abs(net) * 2))
        if segment["type"] == "up" and labelled_count:
            direction_ratio = (counts["HH"] + counts["HL"]) / labelled_count
        elif segment["type"] == "down" and labelled_count:
            direction_ratio = (counts["LH"] + counts["LL"]) / labelled_count
        segment.update({"strength": int(strength), "locked": position < len(merged) - 1,
                        "start_time": _time(rows[segment["start_index"]]),
                        "end_time": _time(rows[segment["end_index"]]),
                        "confirmation_time": _time(rows[event["confirmation_index"]]) if event.get("confirmation_index") is not None else None,
                        "evidence": {**counts, "net_change_atr": round(net, 3),
                                     "displacement_atr": round(displacement, 3),
                                     "retracement_ratio": round(retracement, 3),
                                     "direction_ratio": round(direction_ratio, 3),
                                     "direction_efficiency": round(efficiency, 3),
                                     "max_retrace_atr": round(max_retrace_atr, 3),
                                     "slope_consistency": round(slope_consistency, 3),
                                     "opposing_pivots": opposing_count,
                                     "confirmation_quality": event.get("confirmation"),
                                     "confirmation_index": event.get("confirmation_index"),
                                     "segment_anchor_index": event.get("segment_anchor_index", segment["start_index"]),
                                     "scope": event.get("scope")}})
        if segment["type"] == "up":
            segment["reason"] = (f"主结构偏向上涨；HH/HL {counts['HH'] + counts['HL']} 个，"
                                 f"LH/LL {counts['LH'] + counts['LL']} 个，方向一致率 {round(direction_ratio * 100)}%")
        elif segment["type"] == "down":
            segment["reason"] = (f"主结构偏向下跌；LH/LL {counts['LH'] + counts['LL']} 个，"
                                 f"HH/HL {counts['HH'] + counts['HL']} 个，方向一致率 {round(direction_ratio * 100)}%")
        elif segment["type"] == "triangle":
            inside_ratio = box.get("inside_ratio") if box else None
            segment["reason"] = (f"高点与低点边界收敛，内部收盘比例 {round(inside_ratio * 100)}%"
                                  if inside_ratio is not None else
                                  f"高点与低点边界收敛；方向效率 {round(efficiency * 100)}%")
        elif segment["type"] == "sideways":
            if event.get("type") == "range_confirmed" and box:
                segment["reason"] = f"箱体上下沿触碰 {box['high_touches']}/{box['low_touches']} 次，内部收盘比例 {round(box['inside_ratio'] * 100)}%"
            else:
                segment["reason"] = (f"双向结构交错：HH/HL {counts['HH'] + counts['HL']} 个，"
                                     f"LH/LL {counts['LH'] + counts['LL']} 个，方向效率 {round(efficiency * 100)}%")
        else:
            segment["reason"] = "主结构证据尚未完成确认"
    return merged


def analyze(symbol: str, period: str, rows: List[Dict], config: Dict = None) -> Dict:
    rows, cfg = list(rows or []), {**DEFAULT_CONFIG, **(config or {})}
    if not rows:
        return {"symbol": symbol, "period": period, "engine_version": ENGINE_VERSION, "segments": [],
                "events": [], "candidates": [], "current_state": "undetermined", "config": cfg}
    atrs, minimum = _atr_series(rows), float(cfg["min_reversal_atr"])
    atr = atrs[-1]
    levels = {
        "small": _pivots(rows, int(cfg["pivot_legs"]), "small", atrs, minimum),
        "medium": _pivots(rows, int(cfg["medium_pivot_legs"]), "medium", atrs, minimum),
        "large": _pivots(rows, int(cfg["large_pivot_legs"]), "large", atrs, minimum),
    }
    internal_events, internal_candidates, internal_state = _event_stream(rows, levels["small"], atrs, cfg, "internal")
    # Small structure can describe pullbacks but is never allowed to establish
    # or reverse the main trend. Medium drives the main state; large is an
    # independent external context and confirmation layer.
    major_events, major_candidates, major_state = _event_stream(rows, levels["medium"], atrs, cfg, "major")
    external_events, external_candidates, external_state = _event_stream(rows, levels["large"], atrs, cfg, "external")
    box = _range(rows, levels["medium"] or levels["small"], atr, cfg)
    segments = _segments(
        rows,
        major_events,
        None,
        levels["small"],
        levels["medium"],
        atr,
        cfg,
    )
    active_candidate = next((item for item in reversed(major_candidates) if item.get("status") == "candidate"), None)
    # A local range/triangle is an annotation on top of the main Swing state;
    # it must never overwrite the directional bias.
    current_state = major_state
    trendlines = _trendlines(rows, levels, atr, cfg)
    hierarchy = _hierarchy(
        levels,
        {"internal": internal_state, "swing": major_state, "external": external_state},
        {"internal": internal_events, "swing": major_events, "external": external_events},
    )
    local_patterns = _local_patterns(rows, box, trendlines)
    state_detail = (f"{major_state}_reversal_candidate" if active_candidate and active_candidate["direction"] != major_state
                    else f"{major_state}_pullback" if internal_state not in ("undetermined", major_state) and major_state != "undetermined"
                    else major_state)
    recent = levels["small"][-12:]
    evidence = {"higher_highs": sum(p.get("label") == "HH" for p in recent),
                "higher_lows": sum(p.get("label") == "HL" for p in recent),
                "lower_highs": sum(p.get("label") == "LH" for p in recent),
                "lower_lows": sum(p.get("label") == "LL" for p in recent),
                "close_breaks": sum(e["type"] in ("bos", "choch") for e in major_events),
                "wick_sweeps": sum(e["type"] == "liquidity_sweep" for e in internal_events),
                "range_active": bool(box and box.get("active"))}
    return {"symbol": symbol, "period": period, "engine_version": ENGINE_VERSION, "config": cfg, "atr": atr,
            "swings": levels["small"], "pivot_levels": levels, "range": box,
            "trendlines": trendlines,
            "events": major_events + [e for e in internal_events if e["type"] == "liquidity_sweep"],
            "internal_events": internal_events, "major_events": major_events,
            "external_events": external_events,
            "candidates": major_candidates + external_candidates[-5:] + internal_candidates[-10:], "segments": segments[-5:],
            "structure_hierarchy": hierarchy, "local_patterns": local_patterns,
            "segment_history": segments[-50:], "current_state": current_state, "state_detail": state_detail,
            "internal_state": internal_state, "major_state": major_state, "external_state": external_state,
            "active_candidate": active_candidate,
            "evidence": evidence,
            "structure_levels": {name: {"pivot_count": len(items), "latest": items[-1] if items else None}
                                 for name, items in levels.items()},
            "last_bar_time": _time(rows[-1]), "analyzed_at": datetime.now(timezone.utc).isoformat()}


def _config_signature(config: Dict) -> str:
    return json.dumps(config, sort_keys=True, ensure_ascii=True, default=str)


def _preserve_locked_segments(cached: Dict, result: Dict, rows: List[Dict]) -> Dict:
    """Carry finalized segment boundaries forward by bar timestamp.

    The active segment may evolve; a finalized segment cannot be relocated by
    later pivots or by changing the requested history length.
    """
    indexes = {str(_time(row)): index for index, row in enumerate(rows)}
    locked = []
    for old in cached.get("segment_history") or cached.get("segments") or []:
        if not old.get("locked"):
            continue
        start, end = indexes.get(str(old.get("start_time"))), indexes.get(str(old.get("end_time")))
        if start is None or end is None or end < start:
            continue
        locked.append({**old, "start_index": start, "end_index": end,
                       "bars": end - start + 1, "locked": True})
    if not locked:
        return result
    boundary = locked[-1]["end_index"]
    fresh = []
    for item in result.get("segment_history", []):
        if item["end_index"] <= boundary:
            continue
        candidate = dict(item)
        candidate["start_index"] = max(boundary + 1, candidate["start_index"])
        if candidate["end_index"] >= candidate["start_index"]:
            candidate["bars"] = candidate["end_index"] - candidate["start_index"] + 1
            candidate["start_time"] = _time(rows[candidate["start_index"]])
            fresh.append(candidate)
    history = (locked + fresh)[-50:]
    result["segment_history"] = history
    result["segments"] = history[-5:]
    result["locked_segment_count"] = len(locked)
    return result


def analyze_incremental(symbol: str, period: str, rows: List[Dict], config: Dict = None) -> Dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    key, latest, signature = f"{symbol}::{period.upper()}", (_time(rows[-1]) if rows else None), _config_signature(cfg)
    window_signature = (str(_time(rows[0])) if rows else None, str(latest), len(rows))
    cached = _CACHE.get(key)
    if (cached and cached.get("engine_version") == ENGINE_VERSION
            and cached.get("last_bar_time") == latest
            and cached.get("config_signature") == signature
            and tuple(cached.get("window_signature") or ()) == window_signature):
        return {**cached, "calculation_mode": "cached"}
    result = analyze(symbol, period, rows, cfg)
    if (cached and cached.get("engine_version") == ENGINE_VERSION
            and cached.get("config_signature") == signature):
        result = _preserve_locked_segments(cached, result, rows)
    result.update({"config_signature": signature, "window_signature": window_signature,
                   "calculation_mode": "incremental" if cached else "initial",
                   "previous_last_bar_time": cached.get("last_bar_time") if cached else None,
                   "machine_context": {"major_state": result.get("major_state"),
                                       "external_state": result.get("external_state"),
                                       "internal_state": result.get("internal_state"),
                                       "active_candidate": result.get("active_candidate"),
                                       "latest_pivots": {name: data.get("latest")
                                                         for name, data in result.get("structure_levels", {}).items()}}})
    _CACHE[key] = result
    return result


def restore_snapshot(snapshot: Dict):
    if (isinstance(snapshot, dict) and snapshot.get("engine_version") == ENGINE_VERSION
            and snapshot.get("symbol") and snapshot.get("period")):
        _CACHE[f"{snapshot['symbol']}::{str(snapshot['period']).upper()}"] = snapshot
