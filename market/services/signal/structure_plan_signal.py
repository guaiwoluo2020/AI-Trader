"""K-line driven structure plans with deterministic Tick evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import time
from typing import Dict, List, Optional

from ...models import SignalSource, TradingSignal
from ...store import KlineStore
from ...store.structure_plan_store import StructureTradePlanRepository
from ..market_structure_engine_v2 import analyze


PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bar_time(row: Dict) -> int:
    value = int(_number(row.get("timestamp") or row.get("time") or 0))
    # EA 可能上报毫秒时间戳；计划有效期统一使用 Unix 秒。
    return value // 1000 if value > 10_000_000_000 else value


def _hash(*parts, length=32) -> str:
    raw = ":".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


class StructurePlanBuilder:
    """Convert one closed-bar structure snapshot into lifecycle plans."""

    def __init__(self, params: Optional[Dict] = None):
        self.params = params or {}

    def _param(self, name, default):
        return self.params.get(name, default)

    @staticmethod
    def _layer_price(hierarchy: Dict, layer: str, name: str) -> float:
        return _number(((hierarchy.get(layer) or {}).get(name) or {}).get("price"))

    def _next_target(self, hierarchy: Dict, direction: str, price: float) -> float:
        names = ("weak_high", "protected_high") if direction == "buy" else (
            "weak_low", "protected_low"
        )
        values = []
        for layer in ("swing", "external"):
            for name in names:
                value = self._layer_price(hierarchy, layer, name)
                if (direction == "buy" and value > price) or (
                    direction == "sell" and 0 < value < price
                ):
                    values.append(value)
        return min(values) if direction == "buy" and values else (
            max(values) if values else 0.0
        )

    def _protected_reference(self, hierarchy: Dict, direction: str, entry: float) -> float:
        """Return the nearest valid protected point from the current structure.

        Internal structure is preferred for a small-event stop, then swing and
        external structure are used as progressively safer fallbacks.  A point
        on the wrong side of the entry is ignored so a stale/invalid hierarchy
        cannot create an inverted stop.
        """
        name = "protected_low" if direction == "buy" else "protected_high"
        candidates = []
        for layer in ("internal", "swing", "external"):
            value = self._layer_price(hierarchy, layer, name)
            if (direction == "buy" and 0 < value < entry) or (
                direction == "sell" and value > entry
            ):
                candidates.append(value)
        if not candidates:
            return 0.0
        return max(candidates) if direction == "buy" else min(candidates)

    @staticmethod
    def _hierarchy_snapshot(hierarchy: Dict) -> Dict:
        result = {}
        for layer in ("internal", "swing", "external"):
            item = hierarchy.get(layer) or {}
            result[layer] = {
                key: ((item.get(key) or {}).get("price"))
                for key in ("protected_high", "protected_low", "weak_high", "weak_low")
                if (item.get(key) or {}).get("price") is not None
            }
        return result

    def _plan(
        self, *, source_id: str, symbol: str, period: str, anchor: int,
        setup_type: str, direction: str, entry_mode: str, status: str,
        entry: float = 0, zone_lower: float = 0, zone_upper: float = 0,
        stop_loss: float = 0, take_profit: float = 0,
        confidence: int = 0, reason: str = "", valid_from: int = 0,
        expires_at: int = 0, invalidation_price: float = 0,
        structure_snapshot: Optional[Dict] = None,
    ) -> Dict:
        group = _hash(source_id, symbol, period, anchor, "group")
        plan_id = _hash(source_id, symbol, period, anchor, setup_type, direction)
        risk = abs(entry - stop_loss) if entry and stop_loss else 0.0
        reward = abs(take_profit - entry) if entry and take_profit else 0.0
        generated_at = int(time.time())
        payload = {
            "plan_id": plan_id, "plan_group_id": group,
            "setup_type": setup_type, "setup_family": self._setup_family(setup_type),
            "direction": direction, "entry_mode": entry_mode, "status": status,
            "entry_price": round(entry, 8),
            "entry_zone": {"lower": round(zone_lower, 8), "upper": round(zone_upper, 8)},
            "stop_loss": round(stop_loss, 8), "take_profit": round(take_profit, 8),
            "invalidation_price": round(invalidation_price or stop_loss, 8),
            "risk_reward_ratio": round(reward / risk, 3) if risk else 0,
            "confidence": max(0, min(100, int(confidence))),
            "reason": reason,
            "valid_from": int(valid_from), "expires_at": int(expires_at),
            "generated_at": generated_at,
            "structure_anchor_time": int(anchor),
            "structure_snapshot": structure_snapshot or {},
        }
        payload["fingerprint"] = _hash(
            json.dumps({
                k: v for k, v in payload.items()
                if k not in {"fingerprint", "generated_at"}
            },
                       sort_keys=True, ensure_ascii=True, default=str)
        )
        return payload

    @staticmethod
    def _setup_family(setup_type: str) -> str:
        if setup_type.startswith("range_"):
            return "range"
        if "triangle" in setup_type:
            return "triangle"
        if "sweep" in setup_type:
            return "liquidity"
        if "reversal" in setup_type:
            return "reversal"
        if setup_type == "no_trade":
            return "observation"
        return "trend_follow"

    def _tradable_plan(self, **kwargs) -> Optional[Dict]:
        entry = _number(kwargs.get("entry"))
        sl = _number(kwargs.get("stop_loss"))
        tp = _number(kwargs.get("take_profit"))
        direction = kwargs.get("direction")
        valid = (
            direction == "buy" and sl < entry < tp
        ) or (
            direction == "sell" and tp < entry < sl
        )
        if not valid:
            return None
        risk = abs(entry - sl)
        rr = abs(tp - entry) / risk if risk else 0
        if rr < max(1.0, _number(self._param("min_real_risk_reward", 2.0))):
            return None
        if int(kwargs.get("confidence") or 0) < int(
            self._param("min_structure_confidence", 60)
        ):
            return None
        return self._plan(**kwargs)

    def build(
        self, source_id: str, symbol: str, period: str,
        rows: List[Dict], structure: Dict,
    ) -> List[Dict]:
        if not rows:
            return []
        bar_time = _bar_time(rows[-1])
        seconds = PERIOD_SECONDS.get(period, 300)
        atr = max(1e-9, _number(structure.get("atr")))
        hierarchy = structure.get("structure_hierarchy") or {}
        box = structure.get("range") or {}
        snapshot = {
            "bar_time": bar_time, "atr": atr,
            "major_state": structure.get("major_state"),
            "internal_state": structure.get("internal_state"),
            "external_state": structure.get("external_state"),
            "range": {key: box.get(key) for key in (
                "active", "pattern", "status", "top", "bottom", "start_index",
                "high_touches", "low_touches", "inside_ratio", "width_atr",
                "breakout_direction",
            )},
            "structure_levels": self._hierarchy_snapshot(hierarchy),
        }
        plans = self._range_plans(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        )
        if plans:
            return plans
        plans = self._event_plans(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        )
        if plans:
            return plans
        state = str(structure.get("major_state") or "undetermined")
        return [self._plan(
            source_id=source_id, symbol=symbol, period=period, anchor=bar_time,
            setup_type="no_trade", direction="none", entry_mode="watch",
            status="watching", confidence=0,
            reason=f"当前结构为 {state}，尚未形成满足条件的结构交易计划",
            valid_from=bar_time, expires_at=bar_time + seconds,
            structure_snapshot=snapshot,
        )]

    def _range_plans(
        self, source_id, symbol, period, rows, structure, snapshot,
        bar_time, seconds,
    ) -> List[Dict]:
        box = structure.get("range") or {}
        if not box or not self._param("enable_range", True):
            return []
        top, bottom = _number(box.get("top")), _number(box.get("bottom"))
        if top <= bottom or bottom <= 0:
            return []
        atr = max(1e-9, _number(structure.get("atr")))
        start_index = max(0, int(box.get("start_index") or 0))
        anchor = _bar_time(rows[start_index]) if start_index < len(rows) else bar_time
        pattern = str(box.get("pattern") or "range")
        status = str(box.get("status") or "candidate")
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        valid_bars = max(1, int(self._param("range_plan_valid_bars", 12)))
        expires = bar_time + seconds * valid_bars
        confidence = max(50, min(95, int(_number(box.get("score"), 60))))
        plans = []

        if status == "failed_breakout" and self._param("enable_false_breakout", True):
            failed = str(box.get("breakout_direction") or "")
            direction = "sell" if failed == "up" else "buy"
            entry = top if direction == "sell" else bottom
            sl = top + stop_buffer if direction == "sell" else bottom - stop_buffer
            tp = bottom + target_buffer if direction == "sell" else top - target_buffer
            plan = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="range_false_breakout", direction=direction,
                entry_mode="touch_and_reclaim", status="active", entry=entry,
                zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                stop_loss=sl, take_profit=tp, confidence=confidence,
                reason=f"{period} 箱体{('上沿' if failed == 'up' else '下沿')}假突破后收盘回到区间",
                valid_from=bar_time, expires_at=expires,
                invalidation_price=sl, structure_snapshot=snapshot,
            )
            return [plan] if plan else []

        if status == "breakout_confirmed" and self._param("enable_range_breakout", True):
            direction = "buy" if box.get("breakout_direction") == "up" else "sell"
            entry = top if direction == "buy" else bottom
            stop_inside = atr * max(0.1, _number(self._param("breakout_stop_inside_atr", 0.3)))
            sl = entry - stop_inside if direction == "buy" else entry + stop_inside
            measured = top + (top-bottom) if direction == "buy" else bottom - (top-bottom)
            obstacle = self._next_target(
                structure.get("structure_hierarchy") or {}, direction, entry
            )
            tp = measured
            if obstacle:
                tp = min(measured, obstacle-target_buffer) if direction == "buy" else max(measured, obstacle+target_buffer)
            plan = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type=("triangle_breakout" if "triangle" in pattern else "range_breakout"),
                direction=direction, entry_mode="breakout_retest", status="active",
                entry=entry, zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                stop_loss=sl, take_profit=tp, confidence=min(95, confidence+5),
                reason=f"{period} {pattern}收盘确认向{('上' if direction == 'buy' else '下')}突破，等待回踩结构边界",
                valid_from=bar_time,
                expires_at=bar_time + seconds * max(1, int(self._param("breakout_retest_valid_bars", 6))),
                invalidation_price=sl, structure_snapshot=snapshot,
            )
            return [plan] if plan else []

        if not box.get("active"):
            return []
        # 局部三角形不能覆盖已确认的主箱体边界交易。价格已经贴近
        # 箱体下沿/上沿时，优先给出边界回收计划，避免把下沿机会误标
        # 为等待突破，更不能在下沿附近生成反向卖出。
        # A confirmed sideways box is tradable at both boundaries even when
        # the latest close is still in the middle.  Plans are evaluated by
        # Tick against their entry zones, so persisting both sides here lets
        # the strategy enter when price subsequently reaches the boundary.
        # This also prevents a local triangle watcher from hiding the major
        # box's lower-boundary buy opportunity.
        if pattern != "range" and str(structure.get("major_state") or structure.get("current_state")) in {"sideways", "range"} and self._param("enable_range_boundary", True):
            boundary_plans = []
            if bottom < top:
                lower = self._tradable_plan(
                    source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                    setup_type="range_lower_reversal", direction="buy",
                    entry_mode="touch_and_reclaim", status="active", entry=bottom,
                    zone_lower=bottom-entry_buffer, zone_upper=bottom+entry_buffer,
                    stop_loss=bottom-stop_buffer, take_profit=top-target_buffer,
                    confidence=confidence,
                    reason=f"{period} 主箱体下沿附近，三角形内部回收后优先按下沿支撑买入",
                    valid_from=bar_time, expires_at=expires,
                    invalidation_price=bottom-stop_buffer, structure_snapshot=snapshot,
                )
                if lower:
                    boundary_plans.append(lower)
            if bottom < top:
                upper = self._tradable_plan(
                    source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                    setup_type="range_upper_reversal", direction="sell",
                    entry_mode="touch_and_reclaim", status="active", entry=top,
                    zone_lower=top-entry_buffer, zone_upper=top+entry_buffer,
                    stop_loss=top+stop_buffer, take_profit=bottom+target_buffer,
                    confidence=confidence,
                    reason=f"{period} 主箱体上沿附近，三角形内部回收后优先按上沿压力卖出",
                    valid_from=bar_time, expires_at=expires,
                    invalidation_price=top+stop_buffer, structure_snapshot=snapshot,
                )
                if upper:
                    boundary_plans.append(upper)
            if boundary_plans:
                latest_close = _number(rows[-1].get("close") or rows[-1].get("close_price"))
                if latest_close > 0:
                    # Only expose the boundary with the highest immediate
                    # likelihood.  When price is at the lower edge, an upper
                    # sell plan is still technically valid but misleading
                    # and far from execution, so do not publish it.
                    return [min(
                        boundary_plans,
                        key=lambda item: abs(_number(item.get("entry_price")) - latest_close),
                    )]
                return boundary_plans
        # Triangles wait for a close-confirmed breakout; broadening structures
        # remain observation-only by default because boundary risk expands.
        if pattern != "range":
            setup = "diverging_no_trade" if pattern == "broadening" else "triangle_breakout_watch"
            directions = ("none",) if pattern == "broadening" else ("buy", "sell")
            result = []
            for direction in directions:
                if direction == "none":
                    result.append(self._plan(
                        source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                        setup_type=setup, direction=direction, entry_mode="close_breakout",
                        status="watching", confidence=confidence,
                        reason="扩散结构边界持续放大，等待更明确事件",
                        valid_from=bar_time, expires_at=expires, structure_snapshot=snapshot,
                    ))
                    continue
                entry = top if direction == "buy" else bottom
                breakout_buffer = max(
                    stop_buffer,
                    atr * max(0.1, _number(self._param("breakout_stop_buffer_atr", 0.8))),
                    (top - bottom) * max(0.05, _number(self._param("breakout_stop_width_ratio", 0.15))),
                )
                target_distance = max(
                    top - bottom,
                    atr * max(1.0, _number(self._param("breakout_target_atr", 3.0))),
                )
                stop = entry - breakout_buffer if direction == "buy" else entry + breakout_buffer
                target = entry + target_distance if direction == "buy" else entry - target_distance
                result.append(self._plan(
                    source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                    setup_type=setup, direction=direction, entry_mode="close_breakout",
                    status="watching", entry=entry,
                    zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                    stop_loss=stop, take_profit=target, confidence=confidence,
                    reason=f"{period} {pattern}等待收盘确认{('上破' if direction == 'buy' else '下破')}；突破价 {entry:.2f}",
                    valid_from=bar_time, expires_at=expires,
                    invalidation_price=stop, structure_snapshot=snapshot,
                ))
            return result

        if self._param("enable_range_boundary", True):
            lower = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="range_lower_reversal", direction="buy",
                entry_mode="touch_and_reclaim", status="active", entry=bottom,
                zone_lower=bottom-entry_buffer, zone_upper=bottom+entry_buffer,
                stop_loss=bottom-stop_buffer, take_profit=top-target_buffer,
                confidence=confidence,
                reason=f"{period} 箱体下沿回收买入计划，上下沿确认 {box.get('low_touches',0)}/{box.get('high_touches',0)} 次",
                valid_from=bar_time, expires_at=expires,
                invalidation_price=bottom-stop_buffer, structure_snapshot=snapshot,
            )
            upper = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="range_upper_reversal", direction="sell",
                entry_mode="touch_and_reclaim", status="active", entry=top,
                zone_lower=top-entry_buffer, zone_upper=top+entry_buffer,
                stop_loss=top+stop_buffer, take_profit=bottom+target_buffer,
                confidence=confidence,
                reason=f"{period} 箱体上沿回落卖出计划，上下沿确认 {box.get('low_touches',0)}/{box.get('high_touches',0)} 次",
                valid_from=bar_time, expires_at=expires,
                invalidation_price=top+stop_buffer, structure_snapshot=snapshot,
            )
            boundary_plans = [item for item in (lower, upper) if item]
            if boundary_plans:
                latest_close = _number(rows[-1].get("close") or rows[-1].get("close_price"))
                if latest_close > 0:
                    boundary_plans = [min(
                        boundary_plans,
                        key=lambda item: abs(_number(item.get("entry_price")) - latest_close),
                    )]
                plans.extend(boundary_plans)
        if self._param("enable_range_breakout", True):
            for direction in ("buy", "sell"):
                plans.append(self._plan(
                    source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                    setup_type="range_breakout_watch", direction=direction,
                    entry_mode="close_breakout", status="watching",
                    confidence=confidence,
                    reason=f"{period} 箱体等待收盘确认{('上破' if direction == 'buy' else '下破')}",
                    valid_from=bar_time, expires_at=expires, structure_snapshot=snapshot,
                ))
        return plans

    def _event_plans(
        self, source_id, symbol, period, rows, structure, snapshot,
        bar_time, seconds,
    ) -> List[Dict]:
        events = structure.get("internal_events") or []
        if not events:
            return []
        atr = max(1e-9, _number(structure.get("atr")))
        hierarchy = structure.get("structure_hierarchy") or {}
        latest = events[-1]
        event_index = int(latest.get("confirmed_at", latest.get("index", -1)) or -1)
        age = len(rows)-1-event_index
        max_age = max(0, int(self._param("max_event_age_bars", 2)))
        if event_index < 0 or age < 0 or age > max_age:
            return []
        anchor = _bar_time(rows[event_index]) if event_index < len(rows) else bar_time
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        expires = bar_time + seconds * max(1, int(self._param("event_plan_valid_bars", 6)))

        if latest.get("type") == "liquidity_sweep" and self._param("enable_liquidity_sweep", True):
            swept = str(latest.get("direction") or "")
            direction = "sell" if swept == "up" else "buy"
            major = str(
                structure.get("major_state")
                or structure.get("current_state") or "undetermined"
            )
            box = structure.get("range") or {}
            # 横盘只是方向状态，不代表箱体上下沿已经达到可交易标准。
            # 未确认边界时，内部 Pivot 扫单只能作为证据，不能单独下单。
            if major in {"sideways", "range"} and not bool(box.get("active")):
                return []
            # 趋势中的 sweep 仅用于顺势回收：上涨结构扫低点做多，
            # 下跌结构扫高点做空，避免内部噪声逆主结构交易。
            if major == "up" and direction != "buy":
                return []
            if major == "down" and direction != "sell":
                return []
            entry = _number(latest.get("level"))
            protected = self._protected_reference(hierarchy, direction, entry)
            sl = (
                protected + stop_buffer if direction == "sell" else protected - stop_buffer
            ) if protected else (
                entry + stop_buffer if direction == "sell" else entry - stop_buffer
            )
            target = self._next_target(hierarchy, direction, entry)
            if not target:
                target = entry - abs(entry-sl)*2 if direction == "sell" else entry + abs(entry-sl)*2
            if direction == "sell": target += target_buffer
            else: target -= target_buffer
            plan = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="liquidity_sweep_reclaim", direction=direction,
                entry_mode="touch_and_reclaim", status="active", entry=entry,
                zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                stop_loss=sl, take_profit=target, confidence=70,
                reason=(
                    f"{period} {'扫过上方高点后回落' if swept == 'up' else '扫过下方低点后回收'}，"
                    f"与{('上涨' if major == 'up' else '下跌' if major == 'down' else '已确认箱体')}结构一致"
                ),
                valid_from=bar_time, expires_at=expires,
                invalidation_price=sl, structure_snapshot=snapshot,
            )
            return [plan] if plan else []

        if latest.get("type") != "bos" or not self._param("enable_trend", True):
            return []
        direction_state = str(latest.get("direction") or "")
        major = str(structure.get("major_state") or "")
        if direction_state != major or major not in {"up", "down"}:
            return []
        displacement = _number(latest.get("displacement_atr"))
        minimum = max(0.0, _number(self._param("min_breakout_displacement_atr", 0.2)))
        if displacement < minimum:
            return []
        direction = "buy" if major == "up" else "sell"
        entry = _number(latest.get("level"))
        protected = self._protected_reference(hierarchy, direction, entry)
        if not protected or not entry:
            return []
        sl = protected-stop_buffer if direction == "buy" else protected+stop_buffer
        target = self._next_target(hierarchy, direction, entry)
        risk = abs(entry-sl)
        if not target:
            target = entry+risk*2 if direction == "buy" else entry-risk*2
        elif direction == "buy":
            target -= target_buffer
        else:
            target += target_buffer
        swing_phase = str(swing.get("phase") or "")
        setup = "structure_reversal" if swing_phase == "reversal_confirmed" else "trend_continuation"
        plan = self._tradable_plan(
            source_id=source_id, symbol=symbol, period=period, anchor=anchor,
            setup_type=setup, direction=direction, entry_mode="breakout_retest",
            status="active", entry=entry,
            zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
            stop_loss=sl, take_profit=target,
            confidence=max(60, min(95, int(60+displacement*20))),
            reason=f"{period} {setup} BOS 已收盘确认，等待回踩突破位",
            valid_from=bar_time, expires_at=expires,
            invalidation_price=sl, structure_snapshot=snapshot,
        )
        return [plan] if plan else []


class StructurePlanSignalGenerator:
    """Build on closed bars; evaluate only cached plans on each Tick."""

    def __init__(
        self, kline_store=None, repository=None, user_id: int = 0,
        account_id: int = 0,
    ):
        self.kline_store = kline_store or KlineStore()
        self.repository = repository or StructureTradePlanRepository()
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)
        self._cache: Dict[tuple, List[Dict]] = {}
        self._last_bar: Dict[tuple, int] = {}
        self._tick_state: Dict[str, Dict] = {}

    def refresh_plans(
        self, symbol: str, period: str, strategy,
        structure: Optional[Dict] = None,
    ) -> List[Dict]:
        period = str(period).upper()
        rows = self.kline_store.get_all_klines(symbol, period)
        if not rows:
            return []
        bar_time = _bar_time(rows[-1])
        all_plans = []
        for config in strategy.get_signal_sources("structure_plan", enabled_only=True):
            if str(config.get("period") or "").upper() != period:
                continue
            source_id = str(config.get("signal_source_id") or "")
            # Market structure plans belong to the user/source/market, not to
            # an execution account or deployment. Every live/paper strategy
            # reads the same closed-bar plan and applies its own risk rules.
            key = (source_id, str(symbol).upper(), period)
            if self._last_bar.get(key) == bar_time:
                all_plans.extend(self._cache.get(key, []))
                continue
            result = structure or analyze(symbol, period, rows[-600:])
            plans = StructurePlanBuilder(config.get("params") or {}).build(
                source_id, symbol, period, rows[-600:], result,
            )
            self.repository.replace_scope(
                self.user_id, 0, "",
                source_id, symbol, period, plans, bar_time,
            )
            self._cache[key] = plans
            self._last_bar[key] = bar_time
            all_plans.extend(plans)
        return all_plans

    def _plans(self, symbol: str, strategy, config: Dict) -> List[Dict]:
        period = str(config.get("period") or "M5").upper()
        source_id = str(config.get("signal_source_id") or "")
        key = (source_id, str(symbol).upper(), period)
        if key not in self._cache:
            self._cache[key] = self.repository.list_current(
                self.user_id, 0, "",
                source_id, symbol, period,
            )
        return self._cache.get(key, [])

    def _triggered(self, plan: Dict, price: float) -> bool:
        zone = plan.get("entry_zone") or {}
        lower, upper = _number(zone.get("lower")), _number(zone.get("upper"))
        if lower <= 0 or upper <= 0 or not lower <= price <= upper:
            return False
        mode = str(plan.get("entry_mode") or "")
        if mode in {"breakout_retest", "touch_or_near"}:
            return True
        if mode != "touch_and_reclaim":
            return False
        plan_id = str(plan.get("plan_id") or "")
        state = self._tick_state.setdefault(plan_id, {"touched": False})
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        if direction == "buy" and price <= entry:
            state["touched"] = True
        elif direction == "sell" and price >= entry:
            state["touched"] = True
        return bool(state["touched"] and (
            (direction == "buy" and price >= entry)
            or (direction == "sell" and price <= entry)
        ))

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        now = int(time.time())
        active, waiting = [], []
        for config in strategy.get_signal_sources("structure_plan", enabled_only=True):
            for plan in self._plans(symbol, strategy, config):
                if plan.get("status") != "active":
                    waiting.append(plan)
                    continue
                if int(plan.get("expires_at") or 0) and now > int(plan["expires_at"]):
                    continue
                invalid = _number(plan.get("invalidation_price"))
                direction = str(plan.get("direction") or "")
                if invalid and (
                    (direction == "buy" and current_price <= invalid)
                    or (direction == "sell" and current_price >= invalid)
                ):
                    continue
                if self._triggered(plan, float(current_price)):
                    active.append((config, plan))
                else:
                    waiting.append(plan)
        signals = []
        for config, plan in active:
            direction = str(plan["direction"])
            valid_from = int(plan.get("valid_from") or 0)
            expires_at = int(plan.get("expires_at") or 0)
            signals.append(TradingSignal(
                symbol=symbol, action=direction,
                market_direction="up" if direction == "buy" else "down",
                state_ready=True, is_entry_trigger=True,
                confidence=int(plan.get("confidence") or 0),
                source=SignalSource.STRUCTURE_PLAN,
                source_period=str(config.get("period") or "M5").upper(),
                signal_source_id=str(config.get("signal_source_id") or ""),
                setup_family=str(plan.get("setup_family") or "structure"),
                setup_type=str(plan.get("setup_type") or "structure_plan"),
                entry_mode=str(plan.get("entry_mode") or "touch_or_near"),
                trigger_price=float(current_price),
                suggested_entry=float(current_price),
                suggested_sl=_number(plan.get("stop_loss")),
                suggested_tp=_number(plan.get("take_profit")),
                risk_reward_ratio=_number(plan.get("risk_reward_ratio")),
                trigger_reason=str(plan.get("reason") or "结构交易计划触发"),
                trade_plan_id=str(plan.get("plan_id") or ""),
                trade_plan_group_id=str(plan.get("plan_group_id") or ""),
                trade_plan_valid_from=valid_from,
                trade_plan_expires_at=expires_at,
                created_at=datetime.now(),
                expires_at=datetime.fromtimestamp(expires_at) if expires_at else datetime.now()+timedelta(seconds=300),
            ))
        if signals:
            return signals
        reason = (
            f"当前有 {len(waiting)} 个结构计划等待价格或收盘确认"
            if waiting else "当前K线结构没有有效交易计划"
        )
        return [TradingSignal(
            symbol=symbol, action="none", market_direction="sideways",
            state_ready=False, is_entry_trigger=False, confidence=0,
            source=SignalSource.STRUCTURE_PLAN,
            source_period=str(next(iter(strategy.get_signal_sources(
                "structure_plan", enabled_only=True
            )), {}).get("period") or "M5"),
            trigger_price=float(current_price), suggested_entry=float(current_price),
            trigger_reason=reason,
        )]

    def __call__(self, symbol, current_price):
        return []
