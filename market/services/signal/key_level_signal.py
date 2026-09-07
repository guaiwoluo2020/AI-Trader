#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关键点位信号生成器
根据关键点位分析生成交易信号
"""

import ast
import math
from typing import Optional, List, Dict
from datetime import datetime

from ...models import TradingSignal
from .signal_rules import (
    automatic_key_levels, build_key_level_signal, build_key_level_state_signal,
)


_EXPRESSION_NAMES = {
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
}
_EXPRESSION_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
    ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.UAdd, ast.USub,
)


def evaluate_key_level_expression(expression: str, price: float) -> List[float]:
    """安全计算一个以 price 为变量的关键点位表达式。"""
    if not str(expression or "").strip():
        return []
    tree = ast.parse(expression, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _EXPRESSION_NODES):
            raise ValueError("关键点位表达式包含不支持的语法")
        if isinstance(node, ast.Name) and node.id not in {"price", *_EXPRESSION_NAMES}:
            raise ValueError(f"关键点位表达式不支持变量 {node.id}")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name)
            or node.func.id not in _EXPRESSION_NAMES
        ):
            raise ValueError("关键点位表达式只能使用允许的数学函数")
    value = eval(  # noqa: S307 - AST is restricted above.
        compile(tree, "<key-level-expression>", "eval"),
        {"__builtins__": {}},
        {"price": float(price), **_EXPRESSION_NAMES},
    )
    level = float(value)
    return [level] if level > 0 and math.isfinite(level) else []


class KeyLevelSignalGenerator:
    """关键点位信号生成器"""

    def __init__(self, kline_store=None):
        # 关键点位配置
        self._key_levels: Dict[str, List[float]] = {}
        self.kline_store = kline_store

        # 阈值（价格距离关键点位的百分比）
        self.threshold = 0.0008  # 万分之八

        # 信号冷却时间（秒）
        # Reversal attempts at the same round-number level are intentionally
        # sparse.  Breakouts use a separate cooldown key and therefore are not
        # blocked by this two-hour reversal lock.
        self.cooldown = 2 * 60 * 60

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}
        self._last_prices: Dict[str, float] = {}
        # Per strategy/source state for the special level-19 staged breakout.
        self._breakout_retest_states: Dict[str, str] = {}

        print("[KeyLevelSignalGenerator] 关键点位信号生成器已初始化")

    @staticmethod
    def _calculate_atr(rows: List[Dict], period: int = 14) -> float:
        """Calculate a simple Wilder-compatible true-range average.

        The legacy key-level generator used only a percentage proximity.  This
        small, deterministic calculation keeps it aligned with the latest
        volatility-aware configuration without adding a second indicator
        dependency.  A short history is still useful during warm-up; callers
        only receive a value when at least two bars have valid OHLC data.
        """
        if not rows:
            return 0.0
        true_ranges = []
        previous_close = None
        for row in rows[-max(2, int(period) + 1):]:
            try:
                high = float(row.get("high", row.get("high_price")))
                low = float(row.get("low", row.get("low_price")))
                close = float(row.get("close", row.get("close_price")))
            except (AttributeError, TypeError, ValueError):
                continue
            if high <= 0 or low <= 0 or close <= 0 or high < low:
                continue
            if previous_close is None:
                tr = high - low
            else:
                tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
            if tr >= 0:
                true_ranges.append(tr)
            previous_close = close
        if len(true_ranges) < 2:
            return 0.0
        return sum(true_ranges[-period:]) / min(period, len(true_ranges))

    def _atr_for(self, symbol: str, period: str = "M1") -> float:
        if self.kline_store is None:
            return 0.0
        try:
            rows = self.kline_store.get_all_klines(symbol, str(period or "M1").upper())
        except Exception:
            return 0.0
        return self._calculate_atr(rows)

    def set_key_levels(self, symbol: str, levels: List[float]) -> None:
        """设置品种的关键点位"""
        self._key_levels[symbol] = sorted(levels)

    def get_key_levels(self, symbol: str, current_price: float) -> List[float]:
        """获取关键点位（如果没有配置则自动计算）"""
        if symbol in self._key_levels:
            return self._key_levels[symbol]

        # 自动计算关键点位
        return self._auto_calculate_key_levels(current_price)

    def _auto_calculate_key_levels(self, current_price: float) -> List[float]:
        """自动计算关键点位"""
        return automatic_key_levels(current_price)

    def _check_cooldown(
        self, symbol: str, key_level: float, strategy_id: str = "",
        signal_source_id: str = "", cooldown: int = None,
        setup_type: str = "", direction: str = "", period: str = "",
    ) -> bool:
        """检查是否在冷却期"""
        key = self._cooldown_key(
            symbol, key_level, strategy_id, signal_source_id,
            setup_type, direction, period,
        )
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < (self.cooldown if cooldown is None else cooldown)
        return False

    def _set_cooldown(
        self, symbol: str, key_level: float, strategy_id: str = "",
        signal_source_id: str = "", setup_type: str = "",
        direction: str = "", period: str = "",
    ) -> None:
        """设置冷却"""
        key = self._cooldown_key(
            symbol, key_level, strategy_id, signal_source_id,
            setup_type, direction, period,
        )
        self._signal_cooldowns[key] = datetime.now()

    @staticmethod
    def _cooldown_key(
        symbol: str, key_level: float, strategy_id: str,
        signal_source_id: str, setup_type: str, direction: str,
        period: str,
    ) -> str:
        return "|".join(str(value or "") for value in (
            strategy_id, signal_source_id, symbol, key_level,
            setup_type, direction, period,
        ))

    def _clear_setup_cooldown(
        self, symbol: str, key_level: float, strategy_id: str,
        signal_source_id: str, setup_type: str, period: str,
    ) -> None:
        """A confirmed breakout ends the old reversal cycle for this level."""
        prefix = self._cooldown_key(
            symbol, key_level, strategy_id, signal_source_id,
            setup_type, "", period,
        ).rsplit("|", 2)[0]
        for key in list(self._signal_cooldowns):
            if key.startswith(prefix + "|"):
                self._signal_cooldowns.pop(key, None)

    def _price_state_key(
        self, symbol: str, key_level: float, strategy_id: str = "",
        signal_source_id: str = "",
    ) -> str:
        return f"{strategy_id}_{signal_source_id}_{symbol}_{key_level}"

    def _breakout_retest_key(
        self, symbol: str, key_level: float, strategy_id: str,
        signal_source_id: str, period: str,
    ) -> str:
        return "|".join(str(value or "") for value in (
            strategy_id, signal_source_id, symbol, key_level, period,
        ))

    def _evaluate_breakout_retest(
        self, signal: TradingSignal, current_price: float,
        previous_price: Optional[float], strategy_id: str,
        source_id: str, period: str, params: Dict,
    ) -> TradingSignal:
        """Evaluate level-19 breakout -> confirmation -> retest -> entry."""
        level = float(signal.key_level or 0)
        if level <= 0:
            return signal
        is_level_19 = str(params.get("setup_mode") or "") == "level_19"
        offset = max(0.0, float(params.get(
            "level_19_confirmation_offset", 1.0
        ) if is_level_19 else params.get(
            "breakout_retest_confirmation_offset", 3.0
        )))
        fallback_tolerance = max(0.0, float(params.get(
            "breakout_retest_tolerance", 1.0
        )))
        try:
            atr = float(params.get("atr") or 0.0)
        except (TypeError, ValueError):
            atr = 0.0
        try:
            tolerance_atr = max(0.0, float(params.get(
                "breakout_retest_tolerance_atr", 0.7
            )))
        except (TypeError, ValueError):
            tolerance_atr = 0.7
        tolerance = atr * tolerance_atr if atr > 0 else fallback_tolerance
        key = self._breakout_retest_key(
            signal.symbol, level, strategy_id, source_id, period
        )
        phase = self._breakout_retest_states.get(key, "idle")
        previous = float(previous_price) if previous_price is not None else None
        confirmation = level + offset

        # The GOLD 19-level pattern also treats the first approach from below
        # as a resistance rejection. It is deliberately emitted only for the
        # explicit level_19 mode; ordinary breakout_retest remains buy-only.
        if (
            str(params.get("setup_mode") or "") == "level_19"
            and phase == "idle"
            and previous is not None
            and previous < current_price < level
            and level - current_price <= tolerance
        ):
            signal.action = "sell"
            signal.market_direction = "down"
            signal.is_entry_trigger = True
            signal.setup_family = "reversal"
            signal.setup_type = "key_level_19_resistance_reversal"
            signal.entry_mode = "touch_or_near"
            signal.suggested_entry = current_price
            signal.suggested_sl = level + 1.0
            signal.suggested_tp = round(
                current_price * (1.0 - float(
                    params.get("take_profit_percent", 0.0032)
                )), 8
            )
            signal.trigger_reason = (
                f"首次接近关键阻力 {level} 未突破，生成阻力反转卖出"
            )
            self._breakout_retest_states[key] = "rejected"
            return signal

        # The 19-level breakout follows the integer-level rule: once price
        # crosses level + 1, buy immediately.  There is no retest entry.
        if (
            is_level_19
            and phase in {"idle", "rejected"}
            and previous is not None
            and previous < confirmation <= current_price
        ):
            signal.action = "buy"
            signal.market_direction = "up"
            signal.is_entry_trigger = True
            signal.setup_family = "breakout"
            signal.setup_type = "key_level_19_breakout"
            signal.entry_mode = "breakout"
            signal.suggested_entry = current_price
            signal.suggested_sl = level - 1.0
            signal.suggested_tp = round(
                current_price * (1.0 + float(
                    params.get("take_profit_percent", 0.0032)
                )), 8
            )
            signal.trigger_reason = (
                f"突破关键位 {level} 上方确认点 {confirmation}，生成买入"
            )
            phase = "triggered"
        elif current_price < level:
            phase = "idle"
        elif phase in {"idle", "rejected"} and previous is not None and previous < level <= current_price:
            phase = "broken"
        elif phase in {"broken", "confirmed"} and current_price >= confirmation:
            phase = "confirmed"
        elif phase == "confirmed":
            # Require a later pullback from above the confirmation boundary;
            # the confirmation tick itself never opens a position.
            if (
                previous is not None
                and previous > confirmation + tolerance
                and level <= current_price <= confirmation + tolerance
            ):
                signal.action = "buy"
                signal.market_direction = "up"
                signal.is_entry_trigger = True
                signal.setup_family = "breakout"
                signal.setup_type = "key_level_19_breakout"
                signal.entry_mode = "breakout"
                signal.suggested_entry = current_price
                signal.suggested_sl = level - 1.0
                signal.suggested_tp = round(
                    current_price * (1.0 + float(
                        params.get("take_profit_percent", 0.0032)
                    )), 8
                )
                signal.trigger_reason = (
                    f"突破关键位 {level} 上方确认点 {confirmation}，生成买入"
                )
                phase = "triggered"
        self._breakout_retest_states[key] = phase
        if not signal.is_entry_trigger:
            signal.action = "none"
            signal.market_direction = "up" if phase in {"broken", "confirmed"} else "sideways"
            signal.setup_family = "breakout"
            signal.setup_type = (
                "key_level_19_resistance_reversal"
                if phase == "rejected" else "key_level_19_breakout"
            )
            signal.entry_mode = "breakout"
            signal.trigger_reason = {
                "idle": f"等待突破关键位 {level}",
                "broken": f"已突破关键位 {level}，等待确认到 {confirmation}",
                "confirmed": f"已确认突破 {confirmation}，等待回踩确认",
                "triggered": "突破回踩已触发",
            }.get(phase, "等待突破回踩确认")
        return signal

    @staticmethod
    def _level_19_candidates(
        levels: List[float], params: Dict, symbol: str = "",
        current_price: Optional[float] = None,
    ) -> List[float]:
        """Return explicitly configured 19-levels, or levels ending in 19.

        We never infer a special level from the current quote alone: this
        prevents enabling the GOLD 4419 rule on unrelated symbols.
        """
        explicit = params.get("level_19_levels") or []
        if explicit:
            return sorted({float(value) for value in explicit if float(value) > 0})
        candidates = sorted({
            float(level) for level in levels
            if int(round(float(level))) % 100 == 19
        })
        if candidates or "gold" not in str(symbol).lower():
            return candidates
        # GOLD integer-level configurations commonly store 4400/4500 while
        # the actionable resistance is 4419/4519. Derive the nearest such
        # level automatically so existing KEY LEVEL sources get the setup
        # without a database rewrite.
        derived = sorted({
            float(int(float(level) // 100) * 100 + 19)
            for level in levels if float(level) > 0
        })
        if current_price and derived:
            nearest = min(derived, key=lambda level: abs(level - current_price))
            return [nearest]
        return derived

    def generate_signal(
        self, symbol: str, current_price: float, strategy_id: str = "",
    ) -> Optional[TradingSignal]:
        """
        生成关键点位信号

        策略逻辑：
        - 价格在关键点位上方，向下接近 → 买入（支撑位）
        - 价格在关键点位下方，向上接近 → 卖出（压力位）
        """
        trigger_config = {
            "use_atr_proximity": True,
            "reversal_entry_tolerance_atr": 0.7,
            "take_profit_percent": 0.0032,
        }
        atr = self._atr_for(symbol)
        if atr > 0:
            trigger_config["atr"] = atr
        signal = build_key_level_signal(
            symbol, current_price, self.get_key_levels(symbol, current_price),
            threshold=self.threshold,
            trigger_config=trigger_config,
        )
        if signal is None:
            return None
        setup_type = str(signal.setup_type or "")
        cooldown = self.cooldown if setup_type == "key_level_reversal" else 0
        if self._check_cooldown(
            symbol, signal.key_level, strategy_id,
            setup_type=setup_type, direction=signal.action,
            cooldown=cooldown,
        ):
            return None
        if cooldown > 0:
            self._set_cooldown(
                symbol, signal.key_level, strategy_id,
                setup_type=setup_type, direction=signal.action,
            )
        print(
            f"[KeyLevelSignalGenerator] 生成信号: {signal.signal_id} "
            f"{signal.action} @ {current_price}, 关键位={signal.key_level}"
        )
        return signal

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        """按每条关键点位实例独立计算和冷却。"""
        signals = []
        for config in strategy.get_signal_sources("key_level", enabled_only=True):
            params = config.get("params") or {}
            mode = params.get("level_mode", "automatic")
            if mode == "levels":
                levels = [
                    float(level) for level in (params.get("levels") or [])
                    if float(level) > 0
                ]
            elif mode == "expression":
                levels = evaluate_key_level_expression(
                    params.get("expression", ""), current_price
                )
            else:
                levels = self.get_key_levels(symbol, current_price)
            trigger_config = dict(params)
            trigger_config.setdefault("use_atr_proximity", True)
            trigger_config.setdefault("reversal_entry_tolerance_atr", 0.7)
            trigger_config.setdefault("take_profit_percent", 0.0032)
            atr = self._atr_for(symbol, config.get("period", "M1"))
            if atr > 0:
                trigger_config["atr"] = atr
            setup_params = {**params, "atr": trigger_config.get("atr", 0.0)}
            signal = build_key_level_state_signal(
                symbol,
                current_price,
                levels,
                threshold=float(params.get(
                    "order_distance",
                    params.get("proximity_threshold", self.threshold),
                )),
                previous_price=None,
                trigger_config=trigger_config,
            )
            source_id = config["signal_source_id"]
            state_key = self._price_state_key(
                symbol, signal.key_level, strategy.strategy_id, source_id
            )
            previous_price = self._last_prices.get(state_key)
            signal = build_key_level_state_signal(
                symbol,
                current_price,
                levels,
                threshold=float(params.get(
                    "order_distance",
                    params.get("proximity_threshold", self.threshold),
                )),
                previous_price=previous_price,
                trigger_config=trigger_config,
            )
            if str(params.get("setup_mode") or "both").lower() in {"breakout_retest", "level_19"}:
                signal = self._evaluate_breakout_retest(
                    signal, current_price, previous_price, strategy.strategy_id,
                    source_id, config.get("period", ""), setup_params,
                )
            self._last_prices[state_key] = current_price
            extra_level_19 = []
            if params.get("level_19_enabled", True):
                for level_19 in self._level_19_candidates(
                    levels, params, symbol, current_price
                ):
                    special_state_key = self._price_state_key(
                        symbol, level_19, strategy.strategy_id,
                        f"{source_id}:19",
                    )
                    special_previous_price = self._last_prices.get(special_state_key)
                    special = build_key_level_state_signal(
                        symbol, current_price, [level_19],
                        threshold=float(params.get(
                            "order_distance",
                            params.get("proximity_threshold", self.threshold),
                        )),
                        previous_price=special_previous_price,
                        trigger_config={**trigger_config, "setup_mode": "level_19"},
                    )
                    special = self._evaluate_breakout_retest(
                        special, current_price, special_previous_price, strategy.strategy_id,
                        source_id, config.get("period", ""), setup_params,
                    )
                    self._last_prices[special_state_key] = current_price
                    if special.is_entry_trigger or special.market_direction == "up":
                        extra_level_19.append(special)
            setup_type = str(signal.setup_type or "")
            if setup_type == "key_level_reversal":
                # Migrate old signal-source configs (often 180 seconds) to the
                # new two-hour reversal protection.  A longer explicit value
                # remains possible; a shorter one cannot weaken the guard.
                configured_cooldown = max(
                    self.cooldown,
                    int(params.get(
                        "reversal_cooldown_seconds",
                        params.get("cooldown_seconds", self.cooldown),
                    ) or 0),
                )
            else:
                # Breakout attempts have their own optional throttle and are
                # never blocked by the reversal cooldown.
                configured_cooldown = params.get("breakout_cooldown_seconds", 0)
            cooldown = max(0, int(configured_cooldown))
            if signal.is_entry_trigger and self._check_cooldown(
                symbol, signal.key_level, strategy.strategy_id, source_id,
                cooldown, setup_type, signal.action, config.get("period", ""),
            ):
                signal.is_entry_trigger = False
            elif signal.is_entry_trigger:
                self._set_cooldown(
                    symbol, signal.key_level, strategy.strategy_id, source_id,
                    setup_type, signal.action, config.get("period", ""),
                )
                if setup_type == "key_level_breakout":
                    self._clear_setup_cooldown(
                        symbol, signal.key_level, strategy.strategy_id, source_id,
                        "key_level_reversal", config.get("period", ""),
                    )
            signal.source_period = config["period"]
            signal.signal_source_id = source_id
            signals.append(signal)
            for special in extra_level_19:
                special_setup = str(special.setup_type or "")
                special_cooldown = max(
                    self.cooldown if "reversal" in special_setup else 0,
                    int(params.get("breakout_cooldown_seconds", 0) or 0),
                )
                if special.is_entry_trigger and self._check_cooldown(
                    symbol, special.key_level, strategy.strategy_id, source_id,
                    special_cooldown, special_setup, special.action,
                    config.get("period", ""),
                ):
                    continue
                if special.is_entry_trigger and special_cooldown > 0:
                    self._set_cooldown(
                        symbol, special.key_level, strategy.strategy_id, source_id,
                        special_setup, special.action, config.get("period", ""),
                    )
                special.source_period = config["period"]
                special.signal_source_id = source_id
                signals.append(special)
        return signals

    def __call__(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """使对象可调用"""
        signal = self.generate_signal(symbol, current_price)
        return signal if signal else None
