#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EA 相关的接口路由
"""

import random
from fastapi import APIRouter, Query, Request
from typing import Optional, List, Dict
from models import TradeInstruction
from server import TradingServer
from market.system_log import get_system_log


# 统计数据日志打印概率 (5%)
STATISTICS_LOG_PROBABILITY = 0.05


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

        # 如果结果不为空，记录到运行日志
        trades = result.get("trades", [])
        close_tickets = result.get("close_tickets", [])
        pivot_alerts = result.get("pivot_alerts", [])

        if trades or close_tickets:
            import json
            system_log = get_system_log()

            # 打印完整返回数据
            print(f"[EA API] 返回给EA的数据: {json.dumps(result, ensure_ascii=False)}")

            # 记录交易指令日志
            if trades:
                for t in trades:
                    action_text = '买入' if t.get('action') == 'b' else '卖出'
                    system_log.add_log(
                        "order_generated",
                        {
                            "order_id": t.get('order_id'),
                            "action": t.get('action'),
                            "price": t.get('price'),
                            "mount": t.get('mount'),
                            "sl": t.get('sl'),
                            "tp": t.get('tp')
                        },
                        symbol=t.get('symbol'),
                        message=f"{action_text} @ {t.get('price')}, 手数={t.get('mount')}"
                    )

            # 记录平仓指令日志
            if close_tickets:
                system_log.add_log(
                    "close_position",
                    {"tickets": close_tickets},
                    symbol=symbol,
                    message=f"平仓指令: {close_tickets}"
                )

            # 记录汇总日志
            system_log.add_log(
                "ea_trade_request",
                {
                    "trades_count": len(trades),
                    "close_count": len(close_tickets),
                    "pivot_alerts_count": len(pivot_alerts)
                },
                symbol=symbol,
                message=f"下发交易指令: {len(trades)}个开仓, {len(close_tickets)}个平仓"
            )

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
        import json
        try:
            data = await request.json()
            server.save_statistics(data)

            # 随机打印日志 (5%概率)
            if random.random() < STATISTICS_LOG_PROBABILITY:
                symbol = data.get('symbol', 'UNKNOWN')
                system_log = get_system_log()
                system_log.add_log(
                    "ea_statistics",
                    {
                        "tick_count": data.get('tickCount'),
                        "bid": data.get('bidPrice'),
                        "ask": data.get('askPrice'),
                        "spread": data.get('spread'),
                        "spread_points": data.get('spreadPoints'),
                        "balance": data.get('balance'),
                        "equity": data.get('equity')
                    },
                    symbol=symbol,
                    message=f"Tick: {data.get('tickCount')}, Spread: {data.get('spreadPoints', 0):.1f}pts, Balance: {data.get('balance')}"
                )

            return {"status": "ok", "message": "统计数据已保存"}
        except Exception as e:
            print(f"[ERROR] Failed to parse JSON: {e}")
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
            symbol = data.get('symbol', '')

            if not ticket:
                return {"status": "error", "message": "缺少订单号"}

            # 添加平仓指令到队列
            server.add_close_position_instruction(symbol, ticket)

            print(f"[EA API] 平仓指令已添加: {symbol} ticket={ticket}")

            # 记录日志
            system_log = get_system_log()
            system_log.add_log(
                "close_position",
                {"ticket": ticket},
                symbol=symbol,
                message=f"Ticket: {ticket}"
            )

            return {"status": "ok", "message": "平仓指令已添加"}

        except Exception as e:
            print(f"[ERROR] close_position 异常: {str(e)}")
            return {"status": "error", "message": str(e)}

    @router.post("/calendar")
    async def send_calendar(request: Request) -> Dict:
        """
        接收 EA 发送的财经日历数据（来自MT5 API）

        EA调用MT5的calendar_*函数获取数据后，推送到此接口

        请求体:
        ```json
        {
            "events": [
                {
                    "id": "12345",
                    "name": "Nonfarm Payrolls",
                    "name_en": "Nonfarm Payrolls",
                    "country": "US",
                    "currency": "USD",
                    "importance": 3,
                    "publish_time": "2026-03-16T20:30:00",
                    "forecast": "200K",
                    "previous": "180K",
                    "actual": "",
                    "unit": "K",
                    "event_type": "indicator"
                }
            ]
        }
        ```

        返回:
        ```json
        {
            "status": "ok",
            "message": "财经日历已更新",
            "count": 150
        }
        ```
        """
        import json as json_module
        import re as re_module
        try:
            # 先获取原始body
            raw_body = await request.body()
            raw_text = raw_body.decode('utf-8', errors='replace')

            print(f"[calendar] 收到请求, 数据长度: {len(raw_text)} 字节")

            # 清理所有控制字符 (0x00-0x1F 和 0x7F)
            # JSON字符串值中不能包含任何原始控制字符
            def clean_control_chars(text):
                # 使用正则表达式一次性清理所有控制字符
                # 包括 TAB(0x09), LF(0x0A), CR(0x0D), DEL(0x7F) 在内
                # 因为 JSON 字符串值中这些字符必须转义，不能直接出现
                import re
                pattern = re.compile(r'[\x00-\x1f\x7f]')
                cleaned = pattern.sub('', text)
                removed_count = len(text) - len(cleaned)
                if removed_count > 0:
                    print(f"[calendar] 已移除 {removed_count} 个控制字符")
                return cleaned

            cleaned_text = clean_control_chars(raw_text)

            try:
                data = json_module.loads(cleaned_text)
            except json_module.JSONDecodeError as e:
                # 如果仍然失败，打印问题位置附近的数据
                print(f"[ERROR] calendar JSON解析失败: {e}")
                error_pos = e.pos if hasattr(e, 'pos') else 0
                start = max(0, error_pos - 50)
                end = min(len(cleaned_text), error_pos + 50)
                print(f"[ERROR] 问题位置附近数据[{start}:{end}]: {repr(cleaned_text[start:end])}")
                return {"status": "error", "message": f"JSON解析失败: {e}"}

            events = data.get('events', [])

            print(f"[calendar] 解析成功, 收到 {len(events)} 个事件")

            if not events:
                print("[calendar] 警告: events数组为空")
                return {"status": "ok", "message": "无数据需要更新", "count": 0}

            from market.news_store import get_news_store
            news_store = get_news_store()

            # 更新财经日历
            updated_count = news_store.update_calendar_from_mt5(events)

            # 记录日志 - MT5上报财经日历
            system_log = get_system_log()
            system_log.add_log(
                "mt5_calendar_update",
                {
                    "events_received": len(events),
                    "events_updated": updated_count,
                    "total_events": news_store.get_status().get('calendar_events', 0)
                },
                message=f"MT5上报财经日历: 收到{len(events)}条, 更新{updated_count}条"
            )

            return {
                "status": "ok",
                "message": "财经日历已更新",
                "count": updated_count
            }

        except Exception as e:
            print(f"[ERROR] calendar 更新异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    @router.post("/calendar_event_result")
    async def send_calendar_event_result(request: Request) -> Dict:
        """
        接收 EA 发送的事件结果（事件发布后EA获取实际值）

        请求体:
        ```json
        {
            "event_id": "12345",
            "actual": "210K",
            "forecast": "200K",
            "previous": "180K"
        }
        ```

        返回:
        ```json
        {
            "status": "ok",
            "message": "事件结果已更新"
        }
        ```
        """
        try:
            data = await request.json()
            event_id = data.get('event_id')
            actual = data.get('actual', '')
            forecast = data.get('forecast', '')
            previous = data.get('previous', '')

            if not event_id:
                return {"status": "error", "message": "缺少事件ID"}

            from market.news_store import get_news_store
            news_store = get_news_store()

            # 获取事件
            event = news_store.get_event_by_id(event_id)
            if not event:
                return {"status": "error", "message": f"未找到事件: {event_id}"}

            # 更新事件结果
            event.actual = actual
            if forecast:
                event.forecast = forecast
            if previous:
                event.previous = previous

            # 计算结果（好于/差于/符合预期）
            result = _calculate_event_result(actual, event.forecast)
            event.result = result
            event.analyzed = True

            # 记录日志 - MT5上报事件结果
            system_log = get_system_log()
            system_log.add_log(
                "mt5_event_result",
                {
                    "event_id": event_id,
                    "event_name": event.name,
                    "actual": actual,
                    "forecast": event.forecast,
                    "previous": previous,
                    "result": result
                },
                symbol=event.currency,
                message=f"MT5事件结果: {event.name} 实际={actual} 预测={event.forecast} ({result})"
            )

            return {
                "status": "ok",
                "message": "事件结果已更新"
            }

        except Exception as e:
            print(f"[ERROR] calendar_event_result 更新异常: {str(e)}")
            return {"status": "error", "message": str(e)}

    @router.post("/trade_history")
    async def receive_trade_history(request: Request) -> Dict:
        """
        接收 EA 发送的交易历史数据

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

        返回:
        ```json
        {
            "status": "ok",
            "message": "交易历史已更新",
            "count": 50
        }
        ```
        """
        import json as json_module
        try:
            data = await request.json()
            deals = data.get('deals', [])

            print(f"[trade_history] 收到 {len(deals)} 条成交记录")

            if not deals:
                return {"status": "ok", "message": "无数据需要更新", "count": 0}

            from market.trade_history_store import get_trade_history_store
            store = get_trade_history_store()

            # 更新交易历史
            new_count = store.update_from_ea(deals)

            # 记录日志
            system_log = get_system_log()
            system_log.add_log(
                "trade_history_update",
                {
                    "deals_received": len(deals),
                    "deals_new": new_count,
                    "total_deals": len(store.get_all_deals())
                },
                message=f"交易历史上报: 收到{len(deals)}条, 新增{new_count}条"
            )

            return {
                "status": "ok",
                "message": "交易历史已更新",
                "count": new_count
            }

        except Exception as e:
            print(f"[ERROR] trade_history 更新异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    return router


def _calculate_event_result(actual: str, forecast: str) -> str:
    """计算事件结果"""
    try:
        # 尝试提取数字
        import re
        actual_num = float(re.sub(r'[^\d.-]', '', actual))
        forecast_num = float(re.sub(r'[^\d.-]', '', forecast))

        if forecast_num == 0:
            return 'unknown'

        diff_pct = (actual_num - forecast_num) / abs(forecast_num)

        if abs(diff_pct) < 0.05:
            return 'in_line'
        elif diff_pct > 0:
            return 'better'
        else:
            return 'worse'
    except:
        return 'unknown'