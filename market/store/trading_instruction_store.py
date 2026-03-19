#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易指令存储模块
"""

from typing import List, Dict, Optional
from datetime import datetime
import threading
from collections import defaultdict

from ..models import TradingInstruction


class TradingInstructionStore:
    """交易指令存储（只负责数据CRUD）"""

    def __init__(self):
        # 按品种分类的指令: {symbol: [TradingInstruction, ...]}
        self._instructions_by_symbol: Dict[str, List[TradingInstruction]] = defaultdict(list)

        # 按ID索引
        self._instructions_by_id: Dict[str, TradingInstruction] = {}

        # 线程锁
        self._lock = threading.RLock()

        print("[TradingInstructionStore] 交易指令存储已初始化")

    # ==================== 添加指令 ====================

    def add_instruction(self, instruction: TradingInstruction) -> str:
        """
        添加交易指令

        Args:
            instruction: 交易指令对象

        Returns:
            指令ID
        """
        with self._lock:
            symbol = instruction.symbol.upper()

            # 存储到两个字典
            self._instructions_by_symbol[symbol].append(instruction)
            self._instructions_by_id[instruction.instruction_id] = instruction

            print(f"[TradingInstructionStore] 添加指令: {instruction.instruction_id} {symbol} {instruction.action}")
            return instruction.instruction_id

    def add_instruction_from_dict(self, data: Dict) -> str:
        """从字典添加指令"""
        instruction = TradingInstruction.from_dict(data)
        return self.add_instruction(instruction)

    def add_instructions_batch(self, instructions: List[TradingInstruction]) -> int:
        """
        批量添加指令

        Args:
            instructions: 指令列表

        Returns:
            添加数量
        """
        count = 0
        for inst in instructions:
            self.add_instruction(inst)
            count += 1
        return count

    # ==================== 获取指令 ====================

    def get_instruction_by_id(self, instruction_id: str) -> Optional[TradingInstruction]:
        """根据ID获取指令"""
        with self._lock:
            return self._instructions_by_id.get(instruction_id)

    def get_instructions_by_symbol(self, symbol: str) -> List[TradingInstruction]:
        """获取指定品种的指令列表"""
        with self._lock:
            return list(self._instructions_by_symbol.get(symbol.upper(), []))

    def get_all_instructions(self) -> List[TradingInstruction]:
        """获取所有指令"""
        with self._lock:
            return list(self._instructions_by_id.values())

    def get_all_instructions_dict(self) -> Dict[str, List[Dict]]:
        """
        获取所有指令（按品种分类）

        Returns:
            {symbol: [instruction_dict, ...]}
        """
        with self._lock:
            result = {}
            for symbol, instructions in self._instructions_by_symbol.items():
                result[symbol] = [inst.to_dict() for inst in instructions]
            return result

    # ==================== 获取并发送指令（EA调用）====================

    def fetch_and_remove_by_symbol(self, symbol: str, current_price: float = None) -> List[Dict]:
        """
        获取满足条件的指令并移除（EA轮询时调用）

        价格过滤逻辑：
        - 买入指令：指令价格 <= 当前价格 → 发送
        - 卖出指令：指令价格 >= 当前价格 → 发送

        Args:
            symbol: 品种
            current_price: 当前价格，None时不做价格过滤

        Returns:
            满足条件的指令列表（字典格式，用于返回给EA）
        """
        with self._lock:
            symbol = symbol.upper()
            instructions = self._instructions_by_symbol.get(symbol, [])

            if not instructions:
                return []

            result = []
            remaining = []

            for inst in instructions:
                should_send = True

                # 价格条件过滤
                if current_price is not None:
                    if inst.action.lower() == 'b':
                        # 买入：指令价格需要 <= 当前价格
                        if inst.price > current_price:
                            should_send = False
                    elif inst.action.lower() == 's':
                        # 卖出：指令价格需要 >= 当前价格
                        if inst.price < current_price:
                            should_send = False

                if should_send:
                    inst.status = "sent"
                    inst.sent_at = datetime.now()
                    result.append(inst.to_dict())  # 返回给EA的格式
                    del self._instructions_by_id[inst.instruction_id]
                else:
                    remaining.append(inst)

            # 更新存储
            self._instructions_by_symbol[symbol] = remaining

            if result:
                print(f"[TradingInstructionStore] 发送指令给EA: {symbol} {len(result)}条 (当前价格: {current_price})")
            if remaining:
                print(f"[TradingInstructionStore] 缓存指令等待条件: {symbol} {len(remaining)}条")

            return result

    # ==================== 移除指令 ====================

    def remove_instruction(self, instruction_id: str) -> Optional[TradingInstruction]:
        """移除指定指令"""
        with self._lock:
            instruction = self._instructions_by_id.get(instruction_id)
            if not instruction:
                return None

            symbol = instruction.symbol.upper()
            self._instructions_by_symbol[symbol] = [
                i for i in self._instructions_by_symbol[symbol] if i.instruction_id != instruction_id
            ]
            del self._instructions_by_id[instruction_id]

            return instruction

    def clear_by_symbol(self, symbol: str) -> int:
        """清空指定品种的指令"""
        with self._lock:
            symbol = symbol.upper()
            instructions = self._instructions_by_symbol.get(symbol, [])
            count = len(instructions)

            for inst in instructions:
                if inst.instruction_id in self._instructions_by_id:
                    del self._instructions_by_id[inst.instruction_id]

            if symbol in self._instructions_by_symbol:
                del self._instructions_by_symbol[symbol]

            print(f"[TradingInstructionStore] 清空 {symbol} 指令: {count}条")
            return count

    def clear_all(self) -> int:
        """清空所有指令"""
        with self._lock:
            count = len(self._instructions_by_id)
            self._instructions_by_symbol.clear()
            self._instructions_by_id.clear()
            print(f"[TradingInstructionStore] 已清空所有指令: {count}条")
            return count

    # ==================== 统计 ====================

    def get_count_by_symbol(self, symbol: str) -> int:
        """获取指定品种的指令数量"""
        with self._lock:
            return len(self._instructions_by_symbol.get(symbol.upper(), []))

    def get_total_count(self) -> int:
        """获取总指令数量"""
        with self._lock:
            return len(self._instructions_by_id)

    def get_status(self) -> Dict:
        """获取存储状态"""
        with self._lock:
            symbols_count = {symbol: len(instructions)
                           for symbol, instructions in self._instructions_by_symbol.items()}
            return {
                "total_instructions": len(self._instructions_by_id),
                "symbols": symbols_count,
            }