#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易服务核心类
"""

from collections import deque, defaultdict
from typing import List, Dict, Optional
import threading

from models import TradeInstruction


class TradingServer:
    """交易服务主类"""
    
    def __init__(self):
        # 交易指令队列 - 按SYMBOL分类
        # 结构: {"SYMBOL1": [TradeInstruction1, ...], "SYMBOL2": [...]}
        self.trade_instructions = defaultdict(list)
        
        # 统计数据历史 - 保留最新10条
        # 结构: deque([{stat_data1}, {stat_data2}, ...], maxlen=10)
        self.statistics_history = deque(maxlen=10)
        
        # 线程锁 - 确保线程安全
        self.lock = threading.RLock()
        
        print("[信息] 交易服务已初始化")

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


    def get_trades_by_symbol(self, symbol: str, price: Optional[float] = None) -> List[Dict]:
        """
        获取指定SYMBOL的交易指令并删除
        根据价格条件过滤指令：
          - 买入指令（action='b'）：如果指令价格 > 当前价格，缓存（等待价格下跌到指令价格）
          - 卖出指令（action='s'）：如果指令价格 < 当前价格，缓存（等待价格上涨到指令价格）
        返回指令列表（JSON格式）
        """
        with self.lock:
            symbol = symbol.upper()
            if symbol not in self.trade_instructions or len(self.trade_instructions[symbol]) == 0:
                return []
            
            # 获取所有指令
            trades = self.trade_instructions[symbol]
            result = []
            cached_trades = []
            
            for t in trades:
                should_send = True
                
                # 如果提供了价格，进行条件检查
                if price is not None:
                    if t.action.lower() == 'b':
                        # 买入指令：如果指令价格 > 当前价格，则缓存
                        if t.price > price:
                            should_send = False
                            cached_trades.append(t)
                    elif t.action.lower() == 's':
                        # 卖出指令：如果指令价格 < 当前价格，则缓存
                        if t.price < price:
                            should_send = False
                            cached_trades.append(t)
                
                if should_send:
                    result.append({
                        "symbol": t.symbol.lower(),
                        "action": t.action.lower(),
                        "mount": t.mount,
                        "price": t.price,
                        "sl": t.sl,
                        "tp": t.tp
                    })
            
            # 更新指令队列：移除已发送的，保留已缓存的
            self.trade_instructions[symbol] = cached_trades
            
            if len(result) > 0:
                print(f"[信息] 推送了 {len(result)} 条 {symbol} 指令给EA (当前价格: {price})")
            if len(cached_trades) > 0:
                print(f"[信息] 缓存了 {len(cached_trades)} 条 {symbol} 指令，等待价格条件满足")
            
            return result

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
                        "symbol": t.symbol.lower(),
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
