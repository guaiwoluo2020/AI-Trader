"""Local-disk storage for large daily review payloads.

MySQL keeps only a small index record; the complete evidence and LLM result
are written atomically to a JSON file so review size is not limited by a JSON
column.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional


def _root() -> Path:
    configured = os.getenv("AI_TRADER_DAILY_REVIEW_DIR", "").strip()
    if configured:
        return Path(configured)
    data_dir = os.getenv("AI_TRADER_DATA_DIR", "").strip()
    return Path(data_dir or (Path(__file__).resolve().parents[2] / "data")) / "daily_reviews"


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unknown"))


def path_for(user_id: int, review_id: str) -> Path:
    return _root() / str(int(user_id or 0)) / f"{_safe(review_id)}.json"


def save(user_id: int, review_id: str, payload: Dict) -> Path:
    path = path_for(user_id, review_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(temporary, path)
    return path


def load(path: str) -> Optional[Dict]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None

