#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位管理相关的接口路由
"""

from fastapi import APIRouter, Request
from typing import Dict, Optional

from market.system_log import get_system_log


def create_position_routes(trading_server=None) -> APIRouter:
    """
    创建仓位管理路由

    Args:
        trading_server: TradingServer 实例
    """
    router = APIRouter()

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

            # 使用新的持仓服务
            result = trading_server.position_service.update_positions(symbol, positions)

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
        positions = trading_server.position_service.get_positions(symbol)
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
        summary = trading_server.position_service.get_summary(symbol)
        return {
            "status": "ok",
            **summary
        }

    @router.get("/positions/{symbol}/{ticket}")
    async def get_position(symbol: str, ticket: int) -> Dict:
        """
        获取单个持仓详情
        """
        position = trading_server.position_service.get_position(symbol, ticket)
        if not position:
            return {"status": "error", "message": "持仓不存在"}
        return {
            "status": "ok",
            "position": position
        }

    # ==================== 交易历史接口 ====================

    @router.post("/ea/trade_history")
    async def receive_trade_history(request: Request) -> Dict:
        """
        EA推送交易历史数据

        请求体:
        ```json
        {
            "deals": [
                {
                    "ticket": 123456,
                    "order": 789012,
                    "symbol": "GOLD#",
                    "type": 0,
                    "entry": 0,
                    "volume": 0.1,
                    "price": 2050.50,
                    "profit": 0,
                    "swap": 0,
                    "commission": -5.0,
                    "time": "2026.03.16 15:30:00",
                    "comment": ""
                }
            ]
        }
        ```
        """
        try:
            data = await request.json()
            deals = data.get('deals', [])

            if not deals:
                return {"status": "ok", "message": "无数据", "count": 0}

            # 使用新的交易历史服务
            new_count = trading_server.trade_history_service.process_deals(deals)

            # 记录日志
            system_log = get_system_log()
            system_log.add_log(
                "trade_history_update",
                {
                    "deals_received": len(deals),
                    "deals_new": new_count,
                    "total_deals": len(trading_server.trade_history_store.get())
                },
                message=f"交易历史上报: 收到{len(deals)}条, 新增{new_count}条"
            )

            return {
                "status": "ok",
                "message": "交易历史已更新",
                "count": new_count
            }

        except Exception as e:
            print(f"[PositionAPI] 接收交易历史异常: {e}")
            return {"status": "error", "message": str(e)}

    @router.get("/trade_history")
    async def get_trade_history(symbol: Optional[str] = None) -> Dict:
        """
        获取交易历史数据

        参数:
        - symbol: 可选，指定品种
        """
        deals = trading_server.trade_history_service.get_deals(symbol)
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            "deals": deals,
            "statistics": statistics
        }

    @router.get("/trade_history/statistics")
    async def get_trade_history_statistics(symbol: Optional[str] = None) -> Dict:
        """
        获取交易历史统计

        参数:
        - symbol: 可选，指定品种
        """
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            **statistics
        }

    return router