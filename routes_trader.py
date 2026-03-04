#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易员相关的接口路由
"""

from fastapi import APIRouter, Query
from typing import Optional, List, Dict
from models import TradeInstruction
from server import TradingServer


def create_trader_routes(server: TradingServer) -> APIRouter:
    """
    创建交易员相关路由
    """
    router = APIRouter()
    
    @router.post("/send_trade_instructions")
    async def send_trade_instructions(instructions: List[TradeInstruction]) -> Dict:
        """
        交易员发送交易指令
        
        参数 (JSON):
        ```json
        [
            {
                "symbol": "EURUSD",
                "action": "b",
                "mount": 0.1,
                "price": 1.0850,
                "sl": 1.0800,
                "tp": 1.0900
            },
            {
                "symbol": "GBPUSD",
                "action": "s",
                "mount": 0.2,
                "price": 1.2700,
                "sl": null,
                "tp": null
            }
        ]
        ```
        
        说明:
        - action: 'b'(买入) 或 's'(卖出)
        - mount: 交易手数
        - price: 交易指令的目标价格（用于 Python 端过滤）
        - sl: 止损价格（可选，默认 0.0）
        - tp: 获利价格（可选，默认 0.005）
        
        返回:
        ```json
        {
            "status": "ok",
            "message": "已添加 2 条交易指令"
        }
        ```
        """
        result = server.add_trade_instruction(instructions)
        added = result.get("added", 0)
        rejected = result.get("rejected", 0)
        msg = f"已添加 {added} 条交易指令"
        if rejected > 0:
            msg += f"，{rejected} 条因价格不合法被拒绝"
        return {
            "status": "ok",
            "message": msg
        }
    
    @router.get("/query_pending_trades")
    async def query_pending_trades(symbol: Optional[str] = Query(None)) -> Dict:
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
        all_trades = server.get_all_pending_trades()
        if symbol:
            symbol = symbol.upper()
            result = {symbol: all_trades.get(symbol, [])}
        else:
            result = all_trades
        
        return {"pending_trades": result}
    
    @router.get("/query_statistics")
    async def query_statistics(
        count: int = Query(10, description="获取最新的统计数据条数（最多10条）")
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
        stats = server.get_latest_statistics(count)
        return {"statistics": stats}
    
    @router.delete("/clear_trades")
    async def clear_trades(symbol: Optional[str] = Query(None)) -> Dict:
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
        count = server.clear_trades(symbol)
        return {
            "status": "ok",
            "message": f"已清空 {count} 条交易指令"
        }
    
    return router
