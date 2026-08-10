#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI入场信号生成器
根据AI分析生成交易信号
"""

from typing import Optional, List, Dict
from datetime import datetime
import time

from ...models import SignalSource, TradingSignal
from .signal_rules import (
    build_ai_entry_signal, direction_action, extract_ai_trend_state,
)


class AIEntrySignalGenerator:
    """AI入场信号生成器"""

    def __init__(self, shared_runtime_repo=None):
        # LLM分析器引用
        self._llm_analyzer = None
        self._shared_runtime_repo = shared_runtime_repo

        # 阈值（价格距离AI入场价的百分比）
        self.threshold = 0.0001  # 万分之一

        # 信号冷却时间（秒）
        self.cooldown = 300  # 5分钟

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}
        self._consumed_recommendations = set()
        self._consumed_order = []

        print("[AIEntrySignalGenerator] AI入场信号生成器已初始化")

    def set_llm_analyzer(self, analyzer) -> None:
        """设置LLM分析器"""
        self._llm_analyzer = analyzer

    def _check_cooldown(
        self, symbol: str, period: str, entry_price: float, direction: str,
        strategy_id: str = "", signal_source_id: str = "",
    ) -> bool:
        """检查是否在冷却期"""
        key = f"{strategy_id}_{signal_source_id}_{symbol}_{period}_{entry_price}_{direction}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < self.cooldown
        return False

    def _set_cooldown(
        self, symbol: str, period: str, entry_price: float, direction: str,
        strategy_id: str = "", signal_source_id: str = "",
    ) -> None:
        """设置冷却"""
        key = f"{strategy_id}_{signal_source_id}_{symbol}_{period}_{entry_price}_{direction}"
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
        signals = self.generate_signals(symbol, current_price)
        return signals[0] if signals else None

    def generate_signals(
        self, symbol: str, current_price: float, strategy_id: str = "",
        signal_source_id: str = "", threshold: float = None,
        min_confidence: int = 0,
    ) -> List[TradingSignal]:
        """生成所有匹配的信号"""
        if not self._llm_analyzer:
            return []

        signals = []
        threshold = self.threshold if threshold is None else float(threshold)
        try:
            matches = self._llm_analyzer.check_entry_price_nearby(
                symbol, current_price, threshold=threshold,
                strategy_id=strategy_id,
            )
        except TypeError as exc:
            # Keep legacy analyzers usable while strategy-aware matching rolls out.
            if "strategy_id" not in str(exc):
                raise
            matches = self._llm_analyzer.check_entry_price_nearby(
                symbol, current_price, threshold=self.threshold,
            )

        for match in matches:
            if signal_source_id and match.get("signal_source_id") not in {
                None, "", signal_source_id
            }:
                continue
            period = match.get('period', '')
            entry_price = match.get('entry_price', 0)
            direction = match.get('direction', 'buy')
            analysis_id = str(match.get("analyzed_at") or "")
            recommendation_key = (
                analysis_id,
                strategy_id,
                signal_source_id,
                symbol,
                period,
                direction,
                round(float(entry_price or 0), 8),
            )
            if analysis_id and recommendation_key in self._consumed_recommendations:
                continue
            if int(match.get("confidence", 0)) < int(min_confidence):
                continue

            # 检查冷却
            if not analysis_id and self._check_cooldown(
                symbol, period, entry_price, direction, strategy_id,
                signal_source_id,
            ):
                continue

            signal = build_ai_entry_signal(
                symbol, current_price, match, threshold=threshold
            )
            if signal:
                if analysis_id:
                    self._remember_consumed(recommendation_key)
                else:
                    self._set_cooldown(
                        symbol, period, entry_price, direction, strategy_id,
                        signal_source_id,
                    )
                signal.signal_source_id = signal_source_id
                signals.append(signal)

        return signals

    def _remember_consumed(self, key) -> None:
        """Bound memory while ensuring one emission per analyzed recommendation."""
        self._consumed_recommendations.add(key)
        self._consumed_order.append(key)
        if len(self._consumed_order) > 5000:
            expired = self._consumed_order.pop(0)
            self._consumed_recommendations.discard(expired)

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        """Return one persistent AI direction state per configured source."""
        signals = []
        for config in strategy.get_signal_sources("ai_entry", enabled_only=True):
            params = config.get("params") or {}
            source_id = config["signal_source_id"]
            if params.get("analysis_mode", "self_analysis") == "shared_reference":
                signals.append(self._shared_reference_signal(
                    symbol, current_price, strategy, config
                ))
                continue
            triggers = [
                signal for signal in self.generate_signals(
                    symbol, current_price, strategy.strategy_id, source_id,
                    float(params.get("entry_threshold", self.threshold)),
                    int(params.get("min_confidence", strategy.min_confidence)),
                ) if signal.source_period == config["period"]
            ]
            trigger = max(triggers, key=lambda item: item.confidence) if triggers else None
            state = self._trend_state(symbol, config["period"])
            if trigger is not None and not state["ready"]:
                state = {
                    "ready": True,
                    "direction": "up" if trigger.action == "buy" else "down",
                    "confidence": trigger.confidence,
                    "reason": trigger.trigger_reason,
                }
            if trigger is None:
                trigger = TradingSignal(
                    symbol=symbol,
                    action=direction_action(state["direction"]),
                    market_direction=state["direction"],
                    state_ready=state["ready"],
                    is_entry_trigger=False,
                    confidence=state["confidence"],
                    source=SignalSource.AI_ENTRY,
                    source_period=config["period"],
                    trigger_price=current_price,
                    trigger_reason=state["reason"],
                    suggested_entry=current_price,
                )
            else:
                trigger_direction = trigger.action
                trigger.market_direction = state["direction"]
                trigger.action = direction_action(state["direction"])
                trigger.state_ready = state["ready"]
                trigger.confidence = state["confidence"]
                trigger.is_entry_trigger = (
                    state["direction"] in {"up", "down"}
                    and trigger_direction == direction_action(state["direction"])
                )
                trigger.trigger_reason = state["reason"] or trigger.trigger_reason
            trigger.signal_source_id = source_id
            signals.append(trigger)
        return signals

    def get_shared_reference_state(
        self, symbol: str, current_price: float, strategy, config: Dict,
    ) -> Dict:
        """Resolve a shared snapshot using the current user's thresholds and price."""
        params = config.get("params") or {}
        share_id = str(params.get("shared_runtime_id") or "")
        shared = (
            self._shared_runtime_repo.get_shared(share_id)
            if self._shared_runtime_repo and share_id else None
        )
        empty = {
            "ready": False, "direction": "sideways", "confidence": 0,
            "reason": "共享AI运行数据已取消共享或不存在", "shared": shared,
            "suggestion": None, "trigger": None, "stale": False,
        }
        if not shared:
            return empty

        result = shared.get("result") or {}
        source_period = str(shared.get("period") or config.get("period") or "")
        source_id = str(shared.get("signal_source_id") or "")
        suggestions = [
            item for item in (result.get("trade_suggestions") or [])
            if not item.get("signal_source_id")
            or item.get("signal_source_id") == source_id
        ]
        suggestion = max(
            suggestions,
            key=lambda item: int(item.get("confidence", 0) or 0),
            default=None,
        )
        trend = extract_ai_trend_state(result, source_period)
        direction = (
            "up" if (suggestion or {}).get("direction") == "buy"
            else "down" if (suggestion or {}).get("direction") == "sell"
            else trend["direction"]
        )
        confidence = int(
            (suggestion or {}).get("confidence") or trend["confidence"] or 0
        )
        original_interval = int(
            (shared.get("signal_params") or {}).get(
                "analysis_interval_minutes", 5
            ) or 5
        )
        stale_after = max(15 * 60, original_interval * 3 * 60)
        stale = bool(result.get("data_stale")) or (
            int(time.time()) - int(shared.get("last_run_at") or 0) > stale_after
        )
        min_confidence = int(params.get("min_confidence", strategy.min_confidence))
        ready = bool((trend["ready"] or suggestion) and not stale)
        reason = (
            "共享AI运行数据已过期，等待共享者更新"
            if stale else str(
                (suggestion or {}).get("reason") or trend["reason"]
            )
        )
        trigger = None
        if ready and suggestion and confidence >= min_confidence:
            local_suggestion = dict(suggestion)
            local_suggestion["period"] = config.get("period", source_period)
            trigger = build_ai_entry_signal(
                symbol, current_price, local_suggestion,
                threshold=float(params.get("entry_threshold", self.threshold)),
                require_suggested_exits=False,
            )
            if trigger:
                trigger.trigger_reason = (
                    f"引用 {shared.get('owner_username', '')} 的共享AI分析: "
                    f"{trigger.trigger_reason}"
                )
        return {
            "ready": ready, "direction": direction, "confidence": confidence,
            "reason": reason, "shared": shared, "suggestion": suggestion,
            "trigger": trigger, "stale": stale,
        }

    def _shared_reference_signal(
        self, symbol: str, current_price: float, strategy, config: Dict,
    ) -> TradingSignal:
        state = self.get_shared_reference_state(
            symbol, current_price, strategy, config
        )
        trigger = state["trigger"]
        shared = state.get("shared") or {}
        recommendation_key = (
            str(shared.get("share_id") or ""),
            int(shared.get("last_run_at") or 0),
            strategy.strategy_id,
            config["signal_source_id"],
        )
        if trigger is not None and recommendation_key in self._consumed_recommendations:
            trigger = None
        elif trigger is not None:
            self._remember_consumed(recommendation_key)
        if trigger is None:
            trigger = TradingSignal(
                symbol=symbol,
                action=direction_action(state["direction"]),
                market_direction=state["direction"],
                state_ready=state["ready"],
                is_entry_trigger=False,
                confidence=state["confidence"],
                source=SignalSource.AI_ENTRY,
                source_period=config["period"],
                trigger_price=current_price,
                trigger_reason=state["reason"],
                suggested_entry=current_price,
            )
        else:
            trigger.market_direction = state["direction"]
            trigger.action = direction_action(state["direction"])
            trigger.state_ready = state["ready"]
            trigger.confidence = state["confidence"]
            trigger.is_entry_trigger = state["direction"] in {"up", "down"}
        trigger.signal_source_id = config["signal_source_id"]
        return trigger

    def _trend_state(self, symbol: str, period: str) -> Dict:
        if not self._llm_analyzer or not hasattr(self._llm_analyzer, "get_analysis"):
            return {
                "ready": False, "direction": "sideways", "confidence": 0,
                "reason": "尚未取得AI趋势分析",
            }
        return extract_ai_trend_state(
            self._llm_analyzer.get_analysis(symbol), period
        )

    def __call__(self, symbol: str, current_price: float) -> List[TradingSignal]:
        """使对象可调用"""
        return self.generate_signals(symbol, current_price)
