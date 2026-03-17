#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位管理相关的接口路由
"""

from fastapi import APIRouter, Request
from typing import Dict, Optional
import json

from market.position_store import get_position_store
from market.system_log import get_system_log


def create_position_routes() -> APIRouter:
    """
    创建仓位管理路由
    """
    router = APIRouter()
    position_store = get_position_store()

    @router.post("/ea/positions")
    async def receive_positions(request: Request) -> Dict:
        """
        EA推送持仓数据

        请求体:
        ```json
        {
            "symbol": "BTCUSD#",
            "positions": [
                {
                    "ticket": 123456,
                    "volume": 0.01,
                    "priceOpen": 70000.00,
                    "type": "BUY",
                    "profit": 100.50,
                    "distanceSL": 50.0,
                    "distanceTP": 100.0
                }
            ]
        }
        ```
        """
        try:
            data = await request.json()
            symbol = data.get('symbol', '')
            positions = data.get('positions', [])

            if not symbol:
                return {"status": "error", "message": "缺少品种信息"}

            result = position_store.update_positions(symbol, positions)

            # 记录日志
            if positions:
                system_log = get_system_log()
                system_log.add_log(
                    "position_update",
                    {
                        "count": len(positions),
                        "closed": result.get("closed", 0)
                    },
                    symbol=symbol,
                    message=f"更新 {len(positions)} 个持仓"
                )

            return result

        except Exception as e:
            print(f"[PositionAPI] 接收持仓数据异常: {e}")
            return {"status": "error", "message": str(e)}

    @router.get("/positions")
    async def get_positions(symbol: Optional[str] = None) -> Dict:
        """
        获取持仓数据

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        positions = position_store.get_positions(symbol)
        return {
            "status": "ok",
            "count": len(positions),
            "positions": positions
        }

    @router.get("/positions/summary")
    async def get_positions_summary(symbol: Optional[str] = None) -> Dict:
        """
        获取持仓汇总

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        summary = position_store.get_summary(symbol)
        return {
            "status": "ok",
            **summary
        }

    @router.get("/positions/{symbol}/{ticket}")
    async def get_position(symbol: str, ticket: int) -> Dict:
        """
        获取单个持仓详情
        """
        position = position_store.get_position(symbol, ticket)
        if not position:
            return {"status": "error", "message": "持仓不存在"}
        return {
            "status": "ok",
            "position": position
        }

    # ==================== 交易历史接口 ====================

    @router.get("/trade_history")
    async def get_trade_history() -> Dict:
        """
        获取交易历史数据
        """
        from market.trade_history_store import get_trade_history_store
        store = get_trade_history_store()

        deals = store.get_all_deals()
        statistics = store.get_statistics()

        return {
            "status": "ok",
            "deals": deals,
            "statistics": statistics
        }

    @router.get("/trade_history/statistics")
    async def get_trade_history_statistics() -> Dict:
        """
        获取交易历史统计
        """
        from market.trade_history_store import get_trade_history_store
        store = get_trade_history_store()

        statistics = store.get_statistics()

        return {
            "status": "ok",
            **statistics
        }

    return router