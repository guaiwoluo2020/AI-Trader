"""In-memory spread/slippage quality sampling and entry gating."""

from __future__ import annotations

import math
import json
import statistics
import threading
import time
from collections import deque
from typing import Dict, Optional


class MarketExecutionQualityService:
    """Aggregate quote quality in memory and persist five-minute windows.

    A missing baseline deliberately allows entry.  The service only blocks new
    entries after it has enough historical windows to make a comparison.
    """

    WINDOW_SECONDS = 300
    HISTORY_SECONDS = 24 * 3600
    MIN_HISTORY_WINDOWS = 3
    SPREAD_MULTIPLIER = 2.5
    SPREAD_P95_MULTIPLIER = 1.5
    SLIPPAGE_MULTIPLIER = 2.0
    BLOCK_SECONDS = 2 * 3600
    SLIPPAGE_BAD_STREAK_LIMIT = 2

    def __init__(self, storage):
        self.storage = storage
        self._lock = threading.RLock()
        self._states: Dict[tuple, Dict] = {}

    @staticmethod
    def _percentile(values, percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return ordered[0]
        rank = (len(ordered) - 1) * percentile
        lower = int(math.floor(rank))
        upper = int(math.ceil(rank))
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)

    @classmethod
    def _window_start(cls, timestamp: int) -> int:
        return int(timestamp) - int(timestamp) % cls.WINDOW_SECONDS

    def _state(self, account_id: int, symbol: str, now: int) -> Dict:
        key = (int(account_id), str(symbol or "").strip())
        state = self._states.get(key)
        if state is None:
            state = {
                "window_start": self._window_start(now),
                "spreads": [],
                "slippages": {"buy": [], "sell": []},
                "slippage_bad_streak": 0,
                "history": deque(maxlen=288),
                "status": "normal",
                "blocked_until": 0,
                "last_gate": {},
                "loaded": False,
            }
            self._states[key] = state
        if not state["loaded"]:
            self._load_history(state, key[0], key[1], now)
            state["loaded"] = True
        return state

    def _load_history(self, state: Dict, account_id: int, symbol: str, now: int) -> None:
        try:
            rows = self.storage.fetchall(
                "SELECT window_start, sample_count, spread_median_points, "
                "spread_p95_points, spread_max_points, buy_slippage_p95_points, "
                "sell_slippage_p95_points FROM market_execution_quality_samples "
                "WHERE account_id=? AND symbol=? AND window_start>=? "
                "ORDER BY window_start DESC LIMIT 288",
                (account_id, symbol, int(now) - self.HISTORY_SECONDS),
            )
        except Exception:
            rows = []
        for row in reversed(rows or []):
            state["history"].append(dict(row))

    def _flush_window(self, account_id: int, symbol: str, state: Dict) -> None:
        spreads = list(state["spreads"])
        slippages = state["slippages"]
        if not spreads and not any(slippages.values()):
            return
        item = {
            "window_start": int(state["window_start"]),
            "sample_count": len(spreads),
            "spread_median_points": self._percentile(spreads, 0.5),
            "spread_p95_points": self._percentile(spreads, 0.95),
            "spread_max_points": max(spreads or [0.0]),
            "buy_slippage_p95_points": self._percentile(slippages["buy"], 0.95),
            "sell_slippage_p95_points": self._percentile(slippages["sell"], 0.95),
            "created_at": int(time.time()),
        }
        self.storage.execute(
            "INSERT INTO market_execution_quality_samples "
            "(account_id,symbol,window_start,sample_count,spread_median_points," 
            "spread_p95_points,spread_max_points,buy_slippage_p95_points," 
            "sell_slippage_p95_points,created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE sample_count=VALUES(sample_count), "
            "spread_median_points=VALUES(spread_median_points), "
            "spread_p95_points=VALUES(spread_p95_points), "
            "spread_max_points=VALUES(spread_max_points), "
            "buy_slippage_p95_points=VALUES(buy_slippage_p95_points), "
            "sell_slippage_p95_points=VALUES(sell_slippage_p95_points), "
            "created_at=VALUES(created_at)",
            (account_id, symbol, item["window_start"], item["sample_count"],
             item["spread_median_points"], item["spread_p95_points"],
             item["spread_max_points"], item["buy_slippage_p95_points"],
             item["sell_slippage_p95_points"], item["created_at"]),
        )
        state["history"].append(item)
        state["spreads"].clear()
        state["slippages"] = {"buy": [], "sell": []}
        state["slippage_bad_streak"] = 0

    def _baseline(self, state: Dict) -> Optional[Dict]:
        history = [item for item in state["history"] if int(item.get("sample_count") or 0) > 0]
        if len(history) < self.MIN_HISTORY_WINDOWS:
            return None
        spreads = [float(item.get("spread_median_points") or 0) for item in history]
        p95 = [float(item.get("spread_p95_points") or 0) for item in history]
        return {
            "median": statistics.median(spreads),
            "p95": statistics.median(p95),
            "buy_slippage_p95": statistics.median(
                float(item.get("buy_slippage_p95_points") or 0) for item in history
            ),
            "sell_slippage_p95": statistics.median(
                float(item.get("sell_slippage_p95_points") or 0) for item in history
            ),
        }

    def _set_status(self, account_id: int, symbol: str, state: Dict, status: str,
                    reason: str, details: Dict, now: int) -> None:
        old_status = state.get("status") or "normal"
        if old_status == status:
            if status == "blocked":
                state["blocked_until"] = now + self.BLOCK_SECONDS
            return
            return
        state["status"] = status
        state["blocked_until"] = now + self.BLOCK_SECONDS if status == "blocked" else 0
        try:
            self.storage.execute(
                "INSERT INTO market_execution_quality_events "
                "(account_id,symbol,status,reason,details_json,created_at) VALUES (?,?,?,?,?,?)",
                (account_id, symbol, status, reason, json.dumps(details, ensure_ascii=False), now),
            )
        except Exception:
            pass

    def record_tick(self, account_id: int, symbol: str, bid: float, ask: float,
                    point_size: float = 0.0, timestamp: Optional[int] = None) -> Dict:
        now = int(timestamp or time.time())
        bid, ask = float(bid or 0), float(ask or 0)
        point_size = float(point_size or 0)
        if bid <= 0 or ask <= 0 or ask < bid:
            return {"allowed": True, "reason_code": "quality_data_invalid"}
        with self._lock:
            state = self._state(account_id, symbol, now)
            current_window = self._window_start(now)
            if current_window != state["window_start"]:
                self._flush_window(account_id, symbol, state)
                state["window_start"] = current_window
            spread_points = (ask - bid) / point_size if point_size > 0 else 0.0
            if spread_points > 0:
                state["spreads"].append(spread_points)
            baseline = self._baseline(state)
            details = {"spread_points": spread_points, "baseline": baseline or {}}
            blocked = False
            if baseline and spread_points > 0:
                threshold = max(
                    baseline["median"] * self.SPREAD_MULTIPLIER,
                    baseline["p95"] * self.SPREAD_P95_MULTIPLIER,
                )
                blocked = threshold > 0 and spread_points > threshold
                details["threshold_points"] = threshold
            if blocked:
                self._set_status(account_id, symbol, state, "blocked", "spread_outlier", details, now)
            elif state.get("status") == "blocked" and now >= int(state.get("blocked_until") or 0):
                self._set_status(account_id, symbol, state, "normal", "spread_recovered", details, now)
            state["last_gate"] = details
            return {
                "allowed": not blocked and state.get("status") != "blocked",
                "reason_code": "execution_quality_blocked" if blocked else "execution_quality_ok",
                "reason": "当前点差异常，暂停新开仓" if blocked else "交易质量检查通过",
                "message": "当前点差异常，暂停新开仓" if blocked else "交易质量检查通过",
                "details": details,
            }

    def record_execution(self, account_id: int, symbol: str, direction: str,
                         requested_price: float, executed_price: float,
                         point_size: float = 0.0, success: bool = True,
                         timestamp: Optional[int] = None) -> None:
        if not success or point_size <= 0:
            return
        adverse = (executed_price - requested_price) if direction == "buy" else (requested_price - executed_price)
        now = int(timestamp or time.time())
        with self._lock:
            state = self._state(account_id, symbol, now)
            if adverse <= 0:
                # A favorable or flat fill breaks the consecutive adverse
                # slippage sequence for this account and symbol.
                state["slippage_bad_streak"] = 0
                return
            if self._window_start(now) != state["window_start"]:
                self._flush_window(account_id, symbol, state)
                state["window_start"] = self._window_start(now)
            state["slippages"].setdefault(direction, []).append(adverse / point_size)
            baseline = self._baseline(state)
            if baseline:
                side_baseline = float(baseline.get(
                    f"{direction}_slippage_p95", 0
                ) or 0)
                if side_baseline > 0 and adverse / point_size > side_baseline * self.SLIPPAGE_MULTIPLIER:
                    state["slippage_bad_streak"] += 1
                else:
                    state["slippage_bad_streak"] = 0
                if state["slippage_bad_streak"] >= self.SLIPPAGE_BAD_STREAK_LIMIT:
                    self._set_status(
                        account_id, symbol, state, "blocked", "slippage_outlier",
                        {
                            "direction": direction,
                            "slippage_points": adverse / point_size,
                            "baseline_p95_points": side_baseline,
                            "threshold_points": side_baseline * self.SLIPPAGE_MULTIPLIER,
                            "streak": state["slippage_bad_streak"],
                        }, now,
                    )

    def check_entry(self, account_id: int, symbol: str) -> Dict:
        with self._lock:
            state = self._states.get((int(account_id), str(symbol or "").strip()))
            if state is None:
                return {"allowed": True, "reason_code": "execution_quality_no_data"}
            if state.get("status") == "blocked" and int(time.time()) < int(state.get("blocked_until") or 0):
                return {"allowed": False, "reason_code": "execution_quality_blocked",
                        "reason": "当前点差或滑点质量异常，暂停新开仓",
                        "message": "当前点差或滑点质量异常，暂停新开仓",
                        "details": dict(state.get("last_gate") or {})}
            return {"allowed": True, "reason_code": "execution_quality_ok"}
