#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略决策服务
综合信号、持仓、资金等做出交易决策
"""

from typing import Callable, List, Dict, Optional
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
        self._allowed_strategy_ids: Optional[set] = None

        print("[StrategyService] 策略决策服务已初始化")

    def set_position_service(self, service) -> None:
        """设置持仓服务"""
        self._position_service = service

    def set_pending_order_service(self, service) -> None:
        """设置待确认订单服务"""
        self._pending_order_service = service

    def set_allowed_strategy_ids(self, strategy_ids: List[str]) -> None:
        """限制当前账户可参与实盘决策的策略。"""
        self._allowed_strategy_ids = set(strategy_ids)

    # ==================== 策略配置 ====================

    def get_strategy(self, symbol: str) -> TradingStrategy:
        """获取品种策略配置"""
        return self.strategy_store.get_or_create_strategy(symbol)

    def get_strategies(self, symbol: str) -> List[TradingStrategy]:
        """获取品种的全部策略配置。"""
        getter = getattr(self.strategy_store, "get_strategies", None)
        if getter:
            strategies = getter(symbol)
            if strategies:
                return strategies
        return [self.get_strategy(symbol)]

    def update_strategy(
        self, symbol: str, data: Dict, strategy_id: str = None
    ) -> TradingStrategy:
        """更新策略配置"""
        return self.strategy_store.update_strategy(symbol, data, strategy_id)

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

        def aggregate_confidence(direction_signals: List[TradingSignal]) -> float:
            """Return a normalized 0-100 confidence, not a weighted contribution."""
            weighted = [
                (
                    signal.confidence,
                    strategy.get_signal_weight(signal.source, signal.source_period),
                )
                for signal in direction_signals
            ]
            total_weight = sum(weight for _, weight in weighted)
            if total_weight <= 0:
                return 0.0
            return round(
                sum(confidence * weight for confidence, weight in weighted)
                / total_weight,
                2,
            )

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
        has_skipped_conflict = (
            strategy.conflict_resolution == ConflictResolution.SKIP
            and bool(buy_signals)
            and bool(sell_signals)
        )
        if direction and not has_skipped_conflict:
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
            "buy_confidence": aggregate_confidence(buy_signals),
            "sell_confidence": aggregate_confidence(sell_signals),
            "consistency": round(consistency, 2),
            "direction": direction,
            "action": action,
            "buy_signals": [s.signal_id for s in buy_signals],
            "sell_signals": [s.signal_id for s in sell_signals],
            "filtered_out": len(signals) - len(filtered_signals),
        }

    # ==================== 决策生成 ====================

    def make_decisions(self, symbol: str, current_price: float,
                       force_signals: List[TradingSignal] = None,
                       strategy_ids: Optional[List[str]] = None) -> List[TradingDecision]:
        """分别使用该品种的所有策略生成决策。"""
        allowed_ids = (
            set(strategy_ids) if strategy_ids is not None
            else self._allowed_strategy_ids
        )
        return [
            decision
            for strategy in self.get_strategies(symbol)
            if allowed_ids is None or strategy.strategy_id in allowed_ids
            if (
                decision := self.make_decision(
                    symbol,
                    current_price,
                    force_signals=force_signals,
                    strategy=strategy,
                )
            ) is not None
        ]

    def make_decision(self, symbol: str, current_price: float,
                     force_signals: List[TradingSignal] = None,
                     strategy: TradingStrategy = None,
                     execution_mode: str = "live",
                     cooldown_scope: str = "live",
                     decision_time: datetime = None,
                     volume_calculator: Callable = None,
                     position_checker: Callable = None,
                     risk_checker: Callable = None) -> Optional[TradingDecision]:
        """
        做出交易决策

        Args:
            symbol: 品种
            current_price: 当前价格
            force_signals: 强制使用的信号（用于测试）

        Returns:
            TradingDecision 或 None
        """
        # 获取策略配置
        strategy = strategy or self.get_strategy(symbol)
        if not strategy.is_runnable_for(execution_mode):
            return None

        # 同一品种的多个策略独立冷却，互不阻塞。
        cooldown_key = f"{cooldown_scope}:{strategy.strategy_id}"
        if self._is_in_cooldown(cooldown_key, decision_time):
            return None

        # 获取信号
        signals = (
            force_signals
            if force_signals is not None
            else self.signal_service.get_active_signals(symbol)
        )

        # 过滤低置信度信号
        signals = [s for s in signals if s.confidence >= strategy.min_confidence]

        if not signals:
            return None

        # 分析信号
        analysis = self.analyze_signals(symbol, signals, strategy)

        if analysis["action"] == "none":
            return None

        action = analysis["action"]

        enabled_signals = [
            signal
            for signal in signals
            if strategy.is_signal_enabled(
                signal.source,
                signal.source_period if signal.source != "key_level" else None,
            )
        ]

        # 选择最佳信号（用于止损止盈）
        best_signal = self._select_best_signal(enabled_signals, action, strategy)
        if not best_signal:
            return None
        directional_signals = [s for s in enabled_signals if s.action == action]
        analysis = {
            **analysis,
            "selected_signal_id": best_signal.signal_id,
            "selected_signal_source": best_signal.source,
            "selected_signal_period": best_signal.source_period,
            "contributing_sources": sorted({s.source for s in directional_signals}),
        }

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
        volume = (
            volume_calculator(symbol, risk_points, strategy)
            if volume_calculator
            else self.risk_manager.calculate_volume(symbol, risk_points, strategy)
        )
        if volume <= 0:
            return None

        # 检查持仓限制
        position_check = (
            position_checker(symbol, strategy, action)
            if position_checker
            else self._check_position_limits(symbol, strategy, action)
        )

        # 检查风险限制
        risk_check = (
            risk_checker(symbol, volume, risk_points, strategy)
            if risk_checker
            else self.risk_manager.check_risk(symbol, volume, risk_points)
        )

        # 如果检查不通过，返回拒绝的决策
        if not position_check.get("allowed", True) or not risk_check.get("allowed", True):
            # 即使被拒绝也要设置冷却，避免频繁推送
            self._set_cooldown(cooldown_key, decision_time)
            warnings = (
                position_check.get("warnings", [])
                + risk_check.get("warnings", [])
            )
            rejection_reason = "；".join(warnings) or "风控检查未通过"
            decision = TradingDecision(
                symbol=symbol,
                strategy_id=strategy.strategy_id,
                strategy_name=strategy.strategy_name,
                auto_execute=strategy.auto_execute,
                action=action,
                decision_type="rejected",
                signals=[s.to_dict() for s in enabled_signals],
                signal_summary=analysis,
                entry_price=entry_price,
                sl=round(sl, 2),
                tp=round(tp, 2),
                volume=volume,
                risk_points=round(risk_points, 2),
                reward_points=round(reward_points, 2),
                risk_reward_ratio=round(rr_ratio, 2),
                decision_reason=f"风控拦截: {rejection_reason}",
                confidence_score=(
                    analysis["buy_confidence"]
                    if action == "buy"
                    else analysis["sell_confidence"]
                ),
                position_check=position_check,
                risk_check=risk_check,
                status="rejected",
                created_at=decision_time,
            )
            return decision

        # 设置决策冷却
        self._set_cooldown(cooldown_key, decision_time)

        # 生成决策理由
        decision_reason = self._generate_decision_reason(analysis, best_signal)

        # 创建决策
        decision = TradingDecision(
            symbol=symbol,
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.strategy_name,
            auto_execute=strategy.auto_execute,
            action=action,
            decision_type="signal_combined" if len(signals) > 1 else "single_signal",
            signals=[s.to_dict() for s in enabled_signals],
            signal_summary=analysis,
            entry_price=entry_price,
            sl=round(sl, 2),
            tp=round(tp, 2),
            volume=volume,
            risk_points=round(risk_points, 2),
            reward_points=round(reward_points, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            decision_reason=decision_reason,
            confidence_score=(
                analysis["buy_confidence"]
                if action == "buy"
                else analysis["sell_confidence"]
            ),
            position_check=position_check,
            risk_check=risk_check,
            created_at=decision_time,
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

    def _is_in_cooldown(
        self, cooldown_key: str, current_time: datetime = None
    ) -> bool:
        """检查是否在冷却期"""
        current_time = current_time or datetime.now()
        with self._cooldown_lock:
            if cooldown_key in self._decision_cooldowns:
                last_time = self._decision_cooldowns[cooldown_key]
                elapsed = (current_time - last_time).total_seconds()
                return elapsed < self.decision_cooldown
            return False

    def _set_cooldown(
        self, cooldown_key: str, current_time: datetime = None
    ) -> None:
        """设置冷却"""
        with self._cooldown_lock:
            self._decision_cooldowns[cooldown_key] = current_time or datetime.now()

    # ==================== 执行决策 ====================

    def execute_decision(self, decision: TradingDecision) -> Optional[str]:
        """
        执行决策（生成待确认订单）

        Args:
            decision: 交易决策

        Returns:
            订单ID 或 None
        """
        if decision.action == "none" or decision.status == "rejected":
            return None

        if not self._pending_order_service:
            print("[StrategyService] 待确认订单服务未设置")
            return None

        order_action = "b" if decision.action == "buy" else "s"

        # 创建订单
        order_id = self._pending_order_service.create_order(
            symbol=decision.symbol,
            action=order_action,
            price=decision.entry_price,
            mount=decision.volume,
            sl=decision.sl,
            tp=decision.tp,
            reason=decision.decision_reason,
            description=(
                f"Strategy: {decision.strategy_name} ({decision.strategy_id})"
            ),
            source="strategy_decision",
            strategy_id=decision.strategy_id,
            strategy_name=decision.strategy_name,
        )

        decision.order_id = order_id
        decision.status = "pending"

        if decision.auto_execute:
            confirmed_order = self._pending_order_service.confirm_order(order_id)
            if confirmed_order:
                decision.auto_executed = True
                decision.status = "confirmed"
                print(f"[StrategyService] 策略自动下单: {order_id}")
            else:
                print(f"[StrategyService] 策略自动下单失败: {order_id}")

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
