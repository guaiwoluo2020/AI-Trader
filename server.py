#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易服务核心类
"""

from collections import deque, defaultdict
from typing import List, Dict, Optional
import threading

from models import TradeInstruction
from market.store import MarketStore, normalize_symbol
from market.pivot_detector import PivotDetector
from market.monitor import PivotMonitor
from market.trend_analyzer import TrendAnalyzer
from market.pending_orders import PendingOrderManager


class TradingServer:
    """交易服务主类"""

    def __init__(self):
        # 交易指令队列 - 按SYMBOL分类
        # 结构: {"SYMBOL1": [TradeInstruction1, ...], "SYMBOL2": [...]}
        self.trade_instructions = defaultdict(list)

        # 平仓指令队列 - 按SYMBOL分类
        # 结构: {"SYMBOL1": [ticket1, ticket2, ...], ...}
        self.close_position_instructions = defaultdict(list)

        # 统计数据历史 - 保留最新10条
        # 结构: deque([{stat_data1}, {stat_data2}, ...], maxlen=10)
        self.statistics_history = deque(maxlen=10)

        # 线程锁 - 确保线程安全
        self.lock = threading.RLock()

        # ==================== 行情模块 ====================
        # K线存储
        self.market_store = MarketStore()
        # 转折点检测器
        self.pivot_detector = PivotDetector()
        # 待确认订单管理器（需要在 PivotMonitor 之前初始化）
        self.pending_orders = PendingOrderManager()
        # 设置订单确认回调
        self.pending_orders.set_confirm_callback(self._on_order_confirmed)
        # 转折点监控器
        self.pivot_monitor = PivotMonitor(self.market_store, self.pivot_detector, self.pending_orders)
        # 趋势分析器
        self.trend_analyzer = TrendAnalyzer()

        print("[信息] 交易服务已初始化")

    def _on_order_confirmed(self, order: Dict):
        """
        订单确认回调 - 将确认的订单加入交易队列
        """
        print(f"[TradingServer] _on_order_confirmed 被调用，订单: {order}")
        try:
            # 创建交易指令
            instruction = TradeInstruction(
                symbol=order.get('symbol', ''),
                action=order.get('action', 'b'),
                mount=order.get('mount', 0.01),
                price=order.get('price', 0),
                sl=order.get('sl', 0),
                tp=order.get('tp', 0)
            )
            print(f"[TradingServer] 创建交易指令: symbol={instruction.symbol}, action={instruction.action}, mount={instruction.mount}, price={instruction.price}, sl={instruction.sl}, tp={instruction.tp}")
            # 添加到交易队列
            result = self.add_trade_instruction([instruction])
            print(f"[TradingServer] 订单已加入交易队列: {result}")
        except Exception as e:
            print(f"[TradingServer] 加入交易队列失败: {e}")
            import traceback
            traceback.print_exc()

    def add_trade_instruction(self, instructions: List[TradeInstruction]) -> dict:
        """
        添加交易指令
        返回一个字典，包含添加和拒绝的数量。

        在此处对缺失的 sl/tp 值进行补全：
          - sl 若未设置保持0.0
          - tp 若未设置则默认 0.005

        同时按照规则进行价格检查：
          * 买入指令: price 需大于 sl 且小于 tp
          * 卖出指令: price 需小于 sl 且大于 tp
        若 sl 或 tp 未设置（<=0），则忽略检查。
        """
        with self.lock:
            added = 0
            rejected = 0
            for instruction in instructions:
                # 填充默认值
                if instruction.sl is None:
                    instruction.sl = 0.0
                if instruction.tp is None or instruction.tp <= 0.0:
                    instruction.tp = 0.005

                # 验证价格与 SL/TP 关系
                if instruction.sl > 0 and instruction.tp > 0:
                    if instruction.action.lower() == 'b':
                        if not (instruction.price > instruction.sl and instruction.price < instruction.tp):
                            print(f"[警告] 忽略无效买入指令: {instruction}")
                            rejected += 1
                            continue
                    elif instruction.action.lower() == 's':
                        if not (instruction.price < instruction.sl and instruction.price > instruction.tp):
                            print(f"[警告] 忽略无效卖出指令: {instruction}")
                            rejected += 1
                            continue

                symbol = instruction.symbol.upper()
                self.trade_instructions[symbol].append(instruction)
                added += 1

            print(f"[信息] 已添加 {added} 条交易指令, 拒绝 {rejected} 条")
            for symbol, trades in self.trade_instructions.items():
                print(f"       {symbol}: {len(trades)} 条待执行")

            return {"added": added, "rejected": rejected}


    def get_trades_by_symbol(self, symbol: str, price: Optional[float] = None) -> Dict:
        """
        获取指定SYMBOL的交易指令并删除

        同时检查价格是否接近转折点，如果有则添加到返回结果中

        返回: {"trades": [...], "pivot_alerts": [...]}
        """
        # 先检查转折点
        pivot_alerts = []
        if price is not None:
            # 统一转换为大写进行检测
            symbol_upper = symbol.upper()
            pivot_alerts = self.pivot_monitor.check_and_alert(symbol_upper, price)
            if pivot_alerts:
                print(f"[信息] {symbol_upper} 当前价格 {price} 接近转折点")

        with self.lock:
            symbol = symbol.upper()
            if symbol not in self.trade_instructions or len(self.trade_instructions[symbol]) == 0:
                return {"trades": [], "pivot_alerts": pivot_alerts}

            # 获取所有指令并直接返回（不再进行价格过滤）
            trades = self.trade_instructions[symbol]
            result = [{
                "symbol": t.symbol,
                "action": t.action.lower(),
                "mount": t.mount,
                "price": t.price,
                "sl": t.sl,
                "tp": t.tp
            } for t in trades]

            # 清空指令队列
            self.trade_instructions[symbol] = []

            if len(result) > 0:
                print(f"[信息] 推送了 {len(result)} 条 {symbol} 指令给EA")

            return {"trades": result, "pivot_alerts": pivot_alerts}

    def save_statistics(self, stat_data: dict) -> None:
        """
        保存统计数据
        自动保留最新10条
        """
        with self.lock:
            self.statistics_history.append(stat_data)
            print(f"[信息] 统计数据已记录 - {stat_data.get('timestamp', 'unknown')}")
            print(f"       当前保存数据条数: {len(self.statistics_history)}")

    def get_latest_statistics(self, count: int = 10) -> List[Dict]:
        """
        获取最新的统计数据
        """
        with self.lock:
            return list(self.statistics_history)[-count:]

    def get_all_pending_trades(self) -> Dict[str, List[Dict]]:
        """
        获取所有待执行的交易指令（不删除）
        用于查询接口
        """
        with self.lock:
            result = {}
            for symbol, trades in self.trade_instructions.items():
                result[symbol] = [
                    {
                        "symbol": t.symbol,
                        "action": t.action.lower(),
                        "mount": t.mount,
                        "price": t.price,
                        "sl": t.sl,
                        "tp": t.tp
                    }
                    for t in trades
                ]
            return result

    def clear_trades(self, symbol: Optional[str] = None) -> int:
        """
        清空交易指令
        如果指定symbol则只清空该symbol
        返回清空的指令数量
        """
        with self.lock:
            if symbol is None:
                total = sum(len(trades) for trades in self.trade_instructions.values())
                self.trade_instructions.clear()
                print(f"[信息] 已清空所有交易指令，共 {total} 条")
                return total
            else:
                symbol = symbol.upper()
                count = len(self.trade_instructions.get(symbol, []))
                if symbol in self.trade_instructions:
                    del self.trade_instructions[symbol]
                print(f"[信息] 已清空 {symbol} 的交易指令，共 {count} 条")
                return count

    def add_close_position_instruction(self, symbol: str, ticket: int) -> None:
        """
        添加平仓指令
        """
        with self.lock:
            symbol = symbol.upper()
            self.close_position_instructions[symbol].append(ticket)
            print(f"[信息] 添加平仓指令: {symbol} ticket={ticket}")

    def get_close_position_instructions(self, symbol: str) -> List[int]:
        """
        获取并清空平仓指令
        """
        with self.lock:
            symbol = symbol.upper()
            tickets = self.close_position_instructions.get(symbol, [])
            self.close_position_instructions[symbol] = []
            if tickets:
                print(f"[信息] 返回平仓指令: {symbol} tickets={tickets}")
            return tickets
