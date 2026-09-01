#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位管理相关的接口路由
"""

from fastapi import APIRouter, Depends, Query, Request
from typing import Dict, Optional

from auth import AuthUser, require_auth
from ea_auth import EAIdentity, require_ea_auth
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine
from mysql_repositories import PositionManagementEventRepository


def create_position_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建仓位管理路由

    Args:
        engine_manager: 多账户交易引擎管理器
    """
    router = APIRouter()
    protected_router = APIRouter()

    @router.post("/ea/positions")
    async def receive_positions(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
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
            trading_server = engine_manager.get_engine_for_ea(identity)
            result = trading_server.position_service.update_positions(symbol, positions)

            # 记录日志
            if positions:
                system_log = trading_server.system_log
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

    @protected_router.get("/positions")
    async def get_positions(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取持仓数据

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        positions = trading_server.position_service.get_positions(symbol)
        return {
            "status": "ok",
            "count": len(positions),
            "positions": positions
        }

    @protected_router.get("/positions/summary")
    async def get_positions_summary(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取持仓汇总

        参数:
        - symbol: 可选，指定品种；不提供则返回所有
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        summary = trading_server.position_service.get_summary(symbol)
        return {
            "status": "ok",
            **summary
        }

    @protected_router.get("/positions/{symbol}/{ticket}")
    async def get_position(
        symbol: str,
        ticket: int,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取单个持仓详情
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        position = trading_server.position_service.get_position(symbol, ticket)
        if not position:
            return {"status": "error", "message": "持仓不存在"}
        return {
            "status": "ok",
            "position": position
        }

    @protected_router.get("/positions/{symbol}/{ticket}/management-events")
    async def get_position_management_events(
        symbol: str,
        ticket: int,
        account_id: Optional[int] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        account, _ = resolve_web_engine(engine_manager, user, account_id)
        events = PositionManagementEventRepository().list_for_position(
            user.user_id, account.account_id, str(ticket),
            limit=page_size + 1, offset=(page - 1) * page_size,
        )
        items = events[:page_size]
        return {"status": "ok", "events": items, "page": page,
                "page_size": page_size, "has_more": len(events) > page_size}

    # ==================== 交易历史接口 ====================

    @router.post("/ea/trade_history")
    async def receive_trade_history(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
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
            trading_server = engine_manager.get_engine_for_ea(identity)
            new_count = trading_server.trade_history_service.process_deals(deals)

            # 记录日志
            system_log = trading_server.system_log
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

    @protected_router.get("/trade_history")
    async def get_trade_history(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取交易历史数据

        参数:
        - symbol: 可选，指定品种
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        # 读取一页加一条探测记录，避免把账户全部成交复制到响应内存。
        all_deals = trading_server.trade_history_service.get_deals(
            symbol, offset=(page - 1) * page_size, limit=page_size + 1,
        )
        start = (page - 1) * page_size
        deals = all_deals[:page_size]
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            "deals": deals,
            "page": page, "page_size": page_size,
            "has_more": len(all_deals) > page_size,
            "statistics": statistics
        }

    @protected_router.get("/trade_history/statistics")
    async def get_trade_history_statistics(
        symbol: Optional[str] = None,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取交易历史统计

        参数:
        - symbol: 可选，指定品种
        """
        _, trading_server = resolve_web_engine(engine_manager, user, account_id)
        statistics = trading_server.trade_history_service.get_statistics(symbol)

        return {
            "status": "ok",
            **statistics
        }

    router.include_router(protected_router)
    return router
