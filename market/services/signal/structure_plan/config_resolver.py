"""Resolve the four-layer market-structure configuration model."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Iterable

_GROUPS = {"structure", "hierarchy", "structure_hierarchy", "primary_structure_rules",
           "pattern_rules", "event_rules", "execution", "plan", "runtime"}


def decode(row: Dict | None) -> Dict:
    if not row:
        return {}
    value = row.get("config_json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value if isinstance(value, dict) else {}


def _leaves(layer: Any) -> Iterable[tuple[str, Any]]:
    if not isinstance(layer, dict):
        return
    for key, value in layer.items():
        if key == "setup_defaults":
            yield key, value
        elif isinstance(value, dict):
            yield from _leaves(value)
        else:
            yield key, value


def _runtime_values(layer: Dict, allowed: set[str]) -> Dict:
    return {key: value for key, value in _leaves(layer) if key in allowed}


def _merge(target: Dict, layer: Dict, allowed: set[str], *, inherit_empty_lists=False) -> None:
    for key, value in _runtime_values(layer, allowed).items():
        if (inherit_empty_lists and key in {"allowed_directions", "blocked_hours", "blocked_setups"}
                and isinstance(value, list) and not value):
            continue
        target[key] = value


def _setup_defaults(base: Dict, setup: str) -> Dict:
    values = base.get("setup_defaults")
    if not isinstance(values, dict):
        return {}
    item = values.get(setup)
    return item if isinstance(item, dict) else {}


def _norm_symbol(value) -> str:
    return str(value or "").strip().upper()


def _norm_period(value) -> str:
    return str(value or "").strip().upper()


def select_overlay_rows(rows, symbol: str, period: str):
    """Split overlays into */period, symbol/*, and symbol/period."""
    wanted_symbol = _norm_symbol(symbol)
    wanted_period = _norm_period(period)
    period_wide = symbol_wide = exact = None
    for row in rows or []:
        row_symbol = _norm_symbol(row.get("symbol"))
        row_period = _norm_period(row.get("period"))
        if row_symbol == "*" and row_period == wanted_period:
            period_wide = row
        elif row_symbol == wanted_symbol and row_period == "*":
            symbol_wide = row
        elif row_symbol == wanted_symbol and row_period == wanted_period:
            exact = row
    return period_wide, symbol_wide, exact


def _rows(storage, table: str, symbol: str, period: str, setup: str = ""):
    wanted_symbol = _norm_symbol(symbol)
    wanted_period = _norm_period(period)
    if table == "structure_symbol_period_configs":
        return storage.fetchall(
            "SELECT symbol, period, config_json FROM structure_symbol_period_configs "
            "WHERE user_id=0 AND status='active' AND symbol IN (?, '*') AND period IN (?, '*')",
            (wanted_symbol, wanted_period),
        )
    return storage.fetchall(
        "SELECT symbol, period, config_json FROM structure_setup_configs "
        "WHERE user_id=0 AND status='active' AND symbol IN (?, '*') AND period IN (?, '*') "
        "AND setup_type=?",
        (wanted_symbol, wanted_period, setup),
    )


def resolve(symbol: str, period: str, setup_type: str, defaults: Dict,
            repository_factory: Callable[[], object] | None = None) -> Dict:
    """Resolve public structure then symbol/period overlays.

    ``repository_factory`` is retained for caller compatibility, but legacy
    runtime-state configuration is deliberately no longer consulted.
    """
    config = dict(defaults)
    allowed = set(defaults)
    wanted_symbol = str(symbol or "").strip().upper()
    wanted_period = str(period or "").strip().upper()
    wanted_setup = str(setup_type or "").strip().lower()
    try:
        from mysql_repositories import get_storage
        storage = get_storage()
        default_row = storage.fetchone(
            "SELECT config_json FROM structure_default_configs WHERE user_id=0 AND status='active'"
        )
        symbol_rows = _rows(storage, "structure_symbol_period_configs", wanted_symbol, wanted_period)
        base = decode(default_row)
        period_wide_row, symbol_wide_row, exact_row = select_overlay_rows(
            symbol_rows, wanted_symbol, wanted_period,
        )
        period_wide, symbol_wide, profile = (
            decode(period_wide_row), decode(symbol_wide_row), decode(exact_row),
        )
        _merge(config, base, allowed)
        _merge(config, period_wide, allowed, inherit_empty_lists=True)
        _merge(config, symbol_wide, allowed, inherit_empty_lists=True)
        _merge(config, profile, allowed, inherit_empty_lists=True)
        config["_structure_layers"] = {
            "default": base,
            "period_wide": period_wide,
            "symbol_wide": symbol_wide,
            "symbol_period": profile,
            "setup_default": {},
            "setup": {},
        }
        if setup_type == "__builder__":
            config["_setup_profiles"] = []
    except Exception as exc:
        print(f"[StructurePlan] 结构配置读取失败，使用公共默认值: {exc}")
        if setup_type == "__builder__":
            config["_setup_profiles"] = []
    return config
