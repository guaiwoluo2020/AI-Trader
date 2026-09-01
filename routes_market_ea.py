"""EA market-data routes extracted from the general market router."""
from __future__ import annotations

from typing import Callable, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from ea_auth import EAIdentity, require_ea_auth


def create_ea_cursor_routes(engine_manager, market_source_policy, restore_kline_memory: Callable,
                            storage_factory: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/ea/kline_cursor/{period}")
    async def get_ea_kline_cursor(
        period: str, symbol: str = Query(...),
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        period = period.upper()
        if period not in {"H4", "H1", "M15", "M5", "M1"}:
            raise HTTPException(status_code=400, detail=f"不支持的周期: {period}")
        market_policy = market_source_policy.resolve(identity.user_id, identity.account_id, symbol)
        if market_policy.get("mode") == "blocked":
            raise HTTPException(status_code=409, detail=market_policy)
        engine = engine_manager.get_engine_for_ea(identity)
        restored_count = restore_kline_memory(identity, symbol, period, engine.kline_service)
        row = storage_factory().fetchone(
            """SELECT MAX(timestamp) AS last_bar_time, COUNT(*) AS bar_count
               FROM historical_klines
               WHERE user_id = ? AND account_id = ? AND symbol = ? AND period = ?""",
            (identity.user_id, 0, symbol, period),
        )
        return {
            "status": "ok", "symbol": symbol, "period": period,
            "server_last_bar_time": int(row["last_bar_time"]) if row and row["last_bar_time"] else 0,
            "stored_bar_count": int(row["bar_count"] or 0) if row else 0,
            "memory_restored_count": restored_count,
            "market_source": market_policy,
        }

    return router
