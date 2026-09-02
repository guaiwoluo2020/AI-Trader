"""Small local fixed-width Tick recorder.

The recorder intentionally keeps no JSON and no database dependency.  It
buffers one minute, keeps at most 30 evenly distributed samples, and appends
fixed-size records to a daily file.  The same files are suitable for replay.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional


MAGIC = b"AITICK01"
VERSION = 1
PRICE_SCALE = 100_000_000
VOLUME_SCALE = 1_000_000
RECORD = struct.Struct("<QQqqqqQ")
HEADER = struct.Struct("<8sIIQQ")  # magic, version, scales, record size


class LocalTickStore:
    """Append sampled ticks to one file per source/symbol/day."""

    def __init__(self, root: Optional[str] = None, max_per_minute: int = 30):
        configured = root or os.getenv("AI_TRADER_TICK_DATA_DIR", "data/ticks")
        self.root = Path(configured).expanduser().resolve()
        self.max_per_minute = max(1, int(max_per_minute or 30))
        self._lock = threading.RLock()
        self._buffers: Dict[str, Dict[int, List[Dict]]] = {}

    @staticmethod
    def _safe(value: object, fallback: str = "unknown") -> str:
        text = str(value or fallback).strip()
        return "".join(char if char.isalnum() or char in "._-" else "_" for char in text)

    def _path(self, source_id: object, symbol: str, event_time_ms: int) -> Path:
        day = time.strftime("%Y-%m-%d", time.gmtime(event_time_ms / 1000))
        return self.root / f"source_{self._safe(source_id)}" / self._safe(symbol) / f"{day}.ticks"

    @staticmethod
    def _sample(rows: List[Dict], limit: int) -> List[Dict]:
        if len(rows) <= limit:
            return rows
        # Preserve the first and last quote and distribute the remaining
        # samples over the whole minute, rather than keeping a burst from one
        # second only.
        indexes = [round(i * (len(rows) - 1) / (limit - 1)) for i in range(limit)]
        return [rows[index] for index in indexes]

    def _ensure_header(self, path: Path) -> None:
        if path.exists() and path.stat().st_size >= HEADER.size:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            if handle.tell() == 0:
                handle.write(HEADER.pack(MAGIC, VERSION, PRICE_SCALE, VOLUME_SCALE, RECORD.size))

    def _flush_minute(self, key: str, minute: int) -> int:
        bucket = self._buffers.get(key, {}).pop(minute, [])
        if not bucket:
            return 0
        path = self._path(bucket[0]["source_id"], bucket[0]["symbol"], bucket[0]["event_time_ms"])
        self._ensure_header(path)
        sampled = self._sample(bucket, self.max_per_minute)
        with path.open("ab") as handle:
            for item in sampled:
                handle.write(RECORD.pack(
                    int(item["event_time_ms"]), int(item["received_at_ms"]),
                    round(float(item["bid"]) * PRICE_SCALE),
                    round(float(item["ask"]) * PRICE_SCALE),
                    round(float(item.get("last_price") or 0) * PRICE_SCALE),
                    round(float(item.get("volume") or 0) * VOLUME_SCALE),
                    int(item.get("sequence") or 0),
                ))
        return len(sampled)

    def record(
        self, source_id: object, symbol: str, bid: float, ask: Optional[float] = None,
        last_price: Optional[float] = None, volume: float = 0,
        sequence: int = 0, event_time_ms: Optional[int] = None,
        received_at_ms: Optional[int] = None,
    ) -> int:
        """Record one quote and return the number flushed to disk, if any."""
        event = int(event_time_ms or time.time() * 1000)
        received = int(received_at_ms or time.time() * 1000)
        symbol = str(symbol or "").strip()
        if not symbol or float(bid or 0) <= 0:
            return 0
        ask = float(ask if ask is not None and ask > 0 else bid)
        key = f"{self._safe(source_id)}::{self._safe(symbol)}"
        minute = event // 60000
        row = {
            "source_id": source_id, "symbol": symbol,
            "event_time_ms": event, "received_at_ms": received,
            "bid": float(bid), "ask": ask, "last_price": last_price or bid,
            "volume": volume, "sequence": sequence,
        }
        with self._lock:
            bucket = self._buffers.setdefault(key, {})
            flushed = 0
            for old_minute in [item for item in bucket if item < minute]:
                flushed += self._flush_minute(key, old_minute)
            bucket.setdefault(minute, []).append(row)
            return flushed

    def flush(self) -> int:
        with self._lock:
            total = 0
            for key, buckets in list(self._buffers.items()):
                for minute in list(buckets):
                    total += self._flush_minute(key, minute)
            return total


_default_store: Optional[LocalTickStore] = None
_default_lock = threading.Lock()


def get_local_tick_store() -> LocalTickStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = LocalTickStore()
        return _default_store


def iter_ticks(path: str) -> Iterator[Dict]:
    """Read fixed-width Tick records in file order for replay."""
    file_path = Path(path)
    with file_path.open("rb") as handle:
        header = handle.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("Tick 文件头不完整")
        magic, version, price_scale, volume_scale, record_size = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION or record_size != RECORD.size:
            raise ValueError("不支持的 Tick 文件格式")
        while True:
            raw = handle.read(RECORD.size)
            if not raw:
                break
            if len(raw) != RECORD.size:
                raise ValueError("Tick 文件末尾记录不完整")
            event, received, bid, ask, last, volume, sequence = RECORD.unpack(raw)
            yield {
                "event_time_ms": event,
                "received_at_ms": received,
                "bid": bid / price_scale,
                "ask": ask / price_scale,
                "last_price": last / price_scale,
                "volume": volume / volume_scale,
                "sequence": sequence,
            }


def list_tick_files(root: Optional[str] = None, symbol: str = "") -> List[Dict]:
    """Return local Tick files for the backtest selector."""
    base = Path(root or os.getenv("AI_TRADER_TICK_DATA_DIR", "data/ticks")).expanduser()
    if not base.exists():
        return []
    wanted = str(symbol or "").strip().lower()
    result = []
    for path in sorted(base.glob("source_*/*/*.ticks")):
        path_symbol = path.parent.name
        if wanted and path_symbol.lower() != wanted:
            continue
        try:
            meta = inspect_tick_file(str(path))
        except (OSError, ValueError):
            # A file being rotated or incomplete must not break the selector.
            continue
        result.append({
            "file_path": str(path),
            "symbol": path_symbol,
            "source": path.parent.parent.name.removeprefix("source_"),
            "date": path.stem,
            "size": path.stat().st_size,
            **meta,
        })
    return result


def inspect_tick_file(path: str) -> Dict:
    """Return coverage metadata without loading all Tick payloads."""
    count = 0
    first = None
    last = None
    with Path(path).open("rb") as handle:
        header = handle.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("Tick 文件头不完整")
        magic, version, _price_scale, _volume_scale, record_size = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION or record_size != RECORD.size:
            raise ValueError("不支持的 Tick 文件格式")
        while True:
            raw = handle.read(RECORD.size)
            if not raw:
                break
            if len(raw) != RECORD.size:
                raise ValueError("Tick 文件末尾记录不完整")
            event_time = RECORD.unpack(raw)[0]
            first = event_time if first is None else first
            last = event_time
            count += 1
    return {
        "start_time_ms": first or 0,
        "end_time_ms": last or 0,
        "tick_count": count,
        "max_ticks_per_minute": 30,
    }
