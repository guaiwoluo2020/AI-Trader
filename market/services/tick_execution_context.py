"""Immutable signal snapshot shared by every execution account for one Tick."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, Iterable, Mapping, Tuple


@dataclass(frozen=True)
class TickExecutionContext:
    tick_id: str
    user_id: int
    source_account_id: int
    symbol: str
    price: float
    captured_at: float
    _signals_by_strategy: Mapping[str, Tuple[object, ...]]

    @classmethod
    def create(
        cls, *, user_id: int, source_account_id: int, symbol: str,
        price: float, captured_at: float,
        signals_by_strategy: Dict[str, Iterable], tick_id: str = "",
    ) -> "TickExecutionContext":
        normalized_symbol = str(symbol or "").upper()
        stable_id = str(tick_id or hashlib.sha256(
            f"{int(user_id)}:{int(source_account_id)}:{normalized_symbol}:"
            f"{float(price):.12g}:{float(captured_at):.6f}".encode("utf-8")
        ).hexdigest()[:32])
        frozen = {
            str(strategy_id): tuple(copy.deepcopy(list(signals or [])))
            for strategy_id, signals in (signals_by_strategy or {}).items()
        }
        return cls(
            tick_id=stable_id,
            user_id=int(user_id),
            source_account_id=int(source_account_id),
            symbol=normalized_symbol,
            price=float(price),
            captured_at=float(captured_at),
            _signals_by_strategy=MappingProxyType(frozen),
        )

    def has_strategy(self, strategy_id: str) -> bool:
        return str(strategy_id) in self._signals_by_strategy

    def signals_for(self, strategy_id: str):
        return copy.deepcopy(list(self._signals_by_strategy.get(str(strategy_id), ())))

    def strategy_ids(self):
        return tuple(self._signals_by_strategy.keys())

    def to_audit_dict(self) -> Dict:
        return {
            "tick_id": self.tick_id,
            "user_id": self.user_id,
            "source_account_id": self.source_account_id,
            "symbol": self.symbol,
            "price": self.price,
            "captured_at": self.captured_at,
            "strategy_ids": list(self.strategy_ids()),
        }
