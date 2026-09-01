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
from ..market_structure_engine_v2 import analyze_incremental as analyze
from sqlite_storage import RuntimeStateRepository


PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
MARKET_STRUCTURE_PLAN_SOURCE_ID = "market-structure"

# Public, market-layer defaults.  These parameters describe how a structure
# becomes a trade plan; they intentionally do not belong to a deployment.
STRUCTURE_PLAN_DEFAULT_CONFIG = {
    "enable_structure_location": True, "enable_range_boundary": True,
    "enable_range_breakout": True, "enable_triangle_prebreakout": True,
    "enable_choch": True, "enable_liquidity_sweep": True, "enable_trend": True,
    "entry_zone_atr": 0.35, "location_proximity_atr": 0.6,
    "stop_buffer_atr": 0.25, "target_buffer_atr": 0.1,
    "min_real_risk_reward": 1.2, "trend_min_real_risk_reward": 0.5,
    # Hidden safety ceiling; normal lifecycle is governed by structure events.
    "max_plan_lifetime_bars": 100,
    "min_structure_confidence": 60,
    "breakout_stop_inside_atr": 0.3, "breakout_stop_buffer_atr": 0.8,
    "breakout_target_atr": 3.0, "breakout_retest_valid_bars": 6,
    "range_plan_valid_bars": 12, "location_plan_valid_bars": 6,
    "require_range_boundary_reclaim": False, "require_location_reclaim": True,
    # Trend continuation is intentionally conservative: a close-confirmed
    # break must also retest and hold the broken level before a plan is
    # tradable.  Keep the threshold aligned with the structure engine.
    "min_breakout_displacement_atr": 0.8,
    "trend_retest_tolerance_atr": 0.25,
    "trend_min_retest_bars": 1,
    "min_choch_displacement_atr": 0.2,
    "min_trendline_touches": 2,
}


def resolve_structure_plan_config(symbol: str, period: str, setup_type: str = "") -> Dict:
    """Resolve config using public defaults, symbol/period, then setup override."""
    config = dict(STRUCTURE_PLAN_DEFAULT_CONFIG)
    try:
        stored_items = RuntimeStateRepository(0, 0).list_entities("market_structure_config")
        stored = stored_items[-1] if stored_items else {}
        allowed = set(STRUCTURE_PLAN_DEFAULT_CONFIG)
        config.update({key: value for key, value in stored.items() if key in allowed})
        for profile in stored.get("profiles", []) if isinstance(stored, dict) else []:
            if (str(profile.get("symbol") or "").upper() == str(symbol).upper()
                    and str(profile.get("period") or "").upper() == str(period).upper()):
                config.update({key: value for key, value in profile.items() if key in allowed})
                break
        wanted_setup = str(setup_type or "").strip().lower()
        matching_setup_profiles = []
        for profile in stored.get("setup_profiles", []) if isinstance(stored, dict) else []:
            if (str(profile.get("symbol") or "").upper() == str(symbol).upper()
                    and str(profile.get("period") or "").upper() == str(period).upper()):
                matching_setup_profiles.append(profile)
        if wanted_setup:
            for profile in stored.get("setup_profiles", []) if isinstance(stored, dict) else []:
                if (
                    str(profile.get("symbol") or "").upper() == str(symbol).upper()
                    and str(profile.get("period") or "").upper() == str(period).upper()
                    and str(profile.get("setup_type") or "").strip().lower() == wanted_setup
                ):
                    config.update({key: value for key, value in profile.items() if key in allowed})
                    break
        if str(setup_type or "") == "__builder__":
            config["_setup_profiles"] = matching_setup_profiles
    except Exception as exc:
        # Plan generation must continue with safe defaults if the optional
        # runtime configuration store is temporarily unavailable.
        print(f"[StructurePlan] 公共计划配置读取失败，使用默认值: {exc}")
    return config


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bar_time(row: Dict) -> int:
    # Trading-plan validity is an absolute instant. The legacy ``timestamp``
    # is MT5 broker wall time and may be UTC+2/+3; EA 2.07+ supplies the
    # canonical UTC value explicitly.
    value = int(_number(
        row.get("timestamp_utc") or row.get("timestamp") or row.get("time") or 0
    ))
    # EA 可能上报毫秒时间戳；计划有效期统一使用 Unix 秒。
    return value // 1000 if value > 10_000_000_000 else value


def _hash(*parts, length=32) -> str:
    raw = ":".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


class StructurePlanBuilder:
    """Convert one closed-bar structure snapshot into lifecycle plans."""

    def __init__(self, params: Optional[Dict] = None, setup_profiles: Optional[List[Dict]] = None):
        self.params = params or {}
        self.setup_profiles = setup_profiles or []
        self._base_params = dict(self.params)
        self._active_setup = ""
        self._rejections: List[str] = []

    def _param(self, name, default):
        return self.params.get(name, default)

    def _activate_setup(self, setup_type: str) -> None:
        """Apply the most specific setup override before deriving a plan."""
        self._active_setup = str(setup_type or "").strip().lower()
        self.params = dict(self._base_params)
        if not self._active_setup:
            return
        for profile in self.setup_profiles:
            if str(profile.get("setup_type") or "").strip().lower() == self._active_setup:
                self.params.update({k: v for k, v in profile.items() if k in STRUCTURE_PLAN_DEFAULT_CONFIG})
                break

    def _reject(self, reason: str) -> None:
        if reason and reason not in self._rejections:
            self._rejections.append(reason)

    def _range_entry_mode(self) -> str:
        return (
            "touch_and_reclaim"
            if self._param("require_range_boundary_reclaim", False)
            else "touch_or_near"
        )

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

    def _trend_retest_confirmed(self, rows: List[Dict], event: Dict, atr: float) -> bool:
        """Require a post-break bar to touch the broken level and reclaim it."""
        index = int(event.get("confirmed_at", event.get("index", -1)) or -1)
        level = _number(event.get("level"))
        direction = str(event.get("direction") or "")
        if index < 0 or level <= 0 or direction not in {"up", "down"}:
            return False
        tolerance = max(0.0, _number(self._param("trend_retest_tolerance_atr", 0.25))) * max(atr, 1e-9)
        required = max(1, int(self._param("trend_min_retest_bars", 1)))
        confirmed = 0
        for row in rows[index + 1:]:
            high, low, close = _number(row.get("high") or row.get("high_price")), _number(row.get("low") or row.get("low_price")), _number(row.get("close") or row.get("close_price"))
            if direction == "up" and low <= level + tolerance and close >= level:
                confirmed += 1
            elif direction == "down" and high >= level - tolerance and close <= level:
                confirmed += 1
            if confirmed >= required:
                return True
        return False

    @staticmethod
    def _location_reclaim_confirmed(rows: List[Dict], entry: float, direction: str,
                                    atr: float) -> bool:
        """Require the latest closed bar to touch and reclaim the HL/LH level."""
        if not rows or entry <= 0:
            return False
        row = rows[-1]
        high = _number(row.get("high") or row.get("high_price"))
        low = _number(row.get("low") or row.get("low_price"))
        close = _number(row.get("close") or row.get("close_price"))
        tolerance = max(0.0, float(atr or 0)) * 0.10
        if direction == "buy":
            return low <= entry + tolerance and close >= entry
        return high >= entry - tolerance and close <= entry

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

    def _exit_candidates(
        self, structure_snapshot: Dict, direction: str, entry: float,
    ) -> tuple[List[Dict], List[Dict]]:
        """Build ordered structural stop/target candidates for position control."""
        levels = structure_snapshot.get("structure_levels") or {}
        atr = max(0.0, _number(structure_snapshot.get("atr")))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        stop_name = "protected_low" if direction == "buy" else "protected_high"
        target_names = (
            ("weak_high", "protected_high")
            if direction == "buy" else ("weak_low", "protected_low")
        )
        stops, targets = [], []
        for rank, layer in enumerate(("internal", "swing", "external"), start=1):
            item = levels.get(layer) or {}
            reference = _number(item.get(stop_name))
            stop = (
                reference - stop_buffer if direction == "buy"
                else reference + stop_buffer
            )
            if reference > 0 and (
                (direction == "buy" and stop < entry)
                or (direction == "sell" and stop > entry)
            ):
                stops.append({
                    "level_id": f"structure_sl_{layer}",
                    "structure_layer": layer,
                    "price": round(stop, 8),
                    "reference_price": round(reference, 8),
                    "rank": rank,
                    "reason": f"{layer} {stop_name}",
                })
            for target_name in target_names:
                reference = _number(item.get(target_name))
                target = (
                    reference - target_buffer if direction == "buy"
                    else reference + target_buffer
                )
                if reference > 0 and (
                    (direction == "buy" and target > entry)
                    or (direction == "sell" and target < entry)
                ):
                    targets.append({
                        "level_id": f"structure_tp_{layer}",
                        "structure_layer": layer,
                        "price": round(target, 8),
                        "reference_price": round(reference, 8),
                        "rank": rank,
                        "reason": f"{layer} {target_name}",
                    })
                    break

        def unique(items: List[Dict], reverse: bool) -> List[Dict]:
            result = {}
            for item in items:
                result.setdefault(round(_number(item["price"]), 8), item)
            return sorted(
                result.values(), key=lambda item: _number(item["price"]),
                reverse=reverse,
            )

        # Stops are ordered from the closest protection to the furthest one;
        # targets are ordered from the nearest realization point outwards.
        return (
            unique(stops, direction == "buy"),
            unique(targets, direction == "sell"),
        )

    def _plan(
        self, *, source_id: str, symbol: str, period: str, anchor: int,
        setup_type: str, direction: str, entry_mode: str, status: str,
        entry: float = 0, zone_lower: float = 0, zone_upper: float = 0,
        stop_loss: float = 0, take_profit: float = 0,
        confidence: int = 0, reason: str = "", valid_from: int = 0,
        expires_at: int = 0, invalidation_price: float = 0,
        minimum_risk_reward: float = 0,
        structure_snapshot: Optional[Dict] = None,
        price_discovery: bool = False,
    ) -> Dict:
        group = _hash(source_id, symbol, period, anchor, "group")
        plan_id = _hash(source_id, symbol, period, anchor, setup_type, direction)
        risk = abs(entry - stop_loss) if entry and stop_loss else 0.0
        reward = abs(take_profit - entry) if entry and take_profit else 0.0
        generated_at = int(time.time())
        safety_bars = max(1, int(_number(self._param("max_plan_lifetime_bars", 100))))
        safety_expiry = int(valid_from or time.time()) + PERIOD_SECONDS.get(str(period).upper(), 300) * safety_bars
        payload = {
            "plan_id": plan_id, "plan_group_id": group,
            "setup_type": setup_type, "setup_family": self._setup_family(setup_type),
            "direction": direction, "entry_mode": entry_mode, "status": status,
            "entry_price": round(entry, 8),
            "entry_zone": {"lower": round(zone_lower, 8), "upper": round(zone_upper, 8)},
            "stop_loss": round(stop_loss, 8), "take_profit": round(take_profit, 8),
            "invalidation_price": round(invalidation_price or stop_loss, 8),
            "risk_reward_ratio": round(reward / risk, 3) if risk else 0,
            "minimum_risk_reward": round(float(minimum_risk_reward or 0), 3),
            "confidence": max(0, min(100, int(confidence))),
            "reason": reason,
            "valid_from": int(valid_from), "expires_at": safety_expiry,
            "generated_at": generated_at,
            "structure_anchor_time": int(anchor),
            "structure_snapshot": structure_snapshot or {},
            "price_discovery": bool(price_discovery),
        }
        snapshot = structure_snapshot or {}
        box = snapshot.get("range") or {}
        pattern_type = box.get("pattern") or snapshot.get("current_pattern") or ""
        segment_id = snapshot.get("structure_segment_id") or _hash(
            symbol, period, anchor, snapshot.get("major_state"), pattern_type,
        )
        plan_phase = {
            "close_breakout": "watching_breakout",
            "breakout_retest": "waiting_retest",
            "touch_and_reclaim": "waiting_reclaim",
            "touch_or_near": "waiting_touch",
        }.get(entry_mode, "active")
        plan_stage = (
            "candidate" if status == "watching" else
            "confirmed" if entry_mode in {"breakout_retest", "touch_and_reclaim"} else
            "active"
        )
        payload["structure_metadata"] = {
            "segment_id": segment_id,
            "revision": _hash(json.dumps(snapshot, sort_keys=True, default=str), length=16),
            "anchor_time": int(anchor),
            "major_state": snapshot.get("major_state") or "",
            "current_state": snapshot.get("current_state") or "",
            "pattern_type": pattern_type,
            "range_top": _number(box.get("top")),
            "range_bottom": _number(box.get("bottom")),
            "upper_boundary": {"slope": _number(box.get("high_slope")), "intercept": _number(box.get("high_intercept"))},
            "lower_boundary": {"slope": _number(box.get("low_slope")), "intercept": _number(box.get("low_intercept"))},
        }
        payload["price_sources"] = self._price_sources(
            setup_type, direction, entry, stop_loss, take_profit, box,
        )
        if setup_type.startswith("range_"):
            payload["boundary_cycle_id"] = _hash(
                symbol, period, anchor, _number(box.get("top")), _number(box.get("bottom")),
            )
            payload["boundary_state"] = "unvisited"
        payload["structure_segment_id"] = segment_id
        payload["plan_phase"] = plan_phase
        payload["plan_stage"] = plan_stage
        payload["invalidation_rules"] = self._invalidation_rules(setup_type)
        payload["tick_invalidation_rules"] = [
            rule for rule in payload["invalidation_rules"]
            if rule in {"protected_level_break", "range_returned_inside"}
        ]
        payload["close_invalidation_rules"] = [
            rule for rule in payload["invalidation_rules"]
            if rule in {"triangle_pattern_break", "range_structure_break", "same_structure_new_plan"}
        ]
        if direction in {"buy", "sell"} and entry > 0:
            stop_candidates, target_candidates = self._exit_candidates(
                structure_snapshot or {}, direction, entry,
            )
            if not stop_candidates and stop_loss > 0:
                stop_candidates = [{
                    "level_id": "structure_sl_internal",
                    "structure_layer": "internal",
                    "price": round(stop_loss, 8),
                    "reference_price": round(stop_loss, 8),
                    "rank": 1,
                    "reason": "当前交易形态失效点",
                }]
            if not target_candidates and take_profit > 0:
                target_candidates = [{
                    "level_id": "structure_tp_internal",
                    "structure_layer": "internal",
                    "price": round(take_profit, 8),
                    "reference_price": round(take_profit, 8),
                    "rank": 1,
                    "reason": (
                        "前方无结构目标，按风险倍数生成临时参考点"
                        if price_discovery else "当前交易形态目标点"
                    ),
                    "source_type": (
                        "risk_reward_projection" if price_discovery
                        else "structure"
                    ),
                }]
            payload["stop_candidates"] = stop_candidates
            payload["target_candidates"] = target_candidates
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

    @staticmethod
    def _invalidation_rules(setup_type: str) -> List[str]:
        rules = ["same_structure_new_plan"]
        if setup_type in {"range_breakout", "triangle_breakout", "triangle_breakout_watch"}:
            rules.append("close_return_to_invalid_boundary")
        if "triangle" in setup_type:
            rules.append("triangle_pattern_break")
        if setup_type.startswith("range_"):
            rules.append("range_structure_break")
        if setup_type in {"structure_location_pullback", "trend_continuation", "structure_reversal"}:
            rules.append("protected_level_break")
        return rules

    @staticmethod
    def _price_sources(setup_type, direction, entry, stop, target, box) -> Dict:
        if setup_type in {"range_lower_reversal", "range_upper_reversal", "range_false_breakout"}:
            entry_source = "range_lower_boundary" if direction == "buy" else "range_upper_boundary"
            stop_source = "range_boundary_atr_buffer"
            target_source = "opposite_range_boundary"
        elif "triangle" in setup_type:
            entry_source, stop_source, target_source = "triangle_boundary", "triangle_opposite_boundary_atr_buffer", "measured_move"
        elif setup_type in {"structure_location_pullback", "trend_continuation", "structure_reversal"}:
            entry_source, stop_source, target_source = "HL/LH_or_trendline", "protected_structure_level_atr_buffer", "next_structure_target"
        else:
            entry_source, stop_source, target_source = "confirmed_structure_event", "protected_structure_level_atr_buffer", "next_structure_target_or_R_projection"
        return {
            "entry": {"source": entry_source, "price": round(_number(entry), 8), "formula": "reference level / structure boundary"},
            "stop_loss": {"source": stop_source, "price": round(_number(stop), 8), "formula": "reference level ± ATR buffer"},
            "take_profit": {"source": target_source, "price": round(_number(target), 8), "formula": "structure target or measured move"},
        }

    def _tradable_plan(self, **kwargs) -> Optional[Dict]:
        minimum_override = kwargs.pop("min_risk_reward_override", None)
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
            self._reject("结构止损、入场和止盈价格关系无效")
            return None
        risk = abs(entry - sl)
        rr = abs(tp - entry) / risk if risk else 0
        minimum_rr = (
            max(0.1, _number(minimum_override))
            if minimum_override is not None
            else max(1.0, _number(self._param("min_real_risk_reward", 1.2)))
        )
        if rr < minimum_rr:
            self._reject(
                f"真实盈亏比 {rr:.2f} 低于最低要求 "
                f"{minimum_rr:.2f}"
            )
            return None
        if int(kwargs.get("confidence") or 0) < int(
            self._param("min_structure_confidence", 60)
        ):
            self._reject(
                f"结构置信度 {int(kwargs.get('confidence') or 0)}% 低于最低要求 "
                f"{int(self._param('min_structure_confidence', 60))}%"
            )
            return None
        kwargs["minimum_risk_reward"] = minimum_rr
        return self._plan(**kwargs)

    def build(
        self, source_id: str, symbol: str, period: str,
        rows: List[Dict], structure: Dict,
    ) -> List[Dict]:
        if not rows:
            return []
        self._rejections = []
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
        # A fresh CHOCH is a reversal candidate and must be evaluated before
        # ordinary trend-location plans, otherwise the old trend could mask it.
        latest_event = (structure.get("internal_events") or [])[-1:]
        if latest_event and latest_event[0].get("type") == "choch":
            plans = self._event_plans(
                source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
            )
            if plans:
                return plans
        else:
            plans = self._location_plans(
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
        detail = "；".join(self._rejections[-3:])
        reason = (
            f"当前结构为 {state}，未生成计划：{detail}"
            if detail else f"当前结构为 {state}，尚未形成满足条件的结构交易计划"
        )
        return [self._plan(
            source_id=source_id, symbol=symbol, period=period, anchor=bar_time,
            setup_type="no_trade", direction="none", entry_mode="watch",
            status="watching", confidence=0,
            reason=reason,
            valid_from=bar_time, expires_at=bar_time + seconds,
            structure_snapshot=snapshot,
        )]

    @staticmethod
    def _latest_labeled_pivot(hierarchy: Dict, labels, kind: str) -> Optional[Dict]:
        candidates = []
        for layer_rank, layer in enumerate(("swing", "internal")):
            for pivot in (hierarchy.get(layer) or {}).get("pivots") or []:
                if pivot.get("kind") == kind and pivot.get("label") in labels:
                    item = dict(pivot)
                    item["layer"] = layer
                    item["layer_rank"] = layer_rank
                    candidates.append(item)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (
            int(item.get("index", -1)), -int(item.get("layer_rank", 0))
        ))

    @staticmethod
    def _projected_trendline(structure: Dict, direction: str, latest_index: int,
                             min_touches: int) -> Optional[Dict]:
        kind = "support" if direction == "buy" else "resistance"
        candidates = []
        for line in structure.get("trendlines") or []:
            if line.get("kind") != kind or line.get("broken_at") is not None:
                continue
            if int(line.get("touches") or 0) < min_touches:
                continue
            slope = _number(line.get("slope"))
            if (direction == "buy" and slope <= 0) or (
                direction == "sell" and slope >= 0
            ):
                continue
            anchor_price = _number(line.get("anchor_price"))
            anchor_index = int(line.get("anchor_index") or 0)
            projected = anchor_price + slope * (latest_index - anchor_index)
            if projected <= 0:
                continue
            item = dict(line)
            item["projected_price"] = projected
            candidates.append(item)
        return max(candidates, key=lambda item: _number(item.get("score"))) if candidates else None

    def _location_candidates(self, rows: List[Dict], structure: Dict,
                             direction: str) -> List[Dict]:
        hierarchy = structure.get("structure_hierarchy") or {}
        result = []
        pivot = self._latest_labeled_pivot(
            hierarchy,
            {"HL"} if direction == "buy" else {"LH"},
            "low" if direction == "buy" else "high",
        )
        if pivot and _number(pivot.get("price")) > 0:
            result.append({
                "price": _number(pivot["price"]),
                "source": f"{pivot.get('layer')} {pivot.get('label')}",
                "confidence": 72 if pivot.get("layer") == "swing" else 66,
                "anchor_index": int(pivot.get("index") or len(rows) - 1),
            })
        protected_name = "protected_low" if direction == "buy" else "protected_high"
        for layer, confidence in (("swing", 76), ("internal", 68)):
            protected = (hierarchy.get(layer) or {}).get(protected_name) or {}
            price = _number(protected.get("price"))
            if price > 0:
                result.append({
                    "price": price, "source": f"{layer} {protected_name}",
                    "confidence": confidence,
                    "anchor_index": int(protected.get("index") or len(rows) - 1),
                })
        line = self._projected_trendline(
            structure, direction, len(rows) - 1,
            max(2, int(self._param("min_trendline_touches", 2))),
        )
        if line:
            result.append({
                "price": _number(line["projected_price"]),
                "source": "上升支撑线" if direction == "buy" else "下降压力线",
                "confidence": min(85, 62 + int(line.get("touches") or 0) * 4),
                "anchor_index": int(line.get("anchor_index") or len(rows) - 1),
            })
        unique = {}
        for item in result:
            key = round(_number(item["price"]), 8)
            if key not in unique or item["confidence"] > unique[key]["confidence"]:
                unique[key] = item
        return list(unique.values())

    def _location_plans(
        self, source_id, symbol, period, rows, structure, snapshot,
        bar_time, seconds,
    ) -> List[Dict]:
        if not self._param("enable_structure_location", True):
            return []
        self._activate_setup("structure_location_pullback")
        major = str(structure.get("major_state") or structure.get("current_state") or "")
        if major not in {"up", "down"}:
            self._reject("当前不是已确认的上涨或下跌主结构")
            return []
        candidate = structure.get("active_candidate") or {}
        if candidate and str(candidate.get("direction") or "") not in {"", major}:
            self._reject("主结构正处于反转候选阶段，等待 CHOCH/BOS 确认")
            return []

        direction = "buy" if major == "up" else "sell"
        latest = rows[-1]
        close = _number(latest.get("close") or latest.get("close_price"))
        atr = max(1e-9, _number(structure.get("atr")))
        hierarchy = structure.get("structure_hierarchy") or {}
        protected_name = "protected_low" if direction == "buy" else "protected_high"
        swing_protected = self._layer_price(hierarchy, "swing", protected_name)
        if swing_protected and (
            (direction == "buy" and close < swing_protected)
            or (direction == "sell" and close > swing_protected)
        ):
            self._reject(
                f"收盘价已{'跌破' if direction == 'buy' else '突破'} Swing "
                f"{protected_name} {swing_protected:.2f}，原趋势位置计划失效"
            )
            return []
        proximity = atr * max(0.05, _number(self._param("location_proximity_atr", 0.6)))
        candidates = self._location_candidates(rows, structure, direction)
        if not candidates:
            self._reject("当前趋势没有可用的 HL/LH、保护点或有效趋势线")
            return []
        nearby = [item for item in candidates if abs(item["price"] - close) <= proximity]
        if not nearby:
            nearest = min(candidates, key=lambda item: abs(item["price"] - close))
            self._reject(
                f"当前价距最近结构位 {nearest['price']:.2f} 为 "
                f"{abs(nearest['price'] - close) / atr:.2f} ATR，尚未进入位置区域"
            )
            return []
        level = min(
            nearby,
            key=lambda item: (abs(item["price"] - close), -int(item["confidence"])),
        )
        entry = _number(level["price"])
        entry_mode = (
            "touch_and_reclaim"
            if self._param("require_location_reclaim", True) else "touch_or_near"
        )
        if entry_mode == "touch_and_reclaim" and not self._location_reclaim_confirmed(
            rows, entry, direction, atr
        ):
            self._reject(
                f"最近收盘K线尚未触碰并{'站回' if direction == 'buy' else '跌回'}"
                f"{level['source']} {entry:.2f}，不提前生成趋势回撤计划"
            )
            return []
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        protected = self._protected_reference(hierarchy, direction, entry)
        if direction == "buy":
            sl = min(entry, protected) - stop_buffer if protected else entry - stop_buffer
        else:
            sl = max(entry, protected) + stop_buffer if protected else entry + stop_buffer
        target = self._next_target(hierarchy, direction, entry)
        price_discovery = not bool(target)
        if price_discovery:
            # A trending market at a new high/low has no historical resistance
            # or support ahead. Keep the plan tradable with a 1R reference;
            # multi-level management replaces it with configurable R exits and
            # a trailing-stop runner.
            risk = abs(entry - sl)
            target = entry + risk if direction == "buy" else entry - risk
        else:
            target = target - target_buffer if direction == "buy" else target + target_buffer
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        valid_bars = max(1, int(self._param("location_plan_valid_bars", 6)))
        plan = self._tradable_plan(
            source_id=source_id, symbol=symbol, period=period,
            anchor=_bar_time(rows[min(len(rows) - 1, max(0, level["anchor_index"]))]),
            setup_type="structure_location_pullback", direction=direction,
            entry_mode=entry_mode, status="active", entry=entry,
            zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
            stop_loss=sl, take_profit=target,
            min_risk_reward_override=self._param(
                "trend_min_real_risk_reward", 0.5
            ),
            confidence=int(level["confidence"]),
            reason=(
                f"{period} {'上涨' if direction == 'buy' else '下跌'}主结构中，"
                f"价格进入{level['source']} {entry:.2f} 附近，等待触及后"
                f"{'回收' if entry_mode == 'touch_and_reclaim' else '确认'}顺势"
                f"{'买入' if direction == 'buy' else '卖出'}"
            ),
            valid_from=bar_time, expires_at=bar_time + seconds * valid_bars,
            invalidation_price=sl, structure_snapshot=snapshot,
            price_discovery=price_discovery,
        )
        return [plan] if plan else []

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
            self._activate_setup("range_false_breakout")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
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
            self._activate_setup("triangle_breakout" if "triangle" in pattern else "range_breakout")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
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
                    entry_mode=self._range_entry_mode(), status="active", entry=bottom,
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
                    entry_mode=self._range_entry_mode(), status="active", entry=top,
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
            self._activate_setup(setup)
            # A directional triangle carries a structural bias.  Do not expose
            # the opposite breakout as an equally likely trade: an ascending
            # triangle watches only the upper-boundary break, while a
            # descending triangle watches only the lower-boundary break.
            # Only a neutral/converging triangle remains two-sided until its
            # closing-bar confirmation.
            if pattern == "broadening":
                directions = ("none",)
            elif pattern in {"ascending_triangle", "ascending"}:
                directions = ("buy",)
            elif pattern in {"descending_triangle", "descending"}:
                directions = ("sell",)
            else:
                directions = ("buy", "sell")
            result = []

            # An ascending triangle can be entered once at the late-stage
            # rising support before the breakout, then entered a second time
            # after the upper-boundary close confirmation.  The early entry is
            # deliberately limited to the convergence end and requires the
            # price to be near the projected lower trendline; otherwise only
            # the breakout watcher is exposed.
            if (pattern in {"ascending_triangle", "ascending", "descending_triangle", "descending"}
                    and self._param("enable_triangle_prebreakout", True)):
                close = _number(rows[-1].get("close") or rows[-1].get("close_price"))
                low_slope = _number(box.get("low_slope"))
                low_intercept = _number(box.get("low_intercept"))
                high_slope = _number(box.get("high_slope"))
                high_intercept = _number(box.get("high_intercept"))
                is_ascending = pattern in {"ascending_triangle", "ascending"}
                direction = "buy" if is_ascending else "sell"
                level = (
                    low_intercept + low_slope * (len(rows) - 1)
                    if is_ascending else
                    high_intercept + high_slope * (len(rows) - 1)
                )
                width_atr = _number(box.get("width_atr"))
                late_convergence = width_atr > 0 and width_atr <= 2.5
                near_entry_level = level > 0 and close > 0 and abs(close - level) <= entry_buffer
                if late_convergence and near_entry_level:
                    self._activate_setup("triangle_prebreakout_pullback")
                    entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
                    stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
                    target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
                    protected = self._protected_reference(
                        structure.get("structure_hierarchy") or {}, direction, level
                    )
                    if direction == "buy":
                        sl = min(level, protected) - stop_buffer if protected else level - stop_buffer
                        tp = top - target_buffer
                        setup_reason = "价格接近抬升支撑线"
                    else:
                        sl = max(level, protected) + stop_buffer if protected else level + stop_buffer
                        tp = bottom + target_buffer
                        setup_reason = "价格接近下降压力线"
                    early = self._tradable_plan(
                        source_id=source_id, symbol=symbol, period=period,
                        anchor=anchor, setup_type="triangle_prebreakout_pullback",
                        direction=direction, entry_mode="touch_or_near", status="active",
                        entry=level, zone_lower=level-entry_buffer,
                        zone_upper=level+entry_buffer, stop_loss=sl,
                        take_profit=tp, confidence=min(90, confidence + 3),
                        reason=(f"{period} {'上升' if is_ascending else '下降'}三角形收敛末端，"
                                f"{setup_reason}，先布局{'买入' if is_ascending else '卖出'}；"
                                f"止损置于最近{'HL/保护低点' if is_ascending else 'LH/保护高点'}外侧，"
                                f"突破{'上沿' if is_ascending else '下沿'}后可再次{'买入' if is_ascending else '卖出'}"),
                        valid_from=bar_time, expires_at=expires,
                        invalidation_price=sl, structure_snapshot=snapshot,
                    )
                    if early:
                        result.append(early)
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
            self._activate_setup("range_lower_reversal")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            lower = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="range_lower_reversal", direction="buy",
                entry_mode=self._range_entry_mode(), status="active", entry=bottom,
                zone_lower=bottom-entry_buffer, zone_upper=bottom+entry_buffer,
                stop_loss=bottom-stop_buffer, take_profit=top-target_buffer,
                confidence=confidence,
                reason=f"{period} 箱体下沿回收买入计划，上下沿确认 {box.get('low_touches',0)}/{box.get('high_touches',0)} 次",
                valid_from=bar_time, expires_at=expires,
                invalidation_price=bottom-stop_buffer, structure_snapshot=snapshot,
            )
            self._activate_setup("range_upper_reversal")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            upper = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="range_upper_reversal", direction="sell",
                entry_mode=self._range_entry_mode(), status="active", entry=top,
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
            self._activate_setup("range_breakout_watch")
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
        swing = hierarchy.get("swing") or {}
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

        if latest.get("type") == "choch" and self._param("enable_choch", True):
            self._activate_setup("choch_reversal")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            direction_state = str(latest.get("direction") or "")
            if direction_state not in {"up", "down"}:
                self._reject("CHOCH 事件没有明确的反转方向")
                return []
            displacement = _number(latest.get("displacement_atr"))
            minimum = max(0.0, _number(
                self._param("min_choch_displacement_atr", 0.2)
            ))
            if displacement < minimum:
                self._reject(
                    f"CHOCH 位移 {displacement:.2f} ATR 低于最低确认要求 {minimum:.2f} ATR"
                )
                return []
            direction = "buy" if direction_state == "up" else "sell"
            entry = _number(latest.get("level"))
            if entry <= 0:
                self._reject("CHOCH 没有有效的突破结构位")
                return []
            protected = self._protected_reference(hierarchy, direction, entry)
            sl = (
                protected - stop_buffer if direction == "buy" else protected + stop_buffer
            ) if protected else (
                entry - stop_buffer if direction == "buy" else entry + stop_buffer
            )
            target = self._next_target(hierarchy, direction, entry)
            risk = abs(entry - sl)
            price_discovery = not bool(target)
            if price_discovery:
                target = entry + risk * 2 if direction == "buy" else entry - risk * 2
            elif direction == "buy":
                target -= target_buffer
            else:
                target += target_buffer
            plan = self._tradable_plan(
                source_id=source_id, symbol=symbol, period=period, anchor=anchor,
                setup_type="choch_reversal", direction=direction,
                entry_mode="breakout_retest", status="active", entry=entry,
                zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                stop_loss=sl, take_profit=target,
                confidence=max(65, min(95, int(65 + displacement * 20))),
                reason=(
                    f"{period} {('向上' if direction == 'buy' else '向下')} CHOCH 收盘确认，"
                    f"等待反转位 {entry:.2f} 回踩后"
                    f"{'买入' if direction == 'buy' else '卖出'}"
                ),
                valid_from=bar_time, expires_at=expires,
                invalidation_price=sl, structure_snapshot=snapshot,
                price_discovery=price_discovery,
            )
            return [plan] if plan else []

        if latest.get("type") == "liquidity_sweep" and self._param("enable_liquidity_sweep", True):
            self._activate_setup("liquidity_sweep_reclaim")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
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
            price_discovery = not bool(target)
            if price_discovery:
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
                price_discovery=price_discovery,
            )
            return [plan] if plan else []

        if latest.get("type") != "bos" or not self._param("enable_trend", True):
            return []
        direction_state = str(latest.get("direction") or "")
        major = str(structure.get("major_state") or "")
        current = str(structure.get("current_state") or structure.get("internal_state") or "")
        if direction_state != major or current != major or major not in {"up", "down"}:
            self._reject("趋势延续要求主结构、当前结构与突破方向一致")
            return []
        displacement = _number(latest.get("displacement_atr"))
        minimum = max(0.0, _number(self._param("min_breakout_displacement_atr", 0.8)))
        if displacement < minimum:
            self._reject(f"趋势突破位移 {displacement:.2f} ATR 低于最低要求 {minimum:.2f} ATR")
            return []
        if latest.get("confirmation") and str(latest.get("confirmation")) not in {"close_confirmed", "retest_confirmed", "continuation_confirmed"}:
            self._reject("趋势延续必须经过收盘突破确认")
            return []
        if latest.get("confirmation") and latest.get("retest_status") not in {"touched_and_held", "held_without_touch", "retest_confirmed", "continuation_confirmed"} and not self._trend_retest_confirmed(rows, latest, atr):
            self._reject("趋势延续尚未完成突破后的回踩确认")
            return []
        direction = "buy" if major == "up" else "sell"
        entry = _number(latest.get("level"))
        protected = self._protected_reference(hierarchy, direction, entry)
        if not protected or not entry:
            return []
        sl = protected-stop_buffer if direction == "buy" else protected+stop_buffer
        target = self._next_target(hierarchy, direction, entry)
        risk = abs(entry-sl)
        price_discovery = not bool(target)
        if price_discovery:
            target = entry+risk*2 if direction == "buy" else entry-risk*2
        elif direction == "buy":
            target -= target_buffer
        else:
            target += target_buffer
        swing_phase = str(swing.get("phase") or "")
        setup = "structure_reversal" if swing_phase == "reversal_confirmed" else "trend_continuation"
        self._activate_setup(setup)
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
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
            price_discovery=price_discovery,
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
            # One user/symbol/period has exactly one canonical market-layer
            # plan set. Strategy signal-source instances merely subscribe to
            # it and must not create duplicate plan rows.
            source_id = MARKET_STRUCTURE_PLAN_SOURCE_ID
            # Market structure plans belong to the user/source/market, not to
            # an execution account or deployment. Every live/paper strategy
            # reads the same closed-bar plan and applies its own risk rules.
            key = (source_id, str(symbol).upper(), period)
            if self._last_bar.get(key) == bar_time:
                all_plans.extend(self._cache.get(key, []))
                continue
            result = structure or analyze(symbol, period, rows[-600:])
            # Structure plans are generated from the canonical market-layer
            # config, not duplicated strategy parameters.  Strategy config is
            # only used later for execution filtering and risk management.
            resolved_config = resolve_structure_plan_config(symbol, period, "__builder__")
            setup_profiles = resolved_config.pop("_setup_profiles", []) if isinstance(resolved_config, dict) else []
            plans = StructurePlanBuilder(
                resolved_config, setup_profiles=setup_profiles
            ).build(
                source_id, symbol, period, rows[-600:], result,
            )
            plans = self._resolve_plan_conflicts(plans)
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
        source_id = MARKET_STRUCTURE_PLAN_SOURCE_ID
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
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") in {"touched", "reclaimed", "triggered"}:
                # A boundary opportunity becomes eligible for a future cycle
                # only after price has left its entry zone.
                if price < lower or price > upper:
                    plan["boundary_state"] = "left_boundary"
                    self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "left_boundary"})
            return False
        mode = str(plan.get("entry_mode") or "")
        if mode in {"breakout_retest", "touch_or_near"}:
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") == "triggered":
                return False
            if str(plan.get("setup_type") or "").startswith("range_"):
                plan["boundary_state"] = "triggered"
                self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "triggered"})
            return True
        if mode != "touch_and_reclaim":
            return False
        plan_id = str(plan.get("plan_id") or "")
        state = self._tick_state.setdefault(plan_id, {"touched": False})
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        if direction == "buy" and price <= entry:
            state["touched"] = True
            if str(plan.get("setup_type") or "").startswith("range_"):
                self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "touched"})
        elif direction == "sell" and price >= entry:
            state["touched"] = True
            if str(plan.get("setup_type") or "").startswith("range_"):
                self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "touched"})
        result = bool(state["touched"] and (
            (direction == "buy" and price >= entry)
            or (direction == "sell" and price <= entry)
        ))
        if result and str(plan.get("setup_type") or "").startswith("range_"):
            self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "triggered"})
        return result

    @staticmethod
    def _resolve_plan_conflicts(plans: List[Dict]) -> List[Dict]:
        """Keep one direction when simultaneous active plans conflict."""
        actionable = [p for p in plans if str(p.get("direction") or "") in {"buy", "sell"}]
        buys = [p for p in actionable if p.get("direction") == "buy"]
        sells = [p for p in actionable if p.get("direction") == "sell"]
        if not buys or not sells:
            return plans
        winner = max(actionable, key=lambda p: (
            int(p.get("confidence") or 0),
            _number(p.get("risk_reward_ratio")),
            -abs(_number(p.get("entry_price")) - _number(p.get("trigger_price"))),
        ))
        return [p for p in plans if p not in actionable or p is winner]

    def _event_invalidated(self, plan: Dict, price: float) -> str:
        """Evaluate cheap Tick-time invalidations from the persisted snapshot."""
        rules = set(plan.get("tick_invalidation_rules") or [])
        metadata = plan.get("structure_metadata") or {}
        top = _number(metadata.get("range_top"))
        bottom = _number(metadata.get("range_bottom"))
        setup = str(plan.get("setup_type") or "")
        direction = str(plan.get("direction") or "")
        # A confirmed breakout is no longer valid once price trades back into
        # the originating range.  The close-based structure refresh provides
        # the authoritative confirmation; this Tick guard prevents an order
        # from being opened during the failed-breakout interval.
        if "close_return_to_invalid_boundary" in rules and top > bottom > 0:
            if bottom < price < top:
                return "range_returned_inside"
        if "protected_level_break" in rules:
            invalid = _number(plan.get("invalidation_price"))
            if invalid and ((direction == "buy" and price <= invalid) or (direction == "sell" and price >= invalid)):
                return "protected_level_broken"
        if "triangle_pattern_break" in rules and setup.startswith("triangle_") and top > bottom > 0:
            if direction == "buy" and price < bottom:
                return "triangle_pattern_broken"
            if direction == "sell" and price > top:
                return "triangle_pattern_broken"
        return ""

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        now = int(time.time())
        active, waiting = [], []
        seen_plan_ids = set()
        for config in strategy.get_signal_sources("structure_plan", enabled_only=True):
            params = dict(config.get("params") or {})
            allowed_directions = {
                str(item) for item in params.get(
                    "allowed_directions", ["buy", "sell"]
                ) if str(item) in {"buy", "sell"}
            }
            for plan in self._plans(symbol, strategy, config):
                plan_id = str(plan.get("plan_id") or "")
                if plan_id and plan_id in seen_plan_ids:
                    continue
                if plan_id:
                    seen_plan_ids.add(plan_id)
                direction = str(plan.get("direction") or "")
                if direction in {"buy", "sell"} and direction not in allowed_directions:
                    continue
                valid_from = int(plan.get("valid_from") or 0)
                if plan.get("status") != "active":
                    waiting.append(plan)
                    continue
                if int(plan.get("expires_at") or 0) and now > int(plan["expires_at"]):
                    continue
                invalid_reason = self._event_invalidated(plan, float(current_price))
                if invalid_reason:
                    self.repository.invalidate_plan(plan_id, invalid_reason)
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
                minimum_risk_reward=_number(plan.get("minimum_risk_reward")),
                stop_candidates=list(plan.get("stop_candidates") or []),
                target_candidates=list(plan.get("target_candidates") or []),
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
