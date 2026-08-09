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

    def __init__(self):
        # 关键点位配置
        self._key_levels: Dict[str, List[float]] = {}

        # 阈值（价格距离关键点位的百分比）
        self.threshold = 0.0008  # 万分之八

        # 信号冷却时间（秒）
        self.cooldown = 180

        # 冷却记录
        self._signal_cooldowns: Dict[str, datetime] = {}

        print("[KeyLevelSignalGenerator] 关键点位信号生成器已初始化")

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
    ) -> bool:
        """检查是否在冷却期"""
        key = f"{strategy_id}_{signal_source_id}_{symbol}_{key_level}"
        if key in self._signal_cooldowns:
            last_time = self._signal_cooldowns[key]
            elapsed = (datetime.now() - last_time).total_seconds()
            return elapsed < (self.cooldown if cooldown is None else cooldown)
        return False

    def _set_cooldown(
        self, symbol: str, key_level: float, strategy_id: str = "",
        signal_source_id: str = "",
    ) -> None:
        """设置冷却"""
        key = f"{strategy_id}_{signal_source_id}_{symbol}_{key_level}"
        self._signal_cooldowns[key] = datetime.now()

    def generate_signal(
        self, symbol: str, current_price: float, strategy_id: str = "",
    ) -> Optional[TradingSignal]:
        """
        生成关键点位信号

        策略逻辑：
        - 价格在关键点位上方，向下接近 → 买入（支撑位）
        - 价格在关键点位下方，向上接近 → 卖出（压力位）
        """
        signal = build_key_level_signal(
            symbol, current_price, self.get_key_levels(symbol, current_price),
            threshold=self.threshold,
        )
        if signal is None or self._check_cooldown(
            symbol, signal.key_level, strategy_id
        ):
            return None
        self._set_cooldown(symbol, signal.key_level, strategy_id)
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
            signal = build_key_level_state_signal(
                symbol,
                current_price,
                levels,
                threshold=float(params.get("proximity_threshold", self.threshold)),
            )
            source_id = config["signal_source_id"]
            cooldown = max(0, int(params.get("cooldown_seconds", self.cooldown)))
            if signal.is_entry_trigger and self._check_cooldown(
                symbol, signal.key_level, strategy.strategy_id, source_id, cooldown
            ):
                signal.is_entry_trigger = False
            elif signal.is_entry_trigger:
                self._set_cooldown(
                    symbol, signal.key_level, strategy.strategy_id, source_id
                )
            signal.source_period = config["period"]
            signal.signal_source_id = source_id
            signals.append(signal)
        return signals

    def __call__(self, symbol: str, current_price: float) -> Optional[TradingSignal]:
        """使对象可调用"""
        signal = self.generate_signal(symbol, current_price)
        return signal if signal else None
