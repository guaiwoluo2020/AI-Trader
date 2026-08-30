"""Local-disk persistence for market-structure incremental snapshots.

The current snapshot is intentionally a single atomically replaced file. A
bounded history is written only at meaningful checkpoints, so a one-minute
symbol does not create one permanent file per bar.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional


def _root() -> Path:
    configured = os.getenv("AI_TRADER_STRUCTURE_SNAPSHOT_DIR", "").strip()
    if configured:
        return Path(configured)
    data_dir = os.getenv("AI_TRADER_DATA_DIR", "").strip()
    return Path(data_dir or (Path(__file__).resolve().parents[2] / "data")) / "structure_snapshots"


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))


def current_path(user_id: int, account_id: int, symbol: str, period: str) -> Path:
    return (_root() / str(int(user_id or 0)) / str(int(account_id or 0)) /
            _safe(symbol) / _safe(str(period).upper()) / "current.json")


def _read(path: Path) -> Optional[Dict]:
    try:
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def load_current(user_id: int, account_id: int, symbol: str, period: str) -> Optional[Dict]:
    return _read(current_path(user_id, account_id, symbol, period))


def _write_atomic(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(temporary, path)


def _checkpoint_required(previous: Optional[Dict], result: Dict, now: Optional[float] = None) -> bool:
    if not previous:
        return True
    old_state = (previous.get("major_state"), previous.get("internal_state"), previous.get("external_state"))
    new_state = (result.get("major_state"), result.get("internal_state"), result.get("external_state"))
    if old_state != new_state or previous.get("config_signature") != result.get("config_signature"):
        return True
    old_events = previous.get("events") or []
    new_events = result.get("events") or []
    old_last = old_events[-1] if old_events else None
    new_last = new_events[-1] if new_events else None
    if (old_last or {}).get("confirmed_at") != (new_last or {}).get("confirmed_at"):
        return True
    try:
        return float(now or time.time()) - float(previous.get("checkpoint_at") or 0) >= 30 * 60
    except (TypeError, ValueError):
        return True


def save_current(result: Dict, user_id: int, account_id: int) -> Path:
    path = current_path(user_id, account_id, result.get("symbol", ""), result.get("period", "M5"))
    payload = dict(result)
    payload["snapshot_path"] = str(path)
    payload["snapshot_saved_at"] = time.time()
    _write_atomic(path, payload)
    return path


def save_checkpoint(result: Dict, user_id: int, account_id: int) -> Optional[Path]:
    path = current_path(user_id, account_id, result.get("symbol", ""), result.get("period", "M5"))
    previous = _read(path)
    now = time.time()
    payload = dict(result)
    payload["snapshot_path"] = str(path)
    payload["checkpoint_at"] = now
    payload["snapshot_saved_at"] = now
    _write_atomic(path, payload)
    if not _checkpoint_required(previous, result, now):
        return None
    history = path.parent / "history"
    history_path = history / f"{_safe(result.get('engine_version', 'v1'))}-{int(now)}.json"
    _write_atomic(history_path, payload)
    files = sorted(history.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in files[200:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return history_path
