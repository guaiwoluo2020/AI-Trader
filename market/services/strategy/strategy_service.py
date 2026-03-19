#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略决策服务
综合信号、持仓、资金等做出交易决策
"""

from typing import List, Dict, Optional
from datetime import datetime
import threading

from ...models import TradingSignal, TradingStrategy, TradingDecision
from ...models import ConsistencyRequirement, ConflictResolution
from ...models import StopLossMode, TakeProfitMode
from ...store import StrategyStore
from ..signal import SignalService
from .risk_manager import RiskManager


class StrategyService:
    """策略决策服务"""

    def __init__(self, strategy_store: StrategyStore = None,
                 signal_service: SignalService = None,
                 risk_manager: RiskManager = None):
        self.strategy_store = strategy_store or StrategyStore()
        self.signal_service = signal_service or SignalService()
        self.risk_manager = risk_manager or RiskManager()

        # 持仓服务引用（外部设置）
        self._position_service = None

        # 待确认订单服务引用（外部设置）
        self._pending_order_service = None

        # 决策冷却
        self._decision_cooldowns: Dict[str, datetime] = {}
        self._cooldown_lock = threading.Lock()
        self.decision_cooldown = 60  # 60秒冷却

        print("[StrategyService] 策略决策服务已初始化")

    def set_position_service(self, service) -> None:
        """设置持仓服务"""
        self._position_service = service

    def set_pending_order_service(self, service) -> None:
        """设置待确认订单服务"""
        self._pending_order_service = service

    # ==================== 策略配置 ====================

    def get_strategy(self, symbol: str) -> TradingStrategy:
        """获取品种策略配置"""
        return self.strategy_store.get_or_create_strategy(symbol)

    def update_strategy(self, symbol: str, data: Dict) -> TradingStrategy:
        """更新策略配置"""
        return self.strategy_store.update_strategy(symbol, data)

    def get_all_strategies(self) -> List[TradingStrategy]:
        """获取所有策略"""
        return self.strategy_store.get_all_strategies()

    # ==================== 信号综合分析 ====================

    def analyze_signals(self, symbol: str, signals: List[TradingSignal],
                       strategy: TradingStrategy) -> Dict:
        """
        综合分析信号

        Args:
            symbol: 品种
            signals: 信号列表
            strategy: 策略配置

        Returns:
            分析结果
        """
        if not signals:
            return {
                "total_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "buy_weighted_score": 0,
                "sell_weighted_score": 0,
                "consistency": 0,
                "direction": None,
                "action": "none",
            }

        # 过滤掉未启用的信号
        filtered_signals = []
        for s in signals:
            period = s.source_period if s.source != "key_level" else None
            if strategy.is_signal_enabled(s.source, period):
                filtered_signals.append(s)

        if not filtered_signals:
            return {
                "total_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "buy_weighted_score": 0,
                "sell_weighted_score": 0,
                "consistency": 0,
                "direction": None,
                "action": "none",
                "filtered_out": len(signals),
            }

        buy_signals = [s for s in filtered_signals if s.action == "buy"]
        sell_signals = [s for s in filtered_signals if s.action == "sell"]

        # 计算加权分数（使用新的周期级别权重）
        buy_score = sum(
            s.confidence * strategy.get_signal_weight(s.source, s.source_period) / 100
            for s in buy_signals
        )
        sell_score = sum(
            s.confidence * strategy.get_signal_weight(s.source, s.source_period) / 100
            for s in sell_signals
        )

        # 计算一致性
        total = len(filtered_signals)
        majority_count = max(len(buy_signals), len(sell_signals))
        consistency = majority_count / total if total > 0 else 0

        # 确定方向
        direction = None
        if buy_score > sell_score:
            direction = "buy"
        elif sell_score > buy_score:
            direction = "sell"

        # 检查一致性要求
        action = "none"
        if direction:
            if strategy.consistency_requirement == ConsistencyRequirement.ANY:
                action = direction
            elif strategy.consistency_requirement == ConsistencyRequirement.MAJORITY:
                if consistency >= 0.5:
                    action = direction
            elif strategy.consistency_requirement == ConsistencyRequirement.ALL:
                if consistency == 1.0:
                    action = direction

        return {
            "total_count": total,
            "buy_count": len(buy_signals),
            "sell_count": len(sell_signals),
            "buy_weighted_score": round(buy_score, 2),
            "sell_weighted_score": round(sell_score, 2),
            "consistency": round(consistency, 2),
            "direction": direction,
            "action": action,
            "buy_signals": [s.signal_id for s in buy_signals],
            "sell_signals": [s.signal_id for s in sell_signals],
            "filtered_out": len(signals) - len(filtered_signals),
        }

    # ==================== 决策生成 ====================

    def make_decision(self, symbol: str, current_price: float,
                     force_signals: List[TradingSignal] = None) -> Optional[TradingDecision]:
        """
        做出交易决策

        Args:
            symbol: 品种
            current_price: 当前价格
            force_signals: 强制使用的信号（用于测试）

        Returns:
            TradingDecision 或 None
        """
        # 检查决策冷却
        if self._is_in_cooldown(symbol):
            return None

        # 获取策略配置
        strategy = self.get_strategy(symbol)
        if not strategy.enabled:
            return None

        # 获取信号
        signals = force_signals if force_signals else self.signal_service.get_active_signals(symbol)

        # 过滤低置信度信号
        signals = [s for s in signals if s.confidence >= strategy.min_confidence]

        if not signals:
            return None

        # 分析信号
        analysis = self.analyze_signals(symbol, signals, strategy)

        if analysis["action"] == "none":
            return None

        action = analysis["action"]

        # 选择最佳信号（用于止损止盈）
        best_signal = self._select_best_signal(signals, action, strategy)
        if not best_signal:
            return None

        # 计算止损止盈
        entry_price = current_price
        sl, tp = self._calculate_sl_tp(entry_price, best_signal, strategy)

        if not sl or not tp or sl == 0 or tp == 0:
            print(f"[StrategyService] 无效的止损止盈: sl={sl}, tp={tp}")
            return None

        # 计算风险
        risk_points = abs(entry_price - sl)
        reward_points = abs(tp - entry_price)
        rr_ratio = reward_points / risk_points if risk_points > 0 else 0

        # 检查风险回报比
        if rr_ratio < strategy.min_risk_reward:
            print(f"[StrategyService] 风险回报比 {rr_ratio:.2f} 低于最小要求 {strategy.min_risk_reward}")
            return None

        # 动态止损范围（根据价格调整）
        # 最小止损 = 价格的 0.05% 或 5 点（取较大）
        # 最大止损 = 价格的 2% 或 100 点（取较小）
        price_min_sl = entry_price * 0.0005  # 价格的 0.05%
        price_max_sl = entry_price * 0.02    # 价格的 2%

        # 确保 min <= max
        dynamic_min_sl = max(1.0, price_min_sl)  # 最小至少 1 点
        dynamic_max_sl = max(dynamic_min_sl, price_max_sl)  # 最大至少等于最小

        # 如果动态范围不合理，跳过
        if dynamic_min_sl > dynamic_max_sl:
            print(f"[StrategyService] 动态止损范围无效: [{dynamic_min_sl:.2f}, {dynamic_max_sl:.2f}], 跳过决策")
            return None

        # 检查止损点数
        if risk_points < dynamic_min_sl or risk_points > dynamic_max_sl:
            print(f"[StrategyService] 止损点数 {risk_points:.2f} 不在动态范围 [{dynamic_min_sl:.2f}, {dynamic_max_sl:.2f}] (价格={entry_price:.2f})")
            return None

        # 计算手数
        volume = self.risk_manager.calculate_volume(symbol, risk_points, strategy)
        if volume <= 0:
            return None

        # 检查持仓限制
        position_check = self._check_position_limits(symbol, strategy, action)

        # 检查风险限制
        risk_check = self.risk_manager.check_risk(symbol, volume, risk_points)

        # 如果检查不通过，返回拒绝的决策
        if not position_check.get("allowed", True) or not risk_check.get("allowed", True):
            # 即使被拒绝也要设置冷却，避免频繁推送
            self._set_cooldown(symbol)
            decision = TradingDecision(
                symbol=symbol,
                strategy_id=strategy.strategy_id,
                action="none",
                decision_type="rejected",
                signals=[s.to_dict() for s in signals],
                signal_summary=analysis,
                decision_reason="风控检查未通过",
                confidence_score=0,
                position_check=position_check,
                risk_check=risk_check,
                status="rejected",
            )
            return decision

        # 设置决策冷却
        self._set_cooldown(symbol)

        # 生成决策理由
        decision_reason = self._generate_decision_reason(analysis, best_signal)

        # 创建决策
        decision = TradingDecision(
            symbol=symbol,
            strategy_id=strategy.strategy_id,
            action=action,
            decision_type="signal_combined" if len(signals) > 1 else "single_signal",
            signals=[s.to_dict() for s in signals],
            signal_summary=analysis,
            entry_price=entry_price,
            sl=round(sl, 2),
            tp=round(tp, 2),
            volume=volume,
            risk_points=round(risk_points, 2),
            reward_points=round(reward_points, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            decision_reason=decision_reason,
            confidence_score=analysis["buy_weighted_score"] if action == "buy" else analysis["sell_weighted_score"],
            position_check=position_check,
            risk_check=risk_check,
        )

        print(f"[StrategyService] 生成决策: {decision.decision_id} {action} {symbol} @ {entry_price}")

        return decision

    def _select_best_signal(self, signals: List[TradingSignal],
                           action: str, strategy: TradingStrategy) -> Optional[TradingSignal]:
        """选择最佳信号"""
        filtered = [s for s in signals if s.action == action]
        if not filtered:
            return None

        if strategy.conflict_resolution == ConflictResolution.HIGHEST_CONFIDENCE:
            return max(filtered, key=lambda s: s.confidence)
        elif strategy.conflict_resolution == ConflictResolution.HIGHEST_WEIGHT:
            return max(filtered, key=lambda s: s.confidence * strategy.get_signal_weight(s.source, s.source_period))
        else:
            return filtered[0]

    def _calculate_sl_tp(self, entry_price: float, signal: TradingSignal,
                        strategy: TradingStrategy) -> tuple:
        """计算止损止盈"""
        # 止损
        if strategy.sl_mode == StopLossMode.SIGNAL:
            sl = signal.suggested_sl
        elif strategy.sl_mode == StopLossMode.FIXED_POINTS:
            if signal.action == "buy":
                sl = entry_price - strategy.sl_fixed_points
            else:
                sl = entry_price + strategy.sl_fixed_points
        else:
            sl = signal.suggested_sl

        # 止盈
        if strategy.tp_mode == TakeProfitMode.SIGNAL:
            tp = signal.suggested_tp
        elif strategy.tp_mode == TakeProfitMode.FIXED_POINTS:
            if signal.action == "buy":
                tp = entry_price + strategy.tp_fixed_points
            else:
                tp = entry_price - strategy.tp_fixed_points
        elif strategy.tp_mode == TakeProfitMode.RISK_REWARD:
            risk = abs(entry_price - sl)
            if signal.action == "buy":
                tp = entry_price + risk * strategy.tp_risk_reward
            else:
                tp = entry_price - risk * strategy.tp_risk_reward
        else:
            tp = signal.suggested_tp

        return sl, tp

    def _check_position_limits(self, symbol: str, strategy: TradingStrategy,
                               action: str) -> Dict:
        """检查持仓限制"""
        current_positions = 0
        same_direction = 0
        opposite_direction = 0

        if self._position_service:
            positions = self._position_service.get_positions(symbol)
            current_positions = len(positions)
            for pos in positions:
                # PositionData.to_dict() 返回 direction 字段
                pos_direction = pos.get('direction', '')
                if pos_direction == action:
                    same_direction += 1
                else:
                    opposite_direction += 1

        return self.risk_manager.check_position_limit(
            symbol, strategy, current_positions, same_direction, opposite_direction, action
        )

    def _generate_decision_reason(self, analysis: Dict, signal: TradingSignal) -> str:
        """生成决策理由"""
        reasons = []

        total = analysis["total_count"]
        buy_count = analysis["buy_count"]
        sell_count = analysis["sell_count"]
        direction = analysis["direction"]

        if total == 1:
            reasons.append(f"单一信号({signal.source})建议{direction}")
        else:
            reasons.append(f"{total}个信号中{buy_count}个买入、{sell_count}个卖出")

        reasons.append(f"综合判断: {direction}")
        reasons.append(f"风险回报比: {signal.risk_reward_ratio:.2f}")

        return " | ".join(reasons)

    def _is_in_cooldown(self, symbol: str) -> bool:
        """检查是否在冷却期"""
        with self._cooldown_lock:
            if symbol in self._decision_cooldowns:
                last_time = self._decision_cooldowns[symbol]
                elapsed = (datetime.now() - last_time).total_seconds()
                return elapsed < self.decision_cooldown
            return False

    def _set_cooldown(self, symbol: str) -> None:
        """设置冷却"""
        with self._cooldown_lock:
            self._decision_cooldowns[symbol] = datetime.now()

    # ==================== 执行决策 ====================

    def execute_decision(self, decision: TradingDecision) -> Optional[str]:
        """
        执行决策（生成待确认订单）

        Args:
            decision: 交易决策

        Returns:
            订单ID 或 None
        """
        if decision.action == "none":
            return None

        if not self._pending_order_service:
            print("[StrategyService] 待确认订单服务未设置")
            return None

        # 创建订单
        order_id = self._pending_order_service.create_order(
            symbol=decision.symbol,
            action=decision.action,
            price=decision.entry_price,
            mount=decision.volume,
            sl=decision.sl,
            tp=decision.tp,
            reason=decision.decision_reason,
            description=f"Strategy: {decision.strategy_id}",
            source="strategy_decision",
        )

        decision.order_id = order_id
        decision.status = "confirmed"

        print(f"[StrategyService] 决策已执行，订单ID: {order_id}")
        return order_id

    # ==================== 状态 ====================

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            "strategy_store": self.strategy_store.get_status(),
            "signal_service": self.signal_service.get_status(),
            "risk_manager": self.risk_manager.get_status(),
        }