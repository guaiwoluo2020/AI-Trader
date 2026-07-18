#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情相关的接口路由
包括K线数据接收、查询、WebSocket推送等
"""

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import json
import random

from auth import require_auth
from market.models import KlineData
from market.store import KlineStore
from market.services import KlineService, PivotService, TechService, PendingOrderService
from market.trade_config import TradeConfig
from market.system_log import get_system_log


def create_market_routes(
    kline_store: KlineStore,
    kline_service: KlineService,
    pivot_service: PivotService,
    tech_service: TechService,
    pending_order_service: PendingOrderService,
    trading_server = None
) -> APIRouter:
    """
    创建行情相关路由

    Args:
        kline_store: K线存储
        kline_service: K线服务
        pivot_service: 转折点服务
        tech_service: 技术分析服务
        pending_order_service: 待确认订单服务
        trading_server: TradingServer 实例
    """
    router = APIRouter()
    protected_router = APIRouter(dependencies=[Depends(require_auth)])

    # 增量K线日志打印概率 (5%)
    KLINE_LOG_PROBABILITY = 0.05

    # ==================== EA端接口 ====================

    @router.post("/ea/kline/{period}")
    async def receive_kline(period: str, request: Request) -> Dict:
        """
        EA推送K线数据
        """
        period = period.upper()

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
                staleness = kline_service.check_staleness(symbol, period, klines)

                if staleness.get('latest_kline_time'):
                    trade_config = TradeConfig.get_instance()
                    timezone_offset_hours = trade_config.mt5_timezone_offset

                    staleness = kline_service.check_staleness(
                        symbol, period, klines, timezone_offset_hours
                    )

                    if staleness.get('is_stale'):
                        system_log = get_system_log()
                        system_log.add_log(
                            "ea_kline_stale",
                            {
                                "period": period,
                                "latest_kline_time": staleness.get('latest_kline_time').isoformat() if staleness.get('latest_kline_time') else None,
                                "kline_time_local": staleness.get('kline_time_local').isoformat() if staleness.get('kline_time_local') else None,
                                "time_diff_seconds": staleness.get('time_diff_seconds'),
                                "period_interval": staleness.get('period_interval')
                            },
                            symbol=symbol,
                            message=f"K线数据过期，最新K线距当前 {staleness.get('time_diff_seconds')}秒，可能休市"
                        )
                        print(f"[MarketAPI] {symbol} {period} 全量K线数据过期")
                        return {
                            "status": "ok",
                            "count": 0,
                            "message": "K线数据过期，可能休市",
                            "stale": True,
                            "latest_kline_time": staleness.get('latest_kline_time').isoformat() if staleness.get('latest_kline_time') else None,
                            "time_diff_seconds": staleness.get('time_diff_seconds')
                        }

            # 检查是否需要全量数据
            if not is_full and not kline_service.is_initialized(symbol, period):
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
            if not is_full and kline_service.is_initialized(symbol, period):
                continuity = kline_service.check_continuity(symbol, period, klines)
                if not continuity["is_continuous"]:
                    print(f"[MarketAPI] {symbol} {period} 数据不连续，缺失 {continuity['gap_count']} 个周期")
                    return JSONResponse(
                        status_code=400,
                        content={
                            "status": "error",
                            "code": 8888,
                            "message": f"数据不连续，缺失 {continuity['gap_count']} 个周期，需要全量数据"
                        }
                    )

            # 保存K线数据
            result = kline_service.process_kline_data(symbol, period, klines, is_full)

            # 记录日志
            if is_full or random.random() < KLINE_LOG_PROBABILITY:
                system_log = get_system_log()
                event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                system_log.add_log(
                    event_type,
                    {"period": period, "count": len(klines), "is_full": is_full},
                    symbol=symbol,
                    message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                )

            if result['status'] == 'ok':
                # 更新转折点
                all_klines = kline_service.get_all_kline_objects(symbol, period)
                if all_klines:
                    pivot_service.update_pivots(symbol, period, all_klines)

            return result

        except Exception as e:
            print(f"[MarketAPI] 接收K线数据异常: {e}")
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": str(e)}
            )

    @router.post("/ea/kline_batch")
    async def receive_kline_batch(request: Request) -> Dict:
        """EA批量推送多个周期的K线数据"""
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

                result = kline_service.process_kline_data(symbol, period, klines, is_full)
                results[period] = result

                if is_full or random.random() < KLINE_LOG_PROBABILITY:
                    event_type = "ea_kline_full" if is_full else "ea_kline_incremental"
                    system_log.add_log(
                        event_type,
                        {"period": period, "count": len(klines), "is_full": is_full},
                        symbol=symbol,
                        message=f"{'全量' if is_full else '增量'} {period} {len(klines)}条"
                    )

                if result['status'] == 'ok':
                    all_klines = kline_service.get_all_kline_objects(symbol, period)
                    if all_klines:
                        pivot_service.update_pivots(symbol, period, all_klines)

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

    @protected_router.get("/market/kline/{symbol}")
    async def get_kline(
        symbol: str,
        period: str = Query("M5", description="周期: H4/H1/M15/M5/M1"),
        count: int = Query(100, description="返回条数")
    ) -> Dict:
        """获取K线数据"""
        period = period.upper()
        klines = kline_service.get_klines(symbol, period, count)

        return {
            "status": "ok",
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "data": klines
        }

    @protected_router.get("/market/pivots/{symbol}")
    async def get_pivots(
        symbol: str,
        period: str = Query(None, description="周期，不指定则返回全部"),
        direction: str = Query(None, description="方向: high/low"),
        count: int = Query(50, description="返回条数")
    ) -> Dict:
        """获取转折点数据"""
        if period:
            period = period.upper()
            pivots = pivot_service.get_pivots(symbol, period, direction, count)
            return {
                "status": "ok",
                "symbol": symbol,
                "period": period,
                "count": len(pivots),
                "data": pivots
            }
        else:
            result = {}
            for p in ['H4', 'H1', 'M15', 'M5', 'M1']:
                pivots = pivot_service.get_pivots(symbol, p, direction, count)
                if pivots:
                    result[p] = pivots

            return {
                "status": "ok",
                "symbol": symbol,
                "data": result
            }

    @protected_router.get("/market/symbols")
    async def get_symbols() -> Dict:
        """获取所有已存储数据的symbol列表"""
        symbols = kline_service.get_symbols()
        return {
            "status": "ok",
            "symbols": symbols,
            "count": len(symbols)
        }

    @protected_router.get("/market/configured_symbols")
    async def get_configured_symbols() -> Dict:
        """获取配置的品种列表及其数据状态"""
        config = TradeConfig.get_instance()
        configured_symbols = list(config.symbol_config.keys())

        symbols_status = []
        for symbol in configured_symbols:
            m1_status = kline_service.check_m1_updated_within(symbol, 180)
            latest_m1_time = kline_store.get_latest_kline_time(symbol, 'M1')

            period_counts = {}
            with kline_store._lock:
                for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
                    period_counts[period] = len(kline_store._klines[symbol][period])

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

    @protected_router.get("/market/status")
    async def get_market_status() -> Dict:
        """获取行情存储状态"""
        store_status = kline_service.get_status()
        pivot_status = pivot_service.get_status()

        # 使用 trading_server 获取状态
        server_status = trading_server.get_status() if trading_server else {}

        return {
            "status": "ok",
            "store": store_status,
            "pivots": pivot_status,
            "server": server_status
        }

    @protected_router.get("/market/thresholds")
    async def get_thresholds() -> Dict:
        """获取各周期的接近阈值"""
        thresholds = pivot_service.THRESHOLDS

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

    @protected_router.get("/trend/{symbol}")
    async def get_trend(symbol: str) -> Dict:
        """获取单个品种的趋势分析"""
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            tech_service.analyze_trend(symbol, period)

        resonance = tech_service.analyze_resonance(symbol)
        changes = tech_service.get_trend_changes(symbol, 10)

        return {
            "status": "ok",
            "symbol": symbol,
            "resonance": resonance,
            "trend_changes": changes
        }

    @protected_router.post("/trend/generate_order/{symbol}")
    async def generate_trade_order(symbol: str) -> Dict:
        """基于趋势分析生成交易建议"""
        for period in ['H4', 'H1', 'M15', 'M5', 'M1']:
            tech_service.analyze_trend(symbol, period)

        current_price = kline_service.get_latest_price(symbol)
        if not current_price:
            return {"status": "error", "message": "无法获取当前价格"}

        suggestion = tech_service.generate_trade_suggestion(symbol, current_price)

        if not suggestion:
            return {
                "status": "ok",
                "message": "当前无交易建议",
                "resonance": tech_service.analyze_resonance(symbol)
            }

        order_id = pending_order_service.create_order_from_dict(suggestion)

        return {
            "status": "ok",
            "message": "交易建议已生成",
            "order_id": order_id,
            "suggestion": suggestion
        }

    # ==================== 待确认订单接口 ====================

    @protected_router.get("/pending_orders")
    async def get_pending_orders(symbol: Optional[str] = None) -> Dict:
        """获取待确认订单列表"""
        orders = pending_order_service.get_orders_dict(symbol)
        return {
            "status": "ok",
            "count": len(orders),
            "orders": orders
        }

    @protected_router.post("/pending_orders/{order_id}/confirm")
    async def confirm_pending_order(order_id: str, request: Request = None) -> Dict:
        """确认待确认订单"""
        update_data = {}
        if request:
            try:
                update_data = await request.json()
            except:
                pass

        # 获取订单并确认
        order = pending_order_service.confirm_order(order_id, update_data)
        if not order:
            return {"status": "error", "message": "订单不存在"}

        system_log = get_system_log()
        action_text = '买入' if order.action == 'b' else '卖出'
        symbol = order.symbol
        mount = order.mount
        price = order.price
        sl = order.sl
        tp = order.tp

        system_log.add_log(
            "order_confirmed",
            {
                "order_id": order_id,
                "action": order.action,
                "price": price,
                "mount": mount,
                "sl": sl,
                "tp": tp
            },
            symbol=symbol,
            message=f"{action_text} @ {price}, 手数={mount}, SL={sl}, TP={tp}"
        )

        print(f"[订单确认] {symbol} | {action_text} | 价格={price} | 手数={mount} | SL={sl} | TP={tp}")

        return {
            "status": "ok",
            "message": "订单已确认",
            "order": order.to_dict()
        }

    @protected_router.post("/pending_orders/{order_id}/reject")
    async def reject_pending_order(order_id: str) -> Dict:
        """拒绝待确认订单"""
        order = pending_order_service.reject_order(order_id)
        if not order:
            return {"status": "error", "message": "订单不存在"}

        system_log = get_system_log()
        system_log.add_log(
            "order_rejected",
            {"order_id": order_id, "action": order.action, "price": order.price},
            symbol=order.symbol,
            message=f"订单已拒绝"
        )

        return {
            "status": "ok",
            "message": "订单已拒绝"
        }

    # ==================== 交易配置接口 ====================

    @protected_router.get("/trade_config")
    async def get_trade_config() -> Dict:
        """获取交易配置"""
        config = TradeConfig.get_instance()
        return {
            "status": "ok",
            "config": config.to_dict()
        }

    @protected_router.post("/trade_config")
    async def update_trade_config(request: Request) -> Dict:
        """更新交易配置"""
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

    # ==================== 策略决策接口 ====================

    @protected_router.get("/strategy")
    async def get_all_strategies() -> Dict:
        """获取所有策略配置"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        strategies = trading_server.strategy_service.get_all_strategies()
        return {
            "status": "ok",
            "count": len(strategies),
            "strategies": [s.to_dict() for s in strategies]
        }

    @protected_router.get("/strategy/decisions")
    async def get_decisions(symbol: Optional[str] = None, count: int = 20) -> Dict:
        """获取决策历史"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        decisions = trading_server.get_decision_history(symbol, count)
        return {
            "status": "ok",
            "count": len(decisions),
            "decisions": decisions
        }

    @protected_router.get("/strategy/{symbol}")
    async def get_strategy(symbol: str) -> Dict:
        """获取品种策略配置"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        strategy = trading_server.strategy_service.get_strategy(symbol)
        return {
            "status": "ok",
            "strategy": strategy.to_dict()
        }

    @protected_router.post("/strategy/{symbol}")
    async def update_strategy(symbol: str, request: Request) -> Dict:
        """更新品种策略配置"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        try:
            data = await request.json()
            strategy = trading_server.strategy_service.update_strategy(symbol, data)
            return {
                "status": "ok",
                "message": "策略配置已更新",
                "strategy": strategy.to_dict()
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @protected_router.delete("/strategy/{symbol}")
    async def delete_strategy(symbol: str) -> Dict:
        """删除品种策略配置"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        success = trading_server.strategy_service.strategy_store.delete_strategy(symbol)
        if success:
            return {"status": "ok", "message": "策略配置已删除"}
        return {"status": "error", "message": "策略配置不存在"}

    @protected_router.post("/strategy/trigger/{symbol}")
    async def trigger_strategy_decision(symbol: str) -> Dict:
        """手动触发策略决策"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        current_price = kline_service.get_latest_price(symbol)
        if not current_price:
            return {"status": "error", "message": "无法获取当前价格"}

        result = trading_server.process_price(symbol, current_price)
        return {
            "status": "ok",
            "result": result
        }

    # ==================== 系统日志接口 ====================

    @protected_router.get("/system/logs")
    async def get_system_logs(count: int = 50, event_type: str = None,
                               symbol: str = None) -> Dict:
        """获取系统运行日志"""
        system_log = get_system_log()

        event_types = None
        if event_type:
            event_types = [et.strip() for et in event_type.split(',') if et.strip()]

        logs = system_log.get_logs(count, event_types, symbol)
        return {
            "status": "ok",
            "count": len(logs),
            "logs": logs
        }

    @protected_router.delete("/system/logs")
    async def clear_system_logs() -> Dict:
        """清空系统日志"""
        system_log = get_system_log()
        system_log.clear_logs()
        return {"status": "ok", "message": "日志已清空"}

    # ==================== WebSocket接口 ====================

    @router.websocket("/ws/market")
    async def websocket_market(websocket: WebSocket):
        """WebSocket连接"""
        await websocket.accept()

        # 注册到 TradingServer（内部会自动注册到 llm_analyzer 和 system_log）
        if trading_server:
            trading_server.add_ws_client(websocket)

        system_log = get_system_log()
        system_log.add_ws_client(websocket)

        try:
            await websocket.send_text(json.dumps({
                "type": "connected",
                "message": "已连接到行情监控服务"
            }))

            while True:
                try:
                    data = await websocket.receive_text()
                    msg = json.loads(data)

                    if msg.get('type') == 'ping':
                        await websocket.send_text(json.dumps({"type": "pong"}))

                except WebSocketDisconnect:
                    break

        except Exception as e:
            print(f"[WebSocket] 连接异常: {e}")

        finally:
            if trading_server:
                trading_server.remove_ws_client(websocket)
            system_log.remove_ws_client(websocket)

    # ==================== 大模型分析接口 ====================

    @protected_router.get("/llm/analysis")
    async def get_llm_analysis(symbol: Optional[str] = None) -> Dict:
        """获取大模型分析结果"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        result = trading_server.get_llm_analysis(symbol)
        return {
            "status": "ok",
            "data": result
        }

    @protected_router.get("/llm/status")
    async def get_llm_status() -> Dict:
        """获取大模型分析器状态"""
        if not trading_server:
            return {"status": "ok", "data": {"enabled": False, "message": "TradingServer 未初始化"}}

        return {
            "status": "ok",
            "data": trading_server.get_llm_status()
        }

    @protected_router.get("/llm/config")
    async def get_llm_config() -> Dict:
        """获取大模型配置"""
        if not trading_server:
            return {"status": "ok", "config": {"enabled": False, "message": "TradingServer 未初始化"}}

        return {
            "status": "ok",
            "config": trading_server.get_llm_config()
        }

    @protected_router.post("/llm/trigger")
    async def trigger_llm_analysis() -> Dict:
        """手动触发大模型分析"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        return trading_server.trigger_llm_analysis()

    @protected_router.post("/llm/configure")
    async def configure_llm(request: Request) -> Dict:
        """配置大模型参数"""
        if not trading_server:
            return {"status": "error", "message": "TradingServer 未初始化"}

        try:
            data = await request.json()
            result = trading_server.configure_llm(
                api_key=data.get("api_key"),
                api_base=data.get("api_base"),
                model=data.get("model")
            )
            return {"status": "ok", "data": result}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    router.include_router(protected_router)
    return router
