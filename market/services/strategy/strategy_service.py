#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略决策服务
综合信号、持仓、资金等做出交易决策
"""

from typing import Callable, List, Dict, Optional
from datetime import datetime
import threading

from ...models import (
    PositionManagementPolicy, TradingSignal, TradingStrategy, TradingDecision,
)
from ...models import ConsistencyRequirement, ConflictResolution
from ...store import StrategyStore
from sqlite_storage import PositionManagementPolicyRepository
from ..signal import SignalService
from ..position_manager import PositionManager
from .risk_manager import RiskManager


class StrategyService:
    """策略决策服务"""

    MAJORITY_THRESHOLD = 0.60

    def __init__(self, strategy_store: StrategyStore = None,
                 signal_service: SignalService = None,
                 risk_manager: RiskManager = None):
        self.strategy_store = strategy_store or StrategyStore()
        self.signal_service = signal_service or SignalService()
        self.risk_manager = risk_manager or RiskManager()
        self.position_manager = PositionManager()
        self.position_policy_repository = PositionManagementPolicyRepository()
        self._pivot_service = None

        # 持仓服务引用（外部设置）
        self._position_service = None

        # 待确认订单服务引用（外部设置）
        self._pending_order_service = None

        # 决策冷却
        self._decision_cooldowns: Dict[str, datetime] = {}
        self._cooldown_lock = threading.Lock()
        self.decision_cooldown = 60  # 60秒冷却
        self._allowed_strategy_ids: Optional[set] = None
        self._consensus_directions: Dict[str, str] = {}

        print("[StrategyService] 策略决策服务已初始化")

    def set_position_service(self, service) -> None:
        """设置持仓服务"""
        self._position_service = service

    def set_pending_order_service(self, service) -> None:
        """设置待确认订单服务"""
        self._pending_order_service = service

    def set_pivot_service(self, service) -> None:
        self._pivot_service = service

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
            return getter(symbol)
        strategy = self.strategy_store.get_strategy(symbol)
        return [strategy] if strategy is not None else []

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
        enabled_sources = strategy.get_signal_sources(enabled_only=True)
        total_sources = len(enabled_sources)

        # Keep one latest state per configured source instance. Tests and manual
        # signals without an instance id fall back to source + period identity.
        latest = {}
        filtered_out = 0
        for signal in signals or []:
            period = signal.source_period if signal.source != "key_level" else None
            if not strategy.is_signal_enabled(
                signal.source, period, signal.signal_source_id
            ):
                filtered_out += 1
                continue
            key = signal.signal_source_id or f"{signal.source}:{signal.source_period}"
            previous = latest.get(key)
            if previous is None or signal.created_at >= previous.created_at:
                latest[key] = signal
        filtered_signals = list(latest.values())
        if total_sources == 0:
            total_sources = len(filtered_signals)

        ready_signals = [
            signal for signal in filtered_signals if signal.state_ready
        ]
        up_signals = [
            signal for signal in ready_signals
            if signal.market_direction == "up"
        ]
        down_signals = [
            signal for signal in ready_signals
            if signal.market_direction == "down"
        ]
        sideways_signals = [
            signal for signal in ready_signals
            if signal.market_direction == "sideways"
        ]

        def aggregate_confidence(direction_signals: List[TradingSignal]) -> float:
            """Return a normalized 0-100 confidence, not a weighted contribution."""
            weighted = [
                (
                    signal.confidence,
                    strategy.get_signal_weight(
                        signal.source, signal.source_period,
                        signal.signal_source_id,
                    ),
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
            s.confidence * strategy.get_signal_weight(
                s.source, s.source_period, s.signal_source_id
            ) / 100
            for s in up_signals
        )
        sell_score = sum(
            s.confidence * strategy.get_signal_weight(
                s.source, s.source_period, s.signal_source_id
            ) / 100
            for s in down_signals
        )

        # Votes determine direction; weights only affect confidence.
        direction = None
        directional_count = 0
        if len(up_signals) > len(down_signals):
            direction = "buy"
            directional_count = len(up_signals)
        elif len(down_signals) > len(up_signals):
            direction = "sell"
            directional_count = len(down_signals)
        elif up_signals and down_signals:
            # ANY may still resolve an equal vote by score. Majority/ALL do not.
            if strategy.consistency_requirement == ConsistencyRequirement.ANY:
                if buy_score > sell_score:
                    direction = "buy"
                    directional_count = len(up_signals)
                elif sell_score > buy_score:
                    direction = "sell"
                    directional_count = len(down_signals)
        consistency = (
            directional_count / total_sources if total_sources > 0 else 0
        )
        direction_signals = up_signals if direction == "buy" else down_signals
        direction_confidence = aggregate_confidence(direction_signals)

        # 检查一致性要求
        action = "none"
        has_skipped_conflict = (
            strategy.conflict_resolution == ConflictResolution.SKIP
            and bool(up_signals)
            and bool(down_signals)
        )
        if direction and not has_skipped_conflict:
            if strategy.consistency_requirement == ConsistencyRequirement.ANY:
                if direction_confidence >= strategy.min_confidence:
                    action = direction
            elif strategy.consistency_requirement == ConsistencyRequirement.MAJORITY:
                if (
                    consistency >= self.MAJORITY_THRESHOLD
                    and direction_confidence >= strategy.min_confidence
                ):
                    action = direction
            elif strategy.consistency_requirement == ConsistencyRequirement.ALL:
                if (
                    directional_count == total_sources
                    and len(ready_signals) == total_sources
                    and direction_confidence >= strategy.min_confidence
                ):
                    action = direction

        triggered = any(
            signal.is_entry_trigger
            and signal.market_direction == (
                "up" if action == "buy" else "down"
            )
            for signal in direction_signals
        ) if action != "none" else False

        return {
            "total_count": total_sources,
            "reported_count": len(filtered_signals),
            "ready_count": len(ready_signals),
            "missing_count": max(0, total_sources - len(filtered_signals)),
            "buy_count": len(up_signals),
            "sell_count": len(down_signals),
            "sideways_count": len(sideways_signals),
            "buy_weighted_score": round(buy_score, 2),
            "sell_weighted_score": round(sell_score, 2),
            "buy_confidence": aggregate_confidence(up_signals),
            "sell_confidence": aggregate_confidence(down_signals),
            "consistency": round(consistency, 2),
            "majority_threshold": self.MAJORITY_THRESHOLD,
            "direction": direction,
            "action": action,
            "triggered": triggered,
            "buy_signals": [s.signal_id for s in up_signals],
            "sell_signals": [s.signal_id for s in down_signals],
            "sideways_signals": [s.signal_id for s in sideways_signals],
            "filtered_out": filtered_out,
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
                     risk_checker: Callable = None,
                     position_policy: PositionManagementPolicy = None,
                     position_context: Optional[Dict] = None) -> Optional[TradingDecision]:
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

        # 获取信号
        signals = (
            force_signals
            if force_signals is not None
            else self.signal_service.get_active_signals(symbol)
        )

        # Strategy-scoped signals must never leak into another strategy.
        signals = [
            signal for signal in signals
            if not getattr(signal, "strategy_id", "")
            or signal.strategy_id == strategy.strategy_id
        ]

        # 分析信号
        analysis = self.analyze_signals(symbol, signals, strategy)

        if analysis["action"] == "none":
            self._consensus_directions[cooldown_key] = "none"
            return None

        action = analysis["action"]
        previous_direction = self._consensus_directions.get(cooldown_key, "none")
        consensus_changed = previous_direction != action
        analysis["consensus_changed"] = consensus_changed
        if not analysis["triggered"] and not consensus_changed:
            return None
        if self._is_in_cooldown(cooldown_key, decision_time):
            return None

        enabled_signals = [
            signal
            for signal in signals
            if strategy.is_signal_enabled(
                signal.source,
                signal.source_period if signal.source != "key_level" else None,
                signal.signal_source_id,
            )
        ]

        # 选择最佳信号（用于止损止盈）
        best_signal = self._select_best_signal(enabled_signals, action, strategy)
        if not best_signal:
            return None
        market_direction = "up" if action == "buy" else "down"
        directional_signals = [
            signal for signal in enabled_signals
            if signal.state_ready and signal.market_direction == market_direction
        ]
        analysis = {
            **analysis,
            "selected_signal_id": best_signal.signal_id,
            "selected_signal_source": best_signal.source,
            "selected_signal_period": best_signal.source_period,
            "selected_signal_source_id": best_signal.signal_source_id,
            "contributing_sources": sorted({s.source for s in directional_signals}),
        }

        # 持仓管理器先生成初始保护方案，后续仓位管理继续使用同一快照。
        entry_price = current_price
        if position_policy is None and strategy.position_management_policy_id:
            user_id = int(getattr(self.strategy_store, "_user_id", 0) or 0)
            resolve_policy = getattr(
                self.position_policy_repository, "get_for_strategy", None
            )
            position_policy = (
                resolve_policy(user_id, strategy)
                if resolve_policy else self.position_policy_repository.get(
                    user_id, strategy.position_management_policy_id
                )
            )
        # Directly constructed strategies are used by isolated engines/tests.
        if position_policy is None:
            position_policy = PositionManagementPolicy(
                name="默认持仓管理", user_id=0,
                config={
                    "initial_stop_rules": [
                        {"type": "signal"},
                        {"type": "fixed_percent", "value": 0.003},
                    ],
                    "initial_take_profit_rules": [
                        {"type": "signal"},
                        {"type": "risk_reward", "value": 2},
                    ],
                    "management_rules": [],
                    "min_risk_reward": strategy.min_risk_reward,
                },
            )
        context = position_context or {}
        pivots = context.get("pivots")
        if pivots is None and self._pivot_service is not None:
            pivots = []
            for period in self._pivot_service.pivot_store.get_all_periods(symbol):
                pivots.extend(
                    item.to_dict()
                    for item in self._pivot_service.pivot_store.get_pivot_objects(
                        symbol, period
                    )
                )
        try:
            plan = self.position_manager.create_plan(
                position_policy, action, entry_price,
                signal_stop_loss=best_signal.suggested_sl,
                signal_take_profit=best_signal.suggested_tp,
                pivots=pivots or [], atr=float(context.get("atr", 0)),
                current_time=int(context.get("time", 0) or 0),
            )
        except ValueError as exc:
            print(f"[StrategyService] 持仓管理方案无法生成开仓计划: {exc}")
            return None
        sl, tp = plan.stop_loss, plan.take_profit
        analysis["position_management"] = {
            "policy_id": plan.policy_id,
            "policy_snapshot": plan.policy_snapshot,
            "stop_rule": plan.stop_rule,
            "take_profit_rule": plan.take_profit_rule,
            "initial_risk": plan.initial_risk,
            "explanation": plan.explanation,
        }

        has_fixed_take_profit = bool(tp and tp > 0)
        if not sl or sl == 0:
            print(f"[StrategyService] 无效的止损止盈: sl={sl}, tp={tp}")
            return None

        # 计算风险
        risk_points = abs(entry_price - sl)
        reward_points = abs(tp - entry_price) if has_fixed_take_profit else 0
        rr_ratio = (
            reward_points / risk_points
            if risk_points > 0 and has_fixed_take_profit else 0
        )

        # 检查风险回报比
        if has_fixed_take_profit and rr_ratio < strategy.min_risk_reward:
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
            self._consensus_directions[cooldown_key] = action
            warnings = (
                position_check.get("warnings", [])
                + risk_check.get("warnings", [])
            )
            rejection_reason = "；".join(warnings) or "风控检查未通过"
            decision = TradingDecision(
                symbol=symbol,
                strategy_id=strategy.strategy_id,
                strategy_name=strategy.strategy_name,
                auto_execute=True,
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
        self._consensus_directions[cooldown_key] = action

        # 生成决策理由
        decision_reason = self._generate_decision_reason(analysis, best_signal)

        # 创建决策
        decision = TradingDecision(
            symbol=symbol,
            strategy_id=strategy.strategy_id,
            strategy_name=strategy.strategy_name,
            auto_execute=True,
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
        market_direction = "up" if action == "buy" else "down"
        filtered = [
            signal for signal in signals
            if signal.state_ready and signal.market_direction == market_direction
        ]
        if not filtered:
            return None

        triggered = [signal for signal in filtered if signal.is_entry_trigger]
        candidates = triggered or filtered

        if strategy.conflict_resolution == ConflictResolution.HIGHEST_CONFIDENCE:
            return max(candidates, key=lambda s: s.confidence)
        elif strategy.conflict_resolution == ConflictResolution.HIGHEST_WEIGHT:
            return max(candidates, key=lambda s: s.confidence * strategy.get_signal_weight(
                s.source, s.source_period, s.signal_source_id
            ))
        else:
            return candidates[0]

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
        sideways_count = analysis.get("sideways_count", 0)
        missing_count = analysis.get("missing_count", 0)
        direction = analysis["direction"]

        if total == 1:
            reasons.append(f"单一信号({signal.source})建议{direction}")
        else:
            reasons.append(
                f"{total}个信号源中{buy_count}个上升、{sell_count}个下降、"
                f"{sideways_count}个震荡、{missing_count}个未就绪"
            )

        reasons.append(
            f"综合判断: {direction}，一致率{analysis.get('consistency', 0):.0%}"
        )
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
        source = str(decision.signal_summary.get("selected_signal_source", ""))
        source_id = str(
            decision.signal_summary.get("selected_signal_source_id", "")
        )
        stored_strategy = (
            self.strategy_store.get_strategy_by_id(decision.strategy_id)
            if hasattr(self.strategy_store, "get_strategy_by_id") else None
        )
        description = f"AIT|{decision.strategy_id}|{source_id}"

        # 创建订单
        order_id = self._pending_order_service.create_order(
            symbol=decision.symbol,
            action=order_action,
            price=decision.entry_price,
            mount=decision.volume,
            sl=decision.sl,
            tp=decision.tp,
            reason=decision.decision_reason,
            description=description,
            source="strategy_decision",
            strategy_id=decision.strategy_id,
            strategy_name=decision.strategy_name,
            signal_source_id=source_id,
            exit_mode="position_manager",
            trailing_activation_r=1.0,
            trailing_distance_r=1.0,
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
