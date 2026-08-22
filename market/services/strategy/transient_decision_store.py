"""In-process aggregation for high-frequency no-action strategy evaluations."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from threading import RLock
from typing import Dict, List, Tuple

from ...models import TradingDecision


class TransientDecisionStore:
    """Keep waiting decisions out of persistent audit history.

    A quote can arrive many times while a strategy is still waiting for the
    same condition.  One current entry is enough for the execution UI; an
    aggregated count makes that state observable without consuming database
    history reserved for execution and risk events.
    """

    def __init__(self, max_entries_per_account: int = 50):
        self._entries: Dict[Tuple[int, int], OrderedDict[str, TradingDecision]] = {}
        self._max_entries_per_account = max_entries_per_account
        self._lock = RLock()

    @staticmethod
    def _key(decision: TradingDecision) -> str:
        """One waiting aggregate exists per deployed strategy and symbol.

        The reason or proposed direction can fluctuate on every quote.  They
        describe the current observation, not a new operational event.
        """
        return "|".join((
            str(decision.strategy_id or ""),
            str(decision.symbol or "").upper(),
            str(decision.execution_mode or ""),
        ))

    def record(
        self, user_id: int, account_id: int, decision: TradingDecision,
    ) -> TradingDecision:
        """Merge a no-action decision and return the entry shown by the UI."""
        now = decision.created_at or datetime.now()
        scope = (int(user_id or 0), int(account_id or 0))
        key = self._key(decision)
        with self._lock:
            entries = self._entries.setdefault(scope, OrderedDict())
            existing = entries.get(key)
            if existing is None:
                decision.observation_count = 1
                decision.first_observed_at = now
                decision.last_observed_at = now
                entries[key] = decision
                while len(entries) > self._max_entries_per_account:
                    entries.popitem(last=False)
                return decision

            existing.observation_count += 1
            existing.last_observed_at = now
            existing.created_at = now
            existing.signals = decision.signals
            existing.signal_summary = decision.signal_summary
            existing.confidence_score = decision.confidence_score
            existing.decision_reason = decision.decision_reason
            existing.status = decision.status
            entries.move_to_end(key)
            return existing

    def clear_for_strategy(
        self, user_id: int, account_id: int, strategy_id: str, symbol: str = "",
    ) -> None:
        """Start a new waiting interval after an actionable strategy event."""
        scope = (int(user_id or 0), int(account_id or 0))
        expected_strategy = str(strategy_id or "")
        expected_symbol = str(symbol or "").upper()
        with self._lock:
            entries = self._entries.get(scope)
            if not entries:
                return
            for key, item in list(entries.items()):
                if item.strategy_id != expected_strategy:
                    continue
                if expected_symbol and str(item.symbol or "").upper() != expected_symbol:
                    continue
                entries.pop(key, None)

    def list(
        self, user_id: int, account_id: int,
    ) -> List[TradingDecision]:
        scope = (int(user_id or 0), int(account_id or 0))
        with self._lock:
            return list(self._entries.get(scope, {}).values())


transient_decision_store = TransientDecisionStore()
