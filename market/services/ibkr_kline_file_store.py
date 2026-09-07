"""Local disk store for IBKR bars.

IBKR bars are runtime market data, not audit records.  Keep a compact,
deduplicated pipe-delimited file per user/symbol/period so the MySQL
``historical_klines`` table is not used for this high-volume source.
"""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Iterable, List, Optional


_LOCK = threading.RLock()
_MAX_ROWS = 20000
_DEFAULT_RETENTION_DAYS = 30
_DEFAULT_BACKUP_DAYS = 7
_CHINA_TZ = ZoneInfo("Asia/Shanghai")


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


def _configured_days(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def backup_current(now: Optional[int] = None) -> Dict[str, int]:
    """Copy current bars to one dated directory, without touching the live files."""
    root = _root()
    date_key = datetime.fromtimestamp(now or time.time(), tz=_CHINA_TZ).strftime("%Y%m%d")
    target_root = root / "_backups" / date_key
    copied = 0
    with _LOCK:
        for source in root.glob("**/*.bars"):
            if "_backups" in source.parts:
                continue
            relative = source.relative_to(root)
            target = target_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
                copied += 1
            except OSError:
                continue
    return {"files": copied, "date": int(date_key)}


def prune(retention_days: Optional[int] = None, backup_days: Optional[int] = None,
          now: Optional[int] = None) -> Dict[str, int]:
    """Keep recent bars and dated backups; all rewrites are atomic."""
    current = int(now or time.time())
    keep_after = current - 86400 * (retention_days or _configured_days(
        "AI_TRADER_IBKR_KLINE_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS))
    backup_after = current - 86400 * (backup_days or _configured_days(
        "AI_TRADER_IBKR_KLINE_BACKUP_DAYS", _DEFAULT_BACKUP_DAYS))
    root = _root()
    files = rows_removed = backups_removed = 0
    with _LOCK:
        for path in root.glob("**/*.bars"):
            if "_backups" in path.parts:
                continue
            rows = _read(path)
            selected = [(ts, row) for ts, row in sorted(rows.items()) if ts >= keep_after]
            if len(selected) != len(rows):
                temporary = path.with_name(f".{path.name}.{os.getpid()}.prune.tmp")
                temporary.write_text(
                    "".join(
                        f"{ts}|{row['open']:.12g}|{row['high']:.12g}|"
                        f"{row['low']:.12g}|{row['close']:.12g}|{row['volume']:.12g}\n"
                        for ts, row in selected
                    ), encoding="utf-8")
                os.replace(temporary, path)
                rows_removed += len(rows) - len(selected)
                files += 1
        backup_root = root / "_backups"
        if backup_root.exists():
            for directory in backup_root.iterdir():
                if not directory.is_dir() or not re.fullmatch(r"\d{8}", directory.name):
                    continue
                try:
                    stamp = int(datetime.strptime(directory.name, "%Y%m%d")
                                .replace(tzinfo=_CHINA_TZ).timestamp())
                except ValueError:
                    continue
                if stamp < backup_after:
                    shutil.rmtree(directory, ignore_errors=True)
                    backups_removed += 1
    return {"files_pruned": files, "rows_removed": rows_removed,
            "backup_days_removed": backups_removed}


def maintenance(now: Optional[int] = None) -> Dict[str, int]:
    """Daily, deliberately small maintenance operation for IBKR bar files."""
    result = backup_current(now)
    result.update(prune(now=now))
    return result


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
