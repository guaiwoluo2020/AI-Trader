#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统相关的接口路由
"""

from fastapi import APIRouter, Depends
from typing import Dict
from auth import AuthUser, require_auth
from status_payload import build_system_status_payload
from trading_engine_manager import TradingEngineManager


def create_system_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建系统相关路由
    """
    router = APIRouter()
    protected_router = APIRouter()
    
    @router.get("/health")
    async def health_check() -> Dict:
        """
        服务健康检查
        
        返回:
        ```json
        {
            "status": "ok"
        }
        ```
        """
        return {"status": "ok", "ok": True}
    
    @protected_router.get("/status")
    async def get_status(user: AuthUser = Depends(require_auth)) -> Dict:
        """
        获取服务状态
        
        返回:
        ```json
        {
            "status": "ok",
            "pending_instructions": 5,
            "statistics_records": 10,
            "symbols": ["EURUSD", "GBPUSD"]
        }
        ```
        """
        server = engine_manager.get_engine_for_user(user.user_id)
        pending_trades = server.get_all_pending_trades()
        total_instructions = sum(
            len(trades) for trades in pending_trades.values()
        )
        symbols = list(pending_trades.keys())
        latest_statistics = server.statistics_service.get_latest()

        return build_system_status_payload(
            pending_instructions=total_instructions,
            statistics_records=len(server.statistics_history),
            symbols=symbols,
            latest_statistics=latest_statistics,
        )
    
    router.include_router(protected_router)
    return router
