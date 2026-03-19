#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转折点信号生成器
根据转折点分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal, SignalSource
from ...store import PivotStore, KlineStore
from ...services import PivotService


class PivotSignalGenerator:
    """转折点信号生成器"""

    def __init__(self, pivot_service: PivotService = None,
                 pivot_store: PivotStore = None,
                 kline_store: KlineStore = None):
        self.pivot_service = pivot_service
        self.pivot_store = pivot_store or PivotStore()
        self.kline_store = kline_store or KlineStore()

        # 信号冷却时间（秒）
        self.cooldown = 180

        # 已生成的信号冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[PivotSignalGenerator] 转折点信号生成器已初始化")

    def set_pivot_service(self, service: PivotService) -> None:
        """设置转折点服务"""
        self.pivot_service = service

    def _check_cooldown(self, symbol: str, period: str, pivot_price: float) -> bool:
        """检查是否在冷却期"""
        key = f"{symbol}_{period}_{pivot_price}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(self, symbol: str, period: str, pivot_price: float) -> None:
        """设置冷却"""
        key = f"{symbol}_{period}_{pivot_price}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float,
                        period: str = "M1") -> Optional[TradingSignal]:
        """
        生成转折点信号

        Args:
            symbol: 品种
            current_price: 当前价格
            period: 检测周期

        Returns:
            TradingSignal 或 None
        """
        if not self.pivot_service:
            return None

        # 检查是否接近转折点
        near_pivots = self.pivot_service.check_near_pivot(symbol, current_price)

        for pivot in near_pivots:
            # 只处理指定周期
            if pivot.get('period') != period:
                continue

            # 只处理接近类型（不是突破）
            alert_type = pivot.get('alert_type', '')
            if not alert_type.startswith('near_'):
                continue

            pivot_price = pivot.get('price', 0)
            pivot_type = 'low' if 'low' in alert_type else 'high'

            # 检查冷却
            if self._check_cooldown(symbol, period, pivot_price):
                continue

            # 设置冷却
            self._set_cooldown(symbol, period, pivot_price)

            # 确定方向
            if pivot_type == 'low':
                action = "buy"
                # 止损 = 低点 - 固定偏移
                sl_offset = 10.0  # TODO: 从配置获取
                sl = pivot_price - sl_offset
                # 止盈 = 最近的高点
                tp = self.pivot_service.find_nearest_pivot_price(symbol, 'high', current_price)
                print(f"[PivotSignalGenerator] 买入信号: pivot_price={pivot_price:.2f}, sl={sl:.2f}, tp={tp}")
            else:
                action = "sell"
                sl_offset = 10.0
                sl = pivot_price + sl_offset
                tp = self.pivot_service.find_nearest_pivot_price(symbol, 'low', current_price)
                print(f"[PivotSignalGenerator] 卖出信号: pivot_price={pivot_price:.2f}, sl={sl:.2f}, tp={tp}")

            # 如果没有找到反向转折点，或者止盈太近，使用风险回报比
            risk = abs(current_price - sl)
            min_reward = risk * 1.5  # 最小回报 = 1.5 倍风险

            if tp is None:
                if action == "buy":
                    tp = current_price + min_reward
                else:
                    tp = current_price - min_reward
                print(f"[PivotSignalGenerator] 未找到反向转折点，使用风险回报比: tp={tp:.2f}, risk={risk:.2f}")
            else:
                # 检查止盈是否太近
                reward = abs(tp - current_price)
                if reward < min_reward:
                    print(f"[PivotSignalGenerator] 止盈太近: reward={reward:.2f} < min_reward={min_reward:.2f}, 使用风险回报比")
                    if action == "buy":
                        tp = current_price + min_reward
                    else:
                        tp = current_price - min_reward

            # 计算风险回报比
            risk = abs(current_price - sl)
            reward = abs(tp - current_price)
            rr_ratio = reward / risk if risk > 0 else 0

            # 止损点数验证 - 如果止损点数太大，跳过这个 pivot
            risk_points = abs(current_price - sl)
            max_allowed_risk = current_price * 0.02  # 最大风险 = 当前价格的 2%
            if risk_points > max_allowed_risk:
                print(f"[PivotSignalGenerator] 止损点数过大: {risk_points:.2f} > {max_allowed_risk:.2f}, 跳过信号")
                continue

            # 止损止盈验证
            if sl <= 0 or tp <= 0:
                print(f"[PivotSignalGenerator] 无效的止损止盈: sl={sl}, tp={tp}, 跳过信号")
                continue

            # 止损方向验证
            if action == "buy" and sl >= current_price:
                print(f"[PivotSignalGenerator] 买入止损无效: sl={sl} >= price={current_price}")
                continue
            if action == "sell" and sl <= current_price:
                print(f"[PivotSignalGenerator] 卖出止损无效: sl={sl} <= price={current_price}")
                continue

            # 创建信号
            signal = TradingSignal(
                symbol=symbol,
                action=action,
                confidence=60,  # 基础置信度
                source=SignalSource.PIVOT,
                source_period=period,
                trigger_price=current_price,
                trigger_reason=f"{period}接近{pivot_type}点 {pivot_price:.2f}",
                suggested_entry=current_price,
                suggested_sl=sl,
                suggested_tp=tp,
                risk_reward_ratio=round(rr_ratio, 2),
                pivot_price=pivot_price,
                pivot_type=pivot_type,
            )

            print(f"[PivotSignalGenerator] 生成信号: {signal.signal_id} {action} @ {current_price}, SL={sl:.2f}, TP={tp:.2f}")
            return signal

        return None

    def generate_signals(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """生成所有周期的信号"""
        signals = []
        for period in ['M1', 'M5']:
            signal = self.generate_signal(symbol, current_price, period)
            if signal:
                signals.append(signal)
        return signals

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用，用于注册到SignalService"""
        return self.generate_signals(symbol, current_price)