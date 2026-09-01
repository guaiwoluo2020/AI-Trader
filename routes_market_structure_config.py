"""Administrator routes for market-structure configuration."""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends

from auth import AuthUser, require_admin
from mysql_repositories import RuntimeStateRepository


def create_market_structure_config_routes(market_defaults: Dict, plan_defaults: Dict) -> APIRouter:
    router = APIRouter()
    allowed = {**market_defaults, **plan_defaults}
    integer_keys = {
        "pivot_legs", "medium_pivot_legs", "large_pivot_legs", "break_confirm_bars",
        "retest_bars", "range_min_touches", "range_min_bars", "min_segment_bars",
        "trendline_min_touches", "trendline_min_bars",
    }

    @router.get("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def get_config(user: AuthUser = Depends(require_admin)):
        items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored = items[-1] if items else {}
        return {
            "status": "ok",
            "config": {**allowed, **{k: v for k, v in stored.items() if k in allowed}},
            "profiles": stored.get("profiles", []) if isinstance(stored, dict) else [],
            "setup_profiles": stored.get("setup_profiles", []) if isinstance(stored, dict) else [],
        }

    @router.put("/admin/market-structure/config", dependencies=[Depends(require_admin)])
    async def put_config(payload: Dict, user: AuthUser = Depends(require_admin)):
        cfg = dict(allowed)
        def normalize(item, *, setup=False):
            if not item.get("symbol") or not item.get("period") or (setup and not item.get("setup_type")):
                return None
            result = {"symbol": str(item["symbol"]).strip(), "period": str(item["period"]).upper()}
            if setup:
                result["setup_type"] = str(item["setup_type"]).strip().lower()
            for key in allowed:
                if key in item:
                    try:
                        value = float(item[key])
                        result[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                    except (TypeError, ValueError):
                        pass
            return result
        for key in allowed:
            if key in payload:
                try:
                    value = float(payload[key])
                    cfg[key] = max(1, int(value)) if key in integer_keys else max(0.0, value)
                except (TypeError, ValueError):
                    pass
        profiles = [x for x in (normalize(item) for item in (payload.get("profiles") or []) if isinstance(item, dict)) if x]
        setup_profiles = [x for x in (normalize(item, setup=True) for item in (payload.get("setup_profiles") or []) if isinstance(item, dict)) if x]
        cfg["profiles"] = profiles; cfg["setup_profiles"] = setup_profiles
        RuntimeStateRepository(0, 0).upsert_entity("market_structure_config", "default", cfg, status="active")
        return {"status": "ok", "config": {k: v for k, v in cfg.items() if k in allowed}, "profiles": profiles, "setup_profiles": setup_profiles}

    return router
