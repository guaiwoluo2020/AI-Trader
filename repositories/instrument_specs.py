"""Account-scoped broker instrument volume specifications."""
from __future__ import annotations

import math
import time
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Optional

from mysql_repositories import get_storage


DEFAULT_SPEC = {
    "min_volume": 0.01,
    "volume_step": 0.01,
    "max_volume": 100.0,
    "volume_digits": 2,
    "contract_size": 1.0,
    "source": "default",
}


class InstrumentSpecRepository:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    def get(self, account_id: int, symbol: str) -> Dict:
        row = self.storage.fetchone(
            "SELECT account_id,symbol,min_volume,volume_step,max_volume,"
            "volume_digits,contract_size,source,updated_at "
            "FROM account_instrument_specs WHERE account_id=? AND symbol=?",
            (int(account_id or 0), str(symbol or "").strip()),
        )
        result = dict(DEFAULT_SPEC)
        result.update({"account_id": int(account_id or 0), "symbol": str(symbol or "").strip()})
        if row:
            result.update(dict(row))
        return result

    def upsert(self, account_id: int, symbol: str, payload: Dict) -> Dict:
        account_id = int(account_id or 0)
        symbol = str(symbol or "").strip()
        if account_id <= 0 or not symbol:
            raise ValueError("account_id 和 symbol 不能为空")
        spec = dict(DEFAULT_SPEC)
        spec.update(payload or {})
        min_volume = max(0.00000001, float(spec.get("min_volume") or 0.01))
        step = max(0.00000001, float(spec.get("volume_step") or min_volume))
        max_volume = max(min_volume, float(spec.get("max_volume") or 100.0))
        digits = max(0, min(8, int(spec.get("volume_digits") or 2)))
        contract = max(0.00000001, float(spec.get("contract_size") or 1.0))
        source = str(spec.get("source") or "broker")[:32]
        now = int(time.time())
        self.storage.execute(
            "INSERT INTO account_instrument_specs "
            "(account_id,symbol,min_volume,volume_step,max_volume,volume_digits,contract_size,source,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON DUPLICATE KEY UPDATE min_volume=VALUES(min_volume),volume_step=VALUES(volume_step),"
            "max_volume=VALUES(max_volume),volume_digits=VALUES(volume_digits),"
            "contract_size=VALUES(contract_size),source=VALUES(source),updated_at=VALUES(updated_at)",
            (account_id, symbol, min_volume, step, max_volume, digits, contract, source, now),
        )
        return self.get(account_id, symbol)


def normalize_volume(volume: float, spec: Optional[Dict] = None, *, opening: bool = True,
                     current_volume: Optional[float] = None) -> float:
    """Normalize an opening/closing quantity to broker min/step rules.

    Opening quantities are rounded down to avoid exceeding risk. Closing quantities
    are also rounded down; if the requested close would leave an untradeable residue,
    the whole remaining position is returned.
    """
    spec = {**DEFAULT_SPEC, **(spec or {})}
    value = max(0.0, float(volume or 0.0))
    minimum = max(0.00000001, float(spec.get("min_volume") or 0.01))
    step = max(0.00000001, float(spec.get("volume_step") or minimum))
    maximum = max(minimum, float(spec.get("max_volume") or 100.0))
    if current_volume is not None:
        current = max(0.0, float(current_volume))
        if value >= current - step * 0.5:
            return round(current, int(spec.get("volume_digits") or 2))
    units = math.floor((value + 1e-12) / step)
    normalized = units * step
    if opening:
        normalized = max(minimum, normalized)
        normalized = min(maximum, normalized)
    elif normalized < minimum:
        return round(current, int(spec.get("volume_digits") or 2)) if current_volume is not None else 0.0
    digits = max(0, min(8, int(spec.get("volume_digits") or 2)))
    return round(normalized, digits)
