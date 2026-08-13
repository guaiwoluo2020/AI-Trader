#!/usr/bin/env python3
"""Shared position plan and runtime management engine."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, Optional, Tuple

from market.models.position_management import (
    PositionAction, PositionManagementPolicy, PositionPlan,
)


def _buffer_value(buffer: Dict, entry_price: float, atr: float) -> float:
    buffer = buffer or {}
    value = max(0.0, float(buffer.get("value", 0)))
    kind = buffer.get("type", "fixed_points")
    if kind == "fixed_percent":
        return entry_price * value
    if kind == "atr":
        return atr * value
    return value


class PositionManager:
    """Stateless calculations; callers persist the returned plan and state."""

    PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}

    @staticmethod
    def _timestamp(value) -> int:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, datetime):
            return int(value.timestamp())
        if isinstance(value, str) and value:
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except ValueError:
                for fmt in ("%Y.%m.%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        return int(datetime.strptime(value, fmt).timestamp())
                    except ValueError:
                        continue
        return 0

    @staticmethod
    def _pivot_candidate(
        direction: str, entry_price: float, rule: Dict,
        pivots: Iterable[Dict], atr: float, for_stop: bool,
        current_time: int = 0,
    ) -> Optional[float]:
        period = rule.get("period", "M5")
        pivot_direction = (
            "low" if (direction == "buy") == for_stop else "high"
        )
        candidates = []
        for pivot in pivots or []:
            if pivot.get("period") != period or pivot.get("direction") != pivot_direction:
                continue
            price = float(pivot.get("price", 0))
            if price <= 0:
                continue
            confirmed_at = PositionManager._timestamp(pivot.get("confirmed_at"))
            if current_time and confirmed_at and confirmed_at > current_time:
                continue
            pivot_time = PositionManager._timestamp(pivot.get("timestamp"))
            max_age = int(rule.get("max_age_bars", 0) or 0)
            if current_time and pivot_time and max_age:
                if current_time - pivot_time > (
                    max_age * PositionManager.PERIOD_SECONDS.get(period, 60)
                ):
                    continue
            valid_side = (
                price < entry_price if pivot_direction == "low" else price > entry_price
            )
            if valid_side:
                candidates.append(pivot)
        if not candidates:
            return None
        selected = min(candidates, key=lambda item: abs(float(item["price"]) - entry_price))
        price = float(selected["price"])
        buffer = _buffer_value(rule.get("buffer") or {}, entry_price, atr)
        return price - buffer if pivot_direction == "low" else price + buffer

    def _resolve_rule(
        self, rules, direction: str, entry_price: float, signal_price: float,
        initial_risk: float, pivots, atr: float, for_stop: bool,
        current_time: int = 0,
    ) -> Tuple[Optional[float], Optional[Dict]]:
        sign = 1 if direction == "buy" else -1
        for rule in rules:
            kind = rule.get("type")
            candidate = None
            if kind == "signal" and signal_price > 0:
                candidate = signal_price
            elif kind == "pivot":
                candidate = self._pivot_candidate(
                    direction, entry_price, rule, pivots, atr, for_stop
                    , current_time
                )
            elif kind == "fixed_points":
                candidate = entry_price + (-sign if for_stop else sign) * float(rule["value"])
            elif kind == "fixed_percent":
                distance = entry_price * float(rule["value"])
                candidate = entry_price + (-sign if for_stop else sign) * distance
            elif kind == "atr" and atr > 0:
                candidate = entry_price + (-sign if for_stop else sign) * atr * float(rule["value"])
            elif kind == "risk_reward" and not for_stop and initial_risk > 0:
                candidate = entry_price + sign * initial_risk * float(rule["value"])
            elif kind == "none" and not for_stop:
                candidate = 0.0
            if candidate is None:
                continue
            valid = (
                candidate < entry_price if direction == "buy" and for_stop
                else candidate > entry_price if direction == "sell" and for_stop
                else candidate > entry_price if direction == "buy"
                else candidate < entry_price
            )
            if candidate == 0 and not for_stop:
                valid = True
            if valid:
                return float(candidate), rule
        return None, None

    def create_plan(
        self, policy: PositionManagementPolicy, direction: str,
        entry_price: float, signal_stop_loss: float = 0,
        signal_take_profit: float = 0, pivots=None, atr: float = 0,
        current_time: int = 0,
    ) -> PositionPlan:
        direction = str(direction).lower()
        if direction not in {"buy", "sell"} or entry_price <= 0:
            raise ValueError("开仓方向或价格无效")
        config = policy.config
        stop, stop_rule = self._resolve_rule(
            config["initial_stop_rules"], direction, entry_price,
            float(signal_stop_loss or 0), 0, pivots, atr, True, current_time,
        )
        if stop is None:
            raise ValueError("没有止损规则能够生成有效价格")
        risk = abs(entry_price - stop)
        minimum = float(config.get("min_stop_distance", 0))
        maximum = float(config.get("max_stop_distance", 0))
        if minimum and risk < minimum:
            raise ValueError("止损距离小于持仓管理方案限制")
        if maximum and risk > maximum:
            raise ValueError("止损距离超过持仓管理方案限制")
        take_profit, take_rule = self._resolve_rule(
            config["initial_take_profit_rules"], direction, entry_price,
            float(signal_take_profit or 0), risk, pivots, atr, False, current_time,
        )
        if take_profit is None:
            raise ValueError("没有止盈规则能够生成有效价格")
        reward = abs(take_profit - entry_price) if take_profit else 0
        rr = reward / risk if risk and take_profit else 0
        if take_profit and rr < float(config.get("min_risk_reward", 0)):
            raise ValueError("生成的盈亏比低于持仓管理方案要求")
        return PositionPlan(
            stop_loss=stop, take_profit=take_profit, initial_risk=risk,
            risk_reward=rr, policy_id=policy.policy_id,
            policy_snapshot=policy.to_dict(), stop_rule=dict(stop_rule),
            take_profit_rule=dict(take_rule),
            explanation=[
                f"止损使用 {stop_rule['type']} 规则",
                f"止盈使用 {take_rule['type']} 规则",
            ],
        )

    def evaluate(
        self, policy_config: Dict, position: Dict, market: Dict,
        pivots=None, reverse_signal: bool = False,
    ) -> PositionAction:
        direction = position["direction"]
        entry = float(position["entry_price"])
        current_sl = float(position["stop_loss"])
        risk = float(position.get("initial_risk") or abs(entry - current_sl))
        price = float(market.get("price", market.get("close", 0)))
        volume = float(position.get("remaining_volume") or position.get("volume") or 0)
        favorable = float(position.get("favorable_price") or entry)
        favorable = max(favorable, price) if direction == "buy" else min(favorable, price)
        candidates = []
        events = []
        profit = favorable - entry if direction == "buy" else entry - favorable
        profit_r = profit / risk if risk > 0 else 0.0
        triggered_partials = set(position.get("partial_levels_done") or [])
        if isinstance(position.get("partial_levels_done"), str):
            triggered_partials = {
                item for item in position["partial_levels_done"].split(",") if item
            }

        def add_event(rule_type: str, status: str, message: str, **payload):
            events.append({
                "rule_type": rule_type,
                "status": status,
                "message": message,
                "price": price,
                "entry_price": entry,
                "stop_loss": current_sl,
                "favorable_price": favorable,
                "profit_r": round(profit_r, 4),
                **payload,
            })

        for rule in policy_config.get("management_rules", []):
            kind = rule.get("type")
            if kind == "reverse_signal" and reverse_signal:
                add_event(kind, "triggered", "出现反向信号，触发退出")
                return PositionAction(
                    "close", reason="reverse_signal", events=events
                )
            if kind == "max_holding_bars":
                holding_bars = int(position.get("holding_bars", 0))
                opened_at = position.get("opened_at")
                current_time = market.get("time")
                if opened_at is not None and current_time is not None:
                    if hasattr(opened_at, "timestamp"):
                        opened_at = opened_at.timestamp()
                    period_seconds = self.PERIOD_SECONDS.get(rule.get("period", "M1"), 60)
                    holding_bars = max(
                        holding_bars,
                        int(max(0, float(current_time) - float(opened_at)) // period_seconds),
                    )
                if holding_bars >= int(rule["bars"]):
                    add_event(
                        kind, "triggered",
                        f"持仓 {holding_bars} 根K线，达到时间退出条件",
                        holding_bars=holding_bars,
                    )
                    return PositionAction(
                        "close", reason="max_holding_bars", events=events
                    )
                add_event(
                    kind, "checked",
                    f"持仓 {holding_bars} 根K线，未达到 {rule['bars']} 根",
                    holding_bars=holding_bars,
                )
            if kind == "break_even" and risk > 0:
                if profit >= risk * float(rule["activation_r"]):
                    offset = risk * float(rule.get("offset_r", 0))
                    candidate = entry + offset if direction == "buy" else entry - offset
                    candidates.append(candidate)
                    add_event(
                        kind, "triggered",
                        f"浮盈 {profit_r:.2f}R 达到保本 {rule['activation_r']}R",
                        candidate_stop_loss=candidate,
                    )
                else:
                    add_event(
                        kind, "checked",
                        f"浮盈 {profit_r:.2f}R，未达到保本 {rule['activation_r']}R",
                    )
            if kind == "trailing_stop" and risk > 0:
                if profit >= risk * float(rule["activation_r"]):
                    distance = risk * float(rule["distance_r"])
                    candidate = (
                        favorable - distance
                        if direction == "buy" else favorable + distance
                    )
                    candidates.append(candidate)
                    add_event(
                        kind, "triggered",
                        f"浮盈 {profit_r:.2f}R 达到移动止损 {rule['activation_r']}R",
                        candidate_stop_loss=candidate,
                        distance_r=rule["distance_r"],
                    )
                else:
                    add_event(
                        kind, "checked",
                        f"浮盈 {profit_r:.2f}R，未达到移动止损 {rule['activation_r']}R",
                    )
            if kind == "partial_take_profit" and risk > 0 and volume > 0:
                for level in rule.get("levels", []):
                    level_id = str(level.get("level_id") or "")
                    if not level_id or level_id in triggered_partials:
                        continue
                    trigger_r = float(level.get("trigger_r", 0))
                    if profit_r >= trigger_r:
                        close_percent = min(
                            100.0, max(0.0, float(level.get("close_percent", 0)))
                        )
                        close_volume = volume * close_percent / 100.0
                        move_sl = level.get("move_sl", "none")
                        partial_stop = None
                        if move_sl == "break_even":
                            partial_stop = entry
                        elif move_sl == "trail":
                            trail_rule = next(
                                (
                                    item for item in policy_config.get("management_rules", [])
                                    if item.get("type") == "trailing_stop"
                                ),
                                {},
                            )
                            distance_r = float(trail_rule.get("distance_r", 0.8) or 0.8)
                            distance = risk * distance_r
                            partial_stop = (
                                favorable - distance
                                if direction == "buy" else favorable + distance
                            )
                        if partial_stop is not None:
                            can_tighten = (
                                current_sl < partial_stop < price if direction == "buy"
                                else price < partial_stop < current_sl
                            )
                            if not can_tighten:
                                partial_stop = None
                        add_event(
                            kind, "triggered",
                            f"浮盈 {profit_r:.2f}R 达到分批止盈 {trigger_r}R，平 {close_percent:.0f}%",
                            level_id=level_id,
                            close_percent=close_percent,
                            close_volume=close_volume,
                            move_sl=move_sl,
                            candidate_stop_loss=partial_stop,
                        )
                        return PositionAction(
                            "partial_close",
                            stop_loss=partial_stop,
                            close_percent=close_percent,
                            close_volume=close_volume,
                            level_id=level_id,
                            reason="partial_take_profit",
                            events=events,
                        )
                    add_event(
                        kind, "checked",
                        f"浮盈 {profit_r:.2f}R，未达到分批止盈 {trigger_r}R",
                        level_id=level_id,
                    )
            if kind == "pivot_trailing":
                candidate = self._pivot_candidate(
                    direction, price, rule, pivots or [],
                    float(market.get("atr", 0)), True,
                    int(market.get("time", 0) or 0),
                )
                if candidate is not None:
                    candidates.append(candidate)
                    add_event(
                        kind, "triggered",
                        "转折点跟进给出新的止损候选",
                        candidate_stop_loss=candidate,
                    )
                else:
                    add_event(kind, "checked", "暂无可用转折点跟进止损")
        valid = [candidate for candidate in candidates if (
            current_sl < candidate < price if direction == "buy"
            else price < candidate < current_sl
        )]
        if not valid:
            return PositionAction(events=events)
        new_stop = max(valid) if direction == "buy" else min(valid)
        add_event(
            "stop_loss_update", "triggered",
            f"止损从 {current_sl:.5f} 调整为 {new_stop:.5f}",
            new_stop_loss=new_stop,
        )
        return PositionAction(
            "modify_sl", stop_loss=new_stop,
            reason="position_management", events=events,
        )
