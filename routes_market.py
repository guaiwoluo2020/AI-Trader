#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情相关的接口路由
包括K线数据接收、查询、WebSocket推送等
"""

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import json
import random

from market.store import MarketStore
from market.pivot_detector import PivotDetector
from market.monitor import PivotMonitor, TradeConfig
from market.trend_analyzer import TrendAnalyzer
from market.pending_orders import PendingOrderManager
from market.llm_analyzer import LLMAnalyzer
from market.system_log import get_system_log


def create_market_routes(store: MarketStore, detector: PivotDetector,
                         monitor: PivotMonitor, trend_analyzer: TrendAnalyzer,
                         pending_orders: PendingOrderManager,
                         llm_analyzer: LLMAnalyzer = None) -> APIRouter:
    """
    创建行情相关路由

    Args:
        store: K线存储
        detector: 转折点检测器
        monitor: 转折点监控器
        trend_analyzer: 趋势分析器
        pending_orders: 待确认订单管理器
        llm_analyzer: 大模型分析器
    """
    router = APIRouter()

    # 增量K线日志打印概率 (5%)
    KLINE_LOG_PROBABILITY = 0.05

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
            symbol = data.get('symbol', 'GOLD')
            is_full = data.get('is_full', False)
            klines = data.get('klines', [])

            if not klines:
                return {"status": "ok", "count": 0, "message": "无数据"}

            # 全量数据时检查K线时效性
            if is_full:
                period_interval = store.PERIOD_INTERVALS.get(period.upper(), 60)
                latest_kline_time = None

                # 获取最新K线时间（取最后一条）
                latest_kline = klines[-1] if klines else None
                if latest_kline:
                    ts = latest_kline.get('timestamp') or latest_kline.get('time')
                    if ts:
                        # 解析时间戳
                        if isinstance(ts, datetime):
                            latest_kline_time = ts
                        else:
                            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
                                try:
                                    latest_kline_time = datetime.strptime(str(ts), fmt)
                                    break
                                except:
                                    continue

                if latest_kline_time:
                    # 获取MT5时区偏移配置
                    # mt5_timezone_offset: MT5时间与本地时间的差值
                    # 正数表示MT5时间比本地时间快，负数表示MT5时间比本地时间慢
                    # 例如：MT5(GMT+2) vs 本地(GMT+8)，MT5比本地慢6小时，offset = -6
                    trade_config = TradeConfig.get_instance()
                    timezone_offset_hours = trade_config.mt5_timezone_offset

                    now_local = datetime.now()

                    # 将K线时间（MT5服务器时间）转换为本地时间进行比较
                    # 本地时间 = MT5时间 - offset（因为offset是MT5相对本地的偏移）
                    # 例如：MT5时间 08:00，offset=-6，本地时间 = 08:00 - (-6) = 08:00 + 6 = 14:00
                    kline_time_local = latest_kline_time - timedelta(hours=timezone_offset_hours)

                    time_diff = (now_local - kline_time_local).total_seconds()

                    # 调试日志
                    print(f"[MarketAPI] {symbol} {period} K线时间检查:")
                    print(f"  - K线时间(MT5): {latest_kline_time}")
                    print(f"  - 转换后本地时间: {kline_time_local}")
                    print(f"  - 当前本地时间: {now_local}")
                    print(f"  - 时区偏移: {timezone_offset_hours}小时")
                    print(f"  - 时间差: {int(time_diff)}秒, 阈值: {period_interval}秒")

                    # 如果超过一个周期，说明数据不是最新的，可能休市
                    if time_diff > period_interval:
                        system_log = get_system_log()
                        system_log.add_log(
                            "ea_kline_stale",
                            {
                                "period": period,
                                "latest_kline_time": latest_kline_time.isoformat(),
                                "kline_time_local": kline_time_local.isoformat(),
                                "now_local": now_local.isoformat(),
                                "timezone_offset_hours": timezone_offset_hours,
                                "time_diff_seconds": int(time_diff),
                                "period_interval": period_interval
                            },
                            symbol=symbol,
                            message=f"K线数据过期，最新K线距当前 {int(time_diff)}秒，可能休市"
                        )
                        print(f"[MarketAPI] {symbol} {period} 全量K线数据过期，K线时间(MT5) {latest_kline_time}，转换为本地时间 {kline_time_local}，距当前 {int(time_diff)}秒，丢弃数据")
                        return {
                            "status": "ok",
                            "count": 0,
                            "message": "K线数据过期，可能休市",
                            "stale": True,
                            "latest_kline_time": latest_kline_time.isoformat(),
                            "kline_time_local": kline_time_local.isoformat(),
                            "time_diff_seconds": int(time_diff),
                            "timezone_offset_hours": timezone_offset_hours
                        }

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

            # 增量数据时检查连续性
            if not is_full and store.is_initialized(symbol, period):
                continuity = store.check_kline_continuity(symbol, period, klines)
                if not continuity["is_continuous"]:
                    print(f"[MarketAPI] {symbol} {period} 数据不连续，缺失 {continuity['gap_count']} 个周期")
                    print(f"[MarketAPI] 现有最后时间: {continuity.get('last_existing_time')}, 新数据最早时间: {continuity.get('first_new_time')}")
                    return JSONResponse(
                        status_code=400,
                        content={
                            "status": "error",
                            "code": 8888,
                            "message": f"数据不连续，缺失 {continuity['gap_count']} 个周期，需要全量数据"
                        }
                    )

            # 保存K线数据
            result = store.save_klines(symbol, period, klines, is_full)

            # 记录日志 - 全量K线总是记录，增量K线5%概率记录
            if is_full or random.random() < KLINE_LOG_PROBABILITY:
                system_log = get_system_log()
                event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                system_log.add_log(
                    event_type,
                    {
                        "period": period,
                        "count": len(klines),
                        "is_full": is_full
                    },
                    symbol=symbol,
                    message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                )

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
            symbol = data.get('symbol', 'GOLD')
            is_full = data.get('is_full', False)
            kline_data = data.get('data', {})

            results = {}
            system_log = get_system_log()

            for period, klines in kline_data.items():
                period = period.upper()
                if period not in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    continue

                result = store.save_klines(symbol, period, klines, is_full)
                results[period] = result

                # 记录日志 - 全量K线总是记录，增量K线5%概率记录
                if is_full or random.random() < KLINE_LOG_PROBABILITY:
                    event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                    system_log.add_log(
                        event_type,
                        {
                            "period": period,
                            "count": len(klines),
                            "is_full": is_full
                        },
                        symbol=symbol,
                        message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                    )

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

    @router.get("/market/configured_symbols")
    async def get_configured_symbols() -> Dict:
        """
        获取配置的品种列表及其数据状态

        返回系统配置中的品种，以及每个品种的K线数据状态
        """
        from market.monitor import TradeConfig
        config = TradeConfig.get_instance()

        # 获取配置的品种
        configured_symbols = list(config.symbol_config.keys())

        # 获取每个品种的状态
        symbols_status = []
        for symbol in configured_symbols:
            # 检查是否有M1数据
            m1_status = store.check_m1_updated_within(symbol, 180)

            # 获取最新M1 K线时间
            latest_m1_time = store.get_latest_kline_time(symbol, 'M1')

            # 获取各周期数据条数
            period_counts = {}
            with store._lock:
                for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    period_counts[period] = len(store._klines[symbol][period])

            symbols_status.append({
                "symbol": symbol,
                "has_data": m1_status["has_data"],
                "m1_count": period_counts.get('M1', 0),
                "latest_m1_time": latest_m1_time.isoformat() if latest_m1_time else None,
                "m1_update_time": m1_status.get("update_time").isoformat() if m1_status.get("update_time") else None,
                "seconds_ago": m1_status.get("seconds_ago"),
                "market_status": m1_status.get("market_status", "closed"),
                "period_counts": period_counts,
                "config": config.symbol_config.get(symbol, {})
            })

        return {
            "status": "ok",
            "symbols": symbols_status,
            "count": len(symbols_status)
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

        # 记录日志
        system_log = get_system_log()
        action_text = '买入' if order.get('action') == 'b' else '卖出'
        symbol = order.get('symbol', '')
        mount = order.get('mount')
        price = order.get('price')
        sl = order.get('sl')
        tp = order.get('tp')

        system_log.add_log(
            "order_confirmed",
            {
                "order_id": order_id,
                "action": order.get('action'),
                "price": price,
                "mount": mount,
                "sl": sl,
                "tp": tp
            },
            symbol=symbol,
            message=f"{action_text} @ {price}, 手数={mount}, SL={sl}, TP={tp}"
        )

        # 打印确认订单信息
        print(f"[订单确认] {symbol} | {action_text} | 价格={price} | 手数={mount} | SL={sl} | TP={tp}")

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
        # 先获取订单信息用于日志
        order = pending_orders.get_order_by_id(order_id)

        success = pending_orders.reject_order(order_id)
        if not success:
            return {"status": "error", "message": "订单不存在"}

        # 记录日志
        if order:
            system_log = get_system_log()
            system_log.add_log(
                "order_rejected",
                {"order_id": order_id, "action": order.get('action'), "price": order.get('price')},
                symbol=order.get('symbol'),
                message=f"订单已拒绝"
            )

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

    # ==================== 系统日志接口 ====================

    @router.get("/system/logs")
    async def get_system_logs(count: int = 50, event_type: str = None,
                               symbol: str = None) -> Dict:
        """
        获取系统运行日志

        Args:
            count: 获取数量，默认50条
            event_type: 过滤事件类型（多个用逗号分隔，如 "order_generated,order_confirmed"）
            symbol: 过滤品种
        """
        system_log = get_system_log()

        # 支持多个事件类型过滤
        event_types = None
        if event_type:
            event_types = [et.strip() for et in event_type.split(',') if et.strip()]

        logs = system_log.get_logs(count, event_types, symbol)
        return {
            "status": "ok",
            "count": len(logs),
            "logs": logs
        }

    @router.delete("/system/logs")
    async def clear_system_logs() -> Dict:
        """清空系统日志"""
        system_log = get_system_log()
        system_log.clear_logs()
        return {"status": "ok", "message": "日志已清空"}

    # ==================== WebSocket接口 ====================

    @router.websocket("/ws/market")
    async def websocket_market(websocket: WebSocket):
        """
        WebSocket连接，用于实时推送转折点提醒和大模型分析更新
        """
        await websocket.accept()
        monitor.add_ws_client(websocket)
        if llm_analyzer:
            llm_analyzer.add_ws_client(websocket)

        # 添加到系统日志的WebSocket客户端列表
        system_log = get_system_log()
        system_log.add_ws_client(websocket)

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
            if llm_analyzer:
                llm_analyzer.remove_ws_client(websocket)
            system_log.remove_ws_client(websocket)

    # ==================== 大模型分析接口 ====================

    @router.get("/llm/analysis")
    async def get_llm_analysis(symbol: Optional[str] = None) -> Dict:
        """
        获取大模型分析结果

        参数:
        - symbol: 可选，指定品种；不提供则返回所有

        返回:
        ```json
        {
            "status": "ok",
            "data": {
                "symbol": {
                    "analysis": {...},
                    "analyzed_at": "2024-01-01T00:00:00"
                }
            }
        }
        ```
        """
        if not llm_analyzer:
            return {"status": "error", "message": "大模型分析器未初始化"}

        result = llm_analyzer.get_analysis(symbol)
        return {
            "status": "ok",
            "data": result
        }

    @router.get("/llm/status")
    async def get_llm_status() -> Dict:
        """
        获取大模型分析器状态

        返回:
        ```json
        {
            "status": "ok",
            "data": {
                "enabled": true,
                "model": "gpt-4o-mini",
                "last_analysis_time": "2024-01-01T00:00:00",
                "symbols_analyzed": ["GOLD", "EURUSD"]
            }
        }
        ```
        """
        if not llm_analyzer:
            return {"status": "ok", "data": {"enabled": False, "message": "大模型分析器未初始化"}}

        return {
            "status": "ok",
            "data": llm_analyzer.get_status()
        }

    @router.get("/llm/config")
    async def get_llm_config() -> Dict:
        """
        获取大模型配置（API Key会脱敏显示）

        返回:
        ```json
        {
            "status": "ok",
            "config": {
                "api_key": "sk-****1234",
                "api_key_set": true,
                "api_base": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "enabled": true
            }
        }
        ```
        """
        if not llm_analyzer:
            return {"status": "ok", "config": {"enabled": False, "message": "大模型分析器未初始化"}}

        return {
            "status": "ok",
            "config": llm_analyzer.get_config()
        }

    @router.post("/llm/trigger")
    async def trigger_llm_analysis() -> Dict:
        """
        手动触发大模型分析
        """
        if not llm_analyzer:
            return {"status": "error", "message": "大模型分析器未初始化"}

        return llm_analyzer.trigger_analysis()

    @router.post("/llm/configure")
    async def configure_llm(request: Request) -> Dict:
        """
        配置大模型参数

        请求体:
        ```json
        {
            "api_key": "your-api-key",
            "api_base": "https://api.openai.com/v1",
            "model": "gpt-4o-mini"
        }
        ```
        """
        if not llm_analyzer:
            return {"status": "error", "message": "大模型分析器未初始化"}

        try:
            data = await request.json()
            result = llm_analyzer.configure(
                api_key=data.get("api_key"),
                api_base=data.get("api_base"),
                model=data.get("model")
            )
            return {"status": "ok", "data": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    return router