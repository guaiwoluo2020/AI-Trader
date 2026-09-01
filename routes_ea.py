#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EA 相关的接口路由
"""

import random
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import Optional, List, Dict
from auth import AuthUser, require_auth
from ea_auth import EAIdentity, ensure_supported_ea_version, require_ea_auth
from models import TradeInstruction
from market_data_source_policy import MarketDataSourcePolicy
from mysql_repositories import (
    EAActivationRepository,
    LiveTradeDealRepository,
    TradeExecutionRepository,
    TradingAccountRepository,
    UserRepository,
)
from instrument_price_store import get_instrument_price_store
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine
from routes_news import _normalize_calendar, _require_items, _validate_day
from market_event_repository import MarketEventRepository
from market.store.structure_plan_store import StructureTradePlanRepository

logger = logging.getLogger(__name__)


# 统计数据日志打印概率 (5%)
STATISTICS_LOG_PROBABILITY = 0.05
_calendar_publisher_account_id: Optional[int] = None
_calendar_publisher_seen_at: float = 0.0
CALENDAR_PUBLISHER_LEASE_SECONDS = 90


def create_ea_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建 EA 相关路由
    """
    router = APIRouter()
    market_source_policy = MarketDataSourcePolicy()

    @router.post("/ea/activate")
    async def activate_ea(request: Request) -> Dict:
        """用下载文件名中的一次性激活码换取 EA 账户凭证。"""
        try:
            data = await request.json()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="激活请求格式无效",
            ) from exc

        ea_version = ensure_supported_ea_version(data.get("ea_version", ""))

        result = EAActivationRepository().consume(
            str(data.get("activation_code", "")),
            mt5_login=str(data.get("mt5_login", "")),
            mt5_server=str(data.get("mt5_server", "")),
            ea_version=ea_version,
            program_name=str(data.get("program_name", "")),
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="激活码无效、已使用或已过期",
            )

        account, ea_token = result
        engine_manager.bind_account(account.user_id, account.account_id)
        market_notice = market_source_policy.activation_notice(
            account.user_id, account.account_id,
        )
        user = UserRepository().get_by_id(account.user_id)
        return {
            "status": "ok",
            "user_id": account.user_id,
            "account_id": account.account_id,
            "ea_token": ea_token,
            "is_admin": bool(user and user.role == "admin"),
            "market_source": market_notice,
        }

    @router.get("/get_trades")
    async def get_trades(
        symbol: str = Query(..., description="交易品种"),
        price: Optional[float] = Query(None, description="当前中间价"),
        identity: EAIdentity = Depends(require_ea_auth),
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
        server = engine_manager.get_engine_for_ea(identity)
        market_policy = market_source_policy.resolve(
            identity.user_id, identity.account_id, symbol,
        )
        tick_results = {}
        if (
            price is not None and float(price) > 0
            and market_policy.get("is_market_primary")
        ):
            tick_results = engine_manager.process_user_market_tick(
                identity.user_id,
                market_source_policy.execution_account_ids(
                    identity.user_id, market_policy.get("broker_name", ""),
                ),
                symbol, float(price),
            )
        result = server.get_trades_by_symbol(
            symbol, price, evaluate_price=False,
        )
        result["process_result"] = tick_results.get(identity.account_id, {})
        result["market_source"] = market_policy
        if market_policy.get("mode") == "blocked":
            server.trading_instruction_service.clear_by_symbol(symbol)
            server.pending_order_service.clear_all()
            result["trades"] = []
            result["pending_orders"] = []
        signal_snapshots = (
            server.get_tick_signal_snapshots(symbol, price)
            if price is not None and float(price) > 0
            and market_policy.get("is_market_primary") else {}
        )
        paper_execution = {"filled": 0, "closed": 0, "rejected": 0}
        if price is not None and float(price) > 0 and market_policy.get("is_market_primary"):
            try:
                # EA 的 get_trades 轮询就是实时 Tick 通道。先用本次报价撮合
                # 上一 Tick 产生的模拟订单，再评估本 Tick 的新策略信号。
                structures = {}
                for structure_period in ("M1", "M5", "M15", "H1", "H4"):
                    structure = server.get_structure_context(symbol, structure_period)
                    if structure:
                        structures[structure_period] = structure
                paper_execution = engine_manager.paper_trading.process_tick(
                    identity.user_id, symbol, float(price), float(price),
                    structures=structures,
                )
            except Exception as exc:
                # 模拟账户故障不能阻断 EA 获取真实交易指令。
                print(f"[PaperTrading] 模拟撮合失败: {exc}")
        paper_orders_created = 0
        try:
            if not market_policy.get("is_market_primary"):
                raise RuntimeError("非主行情账户不重复驱动模拟策略")
            paper_orders_created = engine_manager.paper_trading.process_strategy_signals(
                identity.user_id, symbol, price, server.strategy_service,
                quote_account_id=identity.account_id,
                signal_snapshots=signal_snapshots,
            )
        except Exception as exc:
            # 模拟账户故障不能阻断 EA 获取真实交易指令。
            if market_policy.get("is_market_primary"):
                print(f"[PaperTrading] 创建模拟订单失败: {exc}")
        result["paper_orders_created"] = paper_orders_created
        result["paper_execution"] = paper_execution

        # 如果结果不为空，记录到运行日志
        trades = result.get("trades", [])
        close_tickets = result.get("close_tickets", [])
        position_updates = result.get("position_updates", [])
        position_partials = result.get("position_partials", [])
        pivot_alerts = result.get("pivot_alerts", [])

        if trades or close_tickets or position_updates or position_partials:
            import json
            system_log = server.system_log

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
            if position_updates:
                system_log.add_log(
                    "position_sl_update",
                    {"updates": position_updates},
                    symbol=symbol,
                    message=f"移动止损/修改止损: {len(position_updates)}个"
                )
            if position_partials:
                system_log.add_log(
                    "position_partial_close",
                    {"partials": position_partials},
                    symbol=symbol,
                    message=f"分批止盈: {len(position_partials)}个"
                )

            # 记录汇总日志
            system_log.add_log(
                "ea_trade_request",
                {
                    "trades_count": len(trades),
                    "close_count": len(close_tickets),
                    "position_update_count": len(position_updates),
                    "position_partial_count": len(position_partials),
                    "pivot_alerts_count": len(pivot_alerts)
                },
                symbol=symbol,
                message=(
                    f"下发交易指令: {len(trades)}个开仓, "
                    f"{len(close_tickets)}个平仓, "
                    f"{len(position_updates)}个改止损, "
                    f"{len(position_partials)}个分批止盈"
                )
            )

        return result

    @router.post("/ea/trade_execution")
    async def report_trade_execution(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            report = TradeExecutionRepository().record(
                identity.user_id, identity.account_id, payload
            )
            attribution = report.get("position_attribution") or {}
            trade_plan_id = str(attribution.get("trade_plan_id") or "")
            strategy_id = str(attribution.get("strategy_id") or "")
            if trade_plan_id and strategy_id:
                plan_repo = StructureTradePlanRepository()
                deployment = plan_repo.storage.fetchone(
                    "SELECT deployment_id FROM strategy_deployments "
                    "WHERE user_id=? AND account_id=? AND strategy_id=? "
                    "AND execution_mode='live' ORDER BY updated_at DESC LIMIT 1",
                    (identity.user_id, identity.account_id, strategy_id),
                )
                if deployment:
                    plan_repo.record_execution(
                        identity.user_id, identity.account_id,
                        str(deployment["deployment_id"]), strategy_id,
                        trade_plan_id,
                        str(attribution.get("trade_plan_group_id") or ""),
                        "filled" if report.get("success") else "rejected",
                        order_id=str(report.get("order_id") or ""),
                        reason=str(report.get("error_message") or ""),
                        payload=attribution,
                    )
            server = engine_manager.get_engine_for_ea(identity)
            action = str(report.get("action") or "").lower()
            if action == "partial_close":
                instruction_id = str(report.get("instruction_id") or "")
                parts = instruction_id.split("-")
                if len(parts) >= 3 and parts[0] == "exit" and parts[1].isdigit():
                    ticket = int(parts[1])
                    state = server._managed_position_state.get(ticket) or {}
                    level_ids = parts[2:]
                    if not report.get("success"):
                        done = set(state.get("partial_levels_done") or [])
                        done.difference_update(level_ids)
                        state["partial_levels_done"] = sorted(done)
                    server._position_event_repository.record(
                        int(identity.user_id), int(identity.account_id), str(ticket),
                        "multi_level_exit_execution",
                        (
                            "多层结构退出已由MT5执行"
                            if report.get("success") else
                            f"多层结构退出执行失败，将重试：{report.get('error_message') or '未知原因'}"
                        ),
                        symbol=str(report.get("symbol") or ""), ticket=ticket,
                        rule_type="multi_level_exit_execution",
                        status="executed" if report.get("success") else "failed",
                        price=float(report.get("executed_price") or 0),
                        volume=float(report.get("executed_volume") or 0),
                        payload={
                            "instruction_id": instruction_id,
                            "level_ids": level_ids,
                            "retcode": report.get("retcode"),
                            "error_message": report.get("error_message", ""),
                        },
                    )
            if action == "position_modify_sl":
                message = (
                    f"止损调整成功: ticket={report.get('mt5_position_id')}，"
                    f"实际SL={report.get('executed_price')}"
                    if report["success"] else
                    f"止损调整失败: ticket={report.get('mt5_position_id')}，"
                    f"{report['error_message']}"
                )
            else:
                message = (
                    f"MT5 成交 #{report['mt5_deal']}，滑点 {report['slippage']:.5f}"
                    if report["success"]
                    else f"MT5 下单失败: {report['error_message']}"
                )
            server.system_log.add_log(
                "trade_execution_success" if report["success"] else "trade_execution_failed",
                report,
                symbol=report["symbol"],
                message=message,
            )
            return {"status": "ok", "report": report}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/ea/calendar/daily")
    async def receive_ea_calendar(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        """接收ADMIN主EA上报的MT5公共财经日历。

        财经日历是平台级数据，不按交易账户保存。EA认证之后仍需校验
        user role，普通用户即使知道接口也不能覆盖公共日历。
        """
        global _calendar_publisher_account_id, _calendar_publisher_seen_at
        admin = UserRepository().get_by_id(identity.user_id)
        if admin is None or admin.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="只有ADMIN账户的主EA可以上报财经日历",
            )
        now = __import__("time").time()
        if (
            _calendar_publisher_account_id is not None
            and _calendar_publisher_account_id != identity.account_id
            and now - _calendar_publisher_seen_at < CALENDAR_PUBLISHER_LEASE_SECONDS
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前已有ADMIN主EA负责财经日历上报",
            )
        _calendar_publisher_account_id = identity.account_id
        _calendar_publisher_seen_at = now
        try:
            payload = await request.json()
            day = _validate_day(payload.get("date"))
            source = str(payload.get("source") or "mt5_calendar").strip()
            events = _normalize_calendar(day, _require_items(payload, "events"))
            repository = MarketEventRepository()
            count = repository.replace_calendar_day(day, events, source)
            key_events = [
                {**event, "title": event["name"], "category": "economic_calendar"}
                for event in events
                if int(event.get("importance", 0)) >= 2
            ]
            key_count = repository.replace_key_event_day(day, key_events, source)
            logger.info(
                "EA calendar received: user_id=%s account_id=%s date=%s events=%s key_events=%s source=%s",
                identity.user_id, identity.account_id, day, count, key_count, source,
            )
            return {
                "status": "ok",
                "date": day,
                "count": count,
                "key_event_count": key_count,
                "scope": "global",
                "publisher_user_id": identity.user_id,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"财经日历处理失败: {exc}") from exc

    @router.post("/send_statistics")
    async def send_statistics(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
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
            server = engine_manager.get_engine_for_ea(identity)
            server.save_statistics(data)
            if data.get("balance") is not None and data.get("equity") is not None:
                TradingAccountRepository().update_financial_snapshot(
                    identity.account_id,
                    balance=data.get("balance"),
                    equity=data.get("equity"),
                    free_margin=data.get("freeMargin", data.get("equity")),
                    margin=data.get("margin", 0),
                )
            bid = data.get("bidPrice")
            ask = data.get("askPrice")
            if bid is not None:
                # 策略评估严格由EA的/get_trades Tick通道驱动；统计通道不再
                # 重复触发实盘或模拟策略，避免30秒心跳产生非Tick决策。
                try:
                    get_instrument_price_store().record(
                        identity.user_id,
                        identity.account_id,
                        str(data.get("symbol", "")),
                        float(bid),
                        float(ask) if ask is not None else None,
                    )
                except Exception as exc:
                    # 关联候选是辅助功能，不能影响 EA 统计和模拟撮合。
                    print(f"[InstrumentMapping] 报价观察失败: {exc}")

            # 随机打印日志 (5%概率)
            if random.random() < STATISTICS_LOG_PROBABILITY:
                symbol = data.get('symbol', 'UNKNOWN')
                system_log = server.system_log
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
    async def close_position(
        request: Request,
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
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
            _, server = resolve_web_engine(engine_manager, user, account_id)
            server.add_close_position_instruction(symbol, ticket)

            print(f"[EA API] 平仓指令已添加: {symbol} ticket={ticket}")

            # 记录日志
            system_log = server.system_log
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

    @router.post("/trade_history")
    async def receive_trade_history(
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
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
        try:
            data = await request.json()
            deals = data.get('deals', [])

            if not deals:
                return {
                    "status": "ok", "message": "无数据需要更新", "count": 0,
                    "persisted": {"inserted": 0, "updated": 0, "unchanged": 0, "invalid": 0},
                }

            # 使用新的交易历史服务
            server = engine_manager.get_engine_for_ea(identity)
            new_count = server.trade_history_service.process_deals(deals)
            # 成交历史不能只依赖运行时 24 小时缓存；账户页和策略回放需要
            # 在服务重启后仍可读取，因此同步写入 MySQL 持久化表。
            persisted = LiveTradeDealRepository().record_many(
                identity.user_id, identity.account_id, deals
            )

            changed_count = persisted["inserted"] + persisted["updated"]
            if new_count or changed_count:
                server.system_log.add_log(
                    "trade_history_update",
                    {
                        "deals_received": len(deals),
                        "deals_new_runtime": new_count,
                        "deals_inserted": persisted["inserted"],
                        "deals_updated": persisted["updated"],
                        "deals_unchanged": persisted["unchanged"],
                        "total_deals": len(server.trade_history_store.get()),
                    },
                    message=(
                        f"交易历史上报: 新增{persisted['inserted']}条, "
                        f"更新{persisted['updated']}条"
                    ),
                )

            return {
                "status": "ok",
                "message": "交易历史已更新",
                "count": new_count,
                "persisted_count": changed_count,
                "persisted": persisted,
            }

        except Exception as e:
            print(f"[ERROR] trade_history 更新异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    return router
