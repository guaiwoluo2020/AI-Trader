"""Read-only structure review and Pivot routes."""
from __future__ import annotations

from typing import Dict, Optional
from fastapi import APIRouter, Depends, Query
from auth import AuthUser, require_auth


def create_structure_read_routes(engine_manager) -> APIRouter:
    router = APIRouter()

    @router.get("/market/structure/{symbol}/signal-reviews", dependencies=[Depends(require_auth)])
    async def get_structure_signal_reviews(symbol: str, period: str = Query("M5"), limit: int = Query(30, ge=1, le=90), user: AuthUser = Depends(require_auth)) -> Dict:
        period = period.upper()
        return {"status": "ok", "symbol": symbol, "period": period, "reviews": engine_manager.daily_reviews.list_structure_reviews(user.user_id, symbol, period, limit)}

    @router.get("/market/pivots/{symbol}", dependencies=[Depends(require_auth)])
    async def get_pivots(symbol: str, period: Optional[str] = Query(None), direction: Optional[str] = Query(None), count: int = Query(10, ge=1, le=10), user: AuthUser = Depends(require_auth)) -> Dict:
        service = engine_manager.get_engine_for_user(user.user_id).pivot_service
        if period:
            period = period.upper(); values = service.get_pivots(symbol, period, direction, count)
            return {"status": "ok", "symbol": symbol, "period": period, "count": len(values), "data": values}
        result = {}
        for item in ("H4", "H1", "M15", "M5", "M1"):
            values = service.get_pivots(symbol, item, direction, count)
            if values: result[item] = values
        return {"status": "ok", "symbol": symbol, "data": result}

    return router
