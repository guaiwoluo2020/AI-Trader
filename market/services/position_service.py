#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据服务模块
"""

from typing import Dict, Optional, List

from ..models.position import PositionData
from ..store.position_store import PositionStore


class PositionService:
    """
    持仓数据服务

    功能：
    1. 处理EA上报的持仓数据
    2. 查询持仓信息
    3. 为风险管理提供持仓数据
    """

    def __init__(self, store: PositionStore = None):
        self.store = store or PositionStore()

    def update_positions(self, symbol: str, positions_data: List[Dict]) -> Dict:
        """
        更新持仓数据

        Args:
            symbol: 品种
            positions_data: EA上报的持仓数据列表

        Returns:
            {"status": "ok", "count": N, "closed": M}
        """
        positions = [
            PositionData.from_ea_data(data, symbol)
            for data in positions_data
        ]
        return self.store.update(symbol, positions)

    def get_positions(self, symbol: str = None) -> List[Dict]:
        """获取持仓数据（字典格式）"""
        return self.store.get_dict(symbol)

    def get_position_objects(self, symbol: str = None) -> List[PositionData]:
        """获取持仓数据（对象格式）"""
        return self.store.get(symbol)

    def get_position(self, symbol: str, ticket: int) -> Optional[Dict]:
        """获取单个持仓"""
        pos = self.store.get_by_ticket(symbol, ticket)
        return pos.to_dict() if pos else None

    def get_position_count(self, symbol: str) -> int:
        """获取持仓数量"""
        return self.store.get_count(symbol)

    def get_same_direction_count(self, symbol: str, direction: str) -> int:
        """获取同向持仓数量"""
        return self.store.get_count_by_direction(symbol, direction)

    def get_opposite_direction_count(self, symbol: str, direction: str) -> int:
        """获取反向持仓数量"""
        opposite = "sell" if direction.lower() == "buy" else "buy"
        return self.store.get_count_by_direction(symbol, opposite)

    def get_summary(self, symbol: str = None) -> Dict:
        """获取持仓汇总"""
        return self.store.get_summary(symbol)

    def get_symbols(self) -> List[str]:
        """获取所有有持仓的品种"""
        return self.store.get_symbols()

    def get_status(self) -> Dict:
        """获取服务状态"""
        return self.store.get_status()