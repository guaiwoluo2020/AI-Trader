"""Resolve market-layer structure-plan configuration."""
from __future__ import annotations

from typing import Callable, Dict, Iterable


def resolve(
    symbol: str,
    period: str,
    setup_type: str,
    defaults: Dict,
    repository_factory: Callable[[], object],
) -> Dict:
    """Apply defaults, symbol/period overrides, then setup override.

    Storage is injected to keep this resolver deterministic in tests and free
    of a hard dependency on the runtime repository implementation.
    """
    config = dict(defaults)
    try:
        stored_items = repository_factory().list_entities("market_structure_config")
        stored = stored_items[-1] if stored_items else {}
        allowed = set(defaults)
        if not isinstance(stored, dict):
            stored = {}
        config.update({key: value for key, value in stored.items() if key in allowed})
        wanted_symbol = str(symbol or "").upper()
        wanted_period = str(period or "").upper()
        profiles = stored.get("profiles") or []
        for profile in profiles:
            if (str(profile.get("symbol") or "").upper() == wanted_symbol
                    and str(profile.get("period") or "").upper() == wanted_period):
                config.update({key: value for key, value in profile.items() if key in allowed})
                break
        matching = [
            profile for profile in (stored.get("setup_profiles") or [])
            if (str(profile.get("symbol") or "").upper() == wanted_symbol
                and str(profile.get("period") or "").upper() == wanted_period)
        ]
        wanted_setup = str(setup_type or "").strip().lower()
        if wanted_setup:
            for profile in matching:
                if str(profile.get("setup_type") or "").strip().lower() == wanted_setup:
                    config.update({key: value for key, value in profile.items() if key in allowed})
                    break
        if setup_type == "__builder__":
            config["_setup_profiles"] = matching
    except Exception as exc:
        print(f"[StructurePlan] 公共计划配置读取失败，使用默认值: {exc}")
    return config
