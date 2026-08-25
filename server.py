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
from market.services.strategy.transient_decision_store import transient_decision_store
from market.trade_config import TradeConfig
from market.llm_analyzer import LLMAnalyzer
from market.system_log import SystemLog
from membership import MembershipService
from sqlite_storage import (
    AISignalSourceRepository,
    PositionManagementEventRepository,
    PositionManagementPolicyRepository,
    PlatformInstrumentMappingRepository,
    RuntimeStateRepository,
    SharedAIRuntimeRepository,
    StrategyDeploymentRepository,
    TradeExecutionRepository,
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
        self.instrument_mappings = PlatformInstrumentMappingRepository()
        self._ai_signal_source_repository = AISignalSourceRepository()
        self.memberships = MembershipService()

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
        self.llm_service.set_plan_update_handler(self._record_ai_plan_evaluations)

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
        self._position_partial_instructions = defaultdict(dict)
        self._managed_position_state = {}
        self._position_event_repository = PositionManagementEventRepository()
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
        # 有效决策保留更长审计窗口；无动作决策由 transient_decision_store 内存聚合，
        # 不进入这里，也不占用持久化历史额度。
        self._decision_history: deque = deque(maxlen=1000)
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
        ai_entry_generator = AIEntrySignalGenerator(
            SharedAIRuntimeRepository(), self.user_id
        )
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

    def _live_loss_streak_guard(self, symbol, strategy, action, signal) -> Dict:
        """Protect one live strategy/setup/direction and consume AI plans once."""
        if not self.user_id or not self.account_id or not self._runtime_repository:
            return {"allowed": True, "loss_streak": 0, "scope": "live"}
        storage = self._runtime_repository.storage
        setup_type = str(
            getattr(signal, "setup_type", "") or "generic_entry"
        )
        setup_family = str(getattr(signal, "setup_family", "") or "generic")
        plan_id = str(getattr(signal, "ai_plan_id", "") or "")
        plan_valid_from = int(
            getattr(signal, "ai_plan_valid_from", 0) or 0
        )
        plan_instance_id = (
            f"{plan_id}:{plan_valid_from}"
            if plan_id and plan_valid_from else plan_id
        )

        if plan_instance_id:
            consumed_payloads = storage.fetchall(
                "SELECT payload_json FROM runtime_entities WHERE user_id = ? "
                "AND account_id = ? AND entity_type = 'trading_instruction' "
                "ORDER BY updated_at DESC LIMIT 500",
                (int(self.user_id), int(self.account_id)),
            )
            consumed_payloads.extend(storage.fetchall(
                "SELECT position_attribution_json AS payload_json "
                "FROM trade_execution_reports WHERE user_id = ? AND account_id = ? "
                "ORDER BY reported_at DESC LIMIT 500",
                (int(self.user_id), int(self.account_id)),
            ))
            for item in consumed_payloads:
                try:
                    payload = json.loads(item.get("payload_json") or "{}")
                except (TypeError, ValueError):
                    payload = {}
                attribution = (
                    payload.get("position_attribution") or payload
                    if isinstance(payload, dict) else {}
                )
                if (
                    str(attribution.get("strategy_id") or "") == strategy.strategy_id
                    and str(attribution.get("ai_plan_instance_id") or "")
                    == plan_instance_id
                ):
                    return {
                        "allowed": False, "scope": "live_setup",
                        "setup_type": setup_type, "setup_family": setup_family,
                        "plan_instance_id": plan_instance_id,
                        "reason": "本次AI分析的该交易建议已在此实盘账户触发过，不重复开仓",
                    }

        candidates = storage.fetchall(
            """
            SELECT profit, swap, commission, received_at, mt5_position_id,
                   position_attribution_json
            FROM live_trade_deals
            WHERE user_id = ? AND account_id = ? AND symbol = ?
              AND entry_type IN (1, 2, 3)
            ORDER BY received_at DESC, id DESC
            LIMIT 500
            """,
            (int(self.user_id), int(self.account_id), symbol),
        )
        grouped = {}
        for row in candidates:
            try:
                attribution = json.loads(
                    row.get("position_attribution_json") or "{}"
                )
            except (TypeError, ValueError):
                attribution = {}
            if (
                str(attribution.get("strategy_id") or "") != strategy.strategy_id
                or str(attribution.get("setup_type") or "") != setup_type
                or str(attribution.get("direction") or "") != action
            ):
                continue
            position_id = str(row.get("mt5_position_id") or "")
            item = grouped.setdefault(position_id, {
                "net_profit": 0.0,
                "closed_at": int(row.get("received_at") or 0),
                "position_attribution": attribution,
            })
            item["net_profit"] += sum(
                float(row.get(key) or 0)
                for key in ("profit", "swap", "commission")
            )
            item["closed_at"] = max(
                item["closed_at"], int(row.get("received_at") or 0)
            )
        rows = sorted(
            grouped.values(), key=lambda item: item["closed_at"], reverse=True
        )[:20]
        streak = 0
        for row in rows:
            if float(row.get("net_profit") or 0) < 0:
                streak += 1
            else:
                break
        if not rows or streak == 0:
            return {
                "allowed": True, "loss_streak": streak,
                "scope": "live_setup", "setup_type": setup_type,
            }
        last_loss_at = int(rows[0].get("closed_at") or 0)
        last_attribution = rows[0].get("position_attribution") or {}
        last_plan_id = str(last_attribution.get("ai_plan_id") or "")
        period = str(getattr(signal, "source_period", "M1") or "M1").upper()
        bar_seconds = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}.get(period, 60)
        now = int(time.time())
        if plan_id and plan_valid_from <= last_loss_at:
            return {
                "allowed": False, "loss_streak": streak,
                "scope": "live_setup", "setup_type": setup_type,
                "paused_direction": action,
                "reason": (
                    f"实盘 {setup_type} · {action} 上一笔亏损退出，"
                    "等待该信号源生成下一份AI分析后再评估"
                ),
            }
        if streak < 2:
            return {
                "allowed": True, "loss_streak": streak,
                "scope": "live_setup", "setup_type": setup_type,
                "reset_by": "new_analysis" if plan_id else "not_required",
            }
        if streak == 2 and now < last_loss_at + bar_seconds * 5:
            return {
                "allowed": False, "loss_streak": streak, "scope": "live_setup",
                "setup_type": setup_type,
                "release_at": last_loss_at + bar_seconds * 5,
                "reason": f"实盘 {setup_type} · {action} 连续亏损 {streak} 次，冷却 5 根 {period} K线后再评估",
            }
        if streak >= 3:
            if not plan_id:
                release_at = last_loss_at + bar_seconds * 20
                if now < release_at:
                    return {
                        "allowed": False, "loss_streak": streak,
                        "scope": "live_setup", "setup_type": setup_type,
                        "paused_direction": action, "release_at": release_at,
                        "reason": (
                            f"实盘 {setup_type} · {action} 连续亏损 {streak} 次，"
                            f"非AI信号冷却 20 根 {period} K线后恢复"
                        ),
                    }
                return {
                    "allowed": True, "loss_streak": streak,
                    "scope": "live_setup", "setup_type": setup_type,
                    "cooldown_completed": True,
                }
            new_structure = bool(
                plan_id and plan_id != last_plan_id
                and plan_valid_from > last_loss_at
            )
            if not new_structure:
                return {
                    "allowed": False, "loss_streak": streak, "scope": "live_setup",
                    "setup_type": setup_type,
                    "paused_direction": action,
                    "reason": (
                        f"实盘 {setup_type} · {action} 连续亏损 {streak} 次，"
                        "已暂停该Setup方向；等待结构和价格不同的新AI计划"
                    ),
                }
        return {
            "allowed": True, "loss_streak": streak,
            "scope": "live_setup", "setup_type": setup_type,
            "cooldown_completed": True,
        }

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
            if (
                account.status != "active"
                or not account.trading_enabled
                or not account.auto_trading_enabled
            ):
                return result
            self._risk_manager.set_account_limits(
                max_positions=account.max_total_positions,
                max_single_volume=account.max_single_volume,
                daily_loss_limit=account.daily_loss_limit,
                daily_order_limit=account.daily_order_limit,
            )

        allow_new_orders = self._live_entries_allowed()

        # Each deployed strategy owns its signal generation and cooldown state.
        strategy_ids = self._active_strategy_ids("live")
        self.strategy_service.set_allowed_strategy_ids(strategy_ids)
        allowed_ids = set(strategy_ids)
        decisions = []
        for strategy in self._strategies_for_quote(symbol):
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
            if not allow_new_orders:
                continue
            decision = self.strategy_service.make_decision(
                symbol, current_price, force_signals=signals, strategy=strategy,
                entry_guard=self._live_loss_streak_guard,
                audit_no_action=True,
            )
            if decision is not None:
                # Strategy matching may use a platform canonical symbol (for
                # example BTCUSD), but the EA can only execute its native
                # broker symbol (for example BTCUSDm).
                decision.symbol = symbol
                decisions.append(decision)

        for decision in decisions:
            # 3. 自动执行决策
            if decision.action != "none" and decision.status != "rejected":
                order_id = self.strategy_service.execute_decision(decision)
                if order_id:
                    pending_order = {
                        "order_id": order_id,
                        "symbol": decision.symbol,
                        "strategy_id": decision.strategy_id,
                        "strategy_name": decision.strategy_name,
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

            # Waiting states are aggregated in process only.  Keeping them out
            # of the EA response also avoids returning one payload per quote.
            if (
                decision.action == "none"
                and decision.decision_type == "no_action"
            ):
                continue

            # 创建待确认订单后再序列化和广播，确保携带真实 order_id。
            result["decisions"].append(decision.to_dict())
            self._broadcast_decision(decision)

        # 保留单策略时代的响应字段，旧客户端继续读取第一条结果。
        if result["decisions"]:
            result["decision"] = result["decisions"][0]
        if result["pending_orders"]:
            result["pending_order"] = result["pending_orders"][0]

        return result

    def _strategies_for_quote(self, quote_symbol: str):
        """Match strategy symbols to the EA's native quote symbol."""
        account = (
            self.account_repository.get_by_id(self.user_id, self.account_id)
            if self.user_id is not None and self.account_id else None
        )
        target_server = str(getattr(account, "mt5_server", "") or "")
        candidates = self._strategy_store.get_all_strategies()
        matched = []
        for strategy in candidates:
            if str(strategy.symbol).upper() == str(quote_symbol).upper():
                matched.append(strategy)
                continue
            source_user_id = int(
                getattr(strategy, "source_owner_user_id", 0) or self.user_id or 0
            )
            source_server = self.instrument_mappings.source_server(
                source_user_id, strategy.symbol
            )
            if self.instrument_mappings.compatible(
                source_server, strategy.symbol, target_server, quote_symbol
            ):
                matched.append(strategy)
        return matched

    def _manage_strategy_positions(
        self, strategy, symbol: str, current_price: float, signals: List[TradingSignal],
    ) -> None:
        """Evaluate EA positions with the strategy's independent manager."""
        from market.services import PositionManager

        resolve_policy = getattr(
            self._position_policy_repository, "get_for_strategy", None
        )
        policy = (
            resolve_policy(int(self.user_id or 0), strategy)
            if resolve_policy else self._position_policy_repository.get(
                int(self.user_id or 0), strategy.position_management_policy_id
            )
        )
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
            is_new_position = ticket not in self._managed_position_state
            attribution = {}
            if is_new_position and self.user_id and self.account_id:
                execution = TradeExecutionRepository().find_for_position(
                    int(self.user_id), int(self.account_id), ticket,
                )
                if execution:
                    try:
                        attribution = json.loads(
                            execution.get("position_attribution_json") or "{}"
                        )
                    except (TypeError, ValueError):
                        attribution = {}
            state = self._managed_position_state.setdefault(ticket, {
                "direction": position.direction,
                "entry_price": float(position.price_open),
                "stop_loss": float(position.sl),
                "take_profit": float(position.tp),
                "volume": float(position.volume),
                "remaining_volume": float(position.volume),
                "initial_risk": abs(float(position.price_open) - float(position.sl)),
                "favorable_price": float(position.price_open),
                "holding_bars": 0,
                "opened_at": position.opened_at or datetime.now(),
                "position_attribution": attribution,
                "position_policy_snapshot": attribution.get(
                    "position_policy_snapshot", {}
                ),
            })
            if is_new_position:
                policy_snapshot = attribution.get("position_policy_snapshot") or {}
                self._position_event_repository.record(
                    int(self.user_id or 0), int(self.account_id or 0), str(ticket),
                    "initial_plan", "实盘持仓已纳入持仓管理，记录初始止损止盈保护",
                    symbol=symbol, ticket=ticket, rule_type="initial_plan",
                    status="triggered", price=float(position.price_open),
                    stop_loss=float(position.sl or 0),
                    take_profit=float(position.tp or 0),
                    volume=float(position.volume),
                    payload={
                        "policy_id": str(
                            attribution.get("position_policy_id")
                            or policy_snapshot.get("policy_id")
                            or (policy.policy_id if policy else "")
                        ),
                        "policy_name": str(
                            attribution.get("position_policy_name")
                            or policy_snapshot.get("name")
                            or (policy.name if policy else "")
                        ),
                        "initial_risk": abs(
                            float(position.price_open) - float(position.sl or 0)
                        ),
                        "setup_type": attribution.get("setup_type", ""),
                        "setup_family": attribution.get("setup_family", ""),
                        "setup_profile_id": attribution.get("setup_profile_id", ""),
                        "setup_profile_name": attribution.get("setup_profile_name", ""),
                    },
                )
            state["volume"] = float(position.volume)
            state["remaining_volume"] = float(position.volume)
            state["stop_loss"] = float(position.sl or state["stop_loss"])
            state["take_profit"] = float(position.tp or state["take_profit"])
            state["favorable_price"] = (
                max(state["favorable_price"], current_price)
                if position.is_buy else min(state["favorable_price"], current_price)
            )
            snapshot_config = (
                state.get("position_policy_snapshot") or {}
            ).get("config")
            active_config = snapshot_config or (policy.config if policy else {})
            if not active_config:
                continue
            action = manager.evaluate(
                active_config, state,
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
            TradingServer._record_position_management_events(
                self,
                symbol, ticket, state, action.events
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
            elif action.action == "partial_close" and action.close_volume > 0:
                done = set(state.get("partial_levels_done") or [])
                if action.level_id not in done:
                    close_volume = round(float(action.close_volume), 2)
                    if close_volume <= 0:
                        continue
                    done.add(action.level_id)
                    state["partial_levels_done"] = sorted(done)
                    if action.stop_loss or action.level_id == "signal_take_profit":
                        if action.stop_loss:
                            state["stop_loss"] = float(action.stop_loss)
                        if action.level_id == "signal_take_profit":
                            state["take_profit"] = 0.0
                        self._position_update_instructions[symbol][ticket] = {
                            "ticket": ticket,
                            "sl": round(float(state["stop_loss"]), 8),
                            # The remaining volume must no longer be closed in
                            # full by MT5 at the original AI target.
                            "tp": 0 if action.level_id == "signal_take_profit"
                            else round(float(position.tp or 0), 8),
                            "reason": (
                                f"{action.reason}:clear_tp"
                                if action.level_id == "signal_take_profit"
                                else f"{action.reason}:move_sl"
                            ),
                        }
                    self._position_partial_instructions[symbol][
                        f"{ticket}:{action.level_id}"
                    ] = {
                        "ticket": ticket,
                        "volume": close_volume,
                        "level_id": action.level_id,
                        "reason": action.reason,
                    }

    def _record_position_management_events(
        self, symbol: str, ticket: int, state: Dict, events: List[Dict],
    ) -> None:
        user_id = getattr(self, "user_id", None)
        account_id = getattr(self, "account_id", None)
        if not user_id or not account_id or not events:
            return
        for event in events:
            if event.get("status") not in {"triggered"}:
                continue
            self._position_event_repository.record(
                int(user_id), int(account_id), str(ticket),
                event.get("rule_type") or "position_management",
                event.get("message", ""),
                symbol=symbol,
                ticket=ticket,
                rule_type=event.get("rule_type", ""),
                status=event.get("status", ""),
                price=event.get("price", 0),
                stop_loss=state.get("stop_loss", 0),
                take_profit=state.get("take_profit", 0),
                volume=state.get("remaining_volume") or state.get("volume", 0),
                payload=event,
            )

    # ==================== 订单确认回调 ====================

    def _on_order_confirmed(self, order: PendingOrder):
        """订单确认回调"""
        print(f"[TradingServer] 订单确认: {order.order_id}")

        if not self._live_entries_allowed():
            print(f"[TradingServer] 实盘权限已关闭，忽略开仓订单: {order.order_id}")
            return

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

        if self._live_entries_allowed():
            trades = self.trading_instruction_service.fetch_instructions_for_ea(
                symbol, price
            )
            pending_orders = self.pending_order_service.get_pending_orders_dict(symbol)
        else:
            self.trading_instruction_service.clear_by_symbol(symbol)
            self.pending_order_service.clear_all()
            trades = []
            pending_orders = []

        # 获取平仓指令
        close_tickets = self.get_close_position_instructions(symbol)
        position_updates = list(
            self._position_update_instructions.pop(symbol, {}).values()
        )
        position_partials = list(
            self._position_partial_instructions.pop(symbol, {}).values()
        )

        return {
            "trades": trades,
            "pending_orders": pending_orders,
            "close_tickets": close_tickets,
            "position_updates": position_updates,
            "position_partials": position_partials,
            "process_result": process_result
        }

    def _live_entries_allowed(self) -> bool:
        if self.user_id is None:
            return False
        try:
            self.memberships.assert_live_trading(self.user_id, self.account_id)
            return True
        except ValueError:
            return False

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
        if decision.action == "none" and decision.decision_type == "no_action":
            # Quote-driven waiting states are useful operational context but
            # must not crowd out execution history in MySQL.
            transient_decision_store.record(
                self.user_id, self.account_id, decision,
            )
            return
        transient_decision_store.clear_for_strategy(
            self.user_id, self.account_id, decision.strategy_id, decision.symbol,
        )
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
            self._runtime_repository.trim_entities("strategy_decision", 1000)

    def _record_ai_plan_evaluations(self, updates: List[Dict]) -> None:
        """Persist one conclusion per deployment when an AI price plan changes."""
        for update in updates or []:
            source_id = str(update.get("signal_source_id") or "")
            symbol = str(update.get("symbol") or "")
            suggestions = list(update.get("suggestions") or [])
            current_price = self._latest_market_price(symbol)
            for strategy in self._strategy_store.get_all_strategies():
                binding = next((
                    item for item in strategy.get_signal_sources("ai_entry")
                    if str((item.get("params") or {}).get("ai_signal_source_id") or "") == source_id
                ), None)
                if binding is None:
                    continue
                deployments = [
                    item for item in self.strategy_deployments.list_for_strategy(
                        int(self.user_id or 0), strategy.strategy_id,
                    ) if item.get("status") == "active"
                ]
                if not deployments:
                    continue
                params = binding.get("params") or {}
                def plan_rank(item):
                    confidence = float(item.get("confidence") or 0)
                    entry = float(item.get("entry_price") or 0)
                    distance = (
                        abs(current_price - entry) / entry
                        if current_price > 0 and entry > 0 else float("inf")
                    )
                    # Confidence remains primary; when buy/sell plans have
                    # equal confidence, explain the plan closest to the live
                    # quote instead of whichever item the model returned first.
                    return confidence, -distance

                suggestion = max(suggestions, key=plan_rank, default=None)
                reason, confidence, entry, stop_loss, take_profit = self._ai_plan_conclusion(
                    suggestion,
                    current_price,
                    int(params.get("min_confidence", strategy.min_confidence) or 0),
                    float(strategy.min_risk_reward or 0),
                    float(params.get("entry_threshold", 0.0008) or 0.0008),
                    str(update.get("change_type") or "changed"),
                )
                for deployment in deployments:
                    decision = TradingDecision(
                        symbol=symbol,
                        strategy_id=strategy.strategy_id,
                        strategy_name=strategy.strategy_name,
                        execution_mode=str(deployment.get("execution_mode") or "live"),
                        action="none",
                        decision_type="ai_plan_evaluation",
                        signals=[{
                            "source": "ai_entry",
                            "signal_source_id": source_id,
                            "source_period": update.get("period") or "",
                            "direction": suggestion.get("direction") if suggestion else "",
                            "confidence": confidence,
                            "suggested_entry": entry,
                            "stop_loss": stop_loss,
                            "take_profit": take_profit,
                            "trigger_reason": "AI交易计划已更新，策略即时评估",
                        }],
                        signal_summary={"total_count": 1 if suggestion else 0, "source": "ai_plan"},
                        entry_price=entry,
                        sl=stop_loss,
                        tp=take_profit,
                        decision_reason=reason,
                        confidence_score=confidence,
                        status="skipped",
                    )
                    runtime = RuntimeStateRepository(
                        int(self.user_id or 0), int(deployment["account_id"]),
                    )
                    runtime.upsert_entity(
                        "strategy_decision", decision.decision_id, decision.to_dict(),
                        symbol=symbol, status=decision.status,
                    )
                    runtime.trim_entities("strategy_decision", 1000)
                    transient_decision_store.clear_for_strategy(
                        int(self.user_id or 0), int(deployment["account_id"]),
                        strategy.strategy_id, symbol,
                    )

    @staticmethod
    def _ai_plan_conclusion(
        suggestion: Optional[Dict], current_price: float, minimum_confidence: int,
        minimum_rr: float, entry_threshold: float, change_type: str,
    ) -> tuple:
        if not suggestion:
            return (
                "AI交易计划已撤销或本轮未给出有效建议，策略不执行并等待后续分析。",
                0.0, 0.0, 0.0, 0.0,
            )
        confidence = float(suggestion.get("confidence") or 0)
        entry = float(suggestion.get("entry_price") or 0)
        stop_loss = float(suggestion.get("stop_loss") or 0)
        take_profit = float(suggestion.get("take_profit") or 0)
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        ratio = reward / risk if risk > 0 else 0
        if ratio < minimum_rr:
            return (f"AI计划已{change_type}，但盈亏比 {ratio:.2f} 低于策略要求 {minimum_rr:.2f}，暂不执行。", confidence, entry, stop_loss, take_profit)
        distance = abs(current_price - entry) / entry if current_price > 0 and entry > 0 else None
        if distance is None:
            return ("AI计划已更新，但当前账户暂无可用报价；等待下一次 Tick 评估入场。", confidence, entry, stop_loss, take_profit)
        if distance > entry_threshold:
            return (f"AI计划已{change_type}：当前价 {current_price:.2f} 尚未进入 {entry:.2f} 的入场阈值，等待实时 Tick。", confidence, entry, stop_loss, take_profit)
        return (f"AI计划已{change_type}：当前价 {current_price:.2f} 已接近计划入场 {entry:.2f}；等待下一次 Tick 完成实时信号与风控判断。", confidence, entry, stop_loss, take_profit)

    def update_decision_status(self, order_id: str, status: str) -> bool:
        """按关联订单同步决策状态，并持久化审计结果。"""
        for decision in reversed(self._decision_history):
            if decision.order_id != order_id:
                continue
            decision.status = status
            if status == "confirmed":
                decision.auto_executed = True
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
        """获取当前账户的执行历史和聚合后的内存等待状态。"""
        decisions = list(self._decision_history)
        if self._runtime_repository:
            # Paper execution is driven by the paper engine, which persists its
            # audits directly. Reload so an already-running web engine sees
            # those decisions without needing a restart.
            persisted = []
            # 执行中心只取最近决策，避免每个请求反序列化账户全部运行态。
            recent_limit = max(100, min(500, int(count or 20) * 20))
            for item in self._runtime_repository.list_entities(
                "strategy_decision", limit=recent_limit
            ):
                try:
                    persisted.append(TradingDecision.from_dict(item))
                except (TypeError, ValueError):
                    continue
            decisions_by_id = {item.decision_id: item for item in decisions}
            decisions_by_id.update({item.decision_id: item for item in persisted})
            decisions = list(decisions_by_id.values())
        # Legacy no-action rows can remain in MySQL, but are superseded by the
        # current in-memory aggregate and should not consume the UI history.
        decisions = [
            item for item in decisions
            if not (
                item.action == "none"
                and item.decision_type == "no_action"
            )
        ]
        decisions.extend(transient_decision_store.list(self.user_id, self.account_id))
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
        decisions.sort(key=lambda item: item.created_at or datetime.min)
        return [d.to_dict() for d in reversed(decisions[-max(1, min(count, 1000)):])]

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
                "enabled": True,
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
            if any(
                item.get("strategy_id") in deployed_ids
                for item in card.get("linked_strategies", [])
            ) and card.get("status") != "source_disabled"
        ]
        ai_priority = {
            "analysis_ready": 0, "observing": 1,
            "waiting_analysis": 2, "expired": 3,
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

    @staticmethod
    def _confidence_percent(value) -> int:
        try:
            confidence = float(value or 0)
        except (TypeError, ValueError):
            return 0
        if 0 < confidence <= 1:
            confidence *= 100
        return max(0, min(100, int(round(confidence))))

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
        threshold = float(params.get("entry_threshold", 0.0008) or 0)
        distance_ratio = (
            abs(current_price - entry_price) / current_price
            if current_price > 0 and entry_price > 0 else None
        )
        if not source.get("enabled", True):
            status, reason = "strategy_inactive", "信号源尚未启用"
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
            "strategy_enabled": True,
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

    def _independent_ai_market_card(
        self, source: Dict, analysis: Dict,
    ) -> Dict:
        """Render one AI market card for one managed analysis source."""
        params = dict(source.get("config") or {})
        source_id = str(source.get("signal_source_id") or "")
        source_symbol = str(source.get("symbol") or "")
        period = str(source.get("period") or "M5").upper()
        source_analysis = (
            (analysis.get("source_results") or {}).get(source_id)
            if isinstance(analysis, dict) else None
        )
        if not isinstance(source_analysis, dict):
            resolver = getattr(
                getattr(self, "llm_service", None),
                "get_persisted_source_result", None,
            )
            source_analysis = (
                resolver(source_id, source_symbol) if resolver else None
            )
        if isinstance(source_analysis, dict):
            analysis = source_analysis
        current_price = self._latest_market_price(source_symbol)
        trend = (analysis.get("trend_analysis") or {}).get(period) or {}
        suggestions = [
            item for item in (analysis.get("trade_suggestions") or [])
            if item.get("signal_source_id") == source_id
            or (
                not item.get("signal_source_id")
                and item.get("strategy_id") == "__independent__"
                and item.get("period") == period
            )
        ]
        suggestion = max(
            suggestions,
            key=lambda item: self._confidence_percent(item.get("confidence")),
            default=None,
        )
        direction = self._ai_direction(
            (suggestion or {}).get("direction") or trend.get("trend")
        )
        confidence = self._confidence_percent(
            (suggestion or {}).get("confidence") or trend.get("confidence")
        )
        entry_price = float((suggestion or {}).get("entry_price", 0) or 0)
        threshold = float(params.get("entry_threshold", 0.0008) or 0)
        distance_ratio = (
            abs(current_price - entry_price) / entry_price
            if current_price > 0 and entry_price > 0 else None
        )

        if not source.get("enabled", True):
            status, reason = "source_disabled", "AI 信号源尚未启用"
        elif not analysis:
            status, reason = "waiting_analysis", "等待首次 AI 分析"
        elif analysis.get("data_stale") or analysis.get("market_status") in {
            "stale", "closed",
        }:
            status, reason = "expired", "行情未更新，当前分析仅供参考"
        elif suggestion is not None:
            status, reason = "analysis_ready", "模型已给出可供策略评估的交易建议"
        elif direction == "sideways":
            status, reason = "observing", "AI 判断为区间震荡，当前没有边界交易建议"
        elif suggestion is None:
            status, reason = "observing", "已有方向判断，但模型尚未给出有效入场建议"

        return {
            "card_id": f"source:{source_id}",
            "analysis_mode": "self_analysis",
            "derived_from_shared": False,
            "source_name": source.get("name") or "AI 信号源",
            "signal_source_id": source_id,
            "source_enabled": source.get("enabled", True),
            "symbol": source_symbol,
            "period": period,
            "model": params.get("model", ""),
            "direction": direction,
            "confidence": confidence,
            "status": status,
            "status_reason": reason,
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
            "analyzed_at": analysis.get("analyzed_at"),
            "market_status": analysis.get("market_status", "unknown"),
            "data_stale": bool(analysis.get("data_stale", False)),
            "analysis_interval_minutes": int(
                params.get("analysis_interval_minutes", 5) or 5
            ),
            "kline_count": int(params.get("kline_count", 100) or 100),
            "reference_market_data": params.get("reference_market_data") or [],
            "share_runtime_data": bool(source.get("share_runtime_data", False)),
            "system_prompt": params.get("system_prompt", ""),
            "analysis_prompt_template": params.get(
                "analysis_prompt_template", ""
            ),
        }

    def get_ai_market_cards(self, symbol: str = None) -> List[Dict]:
        """Render exactly one market-analysis card per owned AI source."""
        analyses = self.get_llm_analysis() or {}
        cards = []
        if self.user_id is not None:
            for source in self._ai_signal_source_repository.list(self.user_id):
                source_id = str(source.get("signal_source_id") or "")
                if symbol and source.get("symbol") != symbol:
                    continue
                if (source.get("config") or {}).get(
                    "analysis_mode", "self_analysis"
                ) != "self_analysis":
                    continue
                card = self._independent_ai_market_card(
                    source, analyses.get(source.get("symbol")) or {},
                )
                card["linked_strategies"] = self._linked_ai_strategies(source_id)
                cards.append(card)
        return cards

    def _linked_ai_strategies(self, managed_source_id: str) -> List[Dict]:
        """List consumers without letting their rules alter the AI analysis."""
        linked = []
        for strategy in self._strategy_store.get_all_strategies():
            for binding in strategy.get_signal_sources("ai_entry"):
                params = binding.get("params") or {}
                if str(params.get("ai_signal_source_id") or "") != managed_source_id:
                    continue
                deployment_repo = getattr(self, "strategy_deployments", None)
                linked.append({
                    "strategy_id": strategy.strategy_id,
                    "strategy_name": strategy.strategy_name,
                    "lifecycle_status": strategy.lifecycle_status,
                    "binding_id": binding.get("signal_source_id", ""),
                    "enabled": bool(binding.get("enabled", True)),
                    "deployments": deployment_repo.list_for_strategy(
                        int(self.user_id or 0), strategy.strategy_id,
                    ) if deployment_repo else [],
                })
        return linked

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
