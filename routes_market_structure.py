"""Market-structure query routes."""
from __future__ import annotations

import time
from typing import Dict

from fastapi import APIRouter, Depends, Query

from auth import AuthUser, require_auth
from mysql_repositories import RuntimeStateRepository, get_storage
from market.services.market_structure_engine_v2 import analyze_incremental, DEFAULT_CONFIG
from market.services.market_structure_snapshot_store import current_path, load_current, save_checkpoint
from market.services.market_structure_engine_v2 import restore_snapshot


def create_market_structure_routes(engine_manager, account_repo, plan_defaults: Dict) -> APIRouter:
    router = APIRouter()

    @router.get("/market/structure/{symbol}", dependencies=[Depends(require_auth)])
    async def get_market_structure(symbol: str, period: str = Query("M5"), count: int = Query(600), user: AuthUser = Depends(require_auth)):
        period = period.upper(); limit = min(1000, max(50, count))
        engine = engine_manager.get_market_engine(user.user_id)
        rows = engine.kline_service.get_klines(symbol, period, limit)
        if not rows:
            historical = get_storage().fetchall(
                """SELECT timestamp,timestamp_utc,broker_utc_offset_seconds,
                   open_price AS open,high_price AS high,low_price AS low,
                   close_price AS close,volume FROM historical_klines
                   WHERE user_id=? AND account_id=0 AND symbol=? AND period=?
                   AND (timestamp_utc>=? OR (timestamp_utc=0 AND timestamp>=?))
                   ORDER BY COALESCE(NULLIF(timestamp_utc,0),timestamp) DESC LIMIT ?""",
                (user.user_id, symbol, period, int(time.time()) - 7*86400,
                 int(time.time()) - 7*86400, limit),
            )
            rows = [dict(row) for row in reversed(historical or [])]
        account_id = int(getattr(engine, "account_id", 0) or 0)
        previous = load_current(user.user_id, account_id, symbol, period)
        if previous:
            restore_snapshot(previous)
        cfg = dict(DEFAULT_CONFIG)
        stored_items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored = stored_items[-1] if stored_items else {}
        cfg.update({k: v for k, v in (stored or {}).items() if k in cfg})
        for profile in (stored or {}).get("profiles", []) if isinstance(stored, dict) else []:
            if str(profile.get("symbol") or "").upper() == symbol.upper() and str(profile.get("period") or "").upper() == period:
                cfg.update({k: v for k, v in profile.items() if k in cfg})
                break
        result = analyze_incremental(symbol, period, rows, cfg)
        try:
            save_checkpoint(result, user.user_id, account_id)
        except Exception:
            pass
        RuntimeStateRepository(user.user_id, account_id).upsert_entity(
            "market_structure", f"{symbol}::{period}", {
                "symbol": symbol, "period": period,
                "engine_version": result.get("engine_version"),
                "snapshot_path": str(current_path(user.user_id, account_id, symbol, period)),
                "last_bar_time": result.get("last_bar_time"),
                "config_signature": result.get("config_signature"),
                "updated_at": result.get("analyzed_at"),
            }, symbol=symbol, status=result.get("current_state", "undetermined"),
        )
        return {"status": "ok", "data": result}

    return router
