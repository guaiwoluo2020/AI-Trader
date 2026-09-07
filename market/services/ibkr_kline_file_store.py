"""Local disk store for IBKR bars.

IBKR bars are runtime market data, not audit records.  Keep a compact,
deduplicated pipe-delimited file per user/symbol/period so the MySQL
``historical_klines`` table is not used for this high-volume source.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List


_LOCK = threading.RLock()
_MAX_ROWS = 20000


def _root() -> Path:
    configured = os.getenv("AI_TRADER_IBKR_KLINE_DIR", "").strip()
    if configured:
        return Path(configured)
    data_dir = os.getenv("AI_TRADER_DATA_DIR", "").strip()
    return Path(data_dir or (Path(__file__).resolve().parents[2] / "data")) / "ibkr_klines"


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))


def path_for(user_id: int, symbol: str, period: str) -> Path:
    return _root() / str(int(user_id or 0)) / _safe(symbol) / f"{_safe(period).upper()}.bars"


def _read(path: Path) -> Dict[int, Dict]:
    result: Dict[int, Dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split("|")
        if len(parts) != 6:
            continue
        try:
            timestamp = int(parts[0])
            result[timestamp] = {
                "timestamp": timestamp,
                "open": float(parts[1]), "high": float(parts[2]),
                "low": float(parts[3]), "close": float(parts[4]),
                "volume": float(parts[5]),
            }
        except (TypeError, ValueError):
            continue
    return result


def _timestamp(value) -> int:
    try:
        if isinstance(value, str):
            value = value.replace("Z", "+00:00")
            from datetime import datetime
            return int(datetime.fromisoformat(value).timestamp())
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def save_batch(user_id: int, symbol: str, period: str,
               klines: Iterable[Dict]) -> Path:
    path = path_for(user_id, symbol, period)
    with _LOCK:
        rows = _read(path)
        for item in klines or []:
            timestamp = _timestamp(item.get("timestamp") or item.get("time"))
            if timestamp <= 0:
                continue
            try:
                rows[timestamp] = {
                    "timestamp": timestamp,
                    "open": float(item.get("open", 0)),
                    "high": float(item.get("high", 0)),
                    "low": float(item.get("low", 0)),
                    "close": float(item.get("close", 0)),
                    "volume": float(item.get("volume", 0) or 0),
                }
            except (TypeError, ValueError):
                continue
        selected = sorted(rows.items())[-_MAX_ROWS:]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            "".join(
                f"{ts}|{row['open']:.12g}|{row['high']:.12g}|"
                f"{row['low']:.12g}|{row['close']:.12g}|{row['volume']:.12g}\n"
                for ts, row in selected
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    return path


def load_recent(user_id: int, symbol: str, period: str, limit: int = 1200) -> List[Dict]:
    with _LOCK:
        rows = sorted(_read(path_for(user_id, symbol, period)).values(), key=lambda item: item["timestamp"])
    return rows[-max(1, min(int(limit or 1200), _MAX_ROWS)):]


def latest_cursors(user_id: int, symbol: str) -> Dict[str, int]:
    result = {}
    for period in ("M1", "M5", "M15", "H1", "H4"):
        rows = load_recent(user_id, symbol, period, 1)
        if rows:
            result[period] = int(rows[-1]["timestamp"])
    return result

