#!/usr/bin/env python3
"""Shared position plan and runtime management engine."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from market.models.position_management import (
    PositionAction, PositionManagementPolicy, PositionPlan,
    resolve_position_management_config,
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
    def _multi_level_exit_plan(
        direction: str, entry_price: float, config: Dict,
        stop_candidates: Iterable[Dict], target_candidates: Iterable[Dict],
        atr: float,
    ) -> Tuple[float, float, List[Dict]]:
        settings = config.get("multi_level_exit") or {}
        stops, targets = [], []
        supplied_targets = list(target_candidates or [])
        price_discovery = bool(supplied_targets) and all(
            str(item.get("source_type") or "") == "risk_reward_projection"
            for item in supplied_targets
        )
        for kind, candidates, percentages in (
            ("stop_loss", stop_candidates, settings.get("stop_close_percent") or {}),
            ("take_profit", target_candidates, settings.get("take_profit_close_percent") or {}),
        ):
            seen_layers = set()
            for candidate in candidates or []:
                layer = str(candidate.get("structure_layer") or "").lower()
                price = float(candidate.get("price") or 0)
                if layer not in {"internal", "swing", "external"} or layer in seen_layers:
                    continue
                valid = (
                    direction == "buy" and (
                        price < entry_price if kind == "stop_loss" else price > entry_price
                    )
                ) or (
                    direction == "sell" and (
                        price > entry_price if kind == "stop_loss" else price < entry_price
                    )
                )
                if not valid:
                    continue
                seen_layers.add(layer)
                item = copy.deepcopy(candidate)
                item.update({
                    "type": kind,
                    "level_id": str(candidate.get("level_id") or f"structure_{kind}_{layer}"),
                    "structure_layer": layer,
                    "price": price,
                    "close_percent": min(100.0, max(
                        0.0, float(percentages.get(layer, 0) or 0)
                    )),
                    "status": "waiting",
                })
                (stops if kind == "stop_loss" else targets).append(item)
        if not stops:
            raise ValueError("多层结构持仓管理需要至少一个有效的结构止损候选点")

        stops.sort(
            key=lambda item: abs(float(item["price"]) - entry_price)
        )
        targets.sort(key=lambda item: abs(float(item["price"]) - entry_price))
        # The furthest available structure level always closes the remainder.
        stops[-1]["close_remaining"] = True
        if price_discovery or not targets:
            targets = []
            reference_risk = abs(entry_price - float(stops[0]["price"]))
            sign = 1 if direction == "buy" else -1
            for index, level in enumerate(
                settings.get("price_discovery_take_profit_levels") or [], start=1
            ):
                risk_reward = max(0.0, float(level.get("risk_reward") or 0))
                close_percent = min(100.0, max(
                    0.0, float(level.get("close_percent") or 0)
                ))
                if not risk_reward or not close_percent:
                    continue
                targets.append({
                    "type": "take_profit",
                    "level_id": str(
                        level.get("level_id") or f"price_discovery_tp{index}"
                    ),
                    "structure_layer": "projection",
                    "source_type": "risk_reward_projection",
                    "risk_reward": risk_reward,
                    "price": entry_price + sign * reference_risk * risk_reward,
                    "close_percent": close_percent,
                    "status": "waiting",
                    "reason": f"价格发现阶段 {risk_reward:g}R 分批止盈",
                })
            if not targets:
                raise ValueError("价格发现模式至少需要一个有效的R倍数止盈层级")
            targets.append({
                "type": "runner",
                "level_id": "price_discovery_runner",
                "structure_layer": "dynamic",
                "source_type": "trailing_stop",
                "price": 0.0,
                "close_percent": 0.0,
                "close_remaining": True,
                "reference_risk": reference_risk,
                "status": "waiting",
                "reason": "剩余仓位由最有利价格的移动止损管理",
            })
        else:
            targets[-1]["close_remaining"] = True
        furthest_stop = float(stops[-1]["price"])
        disaster_buffer = max(0.0, float(atr or 0)) * max(
            0.0, float(settings.get("disaster_stop_buffer_atr", 0.50) or 0)
        )
        disaster_stop = (
            furthest_stop - disaster_buffer
            if direction == "buy" else furthest_stop + disaster_buffer
        )
        reference_target = float(next(
            item["price"] for item in targets if item.get("type") == "take_profit"
        ))
        return disaster_stop, reference_target, stops + targets

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
        setup_context: Optional[Dict] = None,
        signal_stop_candidates: Optional[Iterable[Dict]] = None,
        signal_target_candidates: Optional[Iterable[Dict]] = None,
    ) -> PositionPlan:
        direction = str(direction).lower()
        if direction not in {"buy", "sell"} or entry_price <= 0:
            raise ValueError("开仓方向或价格无效")
        config, applied_profile = resolve_position_management_config(
            policy.config, setup_context
        )
        stop, stop_rule = self._resolve_rule(
            config["initial_stop_rules"], direction, entry_price,
            float(signal_stop_loss or 0), 0, pivots, atr, True, current_time,
        )
        if stop is None:
            raise ValueError("没有止损规则能够生成有效价格")
        multi_level = (
            config.get("management_mode") == "multi_level_exit"
            and str((setup_context or {}).get("signal_source") or "")
            == "structure_plan"
        )
        exit_levels: List[Dict] = []
        reference_take_profit = 0.0
        reference_stop = stop
        if multi_level:
            stop, reference_take_profit, exit_levels = self._multi_level_exit_plan(
                direction, entry_price, config,
                signal_stop_candidates or [], signal_target_candidates or [], atr,
            )
            reference_stop = float(next(
                item["price"] for item in exit_levels
                if item.get("type") == "stop_loss"
            ))
            stop_rule = {
                "type": "multi_level_disaster_stop",
                "reference_price": float(exit_levels[
                    len([item for item in exit_levels if item.get("type") == "stop_loss"]) - 1
                ]["price"]),
                "buffer_atr": float(
                    (config.get("multi_level_exit") or {}).get(
                        "disaster_stop_buffer_atr", 0.50
                    ) or 0
                ),
            }
        risk = abs(entry_price - stop)
        minimum = entry_price * float(config.get("min_stop_percent", 0.1) or 0) / 100.0
        maximum = entry_price * float(config.get("max_stop_percent", 0.7) or 0) / 100.0
        if not minimum:
            minimum = float(config.get("min_stop_distance", 0) or 0)
        if not maximum:
            maximum = float(config.get("max_stop_distance", 0) or 0)
        stop_adjustment = None
        if minimum and risk < minimum:
            original_stop = stop
            stop = (
                entry_price - minimum
                if direction == "buy" else entry_price + minimum
            )
            risk = abs(entry_price - stop)
            stop_adjustment = {
                "reason": "signal_stop_below_policy_minimum",
                "original_stop_loss": float(original_stop),
                "adjusted_stop_loss": float(stop),
                "original_distance": float(abs(entry_price - original_stop)),
                "adjusted_distance": float(risk),
                "minimum_distance": float(minimum),
                "minimum_percent": float(config.get("min_stop_percent", 0) or 0),
                "message": (
                    f"AI止损距离 {abs(entry_price - original_stop):.2f} 小于最小止损比例 "
                    f"{float(config.get('min_stop_percent', 0) or 0):.2f}%，"
                    f"已自动调整为 {risk:.2f}"
                ),
            }
        if maximum and risk > maximum:
            raise ValueError(
                f"止损距离 {risk:.2f} 超过持仓管理方案最大比例 "
                f"{float(config.get('max_stop_percent', 0) or 0):.2f}%"
            )
        take_profit, take_rule = self._resolve_rule(
            config["initial_take_profit_rules"], direction, entry_price,
            float(signal_take_profit or 0), risk, pivots, atr, False, current_time,
        )
        if take_profit is None:
            raise ValueError("没有止盈规则能够生成有效价格")
        if multi_level:
            take_profit = reference_take_profit
            take_rule = {"type": "multi_level_structure_targets"}
            reference_risk = abs(entry_price - reference_stop)
            reward = abs(take_profit - entry_price)
            rr = reward / reference_risk if reference_risk else 0
        else:
            reward = abs(take_profit - entry_price) if take_profit else 0
            rr = reward / risk if risk and take_profit else 0
        minimum_rr = float(config.get("min_risk_reward", 0) or 0)
        signal_minimum = float(
            (setup_context or {}).get("signal_min_risk_reward", 0) or 0
        )
        if (
            signal_minimum > 0
            and str((setup_context or {}).get("signal_source") or "")
            == "structure_plan"
            and str((setup_context or {}).get("setup_type") or "")
            == "structure_location_pullback"
        ):
            minimum_rr = signal_minimum
        if take_profit and rr < minimum_rr:
            raise ValueError("生成的盈亏比低于持仓管理方案要求")
        policy_snapshot = policy.to_dict()
        policy_snapshot["config"] = copy.deepcopy(config)
        policy_snapshot["setup_context"] = copy.deepcopy(setup_context or {})
        policy_snapshot["applied_setup_profile"] = copy.deepcopy(applied_profile)
        if multi_level:
            policy_snapshot["exit_levels"] = copy.deepcopy(exit_levels)
            policy_snapshot["disaster_stop_loss"] = float(stop)
            policy_snapshot["reference_take_profit"] = float(reference_take_profit)
        explanation = [
            f"止损使用 {stop_rule['type']} 规则",
            f"止盈使用 {take_rule['type']} 规则",
        ]
        if stop_adjustment:
            explanation.append(stop_adjustment["message"])
        if applied_profile:
            explanation.insert(0, f"匹配场景规则：{applied_profile.get('name')}")
        return PositionPlan(
            stop_loss=stop, take_profit=0.0 if multi_level else take_profit,
            initial_risk=risk,
            risk_reward=rr, policy_id=policy.policy_id,
            policy_snapshot=policy_snapshot, stop_rule=dict(stop_rule),
            take_profit_rule=dict(take_rule),
            explanation=explanation,
            stop_adjustment=stop_adjustment,
            exit_levels=copy.deepcopy(exit_levels),
            disaster_stop_loss=float(stop) if multi_level else 0.0,
            reference_take_profit=float(reference_take_profit) if multi_level else 0.0,
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

        # Structure multi-level exits are virtual: MT5 only carries the remote
        # disaster stop.  Tick crossings close configured slices here.
        exit_levels = list(position.get("exit_levels") or [])
        if exit_levels and volume > 0:
            stop_hits, target_hits = [], []
            for level in exit_levels:
                level_id = str(level.get("level_id") or "")
                if not level_id or level_id in triggered_partials:
                    continue
                trigger = float(level.get("price") or 0)
                if trigger <= 0:
                    continue
                hit = (
                    price <= trigger if direction == "buy" and level.get("type") == "stop_loss"
                    else price >= trigger if direction == "sell" and level.get("type") == "stop_loss"
                    else price >= trigger if direction == "buy" and level.get("type") == "take_profit"
                    else price <= trigger if direction == "sell" and level.get("type") == "take_profit"
                    else False
                )
                if hit:
                    (stop_hits if level.get("type") == "stop_loss" else target_hits).append(level)
            hits = stop_hits or target_hits
            if hits:
                hits.sort(key=lambda item: abs(float(item["price"]) - entry))
                initial_volume = float(
                    position.get("initial_volume") or position.get("volume") or volume
                )
                close_remaining = any(item.get("close_remaining") for item in hits)
                close_volume = volume if close_remaining else min(
                    volume,
                    sum(
                        initial_volume * float(item.get("close_percent") or 0) / 100.0
                        for item in hits
                    ),
                )
                level_ids = [str(item["level_id"]) for item in hits]
                kind = "multi_level_stop_loss" if stop_hits else "multi_level_take_profit"
                action_name = "close" if close_volume >= volume - 1e-9 else "partial_close"
                label = "分批止损" if stop_hits else "分批止盈"
                add_event(
                    kind, "triggered",
                    f"价格触发 {len(hits)} 个结构{label}层级，"
                    f"{'平掉剩余仓位' if action_name == 'close' else f'平 {close_volume:.2f} 手'}",
                    level_id=level_ids[-1], level_ids=level_ids,
                    close_volume=close_volume,
                    triggered_levels=copy.deepcopy(hits),
                    execution_key="|".join(level_ids),
                )
                return PositionAction(
                    action_name, close_volume=close_volume,
                    close_percent=(close_volume / volume * 100.0 if volume else 0),
                    level_id=level_ids[-1], level_ids=level_ids,
                    reason=kind, events=events,
                )

            runner = next((
                item for item in exit_levels
                if item.get("type") == "runner"
                and str(item.get("level_id") or "") not in triggered_partials
            ), None)
            multi_settings = policy_config.get("multi_level_exit") or {}
            if runner and multi_settings.get("runner_trailing_enabled", True):
                runner_risk = max(
                    0.0, float(runner.get("reference_risk") or risk)
                )
                activation_r = max(0.0, float(
                    multi_settings.get("runner_trailing_activation_r", 1.0) or 0
                ))
                distance_r = max(0.0, float(
                    multi_settings.get("runner_trailing_distance_r", 0.8) or 0
                ))
                runner_profit_r = profit / runner_risk if runner_risk > 0 else 0.0
                if runner_risk > 0 and runner_profit_r >= activation_r:
                    trailing_price = (
                        favorable - runner_risk * distance_r
                        if direction == "buy" else favorable + runner_risk * distance_r
                    )
                    hit = (
                        price <= trailing_price if direction == "buy"
                        else price >= trailing_price
                    )
                    if hit:
                        runner_id = str(
                            runner.get("level_id") or "price_discovery_runner"
                        )
                        add_event(
                            "multi_level_runner_trailing", "triggered",
                            f"价格从最有利位置回撤至移动止损 {trailing_price:.2f}，平掉剩余仓位",
                            level_id=runner_id, level_ids=[runner_id],
                            close_volume=volume, trailing_stop=trailing_price,
                            trailing_activation_r=activation_r,
                            trailing_distance_r=distance_r,
                            runner_profit_r=round(runner_profit_r, 4),
                            execution_key=runner_id,
                        )
                        return PositionAction(
                            "close", close_volume=volume, close_percent=100.0,
                            level_id=runner_id, level_ids=[runner_id],
                            reason="multi_level_runner_trailing", events=events,
                        )

        # An AI take-profit is a staged exit: close the configured portion at
        # the signal price, then let the remaining volume be managed by the
        # trailing rules.  The caller clears the fixed TP after this action.
        signal_tp_percent = float(
            policy_config.get("signal_take_profit_close_percent", 0) or 0
        )
        signal_tp = float(position.get("take_profit") or 0)
        signal_tp_hit = (
            signal_tp_percent > 0
            and signal_tp > 0
            and "signal_take_profit" not in triggered_partials
            and (
                price >= signal_tp if direction == "buy"
                else price <= signal_tp
            )
        )
        if signal_tp_hit:
            close_percent = min(100.0, max(0.0, signal_tp_percent))
            close_volume = volume * close_percent / 100.0
            add_event(
                "signal_take_profit", "triggered",
                f"到达 AI 止盈价，平 {close_percent:.0f}%；剩余仓位交给移动止损",
                level_id="signal_take_profit",
                close_percent=close_percent,
                close_volume=close_volume,
            )
            return PositionAction(
                "partial_close",
                close_percent=close_percent,
                close_volume=close_volume,
                level_id="signal_take_profit",
                reason="signal_take_profit_partial",
                events=events,
            )

        for rule in policy_config.get("management_rules", []):
            if not rule.get("enabled", True):
                continue
            kind = rule.get("type")
            if (
                policy_config.get("management_mode") == "multi_level_exit"
                and kind in {
                    "break_even", "pivot_trailing", "structure_trailing",
                    "trailing_stop", "partial_take_profit",
                }
            ):
                # Broker-side SL remains the fixed disaster guard. Structure
                # layers above already own all staged stop/target execution.
                continue
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
                if position.get("break_even_done"):
                    continue
                if profit >= risk * float(rule["activation_r"]):
                    offset = risk * float(rule.get("offset_r", 0))
                    candidate = entry + offset if direction == "buy" else entry - offset
                    can_tighten = (
                        current_sl < candidate < price if direction == "buy"
                        else price < candidate < current_sl
                    )
                    if can_tighten:
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
                        kind, "checked",
                        "转折点跟进评估了止损候选",
                        candidate_stop_loss=candidate,
                    )
                else:
                    add_event(kind, "checked", "暂无可用转折点跟进止损")
            if kind == "structure_trailing":
                hierarchy = market.get("structure_hierarchy") or (market.get("structure") or {}).get("structure_hierarchy") or {}
                layer = hierarchy.get(rule.get("structure_layer", "swing")) or {}
                level_key = "protected_low" if direction == "buy" else "protected_high"
                protected = layer.get(level_key) or {}
                level = float(protected.get("price") or 0)
                if level > 0:
                    buffer_type = rule.get("buffer_type", "atr")
                    value = float(rule.get("buffer_value", 0.15) or 0)
                    buffer = float(market.get("atr", 0) or 0) * value if buffer_type == "atr" else (price * value / 100 if buffer_type == "fixed_percent" else value)
                    candidate = level - buffer if direction == "buy" else level + buffer
                    valid_side = candidate < price if direction == "buy" else candidate > price
                    improves = candidate > current_sl if direction == "buy" else candidate < current_sl
                    improvement = candidate - current_sl if direction == "buy" else current_sl - candidate
                    minimum_improvement = float(market.get("atr", 0) or 0) * float(rule.get("min_improvement_atr", 0.10) or 0)
                    if valid_side and improves and improvement >= minimum_improvement:
                        candidates.append(candidate)
                        add_event(kind, "triggered", f"结构保护点更新，候选止损 {candidate:.5f}", candidate_stop_loss=candidate, protected_level=level, structure_layer=rule.get("structure_layer", "swing"))
                    else:
                        add_event(kind, "checked", "结构保护点未产生足够改善的止损", protected_level=level, candidate_stop_loss=candidate, minimum_improvement=minimum_improvement)
                else:
                    add_event(kind, "checked", "暂无可用结构保护点")
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
