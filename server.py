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

from models import TradeInstruction
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
from market.services import PivotSignalGenerator, KeyLevelSignalGenerator, AIEntrySignalGenerator
from market.services import StatisticsService, PositionService, TradeHistoryService
from market.trade_config import TradeConfig
from market.llm_analyzer import LLMAnalyzer


class TradingServer:
    """
    交易服务主类

    架构：
    - 内部：行情模块 → 信号层 → 策略层 → 订单/指令
    - 对外：只暴露策略服务接口
    """

    def __init__(self):
        # 线程锁
        self.lock = threading.RLock()

        # ==================== 行情模块（内部） ====================
        # 存储层
        self.kline_store = KlineStore()
        self.pivot_store = PivotStore()
        self.llm_store = LLMStore()
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
        self.statistics_store = StatisticsStore()
        self.position_store = PositionStore()
        self.trade_history_store = TradeHistoryStore()

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
        self._strategy_store = StrategyStore()

        # 风险管理器
        self._risk_manager = RiskManager()

        # 服务层
        self.strategy_service = StrategyService(
            self._strategy_store,
            self._signal_service,
            self._risk_manager
        )

        # ==================== 交易配置 ====================
        self.trade_config = TradeConfig.get_instance()

        # 注册信号生成器
        self._setup_signal_generators()

        # ==================== 订单/指令模块 ====================
        # 存储层
        self.pending_order_store = PendingOrderStore()
        self.trading_instruction_store = TradingInstructionStore()

        # 服务层
        self.pending_order_service = PendingOrderService(self.pending_order_store)
        self.trading_instruction_service = TradingInstructionService(self.trading_instruction_store)

        # 设置订单确认回调
        self.pending_order_service.set_confirm_callback(self._on_order_confirmed)

        # 更新策略服务的订单服务引用
        self.strategy_service.set_pending_order_service(self.pending_order_service)

        # 策略服务使用持仓服务进行风险管理
        self.strategy_service.set_position_service(self.position_service)

        # 风险管理器使用统计服务获取账户信息
        self._risk_manager.set_statistics_service(self.statistics_service)

        # ==================== WebSocket 广播 ====================
        self._ws_clients: Set = set()
        self._ws_lock = threading.Lock()
        self._main_loop = None

        # ==================== 决策历史 ====================
        self._decision_history: deque = deque(maxlen=50)

        # 兼容旧代码的别名
        self.market_store = self.kline_store
        self.pivot_detector = self.pivot_service
        self.trend_analyzer = self.tech_service
        self.pending_orders = self.pending_order_service
        self.trade_instructions = defaultdict(list)
        # 统计数据历史兼容（已迁移到 statistics_store）
        self.statistics_history = self.statistics_store._all_data

        print("[TradingServer] 交易服务已初始化")

    def _setup_signal_generators(self):
        """设置信号生成器"""
        # 转折点信号生成器
        pivot_generator = PivotSignalGenerator(
            pivot_service=self.pivot_service,
            pivot_store=self.pivot_store,
            kline_store=self.kline_store
        )
        self._signal_service.register_generator("pivot", pivot_generator)

        # 关键点位信号生成器
        key_level_generator = KeyLevelSignalGenerator()
        self._signal_service.register_generator("key_level", key_level_generator)

        # AI入场信号生成器
        ai_entry_generator = AIEntrySignalGenerator()
        ai_entry_generator.set_llm_analyzer(self.llm_analyzer)
        self._signal_service.register_generator("ai_entry", ai_entry_generator)

    # ==================== WebSocket 管理 ====================

    def set_event_loop(self, loop):
        """设置主事件循环引用"""
        self._main_loop = loop
        print("[TradingServer] 已设置主事件循环")

        # 同时设置内部模块的事件循环
        self.llm_analyzer.set_event_loop(loop)

    def add_ws_client(self, client):
        """添加WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.add(client)
            # 同时注册到内部模块
            self.llm_analyzer.add_ws_client(client)
            print(f"[TradingServer] WebSocket客户端已连接, 当前连接数: {len(self._ws_clients)}")

    def remove_ws_client(self, client):
        """移除WebSocket客户端"""
        with self._ws_lock:
            self._ws_clients.discard(client)
            # 同时从内部模块移除
            self.llm_analyzer.remove_ws_client(client)
            print(f"[TradingServer] WebSocket客户端已断开, 当前连接数: {len(self._ws_clients)}")

    def get_ws_client_count(self) -> int:
        """获取WebSocket客户端数量"""
        with self._ws_lock:
            return len(self._ws_clients)

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
            "pending_order": None
        }

        if not self.trade_config.enabled:
            return result

        # 1. 信号层生成信号
        signals = self._signal_service.generate_signals(symbol, current_price)
        result["signals_generated"] = len(signals)

        if signals:
            print(f"[TradingServer] {symbol} 生成了 {len(signals)} 个信号")

        # 2. 策略层做决策
        decision = self.strategy_service.make_decision(symbol, current_price)

        if decision:
            # 记录决策历史
            self._decision_history.append(decision)

            result["decision"] = decision.to_dict()

            # 广播决策
            self._broadcast_decision(decision)

            # 3. 自动执行决策（如果允许）
            if decision.action != "none" and decision.status != "rejected":
                order_id = self.strategy_service.execute_decision(decision)
                if order_id:
                    result["pending_order"] = {
                        "order_id": order_id,
                        "symbol": decision.symbol,
                        "action": decision.action,
                        "price": decision.entry_price,
                        "volume": decision.volume,
                        "sl": decision.sl,
                        "tp": decision.tp
                    }

        return result

    # ==================== 订单确认回调 ====================

    def _on_order_confirmed(self, order: PendingOrder):
        """订单确认回调"""
        print(f"[TradingServer] 订单确认: {order.order_id}")

        # 广播订单确认
        self._broadcast_pending_order(order)

        try:
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

        return {
            "trades": trades,
            "pending_orders": pending_orders,
            "close_tickets": close_tickets,
            "process_result": process_result
        }

    # ==================== 交易员接口 ====================

    def add_trade_instruction(self, instructions: List[TradeInstruction]) -> dict:
        """添加交易指令（交易员手动下单）"""
        added = 0
        rejected = 0

        for instruction in instructions:
            sl = instruction.sl if instruction.sl is not None else 0.0
            tp = instruction.tp if instruction.tp is not None and instruction.tp > 0 else 0.005

            # 验证止损止盈
            if sl > 0 and tp > 0:
                if instruction.action.lower() == 'b':
                    if not (instruction.price > sl and instruction.price < tp):
                        print(f"[警告] 忽略无效买入指令: {instruction}")
                        rejected += 1
                        continue
                elif instruction.action.lower() == 's':
                    if not (instruction.price < sl and instruction.price > tp):
                        print(f"[警告] 忽略无效卖出指令: {instruction}")
                        rejected += 1
                        continue

            self.trading_instruction_service.create_instruction(
                symbol=instruction.symbol,
                action=instruction.action,
                price=instruction.price,
                mount=instruction.mount,
                sl=sl,
                tp=tp,
                description=instruction.description or "",
                source="manual"
            )
            added += 1

        print(f"[TradingServer] 已添加 {added} 条交易指令, 拒绝 {rejected} 条")
        return {"added": added, "rejected": rejected}

    # ==================== 统计数据 ====================

    def save_statistics(self, stat_data: dict) -> None:
        """保存统计数据"""
        self.statistics_service.process_statistics(stat_data)

    def get_latest_statistics(self, count: int = 10) -> List[Dict]:
        """获取最新的统计数据"""
        stats = self.statistics_store.get_all_recent(count)
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
            if not hasattr(self, '_close_position_instructions'):
                self._close_position_instructions = defaultdict(list)
            self._close_position_instructions[symbol].append(ticket)
            print(f"[TradingServer] 添加平仓指令: {symbol} ticket={ticket}")

    def get_close_position_instructions(self, symbol: str) -> List[int]:
        """获取并清空平仓指令"""
        with self.lock:
            if not hasattr(self, '_close_position_instructions'):
                return []
            tickets = self._close_position_instructions.get(symbol, [])
            self._close_position_instructions[symbol] = []
            if tickets:
                print(f"[TradingServer] 返回平仓指令: {symbol} tickets={tickets}")
            return tickets

    # ==================== 决策历史 ====================

    def get_decision_history(self, symbol: str = None, count: int = 20) -> List[Dict]:
        """获取决策历史"""
        decisions = list(self._decision_history)
        if symbol:
            decisions = [d for d in decisions if d.symbol == symbol]
        return [d.to_dict() for d in decisions[-count:]]

    # ==================== LLM 分析接口（内部封装） ====================

    def get_llm_analysis(self, symbol: str = None) -> Dict:
        """获取大模型分析结果"""
        return self.llm_analyzer.get_analysis(symbol)

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
        return self.llm_analyzer.trigger_analysis()

    def configure_llm(self, api_key: str = None, api_base: str = None, model: str = None) -> Dict:
        """配置大模型参数"""
        return self.llm_analyzer.configure(api_key, api_base, model)

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