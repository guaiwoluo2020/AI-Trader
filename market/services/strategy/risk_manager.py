#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险管理服务
"""

from typing import Dict, Optional
from datetime import datetime

from ...models import TradingStrategy


class RiskManager:
    """风险管理服务"""

    def __init__(self):
        # 账户信息（从外部更新）
        self._account_balance: float = 0.0
        self._account_equity: float = 0.0
        self._free_margin: float = 0.0

        # 每日风险限制
        self._daily_risk_limit: float = 5.0  # 每日最大风险百分比
        self._daily_risk_used: float = 0.0   # 今日已使用风险

        # 品种配置（点值、最小手数等）
        self._symbol_config: Dict[str, Dict] = {}

        # 统计服务引用（用于获取账户信息）
        self._statistics_service = None

        print("[RiskManager] 风险管理服务已初始化")

    def set_statistics_service(self, service) -> None:
        """设置统计服务引用"""
        self._statistics_service = service

    def _refresh_account_info(self) -> None:
        """从统计服务刷新账户信息"""
        if not self._statistics_service:
            return

        try:
            account_info = self._statistics_service.get_account_info()
            if account_info:
                self._account_balance = account_info.get('balance', 0.0)
                self._account_equity = account_info.get('equity', 0.0)
                # free_margin 通常等于 equity - used_margin，这里用 equity 近似
                self._free_margin = account_info.get('equity', 0.0)
        except Exception as e:
            print(f"[RiskManager] 刷新账户信息失败: {e}")

    # ==================== 账户信息 ====================

    def update_account_info(self, balance: float, equity: float, free_margin: float) -> None:
        """更新账户信息"""
        self._account_balance = balance
        self._account_equity = equity
        self._free_margin = free_margin

    def get_account_balance(self) -> float:
        """获取账户余额"""
        return self._account_balance

    def get_account_equity(self) -> float:
        """获取账户权益"""
        return self._account_equity

    # ==================== 品种配置 ====================

    def set_symbol_config(self, symbol: str, config: Dict) -> None:
        """设置品种配置"""
        self._symbol_config[symbol] = config

    def get_symbol_config(self, symbol: str) -> Dict:
        """获取品种配置"""
        return self._symbol_config.get(symbol, {
            "point_value": 1.0,      # 点值
            "min_volume": 0.01,      # 最小手数
            "max_volume": 10.0,      # 最大手数
            "volume_step": 0.01,     # 手数步长
        })

    # ==================== 手数计算 ====================

    def calculate_volume(self, symbol: str, risk_points: float,
                        strategy: TradingStrategy) -> float:
        """
        计算交易手数

        Args:
            symbol: 品种
            risk_points: 风险点数
            strategy: 策略配置

        Returns:
            计算的手数
        """
        config = self.get_symbol_config(symbol)
        point_value = config.get('point_value', 1.0)
        min_volume = config.get('min_volume', 0.01)
        max_volume = config.get('max_volume', 10.0)
        volume_step = config.get('volume_step', 0.01)

        if strategy.volume_mode == "fixed":
            volume = strategy.fixed_volume
        elif strategy.volume_mode == "risk_percent":
            # 根据风险百分比计算手数
            risk_amount = self._account_balance * (strategy.risk_percent / 100)
            # 手数 = 风险金额 / (风险点数 * 点值)
            if risk_points > 0 and point_value > 0:
                volume = risk_amount / (risk_points * point_value)
            else:
                volume = min_volume
        else:
            volume = strategy.fixed_volume

        # 应用最大风险点数限制
        if risk_points > strategy.max_risk_points:
            print(f"[RiskManager] 风险点数 {risk_points} 超过最大限制 {strategy.max_risk_points}")
            return 0.0

        # 限制手数范围
        volume = max(min_volume, min(volume, max_volume))

        # 按步长取整
        volume = round(volume / volume_step) * volume_step

        return volume

    # ==================== 风险检查 ====================

    def check_risk(self, symbol: str, volume: float, risk_points: float) -> Dict:
        """
        检查交易风险

        Args:
            symbol: 品种
            volume: 手数
            risk_points: 风险点数

        Returns:
            检查结果
        """
        # 刷新账户信息
        self._refresh_account_info()

        config = self.get_symbol_config(symbol)
        point_value = config.get('point_value', 1.0)

        # 计算风险金额
        risk_amount = volume * risk_points * point_value
        risk_percent = (risk_amount / self._account_balance * 100) if self._account_balance > 0 else 0

        # 检查每日风险限制
        remaining_risk = self._daily_risk_limit - self._daily_risk_used

        allowed = True
        warnings = []

        # 账户信息是否已初始化
        account_initialized = self._account_balance > 0 or self._free_margin > 0

        if risk_percent > 5:
            allowed = False
            warnings.append(f"单笔风险 {risk_percent:.2f}% 超过5%")

        if risk_percent + self._daily_risk_used > self._daily_risk_limit:
            allowed = False
            warnings.append(f"将超过每日风险限制 {self._daily_risk_limit}%")

        # 只有账户信息已初始化时才检查保证金
        if account_initialized and self._free_margin < risk_amount:
            allowed = False
            warnings.append(f"保证金不足 (可用: {self._free_margin:.2f}, 需要: {risk_amount:.2f})")

        if not account_initialized:
            warnings.append("账户信息未初始化，跳过保证金检查")

        return {
            "allowed": allowed,
            "risk_amount": risk_amount,
            "risk_percent": round(risk_percent, 2),
            "daily_risk_used": self._daily_risk_used,
            "daily_risk_limit": self._daily_risk_limit,
            "remaining_risk": remaining_risk,
            "warnings": warnings,
            "account_initialized": account_initialized,
        }

    # ==================== 持仓检查 ====================

    def check_position_limit(self, symbol: str, strategy: TradingStrategy,
                            current_positions: int, same_direction: int,
                            opposite_direction: int, action: str) -> Dict:
        """
        检查持仓限制

        Args:
            symbol: 品种
            strategy: 策略配置
            current_positions: 当前持仓数
            same_direction: 同向持仓数
            opposite_direction: 反向持仓数
            action: 交易方向 buy/sell

        Returns:
            检查结果
        """
        allowed = True
        warnings = []

        # 检查最大持仓数
        if current_positions >= strategy.max_positions:
            allowed = False
            warnings.append(f"已达到最大持仓数 {strategy.max_positions}")

        # 检查同向持仓
        new_same_direction = same_direction + 1
        if new_same_direction > strategy.max_same_direction:
            allowed = False
            warnings.append(f"同向持仓将超过限制 {strategy.max_same_direction}")

        # 检查持仓冲突策略
        if opposite_direction > 0:
            if strategy.position_conflict == "block":
                allowed = False
                warnings.append("有反向持仓，策略禁止新开仓")
            elif strategy.position_conflict == "allow_same":
                allowed = False
                warnings.append("有反向持仓，策略只允许同向加仓")
            elif strategy.position_conflict == "allow_opposite":
                # 允许反向
                pass

        return {
            "allowed": allowed,
            "current_positions": current_positions,
            "same_direction": same_direction,
            "opposite_direction": opposite_direction,
            "max_positions": strategy.max_positions,
            "max_same_direction": strategy.max_same_direction,
            "warnings": warnings,
        }

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取状态"""
        return {
            "account_balance": self._account_balance,
            "account_equity": self._account_equity,
            "free_margin": self._free_margin,
            "daily_risk_limit": self._daily_risk_limit,
            "daily_risk_used": self._daily_risk_used,
            "symbol_count": len(self._symbol_config),
        }