"""MySQL persistence for strategy-scoped, configured pivot points."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone

from ...models import PivotPoint


PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


def pivot_timestamp(value) -> int:
    if isinstance(value, (int, float)):
        number = int(value)
        return number // 1000 if number >= 10**12 else number
    if isinstance(value, datetime):
        return int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp())
    text = str(value or "").strip()
    if text.isdigit():
        return pivot_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp())
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return int(parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def pivot_config_fingerprint(period: str, params: dict) -> str:
    fields = {
        "period": str(period).upper(),
        "params": params or {},
    }
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calculate_pivot_score(
    age_bars: float, confirmation_count: int, half_life_bars: int,
) -> tuple[int, float]:
    """Return the shared live/replay score and its recency component."""
    age = max(0.0, float(age_bars))
    confirmations = max(1, int(confirmation_count or 1))
    half_life = max(1, int(half_life_bars or 1))
    recency = math.pow(0.5, age / half_life)
    score = min(100, round(recency * 60 + min(4, confirmations) * 10))
    return score, recency


class ConfiguredPivotRepository:
    def __init__(self, storage=None):
        if storage is None:
            from sqlite_storage import get_storage
            storage = get_storage()
        self.storage = storage

    def replace_scope(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, symbol: str, period: str,
        config_fingerprint: str, pivots: list, strength: int,
        max_age_bars: int, reference_time: int = 0,
    ) -> None:
        now = int(time.time())
        period_seconds = PERIOD_SECONDS.get(str(period).upper(), 300)
        rows = []
        for pivot in pivots:
            pivot_time = pivot_timestamp(pivot.timestamp)
            if not pivot_time:
                continue
            confirmed_at = pivot_time + int(strength) * period_seconds
            market_now = int(reference_time or now)
            age_seconds = max(0, market_now - confirmed_at)
            remaining_seconds = int(max_age_bars) * period_seconds - age_seconds
            if remaining_seconds < 0:
                continue
            # Broker Klines may use server-local time. Persist expiration on the
            # application clock so subsequent reads are timezone-independent.
            valid_until = now + remaining_seconds
            identity = "|".join(map(str, (
                user_id, account_id, strategy_id, signal_source_id,
                config_fingerprint, pivot_time, pivot.direction,
            )))
            rows.append((
                hashlib.sha256(identity.encode("utf-8")).hexdigest()[:64],
                int(user_id), int(account_id), str(strategy_id),
                str(signal_source_id), str(symbol), str(period).upper(),
                str(config_fingerprint), pivot_time, confirmed_at, valid_until,
                float(pivot.price), str(pivot.direction), int(strength),
                max(1, int(getattr(pivot, "confirmation_count", 1) or 1)),
                now, now,
            ))
        self.storage.execute(
            "DELETE FROM strategy_pivot_points WHERE user_id = ? AND account_id = ? "
            "AND strategy_id = ? AND signal_source_id = ?",
            (int(user_id), int(account_id), str(strategy_id), str(signal_source_id)),
        )
        self.storage.executemany(
            """
            INSERT INTO strategy_pivot_points(
                pivot_id, user_id, account_id, strategy_id, signal_source_id,
                symbol, period, config_fingerprint, pivot_time, confirmed_at,
                valid_until, price, direction, strength, confirmation_count,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def list_active(
        self, user_id: int, account_id: int, strategy_id: str,
        signal_source_id: str, config_fingerprint: str, now: int = None,
    ) -> list:
        rows = self.storage.fetchall(
            """
            SELECT * FROM strategy_pivot_points
            WHERE user_id = ? AND account_id = ? AND strategy_id = ?
              AND signal_source_id = ? AND config_fingerprint = ?
              AND valid_until >= ?
            ORDER BY pivot_time ASC
            """,
            (
                int(user_id), int(account_id), str(strategy_id),
                str(signal_source_id), str(config_fingerprint),
                int(now or time.time()),
            ),
        )
        result = []
        for row in rows:
            pivot = PivotPoint(
                symbol=row["symbol"], period=row["period"],
                timestamp=int(row["pivot_time"]), price=float(row["price"]),
                direction=row["direction"], strength=int(row["strength"]),
                confirmation_count=int(row.get("confirmation_count") or 1),
            )
            pivot.confirmed_at = int(row["confirmed_at"])
            pivot.valid_until = int(row["valid_until"])
            result.append(pivot)
        return result
