#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易员相关的接口路由
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional, Dict
from auth import AuthUser, require_auth
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine
from mysql_repositories import TradeExecutionRepository, TradingAccountRepository


def create_trader_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建交易员相关路由
    """
    router = APIRouter()
    account_repository = TradingAccountRepository()

    @router.get("/dashboard/overview")
    async def get_dashboard_overview(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account, server = resolve_web_engine(engine_manager, user, account_id)
        account = account_repository.get_by_id(user.user_id, account.account_id)
        return server.get_dashboard_overview(account)
    
    @router.get("/query_pending_trades")
    async def query_pending_trades(
        symbol: Optional[str] = Query(None),
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        查询待执行的交易指令
        
        参数:
        - symbol: 可选，指定交易品种；不提供则返回所有
        
        返回:
        ```json
        {
            "EURUSD": [
                {
                    "symbol": "eurusd",
                    "action": "b",
                    "mount": 0.1,
                    "price": 1.0850,
                    "sl": 1.0800,
                    "tp": 1.0900
                }
            ],
            "GBPUSD": [...]
        }
        ```
        """
        _, server = resolve_web_engine(engine_manager, user, account_id)
        all_trades = server.get_all_pending_trades()
        if symbol:
            result = {symbol: all_trades.get(symbol, [])}
        else:
            result = all_trades
        
        return {"pending_trades": result}
    
    @router.get("/query_statistics")
    async def query_statistics(
        count: int = Query(10, description="获取最新的统计数据条数（最多10条）"),
        symbol: Optional[str] = Query(None, description="交易品种"),
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        查询统计数据历史
        
        参数:
        - count: 获取最新的条数（默认10，最多10）
        
        返回:
        ```json
        {
            "statistics": [
                {
                    "timestamp": "2024-01-15 14:30:45",
                    "tickCount": 1234,
                    "bidPrice": 1.0850,
                    "askPrice": 1.0852,
                    "balance": 10000.00,
                    "equity": 10500.50,
                    "marginLevel": 150.0,
                    "positions": [],
                    "trades": []
                }
            ]
        }
        ```
        """
        count = min(count, 10)
        _, server = resolve_web_engine(engine_manager, user, account_id)
        stats = server.get_latest_statistics(count, symbol)
        available_symbols = server.statistics_store.get_status()["symbols"]
        return {
            "statistics": stats,
            "symbols": sorted(available_symbols),
            "selected_symbol": symbol,
        }
    
    @router.delete("/clear_trades")
    async def clear_trades(
        symbol: Optional[str] = Query(None),
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        清空交易指令
        
        参数:
        - symbol: 可选，指定交易品种；不提供则清空所有
        
        返回:
        ```json
        {
            "status": "ok",
            "message": "已清空 2 条交易指令"
        }
        ```
        """
        _, server = resolve_web_engine(engine_manager, user, account_id)
        count = server.clear_trades(symbol)
        return {
            "status": "ok",
            "message": f"已清空 {count} 条交易指令"
        }

    @router.get("/trade_executions")
    async def list_trade_executions(
        account_id: Optional[int] = Query(None),
        count: int = Query(100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account, _ = resolve_web_engine(engine_manager, user, account_id)
        reports = TradeExecutionRepository().list_for_account(
            user.user_id, account.account_id, count
        )
        return {"status": "ok", "count": len(reports), "reports": reports}
    
    return router
