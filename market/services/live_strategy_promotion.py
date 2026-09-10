"""Helpers for promoting a paper strategy to selected live accounts."""

from __future__ import annotations

from typing import Dict, Iterable, List, Set


def _value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def promotion_candidate_accounts(
    accounts: Iterable[Dict], strategy_symbol: str,
    deployed_account_ids: Set[int],
) -> List[Dict]:
    """Return selectable live accounts for a strategy.

    The UI passes account snapshots, so this helper deliberately only applies
    deterministic eligibility rules.  Symbol discovery/normalization happens
    before this function; the final comparison remains exact here.
    """
    symbol = str(strategy_symbol or "").strip().upper()
    deployed = {int(value) for value in (deployed_account_ids or set())}
    result = []
    for account in accounts or []:
        account_id = int(_value(account, "account_id", 0) or 0)
        account_symbol = str(_value(account, "symbol", "") or "").strip().upper()
        if account_id <= 0 or account_id in deployed:
            continue
        if _value(account, "account_type") not in {"mt5", "ibkr"}:
            continue
        if account_symbol != symbol:
            continue
        if _value(account, "status") != "active":
            continue
        if not all(
            bool(_value(account, field, False))
            for field in ("connected", "enabled", "trading_enabled", "auto_trading_enabled")
        ):
            continue
        result.append(account)
    return result
