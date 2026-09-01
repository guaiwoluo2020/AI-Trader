"""Trade configuration repository."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage


class TradeConfigRepository:
    DEFAULT_CONFIG = {
        "enabled": True,
        "default_volume": 0.01,
        "default_sl_offset": 0.05,
        "mt5_timezone_offset": 0,
        "symbol_config": {
            "GOLD#": {"volume": 0.01, "sl_offset": 0.5},
            "OILCASH#": {"volume": 0.01, "sl_offset": 0.05},
        },
    }

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()

    def get_config(self, user_id: int) -> Dict:
        row = self.storage.fetchone(
            "SELECT config_json FROM user_trade_configs WHERE user_id = ?", (int(user_id),)
        )
        if row:
            return json.loads(row["config_json"])
        config = self._read_legacy_config() or self.DEFAULT_CONFIG
        self.save_config(user_id, config)
        return json.loads(json.dumps(config))

    def save_config(self, user_id: int, config: Dict) -> Dict:
        payload = json.dumps(config, ensure_ascii=False)
        now = int(time.time())
        self.storage.execute(
            """INSERT INTO user_trade_configs(user_id, config_json, created_at, updated_at)
               VALUES(?, ?, ?, ?)
               ON DUPLICATE KEY UPDATE config_json=VALUES(config_json), updated_at=VALUES(updated_at)""",
            (int(user_id), payload, now, now),
        )
        return json.loads(payload)

    @staticmethod
    def _read_legacy_config() -> Optional[Dict]:
        root = Path(__file__).resolve().parents[1]
        data_dir = Path(os.getenv("AI_TRADER_DATA_DIR") or (root / "data"))
        path = data_dir / "trade_config.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

