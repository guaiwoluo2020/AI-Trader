#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EA 相关的接口路由
"""

from fastapi import APIRouter, Query, Request
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
            ],
            "close_tickets": [123456, 789012],
            "pivot_alerts": [
                {
                    "type": "pivot_alert",
                    "symbol": "EURUSD",
                    "period": "H4",
                    "direction": "high",
                    "pivot_price": 1.0900,
                    "current_price": 1.0880,
                    "distance_pct": 0.18,
                    "message": "EURUSD H4 接近高点 1.0900"
                }
            ]
        }
        ```
        """
        result = server.get_trades_by_symbol(symbol, price)
        # 添加平仓指令
        result["close_tickets"] = server.get_close_position_instructions(symbol)

        # 打印完整返回数据用于调试
        import json
        print(f"[EA API] 返回给EA的数据: {json.dumps(result, ensure_ascii=False)}")

        return result

    @router.post("/send_statistics")
    async def send_statistics(request: Request) -> Dict:
        """
        接收 EA 发送的统计数据

        参数 (JSON):
        ```json
        {
            "symbol": "eurusd",
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
        ```

        返回:
        ```json
        {
            "status": "ok",
            "message": "统计数据已保存"
        }
        ```
        """
        # 获取原始请求体用于调试
        body = await request.body()
        print(f"[DEBUG] Raw body type: {type(body)}")
        print(f"[DEBUG] Raw body: {body}")
        print(f"[DEBUG] Raw body length: {len(body)}")

        # 尝试解析JSON
        import json
        try:
            data = await request.json()
            print(f"[DEBUG] Parsed JSON successfully: {data}")
            server.save_statistics(data)
            return {"status": "ok", "message": "统计数据已保存"}
        except Exception as e:
            print(f"[ERROR] Failed to parse JSON: {e}")
            print(f"[ERROR] Body as string: {body.decode('utf-8', errors='ignore')}")
            return {"status": "error", "message": str(e)}

    @router.post("/close_position")
    async def close_position(request: Request) -> Dict:
        """
        平仓指令

        请求体:
        ```json
        {
            "ticket": 123456,
            "symbol": "GOLD#"
        }
        ```

        返回:
        ```json
        {
            "status": "ok",
            "message": "平仓指令已添加"
        }
        ```
        """
        try:
            data = await request.json()
            ticket = data.get('ticket')
            symbol = data.get('symbol', '').upper()

            if not ticket:
                return {"status": "error", "message": "缺少订单号"}

            # 添加平仓指令到队列
            server.add_close_position_instruction(symbol, ticket)

            print(f"[EA API] 平仓指令已添加: {symbol} ticket={ticket}")
            return {"status": "ok", "message": "平仓指令已添加"}

        except Exception as e:
            print(f"[ERROR] close_position 异常: {str(e)}")
            return {"status": "error", "message": str(e)}

    return router