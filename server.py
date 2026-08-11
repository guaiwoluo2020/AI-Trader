#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易服务核心类
内部聚合信号层，对外暴露策略层
"""

from collections import deque, defaultdict
from typing import List, Dict, Optional, Set
from datetime import datetime
import threading
import asyncio
import json
import time

from market.models import KlineData, PivotPoint, LLMConfig, LLMAnalysisResult
from market.models import TechTrendState, TechResonanceResult, TechTradeSuggestion
from market.models import PendingOrder, TradingInstruction, TradingSignal, TradingDecision
from market.models import StatisticsData, PositionData, TradeDeal
from market.store import KlineStore, PivotStore, LLMStore, TechStore
from market.store import PendingOrderStore, TradingInstructionStore, SignalStore, StrategyStore
from market.store import StatisticsStore, PositionStore, TradeHistoryStore
from market.services import KlineService, PivotService, LLMService, TechService
from market.services import PendingOrderService, TradingInstructionService
from market.services import SignalService, StrategyService, RiskManager
from market.services import (
    KeyLevelSignalGenerator, AIEntrySignalGenerator,
    MovingAverageSignalGenerator, AlphaFactorSignalGenerator,
)
from market.services import StatisticsService, PositionService, TradeHistoryService
from market.trade_config import TradeConfig
from market.llm_analyzer import LLMAnalyzer
from market.system_log import SystemLog
from sqlite_storage import (
    PositionManagementPolicyRepository,
    RuntimeStateRepository,
    SharedAIRuntimeRepository,
    StrategyDeploymentRepository,
    TradingAccountRepository,
)


class TradingServer:
    """
    交易服务主类

    架构：
    - 内部：行情模块 → 信号层 → 策略层 → 订单/指令
    - 对外：只暴露策略服务接口
    """

    def __init__(self, user_id: int = None, account_id: int = None):
        self.user_id = user_id
        self.account_id = account_id
        self.strategy_deployments = StrategyDeploymentRepository()
        self.account_repository = TradingAccountRepository()

        # 线程锁
        self.lock = threading.RLock()
        self.system_log = SystemLog(user_id=user_id, account_id=account_id)

        # ==================== 行情模块（内部） ====================
        # 存储层
        self.kline_store = KlineStore()
        self.pivot_store = PivotStore()
        self.llm_store = LLMStore(user_id=user_id, account_id=account_id)
        self.tech_store = TechStore()

        # 服务层
        self.kline_service = KlineService(self.kline_store)
        self.pivot_service = PivotService(self.pivot_store, self.kline_store)
        self.llm_service = LLMService(self.llm_store, self.kline_service)
        self.tech_service = TechService(self.tech_store, self.kline_store, self.pivot_store)

        # LLM 分析器
        self.llm_analyzer = LLMAnalyzer(self.llm_service)

        # ==================== 统计/持仓/交易历史模块 ====================
        # 存储层
        self.statistics_store = StatisticsStore(
            user_id=user_id,
            account_id=account_id,
        )
        self.position_store = PositionStore(
            user_id=user_id,
            account_id=account_id,
        )
        self.trade_history_store = TradeHistoryStore(
            user_id=user_id,
            account_id=account_id,
        )

        # 服务层
        self.statistics_service = StatisticsService(self.statistics_store)
        self.position_service = PositionService(self.position_store)
        self.trade_history_service = TradeHistoryService(self.trade_history_store)

        # TechService 使用统计服务获取价差
        self.tech_service.set_statistics_service(self.statistics_service)

        # ==================== 信号层（内部，不暴露） ====================
        # 存储层
        self._signal_store = SignalStore()

        # 服务层
        self._signal_service = SignalService(self._signal_store)

        # ==================== 策略层（对外暴露） ====================
        # 存储层
        self._strategy_store = StrategyStore(user_id=user_id)
        self.llm_service.set_strategy_store(self._strategy_store)

        # 风险管理器
        self._risk_manager = RiskManager(
            user_id=user_id,
            account_id=account_id,
        )

        # 服务层
        self.strategy_service = StrategyService(
            self._strategy_store,
            self._signal_service,
            self._risk_manager
        )

        # ==================== 交易配置 ====================
        self.trade_config = (
            TradeConfig(user_id=user_id)
            if user_id is not None
            else TradeConfig.get_instance()
        )

        # 注册信号生成器
        self._setup_signal_generators()

        # ==================== 订单/指令模块 ====================
        # 存储层
        self.pending_order_store = PendingOrderStore(
            user_id=user_id,
            account_id=account_id,
        )
        self.trading_instruction_store = TradingInstructionStore(
            user_id=user_id,
            account_id=account_id,
        )
        self._runtime_repository = (
            RuntimeStateRepository(user_id, account_id)
            if user_id is not None
            else None
        )
        self._close_position_instructions = defaultdict(list)
        self._position_update_instructions = defaultdict(dict)
        self._managed_position_state = {}
        self._position_policy_repository = PositionManagementPolicyRepository()
        self._ma_trailing_extremes = {}
        if self._runtime_repository:
            for item in self._runtime_repository.list_entities(
                "close_instruction",
                statuses=["pending"],
            ):
                self._close_position_instructions[item["symbol"]].append(
                    int(item["ticket"])
                )

        # 服务层
        self.pending_order_service = PendingOrderService(self.pending_order_store)
        self.trading_instruction_service = TradingInstructionService(self.trading_instruction_store)

        # 设置订单确认回调
        self.pending_order_service.set_confirm_callback(self._on_order_confirmed)

        # 更新策略服务的订单服务引用
        self.strategy_service.set_pending_order_service(self.pending_order_service)

        # 策略服务使用持仓服务进行风险管理
        self.strategy_service.set_position_service(self.position_service)
        self.strategy_service.set_pivot_service(self.pivot_service)

        # 风险管理器使用统计服务获取账户信息
        self._risk_manager.set_statistics_service(self.statistics_service)
        self._risk_manager.set_trade_history_service(self.trade_history_service)

        # ==================== WebSocket 广播 ====================
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()
        self._main_loop = None

        # ==================== 决策历史 ====================
        self._decision_history: deque = deque(maxlen=200)
        if self._runtime_repository:
            for item in self._runtime_repository.list_entities(
                "strategy_decision"
            ):
                try:
                    self._decision_history.append(TradingDecision.from_dict(item))
                except (TypeError, ValueError):
                    continue

        # 兼容旧代码的别名
        self.market_store = self.kline_store
        self.pivot_detector = self.pivot_service
        self.trend_analyzer = self.tech_service
        self.pending_orders = self.pending_order_service
        self.trade_instructions = defaultdict(list)
        # 统计数据历史兼容（已迁移到 statistics_store）
        self.statistics_history = self.statistics_store._all_data

        print(
            f"[TradingServer] 交易服务已初始化 "
            f"(user_id={self.user_id}, account_id={self.account_id})"
        )

    def _setup_signal_generators(self):
        """设置信号生成器"""
        # 关键点位信号生成器
        key_level_generator = KeyLevelSignalGenerator()
        self._signal_service.register_generator("key_level", key_level_generator)

        # AI入场信号生成器
        ai_entry_generator = AIEntrySignalGenerator(SharedAIRuntimeRepository())
        ai_entry_generator.set_llm_analyzer(self.llm_analyzer)
        self._ai_entry_generator = ai_entry_generator
        self._signal_service.register_generator("ai_entry", ai_entry_generator)

        moving_average_generator = MovingAverageSignalGenerator(self.kline_store)
        self._signal_service.register_generator(
            "moving_average", moving_average_generator
        )

        alpha_factor_generator = AlphaFactorSignalGenerator(self.kline_store)
        self._signal_service.register_generator(
            "alpha_factor", alpha_factor_generator
        )

    # ==================== WebSocket 管理 ====================

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print("[TradingServer] 已设置主事件循环")

        # 同时设置内部模块的事件循环
        self.llm_analyzer.set_event_loop(loop)
        self.system_log.set_event_loop(loop)

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            # 同时注册到内部模块
            self.llm_analyzer.add_ws_client(client)
            self.system_log.add_ws_client(client)
            print(f"[TradingServer] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            # 同时从内部模块移除
            self.llm_analyzer.remove_ws_client(client)
            self.system_log.remove_ws_client(client)
            print(f"[TradingServer] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def get_ws_client_count(self) -> int:
        """获取WebSocket客户端数量"""
        with self._ws_lock:
            return len(self._ws_clients)

    def close(self):
        """停止账户引擎的后台任务并清理连接。"""
        self.llm_analyzer.stop()
        self.system_log.close()
        with self._ws_lock:
            self._ws_clients.clear()

    def can_evict(self) -> bool:
        """有长连接的账户引擎不能被空闲回收。"""
        return self.get_ws_client_count() == 0

    def cleanup_pending_orders(self) -> int:
        before = {
            order.order_id
            for order in self.pending_order_service.get_orders()
        }
        count = self.pending_order_service.cleanup_expired()
        if count:
            after = {
                order.order_id
                for order in self.pending_order_service.get_orders()
            }
            for order_id in before - after:
                self.update_decision_status(order_id, "expired")
        return count

    def cleanup_signals(self) -> int:
        return self._signal_service.cleanup_expired()

    def run_scheduled_llm_analysis(self) -> bool:
        self.llm_service.set_allowed_strategy_ids(
            self._active_strategy_ids("live")
        )
        return self.llm_analyzer.run_scheduled_analysis()

    def _active_strategy_ids(self, mode: str = "live") -> List[str]:
        if self.user_id is None:
            return []
        return self.strategy_deployments.list_active_strategy_ids(
            self.user_id, self.account_id or 0, mode
        )

    def _broadcast(self, data: Dict):
        """广播数据到所有WebSocket客户端"""
        message = json.dumps(data, ensure_ascii=False)

        with self._ws_lock:
            clients = list(self._ws_clients)

        if not clients:
            return

        if self._main_loop and self._main_loop.is_running():
            for client in clients:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._send_to_client(client, message),
                        self._main_loop
                    )
                except Exception as e:
                    print(f"[TradingServer] 发送WebSocket消息失败: {e}")

    async def _send_to_client(self, client, message: str):
        """发送消息到客户端"""
        try:
            await client.send_text(message)
        except Exception as e:
            print(f"[TradingServer] 发送消息到客户端失败: {e}")
            with self._ws_lock:
                self._ws_clients.discard(client)

    def _broadcast_decision(self, decision: TradingDecision):
        """广播交易决策"""
        self._broadcast({
            "type": "trading_decision",
            "data": decision.to_dict()
        })

    def _broadcast_pending_order(self, order: PendingOrder):
        """广播待确认订单"""
        self._broadcast({
            "type": "pending_order",
            "data": order.to_dict()
        })

    # ==================== 价格处理与决策 ====================

    def process_price(self, symbol: str, current_price: float) -> Dict:
        """
        处理价格变动，生成决策

        这是核心入口：
        1. 信号层生成信号
        2. 策略层综合决策
        3. 自动执行决策（生成PendingOrder）

        Args:
            symbol: 品种
            current_price: 当前价格

        Returns:
            处理结果
        """
        result = {
            "signals_generated": 0,
            "decision": None,
            "decisions": [],
            "pending_order": None,
            "pending_orders": [],
        }

        if not self.trade_config.enabled:
            return result

        account = (
            self.account_repository.get_by_id(self.user_id, self.account_id)
            if self.user_id is not None and self.account_id
            else None
        )
        if account is not None:
            if account.status != "active" or not account.trading_enabled:
                return result
            self._risk_manager.set_account_limits(
                max_positions=account.max_total_positions,
                max_single_volume=account.max_single_volume,
                daily_loss_limit=account.daily_loss_limit,
                daily_order_limit=account.daily_order_limit,
            )

        # Each deployed strategy owns its signal generation and cooldown state.
        strategy_ids = self._active_strategy_ids("live")
        self.strategy_service.set_allowed_strategy_ids(strategy_ids)
        allowed_ids = set(strategy_ids)
        decisions = []
        for strategy in self.strategy_service.get_strategies(symbol):
            if strategy.strategy_id not in allowed_ids:
                continue
            signals = self._signal_service.generate_signals_for_strategy(
                symbol, current_price, strategy
            )
            self._manage_strategy_positions(
                strategy, symbol, current_price, signals
            )
            trigger_count = sum(
                1 for signal in signals
                if getattr(signal, "is_entry_trigger", True)
            )
            result["signals_generated"] += trigger_count
            if trigger_count:
                print(
                    f"[TradingServer] {symbol} 策略 {strategy.strategy_id} "
                    f"生成了 {trigger_count} 个入场触发"
                )
            decision = self.strategy_service.make_decision(
                symbol, current_price, force_signals=signals, strategy=strategy
            )
            if decision is not None:
                decisions.append(decision)

        for decision in decisions:
            if account is not None and not account.auto_trading_enabled:
                decision.auto_execute = False
            # 3. 自动执行决策（如果允许）
            if decision.action != "none" and decision.status != "rejected":
                order_id = self.strategy_service.execute_decision(decision)
                if order_id:
                    pending_order = {
                        "order_id": order_id,
                        "symbol": decision.symbol,
                        "strategy_id": decision.strategy_id,
                        "strategy_name": decision.strategy_name,
                        "auto_execute": decision.auto_execute,
                        "auto_executed": decision.auto_executed,
                        "confirmed": decision.auto_executed,
                        "action": decision.action,
                        "price": decision.entry_price,
                        "volume": decision.volume,
                        "sl": decision.sl,
                        "tp": decision.tp
                    }
                    result["pending_orders"].append(pending_order)

            self._record_decision(decision)

            # 创建待确认订单后再序列化和广播，确保携带真实 order_id。
            result["decisions"].append(decision.to_dict())
            self._broadcast_decision(decision)

        # 保留单策略时代的响应字段，旧客户端继续读取第一条结果。
        if result["decisions"]:
            result["decision"] = result["decisions"][0]
        if result["pending_orders"]:
            result["pending_order"] = result["pending_orders"][0]

        return result

    def _manage_strategy_positions(
        self, strategy, symbol: str, current_price: float, signals: List[TradingSignal],
    ) -> None:
        """Evaluate EA positions with the strategy's independent manager."""
        from market.services import PositionManager

        policy = self._position_policy_repository.get(
            int(self.user_id or 0), strategy.position_management_policy_id
        )
        if policy is None:
            return
        pivots = [
            item.to_dict()
            for period in self.pivot_store.get_all_periods(symbol)
            for item in self.pivot_store.get_pivot_objects(symbol, period)
        ]
        reverse = any(
            getattr(signal, "is_entry_trigger", True)
            and getattr(signal, "action", "") in {"buy", "sell"}
            for signal in signals
        )
        manager = PositionManager()
        for position in self.position_service.get_position_objects(symbol):
            parts = str(position.comment or "").split("|")
            if len(parts) != 3 or parts[0] != "AIT":
                continue
            position_strategy_id, source_id = parts[1], parts[2]
            if position_strategy_id != strategy.strategy_id:
                continue
            ticket = int(position.ticket)
            state = self._managed_position_state.setdefault(ticket, {
                "direction": position.direction,
                "entry_price": float(position.price_open),
                "stop_loss": float(position.sl),
                "take_profit": float(position.tp),
                "initial_risk": abs(float(position.price_open) - float(position.sl)),
                "favorable_price": float(position.price_open),
                "holding_bars": 0,
                "opened_at": position.opened_at or datetime.now(),
            })
            state["stop_loss"] = float(position.sl or state["stop_loss"])
            state["take_profit"] = float(position.tp or state["take_profit"])
            state["favorable_price"] = (
                max(state["favorable_price"], current_price)
                if position.is_buy else min(state["favorable_price"], current_price)
            )
            action = manager.evaluate(
                policy.config, state,
                {"price": current_price, "time": int(datetime.now().timestamp())},
                pivots=pivots,
                reverse_signal=(
                    reverse and any(
                        getattr(signal, "is_entry_trigger", True)
                        and getattr(signal, "action", "") in {"buy", "sell"}
                        and getattr(signal, "action", "") != position.direction
                        for signal in signals
                    )
                ),
            )
            if action.action == "close":
                self.add_close_position_instruction(symbol, position.ticket)
                self._managed_position_state.pop(ticket, None)
            elif action.action == "modify_sl" and action.stop_loss:
                state["stop_loss"] = float(action.stop_loss)
                self._position_update_instructions[symbol][ticket] = {
                    "ticket": ticket, "sl": round(float(action.stop_loss), 8),
                    "tp": round(float(position.tp or 0), 8),
                    "reason": action.reason,
                }

    # ==================== 订单确认回调 ====================

    def _on_order_confirmed(self, order: PendingOrder):
        """订单确认回调"""
        print(f"[TradingServer] 订单确认: {order.order_id}")

        # 广播订单确认
        self._broadcast_pending_order(order)

        try:
            self._risk_manager.record_confirmed_order(
                order.order_id,
                order.symbol,
                order.mount,
                abs(order.price - order.sl),
            )
            # 创建交易指令
            instruction_id = self.trading_instruction_service.create_from_pending_order(order)
            print(f"[TradingServer] 交易指令已创建: {instruction_id}")
        except Exception as e:
            print(f"[TradingServer] 创建交易指令失败: {e}")
            import traceback
            traceback.print_exc()

    # ==================== EA 接口 ====================

    def get_trades_by_symbol(self, symbol: str, price: Optional[float] = None) -> Dict:
        """
        EA获取交易数据

        返回：
        - trades: 待执行的交易指令
        - pending_orders: 待确认的订单
        - close_tickets: 平仓指令
        """
        # 处理价格（生成信号和决策）
        process_result = {}
        if price is not None:
            process_result = self.process_price(symbol, price)

        # 获取交易指令
        trades = self.trading_instruction_service.fetch_instructions_for_ea(symbol, price)

        # 获取待确认订单
        pending_orders = self.pending_order_service.get_pending_orders_dict(symbol)

        # 获取平仓指令
        close_tickets = self.get_close_position_instructions(symbol)
        position_updates = list(
            self._position_update_instructions.pop(symbol, {}).values()
        )

        return {
            "trades": trades,
            "pending_orders": pending_orders,
            "close_tickets": close_tickets,
            "position_updates": position_updates,
            "process_result": process_result
        }

    # ==================== 统计数据 ====================

    def save_statistics(self, stat_data: dict) -> None:
        """保存统计数据"""
        self.statistics_service.process_statistics(stat_data)

    def get_latest_statistics(
        self,
        count: int = 10,
        symbol: Optional[str] = None,
    ) -> List[Dict]:
        """获取最新的统计数据，可按品种筛选。"""
        stats = (
            self.statistics_store.get_by_symbol(symbol, count)
            if symbol
            else self.statistics_store.get_all_recent(count)
        )
        return [s.to_dict() for s in stats]

    # ==================== 指令管理 ====================

    def get_all_pending_trades(self) -> Dict[str, List[Dict]]:
        """获取所有待执行的交易指令"""
        return self.trading_instruction_service.get_all_instructions_dict()

    def clear_trades(self, symbol: Optional[str] = None) -> int:
        """清空交易指令"""
        if symbol is None:
            count = self.trading_instruction_service.get_total_count()
            self.trading_instruction_service.clear_all()
            print(f"[TradingServer] 已清空所有交易指令，共 {count} 条")
            return count
        else:
            return self.trading_instruction_service.clear_by_symbol(symbol)

    def add_close_position_instruction(self, symbol: str, ticket: int) -> None:
        """添加平仓指令"""
        with self.lock:
            ticket = int(ticket)
            if ticket not in self._close_position_instructions[symbol]:
                self._close_position_instructions[symbol].append(ticket)
            if self._runtime_repository:
                self._runtime_repository.upsert_entity(
                    "close_instruction",
                    str(ticket),
                    {"symbol": symbol, "ticket": ticket, "status": "pending"},
                    symbol=symbol,
                    status="pending",
                )
            print(f"[TradingServer] 添加平仓指令: {symbol} ticket={ticket}")

    def get_close_position_instructions(self, symbol: str) -> List[int]:
        """获取并清空平仓指令"""
        with self.lock:
            tickets = self._close_position_instructions.get(symbol, [])
            self._close_position_instructions[symbol] = []
            if self._runtime_repository:
                for ticket in tickets:
                    self._runtime_repository.upsert_entity(
                        "close_instruction",
                        str(ticket),
                        {"symbol": symbol, "ticket": ticket, "status": "sent"},
                        symbol=symbol,
                        status="sent",
                    )
            if tickets:
                print(f"[TradingServer] 返回平仓指令: {symbol} tickets={tickets}")
            return tickets

    def set_scope(self, user_id: int, account_id: int) -> None:
        """将绑定前的临时运行数据迁移到正式 MT5 账户。"""
        self.user_id = int(user_id)
        self.account_id = int(account_id)
        for store in (
            self.statistics_store,
            self.position_store,
            self.trade_history_store,
            self.pending_order_store,
            self.trading_instruction_store,
        ):
            store.set_scope(self.user_id, self.account_id)
        self._risk_manager.set_scope(self.user_id, self.account_id)
        if self._runtime_repository:
            self._runtime_repository.migrate_scope(self.account_id)
            self._runtime_repository.set_scope(self.user_id, self.account_id)
        else:
            self._runtime_repository = RuntimeStateRepository(
                self.user_id,
                self.account_id,
            )
        self.llm_store.set_scope(self.user_id, self.account_id)
        self.system_log.set_scope(self.user_id, self.account_id)

    # ==================== 决策历史 ====================

    def _record_decision(self, decision: TradingDecision) -> None:
        self._decision_history.append(decision)
        rejected = decision.status == "rejected"
        self.system_log.add_log(
            "risk_blocked" if rejected else "strategy_decision_created",
            {
                "strategy_id": decision.strategy_id,
                "strategy_name": decision.strategy_name,
                "action": decision.action,
                "confidence": decision.confidence_score,
                "entry_price": decision.entry_price,
                "volume": decision.volume,
                "stop_loss": decision.sl,
                "take_profit": decision.tp,
                "order_id": decision.order_id,
                "position_check": decision.position_check,
                "risk_check": decision.risk_check,
            },
            symbol=decision.symbol,
            message=decision.decision_reason,
            level="warning" if rejected else "info",
            category="risk" if rejected else "trading",
            status=decision.status,
            entity_type="strategy_decision",
            entity_id=decision.decision_id,
            correlation_id=decision.order_id or decision.decision_id,
        )
        if self._runtime_repository:
            self._runtime_repository.upsert_entity(
                "strategy_decision",
                decision.decision_id,
                decision.to_dict(),
                symbol=decision.symbol,
                status=decision.status,
            )
            self._runtime_repository.trim_entities("strategy_decision", 200)

    def update_decision_status(self, order_id: str, status: str) -> bool:
        """按关联订单同步决策状态，并持久化审计结果。"""
        for decision in reversed(self._decision_history):
            if decision.order_id != order_id:
                continue
            decision.status = status
            if status == "confirmed":
                decision.auto_executed = decision.auto_executed or decision.auto_execute
            if self._runtime_repository:
                self._runtime_repository.upsert_entity(
                    "strategy_decision",
                    decision.decision_id,
                    decision.to_dict(),
                    symbol=decision.symbol,
                    status=decision.status,
                )
            return True
        return False

    def get_decision_history(
        self,
        symbol: str = None,
        count: int = 20,
        strategy_id: str = None,
        status: str = None,
        date_from: str = None,
        date_to: str = None,
    ) -> List[Dict]:
        """获取当前账户的持久化决策历史，最新记录优先。"""
        decisions = list(self._decision_history)
        if symbol:
            decisions = [d for d in decisions if d.symbol == symbol]
        if strategy_id:
            decisions = [d for d in decisions if d.strategy_id == strategy_id]
        if status:
            decisions = [d for d in decisions if d.status == status]
        if date_from:
            start = datetime.fromisoformat(date_from)
            decisions = [d for d in decisions if d.created_at and d.created_at >= start]
        if date_to:
            end = datetime.fromisoformat(date_to)
            decisions = [d for d in decisions if d.created_at and d.created_at <= end]
        return [d.to_dict() for d in reversed(decisions[-max(1, min(count, 200)):])]

    def get_dashboard_overview(self, account) -> Dict:
        """Build one account-scoped operational snapshot for the dashboard."""
        now = int(time.time())
        connected = bool(
            account.account_type == "mt5" and account.last_seen_at
            and now - account.last_seen_at <= 120
        )
        risk = self._risk_manager.get_status()
        positions = self.position_service.get_position_objects()
        position_items = sorted(
            (item.to_dict() for item in positions),
            key=lambda item: abs(float(item.get("profit", 0) or 0)),
            reverse=True,
        )
        today = datetime.now().date()
        today_deals = [
            deal for deal in self.trade_history_store.get()
            if deal.time and deal.time.date() == today
        ]
        today_closed = [deal for deal in today_deals if deal.is_exit]
        today_net_profit = sum(
            deal.profit + deal.swap + deal.commission for deal in today_deals
        )
        pending_orders = self.pending_order_service.get_orders_dict()
        instructions = self.get_all_pending_trades()
        pending_instruction_count = sum(
            len(items) for items in instructions.values() if isinstance(items, list)
        )

        deployed_ids = set(self._active_strategy_ids("live"))
        decisions = self.get_decision_history(count=100)
        latest_decisions = {}
        for decision in decisions:
            latest_decisions.setdefault(decision.get("strategy_id", ""), decision)
        active_signals = self._signal_service.get_active_signals()
        latest_signals = {}
        for signal in sorted(
            active_signals, key=lambda item: item.created_at or datetime.min,
            reverse=True,
        ):
            latest_signals.setdefault(signal.strategy_id, signal)
        strategies = []
        for strategy in self._strategy_store.get_all_strategies():
            if strategy.strategy_id not in deployed_ids:
                continue
            decision = latest_decisions.get(strategy.strategy_id)
            signal = latest_signals.get(strategy.strategy_id)
            direction = (
                (decision or {}).get("action")
                or (signal.action if signal else "none")
            )
            confidence = (
                (decision or {}).get("confidence_score")
                or (signal.confidence if signal else 0)
            )
            strategies.append({
                "strategy_id": strategy.strategy_id,
                "strategy_name": strategy.strategy_name,
                "symbol": strategy.symbol,
                "lifecycle_status": strategy.lifecycle_status,
                "enabled": strategy.enabled,
                "auto_execute": strategy.auto_execute,
                "direction": direction,
                "confidence": round(float(confidence or 0), 2),
                "latest_decision": decision,
                "latest_signal_at": (
                    signal.created_at.isoformat()
                    if signal and signal.created_at else None
                ),
            })

        market_health = []
        for symbol in sorted(self.kline_service.get_symbols()):
            status = self.kline_service.check_m1_updated_within(symbol, 180)
            periods = [
                period for period in ("M1", "M5", "M15", "H1", "H4")
                if self.kline_service.is_initialized(symbol, period)
                or self.kline_service.get_klines(symbol, period, 1)
            ]
            latest = status.get("latest_time")
            market_health.append({
                "symbol": symbol,
                "status": status.get("market_status", "closed"),
                "is_stale": bool(status.get("is_stale", True)),
                "seconds_ago": status.get("seconds_ago"),
                "latest_time": latest.isoformat() if latest else None,
                "periods": periods,
            })

        ai_cards = [
            card for card in self.get_ai_market_cards()
            if card.get("strategy_id") in deployed_ids
            and card.get("status") != "strategy_inactive"
        ]
        ai_priority = {
            "decision_created": 0, "signal_formed": 1,
            "ready_to_signal": 2, "waiting_price": 3,
            "observing": 4, "waiting_analysis": 5, "expired": 6,
        }
        ai_cards.sort(key=lambda card: (
            ai_priority.get(card.get("status"), 9),
            -float(card.get("confidence", 0) or 0),
        ))

        attention = []
        if not connected:
            attention.append({
                "type": "mt5_offline", "severity": "error",
                "title": "MT5 终端未连接",
                "detail": "当前账户超过 2 分钟未收到 EA 心跳",
                "path": "/accounts",
            })
        if not account.trading_enabled:
            attention.append({
                "type": "trading_paused", "severity": "warning",
                "title": "账户交易已暂停",
                "detail": "策略仍可观察，但不会产生新的交易执行",
                "path": "/accounts",
            })
        if risk.get("circuit_breaker"):
            attention.append({
                "type": "circuit_breaker", "severity": "error",
                "title": "账户风控已熔断",
                "detail": risk.get("circuit_breaker_reason") or "请检查当日亏损与订单限制",
                "path": "/accounts",
            })
        if pending_orders:
            attention.append({
                "type": "pending_orders", "severity": "warning",
                "title": f"有 {len(pending_orders)} 条策略决策待确认",
                "detail": "请确认交易参数或放弃本次机会",
                "path": "/market",
            })
        if pending_instruction_count:
            attention.append({
                "type": "pending_instructions", "severity": "info",
                "title": f"有 {pending_instruction_count} 条指令等待 MT5 领取",
                "detail": "可前往交易指令查看执行链路",
                "path": "/trades",
            })
        stale_symbols = [item["symbol"] for item in market_health if item["is_stale"]]
        if stale_symbols:
            attention.append({
                "type": "market_stale", "severity": "warning",
                "title": "部分行情数据未及时更新",
                "detail": "、".join(stale_symbols[:4]),
                "path": "/accounts",
            })
        expired_ai = sum(card.get("status") == "expired" for card in ai_cards)
        if expired_ai:
            attention.append({
                "type": "ai_expired", "severity": "warning",
                "title": f"有 {expired_ai} 条 AI 分析已过期",
                "detail": "自主分析或共享数据正在等待更新",
                "path": "/ai-market",
            })

        balance = float(account.balance or risk.get("account_balance", 0) or 0)
        equity = float(account.equity or risk.get("account_equity", 0) or 0)
        free_margin = float(account.free_margin or risk.get("free_margin", 0) or 0)
        margin = float(account.margin or 0)
        return {
            "status": "ok",
            "generated_at": datetime.now().isoformat(),
            "account": {
                "account_id": account.account_id,
                "account_name": account.account_name,
                "account_type": account.account_type,
                "environment": account.environment,
                "currency": account.currency,
                "mt5_login": account.mt5_login,
                "mt5_server": account.mt5_server,
                "ea_version": account.ea_version,
                "connected": connected,
                "last_seen_at": account.last_seen_at,
                "trading_enabled": account.trading_enabled,
                "auto_trading_enabled": account.auto_trading_enabled,
            },
            "financial": {
                "balance": balance,
                "equity": equity,
                "free_margin": free_margin,
                "margin": margin,
                "margin_level": round(equity / margin * 100, 2) if margin > 0 else 0,
                "floating_profit": round(sum(item.profit for item in positions), 2),
                "today_net_profit": round(today_net_profit, 2),
                "today_trade_count": len(today_closed),
            },
            "risk": risk,
            "positions": {
                "count": len(position_items),
                "buy_count": sum(item["direction"] == "buy" for item in position_items),
                "sell_count": sum(item["direction"] == "sell" for item in position_items),
                "items": position_items[:5],
            },
            "pending": {
                "confirmation_count": len(pending_orders),
                "instruction_count": pending_instruction_count,
            },
            "attention": attention,
            "strategies": strategies,
            "ai_opportunities": ai_cards[:3],
            "market_health": market_health,
        }

    # ==================== LLM 分析接口（内部封装） ====================

    def get_llm_analysis(self, symbol: str = None) -> Dict:
        """获取大模型分析结果"""
        return self.llm_analyzer.get_analysis(symbol)

    @staticmethod
    def _ai_direction(value: str) -> str:
        text = str(value or "").lower()
        if text in {"up", "buy", "bullish"} or any(
            marker in text for marker in ("上涨", "上升", "看涨")
        ):
            return "up"
        if text in {"down", "sell", "bearish"} or any(
            marker in text for marker in ("下跌", "下降", "看跌")
        ):
            return "down"
        return "sideways"

    def _latest_market_price(self, symbol: str) -> float:
        klines = self.kline_service.get_klines(symbol, "M1", 1)
        if not klines:
            return 0.0
        try:
            return float(klines[-1].get("close", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            return 0.0

    def _shared_ai_market_card(
        self, strategy, source: Dict, current_price: float,
        active_signals: List[TradingSignal],
    ) -> Dict:
        params = source.get("params") or {}
        source_id = source.get("signal_source_id", "")
        state = self._ai_entry_generator.get_shared_reference_state(
            strategy.symbol, current_price, strategy, source
        )
        shared = state.get("shared") or {}
        suggestion = state.get("suggestion") or {}
        signal = next((
            item for item in active_signals
            if item.strategy_id == strategy.strategy_id
            and item.signal_source_id == source_id
        ), None)
        decision = next((
            candidate for candidate in reversed(self._decision_history)
            if any(
                item.get("signal_source_id") == source_id
                for item in candidate.signals
            )
        ), None)
        confidence = int(state.get("confidence", 0) or 0)
        minimum = int(params.get("min_confidence", 70) or 0)
        entry_price = float(suggestion.get("entry_price", 0) or 0)
        threshold = float(params.get("entry_threshold", 0.0001) or 0)
        distance_ratio = (
            abs(current_price - entry_price) / current_price
            if current_price > 0 and entry_price > 0 else None
        )
        if not strategy.enabled or not source.get("enabled", True):
            status, reason = "strategy_inactive", "策略或信号源尚未启用"
        elif not shared:
            status, reason = "expired", state["reason"]
        elif state.get("stale"):
            status, reason = "expired", state["reason"]
        elif decision is not None:
            status, reason = "decision_created", "共享分析已参与聚合并生成交易决策"
        elif signal is not None and signal.is_entry_trigger:
            status, reason = "signal_formed", "共享分析满足当前策略的置信度与价格条件"
        elif confidence < minimum:
            status, reason = "observing", f"共享置信度 {confidence}% 低于策略要求 {minimum}%"
        elif state.get("direction") == "sideways":
            status, reason = "observing", "共享分析判断为震荡"
        elif not suggestion:
            status, reason = "observing", "共享分析已有方向，但没有有效入场建议"
        elif distance_ratio is None or distance_ratio > threshold:
            status, reason = "waiting_price", "当前账户价格尚未进入共享建议的入场区间"
        else:
            status, reason = "ready_to_signal", "当前账户价格已进入共享建议触发区"
        result = shared.get("result") or {}
        return {
            "card_id": f"{strategy.strategy_id}:{source_id}",
            "analysis_mode": "shared_reference",
            "derived_from_shared": True,
            "strategy_id": strategy.strategy_id,
            "strategy_name": strategy.strategy_name,
            "strategy_lifecycle": strategy.lifecycle_status,
            "strategy_enabled": strategy.enabled,
            "signal_source_id": source_id,
            "source_enabled": source.get("enabled", True),
            "symbol": strategy.symbol,
            "period": source.get("period", ""),
            "model": shared.get("model", ""),
            "source_owner_username": shared.get("owner_username", ""),
            "source_symbol": shared.get("symbol", ""),
            "shared_runtime_id": shared.get("share_id", params.get("shared_runtime_id", "")),
            "direction": state.get("direction", "sideways"),
            "confidence": confidence,
            "min_confidence": minimum,
            "status": status,
            "status_reason": reason,
            "current_price": current_price,
            "entry_price": entry_price,
            "stop_loss": float(suggestion.get("stop_loss", 0) or 0),
            "take_profit": float(suggestion.get("take_profit", 0) or 0),
            "entry_threshold": threshold,
            "distance_ratio": distance_ratio,
            "trend": (result.get("trend_analysis") or {}).get(shared.get("period", "")) or {},
            "overall_trend": result.get("overall_trend"),
            "key_levels": result.get("key_levels"),
            "suggestion": suggestion or None,
            "analyzed_at": result.get("analyzed_at") or shared.get("last_run_at"),
            "market_status": result.get("market_status", "unknown"),
            "data_stale": bool(state.get("stale")),
            "signal": signal.to_dict() if signal else None,
            "decision": decision.to_dict() if decision else None,
        }

    def get_ai_market_cards(self, symbol: str = None) -> List[Dict]:
        """按策略 AI 信号源解释最新分析及其信号转化状态。"""
        analyses = self.get_llm_analysis() or {}
        active_signals = self._signal_service.get_active_signals()
        cards = []
        for strategy in self._strategy_store.get_all_strategies():
            if symbol and strategy.symbol != symbol:
                continue
            for source in strategy.get_signal_sources("ai_entry"):
                params = source.get("params") or {}
                current_price = self._latest_market_price(strategy.symbol)
                if params.get("analysis_mode", "self_analysis") == "shared_reference":
                    cards.append(self._shared_ai_market_card(
                        strategy, source, current_price, active_signals
                    ))
                    continue
                period = source.get("period", "")
                source_id = source.get("signal_source_id", "")
                analysis = analyses.get(strategy.symbol) or {}
                trend = (analysis.get("trend_analysis") or {}).get(period) or {}
                suggestions = [
                    item for item in (analysis.get("trade_suggestions") or [])
                    if item.get("signal_source_id") == source_id
                    or (
                        not item.get("signal_source_id")
                        and item.get("strategy_id") == strategy.strategy_id
                        and item.get("period") == period
                    )
                ]
                suggestion = max(
                    suggestions,
                    key=lambda item: int(item.get("confidence", 0) or 0),
                    default=None,
                )
                signal = next((
                    item for item in active_signals
                    if item.strategy_id == strategy.strategy_id
                    and item.signal_source_id == source_id
                ), None)
                analyzed_at = analysis.get("analyzed_at")
                decision = None
                for candidate in reversed(self._decision_history):
                    if analyzed_at and candidate.created_at:
                        try:
                            if candidate.created_at < datetime.fromisoformat(analyzed_at):
                                continue
                        except (TypeError, ValueError):
                            pass
                    if any(
                        item.get("signal_source_id") == source_id
                        for item in candidate.signals
                    ):
                        decision = candidate
                        break

                direction = self._ai_direction(
                    (suggestion or {}).get("direction") or trend.get("trend")
                )
                confidence = int(
                    (suggestion or {}).get("confidence")
                    or trend.get("confidence")
                    or 0
                )
                min_confidence = int(params.get("min_confidence", 70) or 0)
                entry_price = float((suggestion or {}).get("entry_price", 0) or 0)
                threshold = float(params.get("entry_threshold", 0.0001) or 0)
                distance_ratio = (
                    abs(current_price - entry_price) / entry_price
                    if current_price > 0 and entry_price > 0 else None
                )

                if not strategy.enabled or not source.get("enabled", True):
                    status, status_reason = "strategy_inactive", "策略或信号源尚未启用"
                elif not analysis:
                    status, status_reason = "waiting_analysis", "等待首次 AI 分析"
                elif analysis.get("data_stale") or analysis.get("market_status") in {"stale", "closed"}:
                    status, status_reason = "expired", "行情未更新，当前分析仅供参考"
                elif decision is not None:
                    status, status_reason = "decision_created", "已参与策略聚合并生成交易决策"
                elif signal is not None and signal.is_entry_trigger:
                    status, status_reason = "signal_formed", "置信度与入场价格条件均已满足"
                elif confidence < min_confidence:
                    status, status_reason = (
                        "observing",
                        f"置信度 {confidence}% 低于策略要求 {min_confidence}%",
                    )
                elif direction == "sideways":
                    status, status_reason = "observing", "AI 判断为震荡，暂不形成方向信号"
                elif suggestion is None:
                    status, status_reason = "observing", "已有方向判断，但模型尚未给出有效入场建议"
                elif distance_ratio is None:
                    status, status_reason = "waiting_price", "等待实时价格后检查入场距离"
                elif distance_ratio > threshold:
                    status, status_reason = (
                        "waiting_price",
                        f"当前价格距建议入场价 {distance_ratio * 100:.3f}%",
                    )
                else:
                    status, status_reason = "ready_to_signal", "价格已进入触发区，等待策略处理"

                cards.append({
                    "card_id": f"{strategy.strategy_id}:{source_id}",
                    "analysis_mode": "self_analysis",
                    "derived_from_shared": False,
                    "strategy_id": strategy.strategy_id,
                    "strategy_name": strategy.strategy_name,
                    "strategy_lifecycle": strategy.lifecycle_status,
                    "strategy_enabled": strategy.enabled,
                    "signal_source_id": source_id,
                    "source_enabled": source.get("enabled", True),
                    "symbol": strategy.symbol,
                    "period": period,
                    "model": params.get("model", ""),
                    "direction": direction,
                    "confidence": confidence,
                    "min_confidence": min_confidence,
                    "status": status,
                    "status_reason": status_reason,
                    "current_price": current_price,
                    "entry_price": entry_price,
                    "stop_loss": float((suggestion or {}).get("stop_loss", 0) or 0),
                    "take_profit": float((suggestion or {}).get("take_profit", 0) or 0),
                    "entry_threshold": threshold,
                    "distance_ratio": distance_ratio,
                    "trend": trend,
                    "overall_trend": analysis.get("overall_trend"),
                    "key_levels": analysis.get("key_levels"),
                    "suggestion": suggestion,
                    "analyzed_at": analyzed_at,
                    "market_status": analysis.get("market_status", "unknown"),
                    "data_stale": bool(analysis.get("data_stale", False)),
                    "analysis_interval_minutes": int(
                        params.get("analysis_interval_minutes", 5) or 5
                    ),
                    "kline_count": int(params.get("kline_count", 100) or 100),
                    "share_runtime_data": bool(params.get("share_runtime_data", False)),
                    "system_prompt": params.get("system_prompt", ""),
                    "analysis_prompt_template": params.get(
                        "analysis_prompt_template", ""
                    ),
                    "signal": signal.to_dict() if signal else None,
                    "decision": decision.to_dict() if decision else None,
                })
        return cards

    def get_llm_status(self) -> Dict:
        """获取大模型分析器状态"""
        status = self.llm_analyzer.get_status()
        status["interval_seconds"] = self.llm_analyzer.ANALYZE_INTERVAL
        return status

    def get_llm_config(self) -> Dict:
        """获取大模型配置"""
        return self.llm_analyzer.get_config()

    def trigger_llm_analysis(self) -> Dict:
        """手动触发大模型分析"""
        self.llm_service.set_allowed_strategy_ids(
            self._active_strategy_ids("live")
        )
        return self.llm_analyzer.trigger_analysis()

    def configure_llm(
        self, api_key: str = None, api_base: str = None, model: str = None,
        system_prompt: str = None, analysis_prompt_template: str = None,
    ) -> Dict:
        """配置大模型参数"""
        return self.llm_analyzer.configure(
            api_key, api_base, model, system_prompt, analysis_prompt_template
        )

    # ==================== 状态查询 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "ws_clients": self.get_ws_client_count(),
            "statistics": self.statistics_service.get_status(),
            "positions": self.position_service.get_status(),
            "trade_history": self.trade_history_service.get_status(),
            "pending_orders": self.pending_order_service.get_status(),
            "trading_instructions": self.trading_instruction_service.get_status(),
            "strategy_service": self.strategy_service.get_status(),
        }
