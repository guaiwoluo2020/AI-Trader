"""Metrics for human-labelled market-structure regression samples."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List


def _seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e12 else float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def evaluate_segments(predicted: List[Dict], labels: List[Dict], period_seconds: int) -> Dict:
    """Return direction accuracy, boundary error, false switches and delay."""
    matched, boundary_errors, delays = 0, [], []
    normalized = []
    for item in predicted:
        try:
            normalized.append({**item, "_start": _seconds(item["start_time"]),
                               "_end": _seconds(item["end_time"])})
        except (KeyError, TypeError, ValueError):
            continue
    for label in labels:
        start, end = _seconds(label["start"]), _seconds(label["end"])
        overlaps = [item for item in normalized if item["_end"] >= start and item["_start"] <= end]
        if not overlaps:
            continue
        best = max(overlaps, key=lambda item: min(item["_end"], end) - max(item["_start"], start))
        matched += int(best.get("type") == label.get("expected_type"))
        boundary_errors.append((abs(best["_start"] - start) + abs(best["_end"] - end)) / (2 * period_seconds))
        confirmation = best.get("confirmation_time")
        if confirmation:
            delays.append(max(0, (_seconds(confirmation) - best["_start"]) / period_seconds))
    switches = sum(a.get("type") != b.get("type") for a, b in zip(normalized, normalized[1:]))
    return {"sample_count": len(labels), "matched_count": matched,
            "direction_accuracy": round(matched / len(labels), 4) if labels else 0,
            "mean_boundary_error_bars": round(sum(boundary_errors) / len(boundary_errors), 2) if boundary_errors else None,
            "mean_confirmation_delay_bars": round(sum(delays) / len(delays), 2) if delays else None,
            "structure_switch_count": switches}
