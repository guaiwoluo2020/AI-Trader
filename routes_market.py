#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情相关的接口路由
包括K线数据接收、查询、WebSocket推送等
"""

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict
import json

from market.store import MarketStore
from market.pivot_detector import PivotDetector
from market.monitor import PivotMonitor
from market.trend_analyzer import TrendAnalyzer
from market.pending_orders import PendingOrderManager


def create_market_routes(store: MarketStore, detector: PivotDetector,
                         monitor: PivotMonitor, trend_analyzer: TrendAnalyzer,
                         pending_orders: PendingOrderManager) -> APIRouter:
    """
    创建行情相关路由

    Args:
        store: K线存储
        detector: 转折点检测器
        monitor: 转折点监控器
        trend_analyzer: 趋势分析器
        pending_orders: 待确认订单管理器
    """
    router = APIRouter()

    # ==================== EA端接口 ====================

    @router.post("/ea/kline/{period}")
    async def receive_kline(period: str, request: Request) -> Dict:
        """
        EA推送K线数据

        Args:
            period: 周期 (H4/H1/M15/M5/M1)

        请求体:
        ```json
        {
            "symbol": "GOLD",
            "is_full": false,  // 是否为全量数据
            "klines": [
                {
                    "timestamp": "2024-01-15 14:00:00",
                    "open": 2030.50,
                    "high": 2035.00,
                    "low": 2028.00,
                    "close": 2033.50,
                    "volume": 1234
                }
            ]
        }
        ```

        返回:
        - 成功: {"status": "ok", "count": N}
        - 需要全量数据: {"status": "error", "code": 8888, "message": "需要全量数据"}
        """
        period = period.upper()

        # 验证周期
        if period not in ['H4', 'H1', 'M15', 'M5', 'M1']:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": f"不支持的周期: {period}"}
            )

        try:
            data = await request.json()
            symbol = data.get('symbol', 'GOLD').upper()
            is_full = data.get('is_full', False)
            klines = data.get('klines', [])

            if not klines:
                return {"status": "ok", "count": 0, "message": "无数据"}

            # 检查是否需要全量数据
            if not is_full and not store.is_initialized(symbol, period):
                print(f"[MarketAPI] {symbol} {period} 未初始化，需要全量数据")
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "error",
                        "code": 8888,
                        "message": "需要全量数据"
                    }
                )

            # 保存K线数据
            result = store.save_klines(symbol, period, klines, is_full)

            if result['status'] == 'ok':
                # 更新转折点
                all_klines = store.get_all_klines(symbol, period)
                if all_klines:
                    # 转换为KlineData对象
                    from market.store import KlineData
                    kline_objs = [
                        KlineData(
                            symbol=k['symbol'],
                            period=k['period'],
                            timestamp=k['timestamp'],
                            open_price=k['open'],
                            high=k['high'],
                            low=k['low'],
                            close=k['close'],
                            volume=k['volume']
                        )
                        for k in all_klines
                    ]
                    detector.update_pivots(symbol, period, kline_objs)

            return result

        except Exception as e:
            print(f"[MarketAPI] 接收K线数据异常: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )

    @router.post("/ea/kline_batch")
    async def receive_kline_batch(request: Request) -> Dict:
        """
        EA批量推送多个周期的K线数据

        请求体:
        ```json
        {
            "symbol": "GOLD",
            "is_full": true,
            "data": {
                "H4": [{...}, {...}],
                "H1": [{...}, {...}],
                "M15": [{...}, {...}],
                "M5": [{...}, {...}],
                "M1": [{...}, {...}]
            }
        }
        ```
        """
        try:
            data = await request.json()
            symbol = data.get('symbol', 'GOLD').upper()
            is_full = data.get('is_full', False)
            kline_data = data.get('data', {})

            results = {}
            for period, klines in kline_data.items():
                period = period.upper()
                if period not in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    continue

                result = store.save_klines(symbol, period, klines, is_full)
                results[period] = result

                # 更新转折点
                if result['status'] == 'ok':
                    all_klines = store.get_all_klines(symbol, period)
                    if all_klines:
                        from market.store import KlineData
                        kline_objs = [
                            KlineData(
                                symbol=k['symbol'],
                                period=k['period'],
                                timestamp=k['timestamp'],
                                open_price=k['open'],
                                high=k['high'],
                                low=k['low'],
                                close=k['close'],
                                volume=k['volume']
                            )
                            for k in all_klines
                        ]
                        detector.update_pivots(symbol, period, kline_objs)

            return {
                "status": "ok",
                "symbol": symbol,
                "results": results
            }

        except Exception as e:
            print(f"[MarketAPI] 批量接收K线数据异常: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )

    # ==================== 查询接口 ====================

    @router.get("/market/kline/{symbol}")
    async def get_kline(
        symbol: str,
        period: str = Query("M5", description="周期: H4/H1/M15/M5/M1"),
        count: int = Query(100, description="返回条数")
    ) -> Dict:
        """
        获取K线数据
        """
        symbol = symbol.upper()
        period = period.upper()

        klines = store.get_klines(symbol, period, count)

        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "data": klines
        }

    @router.get("/market/pivots/{symbol}")
    async def get_pivots(
        symbol: str,
        period: str = Query(None, description="周期，不指定则返回全部"),
        direction: str = Query(None, description="方向: high/low"),
        count: int = Query(50, description="返回条数")
    ) -> Dict:
        """
        获取转折点数据
        """
        symbol = symbol.upper()

        if period:
            period = period.upper()
            pivots = detector.get_pivots(symbol, period, direction, count)
            return {
                "status": "ok",
                "symbol": symbol,
                "period": period,
                "count": len(pivots),
                "data": pivots
            }
        else:
            # 返回所有周期的转折点
            result = {}
            for p in ['H4', 'H1', 'M15', 'M5', 'M1']:
                pivots = detector.get_pivots(symbol, p, direction, count)
                if pivots:
                    result[p] = pivots

            return {
                "status": "ok",
                "symbol": symbol,
                "data": result
            }

    @router.get("/market/symbols")
    async def get_symbols() -> Dict:
        """
        获取所有已存储数据的symbol列表
        """
        symbols = store.get_symbols()
        return {
            "status": "ok",
            "symbols": symbols,
            "count": len(symbols)
        }

    @router.get("/market/status")
    async def get_market_status() -> Dict:
        """
        获取行情存储状态
        """
        store_status = store.get_status()
        detector_status = detector.get_status()
        monitor_status = monitor.get_status()

        return {
            "status": "ok",
            "store": store_status,
            "pivots": detector_status,
            "monitor": monitor_status
        }

    @router.get("/market/thresholds")
    async def get_thresholds() -> Dict:
        """
        获取各周期的接近阈值
        """
        thresholds = detector.THRESHOLDS

        return {
            "status": "ok",
            "thresholds": {
                period: {
                    "value": threshold,
                    "percent": f"{threshold * 100:.4f}%",
                    "description": f"千分之{threshold * 1000}"
                }
                for period, threshold in thresholds.items()
            }
        }

    # ==================== 趋势分析接口 ====================

    @router.get("/trend/{symbol}")
    async def get_trend(symbol: str) -> Dict:
        """
        获取单个品种的趋势分析
        """
        from market.store import KlineData

        # 分析每个周期的趋势
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            all_klines = store.get_all_klines(symbol, period)
            if all_klines:
                kline_objs = [
                    KlineData(
                        symbol=k['symbol'],
                        period=k['period'],
                        timestamp=k['timestamp'],
                        open_price=k['open'],
                        high=k['high'],
                        low=k['low'],
                        close=k['close'],
                        volume=k['volume']
                    )
                    for k in all_klines
                ]
                trend_analyzer.analyze_trend(symbol, period, kline_objs)

        # 获取共振分析
        resonance = trend_analyzer.analyze_resonance(symbol)

        # 获取趋势转换历史
        changes = trend_analyzer.get_trend_changes(symbol, 10)

        return {
            "status": "ok",
            "symbol": symbol,
            "resonance": resonance,
            "trend_changes": changes
        }

    @router.post("/trend/generate_order/{symbol}")
    async def generate_trade_order(symbol: str) -> Dict:
        """
        基于趋势分析生成交易建议
        """
        from market.store import KlineData

        # 更新趋势分析
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            all_klines = store.get_all_klines(symbol, period)
            if all_klines:
                kline_objs = [
                    KlineData(
                        symbol=k['symbol'],
                        period=k['period'],
                        timestamp=k['timestamp'],
                        open_price=k['open'],
                        high=k['high'],
                        low=k['low'],
                        close=k['close'],
                        volume=k['volume']
                    )
                    for k in all_klines
                ]
                trend_analyzer.analyze_trend(symbol, period, kline_objs)

        # 获取所有周期的转折点
        all_pivots = []
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            pivot_list = detector.get_pivots(symbol, period, None, 20)
            all_pivots.extend(pivot_list)

        # 获取当前价格
        current_price = store.get_latest_price(symbol)
        if not current_price:
            return {"status": "error", "message": "无法获取当前价格"}

        # 生成交易建议
        suggestion = trend_analyzer.generate_trade_suggestion(symbol, all_pivots, current_price)

        if not suggestion:
            return {
                "status": "ok",
                "message": "当前无交易建议",
                "resonance": trend_analyzer.analyze_resonance(symbol)
            }

        # 添加到待确认订单
        order_id = pending_orders.add_order(suggestion)

        return {
            "status": "ok",
            "message": "交易建议已生成",
            "order_id": order_id,
            "suggestion": suggestion
        }

    # ==================== 待确认订单接口 ====================

    @router.get("/pending_orders")
    async def get_pending_orders(symbol: Optional[str] = None) -> Dict:
        """
        获取待确认订单列表
        """
        orders = pending_orders.get_pending_orders(symbol)
        return {
            "status": "ok",
            "count": len(orders),
            "orders": orders
        }

    @router.post("/pending_orders/{order_id}/confirm")
    async def confirm_pending_order(order_id: str, request: Request = None) -> Dict:
        """
        确认待确认订单，可更新手数、止损、止盈
        """
        # 获取更新数据
        update_data = {}
        if request:
            try:
                update_data = await request.json()
            except:
                pass

        # 更新订单参数
        if update_data:
            order = pending_orders.get_order_by_id(order_id)
            if order:
                if 'mount' in update_data:
                    order['mount'] = update_data['mount']
                if 'sl' in update_data:
                    order['sl'] = update_data['sl']
                if 'tp' in update_data:
                    order['tp'] = update_data['tp']

        order = pending_orders.confirm_order(order_id)
        if not order:
            return {"status": "error", "message": "订单不存在"}

        return {
            "status": "ok",
            "message": "订单已确认",
            "order": order
        }

    @router.post("/pending_orders/{order_id}/reject")
    async def reject_pending_order(order_id: str) -> Dict:
        """
        拒绝待确认订单
        """
        success = pending_orders.reject_order(order_id)
        if not success:
            return {"status": "error", "message": "订单不存在"}

        return {
            "status": "ok",
            "message": "订单已拒绝"
        }

    # ==================== 交易配置接口 ====================

    @router.get("/trade_config")
    async def get_trade_config() -> Dict:
        """
        获取交易配置
        """
        from market.monitor import TradeConfig
        config = TradeConfig.get_instance()
        return {
            "status": "ok",
            "config": config.to_dict()
        }

    @router.post("/trade_config")
    async def update_trade_config(request: Request) -> Dict:
        """
        更新交易配置
        """
        from market.monitor import TradeConfig
        config = TradeConfig.get_instance()

        try:
            data = await request.json()
            config.update(data)
            return {
                "status": "ok",
                "message": "配置已更新",
                "config": config.to_dict()
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ==================== WebSocket接口 ====================

    @router.websocket("/ws/market")
    async def websocket_market(websocket: WebSocket):
        """
        WebSocket连接，用于实时推送转折点提醒
        """
        await websocket.accept()
        monitor.add_ws_client(websocket)

        try:
            # 发送欢迎消息
            await websocket.send_text(json.dumps({
                "type": "connected",
                "message": "已连接到行情监控服务"
            }))

            # 保持连接，等待客户端消息或关闭
            while True:
                try:
                    data = await websocket.receive_text()
                    # 可以处理客户端发来的消息
                    msg = json.loads(data)

                    if msg.get('type') == 'ping':
                        await websocket.send_text(json.dumps({"type": "pong"}))

                except WebSocketDisconnect:
                    break

        except Exception as e:
            print(f"[WebSocket] 连接异常: {e}")

        finally:
            monitor.remove_ws_client(websocket)

    return router