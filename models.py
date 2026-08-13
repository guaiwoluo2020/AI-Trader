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
    tp: Optional[float] = 0.0  # 止盈点，可以缺省；0 表示由 EA 按当前价格计算
    description: Optional[str] = ""  # 订单描述（策略名称）


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


class EmailLoginRequest(BaseModel):
    """用户邮箱验证码登录。"""
    email: str
    verification_code: str


class RegisterRequest(BaseModel):
    """受邀请用户注册。"""
    username: str
    email: str
    verification_code: str
    invitation_code: str
    accepted_private_use_terms: bool = False


class SendEmailCodeRequest(BaseModel):
    """发送邮箱验证码。"""
    email: str
    invitation_code: Optional[str] = None


class InvitationCreateRequest(BaseModel):
    """管理员创建私人邀请。"""
    label: str = ""
    max_uses: int = 1
    expires_days: Optional[int] = 7


class InvitationStatusRequest(BaseModel):
    """管理员启停邀请码。"""
    active: bool


class SystemEmailConfigRequest(BaseModel):
    """管理员邮件服务配置。"""
    smtp_host: str
    smtp_port: int
    use_ssl: bool = True
    sender_email: str
    sender_name: str = "AI Trader"
    password: Optional[str] = None
    enabled: bool = True


class TestSystemEmailRequest(BaseModel):
    """管理员测试邮件请求。"""
    target_email: Optional[str] = None


class UserQuotaOverrideRequest(BaseModel):
    """管理员为指定用户覆盖默认业务资源配额。"""
    max_datasets: Optional[int] = None
    max_strategies: Optional[int] = None
    max_signal_sources: Optional[int] = None


class UserMembershipUpdateRequest(BaseModel):
    """管理员调整用户会员等级和实盘授权。"""
    membership_level: str
    live_trading_enabled: bool = False


class AuthUserInfo(BaseModel):
    """当前登录用户信息"""
    user_id: int
    username: str
    email: Optional[str] = None
    role: str
    membership_level: str = "silver"
    live_trading_enabled: bool = False


class LoginResponse(BaseModel):
    """登录响应"""
    status: str
    token: str
    expires_in: int
    user: AuthUserInfo
    next_path: str
