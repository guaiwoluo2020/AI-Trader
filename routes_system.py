#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统相关的接口路由
"""

from fastapi import APIRouter, Depends
from typing import Dict
from auth import require_auth
from server import TradingServer


def create_system_routes(server: TradingServer) -> APIRouter:
    """
    创建系统相关路由
    """
    router = APIRouter()
    protected_router = APIRouter(dependencies=[Depends(require_auth)])
    
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
        return {"status": "ok"}
    
    @protected_router.get("/status")
    async def get_status() -> Dict:
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
        total_instructions = sum(
            len(trades) for trades in server.trade_instructions.values()
        )
        symbols = list(server.trade_instructions.keys())
        
        return {
            "status": "ok",
            "pending_instructions": total_instructions,
            "statistics_records": len(server.statistics_history),
            "symbols": symbols
        }
    
    router.include_router(protected_router)
    return router
