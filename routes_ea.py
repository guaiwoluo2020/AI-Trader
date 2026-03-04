#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EA 相关的接口路由
"""

from fastapi import APIRouter, Query
from typing import Optional, List, Dict
from models import TradeInstruction
from server import TradingServer


def create_ea_routes(server: TradingServer) -> APIRouter:
    """
    创建 EA 相关路由
    """
    router = APIRouter()
    
    @router.get("/get_trades")
    async def get_trades(
        symbol: str = Query(..., description="交易品种"),
        price: Optional[float] = Query(None, description="当前中间价")
    ) -> Dict:
        """
        获取指定SYMBOL的交易指令
        
        参数:
        - symbol: 交易品种 (e.g., "EURUSD")
        - price: 当前中间价格，用于条件过滤
        
        返回:
        ```json
        {
            "trades": [
                {
                    "symbol": "eurusd",
                    "action": "b",
                    "mount": 0.1,
                    "price": 1.0850,
                    "sl": 1.0800,
                    "tp": 1.0900
                }
            ]
        }
        ```
        """
        trades = server.get_trades_by_symbol(symbol, price)
        return {"trades": trades}
    
    @router.post("/send_statistics")
    async def send_statistics(data: dict) -> Dict:
        """
        接收 EA 发送的统计数据
        
        参数 (JSON):
        ```json
        {
            "timestamp": "2024-01-15 14:30:45",
            "tickCount": 1234,
            "bidPrice": 1.0850,
            "askPrice": 1.0852,
            "balance": 10000.00,
            "equity": 10500.50,
            "marginLevel": 150.0,
            "positions": [
                {
                    "symbol": "eurusd",
                    "tickets": 123456,
                    "type": "buy",
                    "volume": 0.1,
                    "openPrice": 1.0800,
                    "takeProfit": 1.0900,
                    "stopLoss": 1.0750,
                    "profit": 50.00
                }
            ],
            "trades": [
                {
                    "tickets": 789012,
                    "symbol": "eurusd",
                    "action": "buy",
                    "openPrice": 1.0800,
                    "volume": 0.1
                }
            ]
        }
        ```
        
        返回:
        ```json
        {
            "status": "ok",
            "message": "统计数据已保存"
        }
        ```
        """
        server.save_statistics(data)
        return {"status": "ok", "message": "统计数据已保存"}
    
    return router
