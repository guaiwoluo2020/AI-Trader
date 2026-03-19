#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI入场信号生成器
根据AI分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal, SignalSource


class AIEntrySignalGenerator:
    """AI入场信号生成器"""

    def __init__(self):
        # LLM分析器引用
        self._llm_analyzer = None

        # 阈值（价格距离AI入场价的百分比）
        self.threshold = 0.0001  # 万分之一

        # 信号冷却时间（秒）
        self.cooldown = 300  # 5分钟

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[AIEntrySignalGenerator] AI入场信号生成器已初始化")

    def set_llm_analyzer(self, analyzer) -> None:
        """设置LLM分析器"""
        self._llm_analyzer = analyzer

    def _check_cooldown(self, symbol: str, period: str, entry_price: float, direction: str) -> bool:
        """检查是否在冷却期"""
        key = f"{symbol}_{period}_{entry_price}_{direction}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(self, symbol: str, period: str, entry_price: float, direction: str) -> None:
        """设置冷却"""
        key = f"{symbol}_{period}_{entry_price}_{direction}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """
        生成AI入场信号

        Args:
            symbol: 品种
            current_price: 当前价格

        Returns:
            TradingSignal 或 None
        """
        if not self._llm_analyzer:
            return None

        # 检查价格是否接近AI入场价
        matches = self._llm_analyzer.check_entry_price_nearby(
            symbol, current_price, threshold=self.threshold
        )

        if not matches:
            return None

        # 使用第一个匹配
        match = matches[0]
        period = match.get('period', '')
        entry_price = match.get('entry_price', 0)
        direction = match.get('direction', 'buy')
        sl = match.get('stop_loss', 0)
        tp = match.get('take_profit', 0)
        reason = match.get('reason', '')

        # 检查冷却
        if self._check_cooldown(symbol, period, entry_price, direction):
            return None

        # 设置冷却
        self._set_cooldown(symbol, period, entry_price, direction)

        # 确定方向
        action = "buy" if direction == "buy" else "sell"

        # 验证止损止盈
        if not sl or not tp or sl <= 0 or tp <= 0:
            print(f"[AIEntrySignalGenerator] 跳过无效信号: sl={sl}, tp={tp}")
            return None

        # 验证止损方向
        if action == "buy" and sl >= current_price:
            print(f"[AIEntrySignalGenerator] 买入止损无效: sl={sl} >= price={current_price}")
            return None
        if action == "sell" and sl <= current_price:
            print(f"[AIEntrySignalGenerator] 卖出止损无效: sl={sl} <= price={current_price}")
            return None

        # 计算风险回报比
        risk = abs(current_price - sl) if sl else 0
        reward = abs(tp - current_price) if tp else 0
        rr_ratio = reward / risk if risk > 0 else 0

        # 验证风险回报比
        if rr_ratio < 1.0:
            print(f"[AIEntrySignalGenerator] 风险回报比过低: {rr_ratio:.2f}, 跳过信号")
            return None

        # 验证止损点数（最大为价格的 2%）
        max_risk = current_price * 0.02
        if risk > max_risk:
            print(f"[AIEntrySignalGenerator] 止损点数过大: {risk:.2f} > {max_risk:.2f}, 跳过信号")
            return None

        print(f"[AIEntrySignalGenerator] 生成信号: {action} @ {current_price:.2f}, SL={sl:.2f}, TP={tp:.2f}, risk={risk:.2f}, rr={rr_ratio:.2f}")

        # 创建信号
        signal = TradingSignal(
            symbol=symbol,
            action=action,
            confidence=75,  # AI信号置信度较高
            source=SignalSource.AI_ENTRY,
            source_period=period,
            trigger_price=current_price,
            trigger_reason=f"AI建议入场: {reason}",
            suggested_entry=current_price,
            suggested_sl=sl,
            suggested_tp=tp,
            risk_reward_ratio=round(rr_ratio, 2),
            ai_analysis_period=period,
        )

        print(f"[AIEntrySignalGenerator] 生成信号: {signal.signal_id} {action} @ {current_price}, AI入场价={entry_price}")
        return signal

    def generate_signals(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """生成所有匹配的信号"""
        if not self._llm_analyzer:
            return []

        signals = []
        matches = self._llm_analyzer.check_entry_price_nearby(
            symbol, current_price, threshold=self.threshold
        )

        for match in matches:
            period = match.get('period', '')
            entry_price = match.get('entry_price', 0)
            direction = match.get('direction', 'buy')
            sl = match.get('stop_loss', 0)
            tp = match.get('take_profit', 0)
            reason = match.get('reason', '')

            # 检查冷却
            if self._check_cooldown(symbol, period, entry_price, direction):
                continue

            # 设置冷却
            self._set_cooldown(symbol, period, entry_price, direction)

            action = "buy" if direction == "buy" else "sell"

            # 验证止损止盈
            if not sl or not tp or sl <= 0 or tp <= 0:
                print(f"[AIEntrySignalGenerator] 跳过无效信号: sl={sl}, tp={tp}")
                continue

            # 验证止损方向
            if action == "buy" and sl >= current_price:
                continue
            if action == "sell" and sl <= current_price:
                continue

            risk = abs(current_price - sl) if sl else 0
            reward = abs(tp - current_price) if tp else 0
            rr_ratio = reward / risk if risk > 0 else 0

            # 验证风险回报比
            if rr_ratio < 1.0:
                continue

            # 验证止损点数（最大为价格的 2%）
            max_risk = current_price * 0.02
            if risk > max_risk:
                continue

            signal = TradingSignal(
                symbol=symbol,
                action=action,
                confidence=75,
                source=SignalSource.AI_ENTRY,
                source_period=period,
                trigger_price=current_price,
                trigger_reason=f"AI建议入场: {reason}",
                suggested_entry=current_price,
                suggested_sl=sl,
                suggested_tp=tp,
                risk_reward_ratio=round(rr_ratio, 2),
                ai_analysis_period=period,
            )
            signals.append(signal)

        return signals

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用"""
        return self.generate_signals(symbol, current_price)