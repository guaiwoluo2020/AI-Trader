#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据模型定义
"""

from pydantic import BaseModel
from typing import Optional, List


class TradeInstruction(BaseModel):
    """交易指令模型"""
    symbol: str  # 交易品种，如 "gold"
    action: str  # b=买入, s=卖出
    mount: float  # 手数
    price: float  # 指令执行价格（买入时为买入价，卖出时为卖出价）
    sl: Optional[float] = 0.0  # 止损点, 可以缺省
    tp: Optional[float] = 0.0  # 止盈点, 可以缺省，若未指定将在服务端设置为0.005


class StatisticData(BaseModel):
    """统计数据模型"""
    timestamp: str  # 时间戳
    tickCount: int  # TICK计数
    bidPrice: float  # 买价
    askPrice: float  # 卖价
    balance: float  # 账户余额
    equity: float  # 账户权益
    marginLevel: float  # 预付款比例
    positions: list  # 持仓信息
    trades: list  # 交易记录
