"""Central MySQL storage lifecycle and health checks."""

from __future__ import annotations

import threading
from typing import Optional

from mysql_storage import MySQLStorage


_storage: Optional[MySQLStorage] = None
_lock = threading.RLock()


def get_mysql_storage() -> MySQLStorage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = MySQLStorage()
        return _storage


def reset_storage() -> None:
    """Close and discard the process singleton for reloads and tests."""
    global _storage
    with _lock:
        if _storage is not None:
            _storage.close()
        _storage = None


def close_storage() -> None:
    """Close pooled connections during application shutdown."""
    with _lock:
        if _storage is not None:
            _storage.close()


def healthcheck_storage() -> bool:
    """Return whether MySQL can execute a lightweight query."""
    try:
        row = get_mysql_storage().fetchone("SELECT 1 AS ok")
        return bool(row and int(row.get("ok") or 0) == 1)
    except Exception:
        return False
