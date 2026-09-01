#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险管理服务
"""

from typing import Dict
from datetime import datetime
import os
import threading

from ...models import TradingStrategy
from ...risk_clock import risk_day_key, risk_day_start_timestamp
from mysql_repositories import RuntimeStateRepository


class RiskManager:
    """风险管理服务"""

    STATE_TYPE = "risk_state"

    def __init__(self, user_id: int = None, account_id: int = None):
        self._lock = threading.RLock()
        self._repository = (
            RuntimeStateRepository(user_id, account_id)
            if user_id is not None
            else None
        )

        # 账户信息（从外部更新）
        self._account_balance: float = 0.0
        self._account_equity: float = 0.0
        self._free_margin: float = 0.0

        # 每日风险限制
        self._daily_risk_limit: float = 5.0  # 每日最大风险百分比
        self._daily_risk_used: float = 0.0   # 今日已使用风险
        self._daily_loss_limit: float = float(
            os.getenv("AI_TRADER_DAILY_LOSS_LIMIT", "5")
        )
        self._daily_order_limit: int = int(
            os.getenv("AI_TRADER_DAILY_ORDER_LIMIT", "20")
        )
        self._daily_order_count: int = 0
        self._account_max_positions: int = 10
        self._account_max_single_volume: float = 10.0
        self._daily_realized_pnl: float = 0.0
        self._circuit_breaker: bool = False
        self._circuit_breaker_reason: str = ""
        self._risk_date: str = risk_day_key()
        self._recorded_order_ids = set()

        # 品种配置（点值、最小手数等）
        self._symbol_config: Dict[str, Dict] = {}

        # 统计服务引用（用于获取账户信息）
        self._statistics_service = None
        self._trade_history_service = None
        self._load_state()

        print("[RiskManager] 风险管理服务已初始化")

    def set_statistics_service(self, service) -> None:
        """设置统计服务引用"""
        self._statistics_service = service

    def set_trade_history_service(self, service) -> None:
        self._trade_history_service = service

    def set_account_limits(
        self,
        *,
        max_positions: int,
        max_single_volume: float,
        daily_loss_limit: float,
        daily_order_limit: int,
    ) -> None:
        """应用当前交易账户自己的风控阈值。"""
        with self._lock:
            self._account_max_positions = max(1, int(max_positions))
            self._account_max_single_volume = max(0.01, float(max_single_volume))
            self._daily_loss_limit = max(0.1, float(daily_loss_limit))
            self._daily_order_limit = max(1, int(daily_order_limit))

    def _refresh_account_info(self) -> None:
        """从统计服务刷新账户信息"""
        if not self._statistics_service:
            return

        try:
            account_info = self._statistics_service.get_account_info()
            if account_info:
                self._account_balance = account_info.get('balance', 0.0)
                self._account_equity = account_info.get('equity', 0.0)
                self._free_margin = account_info.get(
                    'free_margin',
                    account_info.get('equity', 0.0),
                )
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
        with self._lock:
            self._refresh_account_info()
            account_balance = self._account_balance
        config = self.get_symbol_config(symbol)
        point_value = config.get('point_value', 1.0)
        min_volume = config.get('min_volume', 0.01)
        max_volume = config.get('max_volume', 10.0)
        volume_step = config.get('volume_step', 0.01)

        if strategy.volume_mode == "fixed":
            volume = strategy.fixed_volume
        elif strategy.volume_mode == "risk_percent":
            # 根据风险百分比计算手数
            risk_amount = account_balance * (strategy.risk_percent / 100)
            # 手数 = 风险金额 / (风险点数 * 点值)
            if risk_points > 0 and point_value > 0:
                volume = risk_amount / (risk_points * point_value)
            else:
                volume = min_volume
        else:
            volume = strategy.fixed_volume

        # 止损距离上下限统一由持仓管理方案按品种价格比例处理。
        # 这里不能再使用策略层遗留的固定 max_risk_points：同一个固定点数
        # 对 BTC、黄金和外汇含义完全不同，也会造成实盘返回 0 手、而模拟盘
        # 正常创建订单的执行分叉。

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
        with self._lock:
            self._reset_if_new_day()
            self._refresh_account_info()
            self._refresh_daily_realized_pnl()
            account_balance = self._account_balance
            free_margin = self._free_margin
            daily_risk_used = self._daily_risk_used
            daily_order_count = self._daily_order_count
            daily_realized_pnl = self._daily_realized_pnl
            circuit_breaker = self._circuit_breaker
            circuit_breaker_reason = self._circuit_breaker_reason

        config = self.get_symbol_config(symbol)
        point_value = config.get('point_value', 1.0)

        # 计算风险金额
        risk_amount = volume * risk_points * point_value
        risk_percent = (risk_amount / account_balance * 100) if account_balance > 0 else 0

        # 检查每日风险限制
        remaining_risk = self._daily_risk_limit - daily_risk_used

        allowed = True
        warnings = []

        # 账户信息是否已初始化
        account_initialized = account_balance > 0 or free_margin > 0

        if risk_percent > 5:
            allowed = False
            warnings.append(f"单笔风险 {risk_percent:.2f}% 超过5%")

        if volume > self._account_max_single_volume:
            allowed = False
            warnings.append(
                f"下单手数 {volume:.2f} 超过账户限制 "
                f"{self._account_max_single_volume:.2f}"
            )

        if risk_percent + daily_risk_used > self._daily_risk_limit:
            allowed = False
            warnings.append(f"将超过每日风险限制 {self._daily_risk_limit}%")

        if daily_order_count >= self._daily_order_limit:
            allowed = False
            warnings.append(f"已达到每日订单上限 {self._daily_order_limit}")

        if circuit_breaker:
            allowed = False
            warnings.append(f"账户已熔断: {circuit_breaker_reason}")

        # 只有账户信息已初始化时才检查保证金
        if account_initialized and free_margin < risk_amount:
            allowed = False
            warnings.append(f"保证金不足 (可用: {free_margin:.2f}, 需要: {risk_amount:.2f})")

        if not account_initialized:
            allowed = False
            warnings.append("账户信息未初始化，禁止自动交易")

        return {
            "allowed": allowed,
            "risk_amount": risk_amount,
            "risk_percent": round(risk_percent, 2),
            "daily_risk_used": daily_risk_used,
            "daily_risk_limit": self._daily_risk_limit,
            "remaining_risk": remaining_risk,
            "daily_order_count": daily_order_count,
            "daily_order_limit": self._daily_order_limit,
            "daily_realized_pnl": daily_realized_pnl,
            "circuit_breaker": circuit_breaker,
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
        effective_max_positions = min(
            int(strategy.max_positions), self._account_max_positions
        )
        if current_positions >= effective_max_positions:
            allowed = False
            warnings.append(f"已达到最大持仓数 {effective_max_positions}")

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
            "max_positions": effective_max_positions,
            "max_same_direction": strategy.max_same_direction,
            "warnings": warnings,
        }

    # ==================== 状态 ====================

    def record_confirmed_order(
        self,
        order_id: str,
        symbol: str,
        volume: float,
        risk_points: float,
    ) -> bool:
        """幂等记录已确认订单占用的当日风险。"""
        with self._lock:
            self._reset_if_new_day()
            if order_id in self._recorded_order_ids:
                return False
            self._refresh_account_info()
            point_value = self.get_symbol_config(symbol).get("point_value", 1.0)
            risk_amount = float(volume) * float(risk_points) * point_value
            risk_percent = (
                risk_amount / self._account_balance * 100
                if self._account_balance > 0
                else 0.0
            )
            self._daily_risk_used += risk_percent
            self._daily_order_count += 1
            self._recorded_order_ids.add(order_id)
            self._persist_state()
            return True

    def set_scope(self, user_id: int, account_id: int) -> None:
        with self._lock:
            if self._repository:
                self._repository.migrate_scope(account_id)
                self._repository.set_scope(user_id, account_id)
            else:
                self._repository = RuntimeStateRepository(user_id, account_id)
            self._persist_state()

    def _load_state(self) -> None:
        if not self._repository:
            return
        rows = self._repository.list_entities(self.STATE_TYPE)
        if not rows:
            return
        state = rows[-1]
        self._risk_date = state.get("risk_date", self._risk_date)
        self._daily_risk_used = float(state.get("daily_risk_used", 0))
        self._daily_order_count = int(state.get("daily_order_count", 0))
        self._daily_realized_pnl = float(state.get("daily_realized_pnl", 0))
        self._circuit_breaker = bool(state.get("circuit_breaker", False))
        self._circuit_breaker_reason = state.get("circuit_breaker_reason", "")
        self._recorded_order_ids = set(state.get("recorded_order_ids", []))
        self._reset_if_new_day()

    def _reset_if_new_day(self) -> None:
        today = risk_day_key()
        if self._risk_date == today:
            return
        self._risk_date = today
        self._daily_risk_used = 0.0
        self._daily_order_count = 0
        self._daily_realized_pnl = 0.0
        self._circuit_breaker = False
        self._circuit_breaker_reason = ""
        self._recorded_order_ids.clear()
        self._persist_state()

    def _refresh_daily_realized_pnl(self) -> None:
        if not self._trade_history_service:
            return
        day_start = risk_day_start_timestamp()
        deals = self._trade_history_service.get_deals(hours=25)
        realized = 0.0
        for deal in deals:
            if (
                int(deal.get("deal_timestamp") or 0) < day_start
                or int(deal.get("entry", 0)) not in (1, 2, 3)
            ):
                continue
            realized += (
                float(deal.get("profit", 0))
                + float(deal.get("swap", 0))
                + float(deal.get("commission", 0))
            )
        if realized == self._daily_realized_pnl:
            return
        self._daily_realized_pnl = realized
        if self._account_balance > 0 and realized < 0:
            loss_percent = abs(realized) / self._account_balance * 100
            if loss_percent >= self._daily_loss_limit:
                self._circuit_breaker = True
                self._circuit_breaker_reason = (
                    f"当日亏损 {loss_percent:.2f}% 达到限制 "
                    f"{self._daily_loss_limit:.2f}%"
                )
        self._persist_state()

    def _persist_state(self) -> None:
        if not self._repository:
            return
        payload = {
            "risk_date": self._risk_date,
            "daily_risk_used": self._daily_risk_used,
            "daily_risk_limit": self._daily_risk_limit,
            "daily_order_count": self._daily_order_count,
            "daily_order_limit": self._daily_order_limit,
            "account_max_positions": self._account_max_positions,
            "account_max_single_volume": self._account_max_single_volume,
            "daily_realized_pnl": self._daily_realized_pnl,
            "daily_loss_limit": self._daily_loss_limit,
            "circuit_breaker": self._circuit_breaker,
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "recorded_order_ids": sorted(self._recorded_order_ids),
            "updated_at": datetime.now().isoformat(),
        }
        self._repository.upsert_entity(
            self.STATE_TYPE,
            self._risk_date,
            payload,
            status="active",
        )

    def get_status(self) -> Dict:
        """获取状态"""
        with self._lock:
            self._reset_if_new_day()
            self._refresh_account_info()
            self._refresh_daily_realized_pnl()
        return {
            "account_balance": self._account_balance,
            "account_equity": self._account_equity,
            "free_margin": self._free_margin,
            "daily_risk_limit": self._daily_risk_limit,
            "daily_risk_used": self._daily_risk_used,
            "daily_loss_limit": self._daily_loss_limit,
            "daily_order_count": self._daily_order_count,
            "daily_order_limit": self._daily_order_limit,
            "daily_realized_pnl": self._daily_realized_pnl,
            "risk_date": self._risk_date,
            "circuit_breaker": self._circuit_breaker,
            "circuit_breaker_reason": self._circuit_breaker_reason,
            "symbol_count": len(self._symbol_config),
        }
