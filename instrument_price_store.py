"""Short-lived in-memory quote cache for administrator instrument inspection."""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time
from typing import Deque, Dict, Optional, Tuple

from mysql_repositories import TradingAccountRepository
from repositories.platform import PlatformInstrumentMappingRepository


class InstrumentPriceStore:
    """Keep only the latest few quotes; never persist or compare them automatically."""

    MAX_SAMPLES = 5
    WINDOW_SECONDS = 60.0

    def __init__(self):
        self.accounts = TradingAccountRepository()
        self.mappings = PlatformInstrumentMappingRepository()
        self._samples: Dict[Tuple[str, str], Deque[Dict]] = defaultdict(deque)
        self._lock = threading.RLock()

    @staticmethod
    def _normalize(value: str) -> str:
        return str(value or "").strip().upper()

    def record(
        self, user_id: int, account_id: int, symbol: str,
        bid: float, ask: Optional[float],
    ) -> None:
        symbol = self._normalize(symbol)
        if not symbol or bid <= 0:
            return
        account = self.accounts.get_by_id(int(user_id), int(account_id))
        if account is None:
            return
        server = str(account.mt5_server or "").strip()
        broker = self.mappings.broker_name_from_server(server)
        if not broker:
            return
        now = time.time()
        mid = (float(bid) + float(ask)) / 2 if ask is not None and ask > 0 else float(bid)
        item = {
            "timestamp": int(now),
            "bid": float(bid),
            "ask": float(ask) if ask is not None and ask > 0 else None,
            "mid": mid,
        }
        with self._lock:
            samples = self._samples[(broker.upper(), symbol)]
            samples.append(item)
            self._trim(samples, now)
            while len(samples) > self.MAX_SAMPLES:
                samples.popleft()

    def _trim(self, samples: Deque[Dict], now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while samples and samples[0]["timestamp"] < cutoff:
            samples.popleft()

    def list(self) -> list:
        now = time.time()
        try:
            mapping_rows = self.mappings.list(enabled_only=True)
        except Exception:
            # Quote inspection is auxiliary; a transient DB pool/query issue
            # must not turn the admin settings page into a failed request.
            mapping_rows = []
        mapping_by_key = {
            (
                self._normalize(item.get("effective_broker_name") or item.get("broker_name")),
                self._normalize(item.get("native_symbol")),
            ): item
            for item in mapping_rows
        }
        with self._lock:
            result = []
            for (broker, symbol), samples in list(self._samples.items()):
                self._trim(samples, now)
                if not samples:
                    self._samples.pop((broker, symbol), None)
                    continue
                mapping = mapping_by_key.get((broker, symbol))
                result.append({
                    "broker_name": broker,
                    "symbol": symbol,
                    "sample_count": len(samples),
                    "latest": dict(samples[-1]),
                    "prices": [dict(item) for item in reversed(samples)],
                    "mapped": bool(mapping),
                    "mapping_group": mapping.get("mapping_group", "") if mapping else "",
                    "mapping_id": mapping.get("mapping_id", "") if mapping else "",
                })
            return sorted(
                result,
                key=lambda item: (not item["mapped"], item["broker_name"], item["symbol"]),
            )


_STORE: Optional[InstrumentPriceStore] = None
_STORE_LOCK = threading.Lock()


def get_instrument_price_store() -> InstrumentPriceStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = InstrumentPriceStore()
        return _STORE
