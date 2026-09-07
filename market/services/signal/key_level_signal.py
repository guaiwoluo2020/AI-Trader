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
            self._last_prices[state_key] = current_price
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
        return signals

    def __call__(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """使对象可调用"""
        signal = self.generate_signal(symbol, current_price)
        return signal if signal else None
