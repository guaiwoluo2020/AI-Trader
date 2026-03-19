#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易指令服务模块
"""

from typing import List, Dict, Optional
from datetime import datetime

from ..models import TradingInstruction, PendingOrder
from ..store import TradingInstructionStore


class TradingInstructionService:
    """交易指令服务（处理业务逻辑）"""

    def __init__(self, instruction_store: TradingInstructionStore = None):
        self.store = instruction_store or TradingInstructionStore()
        print("[TradingInstructionService] 交易指令服务已初始化")

    # ==================== 创建指令 ====================

    def create_instruction(self, symbol: str, action: str, price: float,
                          mount: float, sl: float = 0.0, tp: float = 0.005,
                          reason: str = "", description: str = "",
                          source: str = "", order_id: str = None) -> str:
        """
        创建交易指令

        Args:
            symbol: 品种
            action: 方向 (b/s)
            price: 执行价格
            mount: 手数
            sl: 止损
            tp: 止盈
            reason: 原因
            description: 描述
            source: 来源
            order_id: 来源订单ID

        Returns:
            指令ID
        """
        instruction = TradingInstruction(
            symbol=symbol,
            action=action,
            price=price,
            mount=mount,
            sl=sl,
            tp=tp,
            reason=reason,
            description=description,
            source=source,
            order_id=order_id,
        )
        return self.store.add_instruction(instruction)

    def create_instruction_from_dict(self, data: Dict) -> str:
        """从字典创建指令"""
        instruction = TradingInstruction.from_dict(data)
        return self.store.add_instruction(instruction)

    def create_from_pending_order(self, order: PendingOrder) -> str:
        """从待确认订单创建指令"""
        instruction = TradingInstruction.from_pending_order(order)
        return self.store.add_instruction(instruction)

    def create_instructions_batch(self, instructions_data: List[Dict]) -> int:
        """
        批量创建指令

        Args:
            instructions_data: 指令字典列表

        Returns:
            创建数量
        """
        count = 0
        for data in instructions_data:
            # 填充默认值
            if data.get('sl') is None:
                data['sl'] = 0.0
            if data.get('tp') is None or data.get('tp', 0) <= 0:
                data['tp'] = 0.005

            self.create_instruction_from_dict(data)
            count += 1

        print(f"[TradingInstructionService] 批量创建指令: {count}条")
        return count

    # ==================== 查询指令 ====================

    def get_instruction(self, instruction_id: str) -> Optional[TradingInstruction]:
        """获取指令"""
        return self.store.get_instruction_by_id(instruction_id)

    def get_instructions_by_symbol(self, symbol: str) -> List[TradingInstruction]:
        """获取指定品种的指令"""
        return self.store.get_instructions_by_symbol(symbol)

    def get_all_instructions(self) -> List[TradingInstruction]:
        """获取所有指令"""
        return self.store.get_all_instructions()

    def get_all_instructions_dict(self) -> Dict[str, List[Dict]]:
        """获取所有指令（按品种分类）"""
        return self.store.get_all_instructions_dict()

    # ==================== EA获取指令 ====================

    def fetch_instructions_for_ea(self, symbol: str, current_price: float = None) -> List[Dict]:
        """
        EA获取满足条件的指令

        Args:
            symbol: 品种
            current_price: 当前价格，用于价格过滤

        Returns:
            满足条件的指令列表（字典格式）
        """
        return self.store.fetch_and_remove_by_symbol(symbol, current_price)

    # ==================== 清理指令 ====================

    def remove_instruction(self, instruction_id: str) -> Optional[TradingInstruction]:
        """移除指令"""
        return self.store.remove_instruction(instruction_id)

    def clear_by_symbol(self, symbol: str) -> int:
        """清空指定品种的指令"""
        return self.store.clear_by_symbol(symbol)

    def clear_all(self) -> int:
        """清空所有指令"""
        return self.store.clear_all()

    # ==================== 统计 ====================

    def get_count_by_symbol(self, symbol: str) -> int:
        """获取指定品种的指令数量"""
        return self.store.get_count_by_symbol(symbol)

    def get_total_count(self) -> int:
        """获取总指令数量"""
        return self.store.get_total_count()

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return self.store.get_status()