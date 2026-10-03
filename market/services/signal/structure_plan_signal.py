"""K-line driven structure plans with deterministic Tick evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from typing import Dict, List, Optional

from ...models import SignalSource, TradingSignal
from ...store import KlineStore
from ...store.structure_plan_store import StructureTradePlanRepository
from ..market_structure_engine_v2 import analyze_incremental as analyze
from .structure_state import derive_structure_state
from mysql_repositories import RuntimeStateRepository
from .structure_plan.price_calculator import (
    calculate_next_target, protected_reference, exit_candidates,
    location_reclaim_confirmation,
)
from .structure_plan.lifecycle import (
    invalidate_reason, close_invalidate_reason, opportunity_still_valid,
    resolve_conflicts, stage_for, distance_invalidate_reason,
    next_outside_zone_closes, max_entry_zone_widths,
)
from .structure_plan.config_resolver import resolve as resolve_plan_config
from .structure_plan.setup_binding import (
    binding_matches, layer_event, layer_events, layer_pattern,
    resolve_binding, setup_box,
)
from ..market_event_risk_service import active_event


PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
MARKET_STRUCTURE_PLAN_SOURCE_ID = "market-structure"
LEGACY_NON_EXECUTION_SETUPS = {"pressure_reversal", "pressure_zone_breakout"}

# Public, market-layer defaults.  These parameters describe how a structure
# becomes a trade plan; they intentionally do not belong to a deployment.
STRUCTURE_PLAN_DEFAULT_CONFIG = {
    # Empty means all SETUP types are tradable. A symbol/period profile may
    # provide a whitelist to restrict execution without disabling analysis.
    "allowed_setups": [],
    "blocked_setups": [],
    "enabled": True,
    "allowed_directions": ["buy", "sell"],
    "entry_mode": "",
    "bind_pattern": "",
    "bind_event": "",
    "direction_layer": "swing",
    "entry_layer": "internal",
    "require_external_alignment": True,
    "confirmation_bars": 1,
    "min_body_atr": 0.0,
    "min_displacement_atr": 0.0,
    "require_reclaim": False,
    "same_segment_max_trades": 1,
    "blocked_hours": [],
    "enable_structure_location": True, "enable_range_boundary": True,
    "enable_range_breakout": True, "enable_triangle_prebreakout": True,
    "enable_choch": True, "enable_liquidity_sweep": True, "enable_trend": True,
    "entry_zone_atr": 0.35, "location_proximity_atr": 0.4,
    "location_require_swing_external_alignment": True,
    "location_require_internal_confirmation": True,
    "location_reclaim_min_body_atr": 0.5,
    "location_reclaim_min_close_extension_atr": 0.2,
    "stop_buffer_atr": 0.25, "target_buffer_atr": 0.1,
    "max_entry_distance_pct": 0.8,
    # Waiting plans retire once price drifts too far from the frozen entry.
    # 3.5 zone-widths keeps nearby pullbacks alive without parking stale FX plans.
    "max_entry_zone_widths": 3.5,
    "min_real_risk_reward": 1.2, "trend_min_real_risk_reward": 0.5,
    # Hidden safety ceiling; normal lifecycle is governed by structure events.
    "max_plan_lifetime_bars": 100,
    "min_structure_confidence": 60,
    "breakout_stop_inside_atr": 0.3, "breakout_stop_buffer_atr": 0.8,
    "breakout_target_atr": 3.0, "breakout_retest_valid_bars": 6,
    "triangle_breakout_min_body_atr": 0.5,
    "triangle_breakout_min_close_extension_atr": 0.1,
    "triangle_breakout_require_swing_external_alignment": True,
    "range_plan_valid_bars": 12, "location_plan_valid_bars": 6,
    "require_location_reclaim": True,
    # Trend continuation accepts either a held retest or two consecutive
    # closes outside the broken level.  M1 gets a slightly longer observation
    # window because five one-minute bars are still a short-lived event.
    "max_event_age_bars": 2,
    "trend_max_event_age_bars_m1": 5,
    "trend_max_event_age_bars_other": 3,
    "min_breakout_displacement_atr": 0.6,
    # M1 趋势启动更早、噪声更大，允许较小的有效位移；M5/M15 继续
    # 使用更严格的公共阈值。按周期拆分，避免放宽高周期追单风险。
    "trend_min_breakout_displacement_atr_m1": 0.4,
    "trend_min_breakout_displacement_atr_other": 0.6,
    "trend_retest_tolerance_atr": 0.25,
    "trend_min_retest_bars": 1,
    "trend_continuation_hold_bars": 2,
    "trend_hl_min_retrace_atr": 0.3,
    "trend_hl_level_tolerance_atr": 0.35,
    "trend_hl_confirmation_buffer_atr": 0.05,
    "trend_pullback_zone_atr": 0.45,
    "trend_hl_min_spacing_atr": 0.5,
    # False-breakout entries require a confirmed close back inside the range.
    # Keep these controls specific to this setup instead of overloading the
    # generic confirmation fields used by other structure families.
    "false_breakout_require_reclaim_close": True,
    "false_breakout_confirmation_bars": 1,
    "false_breakout_min_reclaim_atr": 0.1,
    # Setup 覆盖字段（默认值为空/继承公共值）
    "target_multiple": 2.0,
    "max_entries_per_opportunity": 1,
    "cooldown_minutes": 0,
    "require_retest": True,
    "retest_tolerance_atr": 0.35,
    "invalidate_on_zone_return": True,
    "trend_require_healthy_phase": True,
    "trend_mature_retest_only": True,
    "trend_mature_retest_only_m1": False,
    # Trend entries are tiered by the distance to the structural invalidation
    # point.  A moderately distant entry is kept as a retest opportunity;
    # an excessively distant stop is not made artificially tighter.
    "trend_normal_stop_atr": 2.5,
    "trend_retest_stop_atr": 4.0,
    "trend_max_stop_atr": 6.0,
    "choch_max_stop_atr": 3.0,
    "min_choch_displacement_atr": 0.2,
    "min_trendline_touches": 2,
    # Reversal setups are paused around regular market opens and manually
    # configured macro events.  Times are evaluated in their native time zone.
}


def resolve_structure_plan_config(symbol: str, period: str, setup_type: str = "") -> Dict:
    """Resolve config using public defaults, symbol/period, then setup override."""
    return resolve_plan_config(
        symbol, period, setup_type, STRUCTURE_PLAN_DEFAULT_CONFIG,
        lambda: RuntimeStateRepository(0, 0),
    )


def setup_is_allowed(symbol: str, period: str, setup_type: str) -> bool:
    """Return the effective symbol/period/setup trading gate."""
    config = resolve_structure_plan_config(symbol, period, setup_type)
    allowed = {
        str(item).strip().lower() for item in (config.get("allowed_setups") or [])
        if str(item).strip()
    }
    setup = str(setup_type or "").strip().lower()
    if allowed and setup not in allowed:
        return False
    return bool(config.get("enabled", True))


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
        self._active_profile = {}
        self._rejections: List[str] = []

    def _param(self, name, default):
        return self.params.get(name, default)

    def _filter_allowed(self, plans: List[Dict]) -> List[Dict]:
        allowed = {
            str(item).strip().lower()
            for item in (self._base_params.get("allowed_setups") or [])
            if str(item).strip()
        }
        def enabled_for(setup: str) -> bool:
            for profile in self.setup_profiles:
                if str(profile.get("setup_type") or "").strip().lower() == setup:
                    return bool(profile.get("enabled", True))
            return True
        def direction_allowed(plan: Dict) -> bool:
            setup = str(plan.get("setup_type") or "").strip().lower()
            profile = next((item for item in self.setup_profiles
                            if str(item.get("setup_type") or "").strip().lower() == setup), None)
            configured = (profile or {}).get("allowed_directions") if profile else self._base_params.get("allowed_directions")
            if not configured:
                return True
            return str(plan.get("direction") or "").strip().lower() in {
                str(item).strip().lower() for item in configured
            }
        directions = {
            str(item).strip().lower()
            for item in (self._base_params.get("allowed_directions") or ["buy", "sell"])
            if str(item).strip().lower() in {"buy", "sell"}
        }
        return [
            plan for plan in plans
            if str(plan.get("setup_type") or "").strip().lower() == "no_trade"
            or (not allowed or str(plan.get("setup_type") or "").strip().lower() in allowed)
            and enabled_for(str(plan.get("setup_type") or "").strip().lower())
            and (not directions or str(plan.get("direction") or "").strip().lower() in directions)
            and direction_allowed(plan)
        ]

    @staticmethod
    def _actionable_plans(plans: List[Dict]) -> List[Dict]:
        result = []
        for plan in plans or []:
            setup = str(plan.get("setup_type") or "").strip().lower()
            if setup in {"", "no_trade"}:
                continue
            if str(plan.get("direction") or "") not in {"buy", "sell"}:
                continue
            if str(plan.get("status") or "") != "active":
                continue
            if _number(plan.get("entry_price")) <= 0:
                continue
            result.append(plan)
        return result

    @staticmethod
    def _watch_plans(plans: List[Dict]) -> List[Dict]:
        result = []
        for plan in plans or []:
            setup = str(plan.get("setup_type") or "").strip().lower()
            if setup in {"", "no_trade"}:
                continue
            if str(plan.get("status") or "") == "watching":
                result.append(plan)
        return result

    def _select_structure_plans(
        self, range_plans: List[Dict], event_plans: List[Dict], location_plans: List[Dict],
    ) -> List[Dict]:
        """Keep every executable SETUP. Watching plans must not block them."""
        groups = (event_plans, range_plans, location_plans)
        chosen = []
        for group in groups:
            chosen.extend(self._actionable_plans(group))
        if chosen:
            return chosen
        watches = []
        for group in groups:
            watches.extend(self._watch_plans(group))
        return watches

    def _activate_setup(self, setup_type: str) -> None:
        """Apply the most specific setup override before deriving a plan."""
        self._active_setup = str(setup_type or "").strip().lower()
        self.params = dict(self._base_params)
        self._active_profile = {}
        if not self._active_setup:
            return
        for profile in self.setup_profiles:
            if str(profile.get("setup_type") or "").strip().lower() == self._active_setup:
                self._active_profile = dict(profile)
                self.params.update({k: v for k, v in profile.items() if k in STRUCTURE_PLAN_DEFAULT_CONFIG or k in {"bind_pattern","bind_event","direction_layer","entry_layer","require_external_alignment"}})
                # Map the optimizer's common controls onto the existing
                # setup-specific gates so recommendations affect generation.
                if "min_displacement_atr" in profile:
                    self.params["min_breakout_displacement_atr"] = profile["min_displacement_atr"]
                if "min_body_atr" in profile:
                    self.params["triangle_breakout_min_body_atr"] = profile["min_body_atr"]
                    self.params["location_reclaim_min_body_atr"] = profile["min_body_atr"]
                if "confirmation_bars" in profile:
                    self.params["trend_continuation_hold_bars"] = profile["confirmation_bars"]
                if "require_reclaim" in profile:
                    self.params["require_location_reclaim"] = profile["require_reclaim"]
                break
        binding = resolve_binding(self._active_setup, self._active_profile)
        self.params["require_external_alignment"] = bool(
            binding.get("require_external_alignment", True)
        )

    def _reject(self, reason: str) -> None:
        if reason and reason not in self._rejections:
            self._rejections.append(reason)

    def _setup_overlay(self, setup_type: str = "") -> dict:
        setup = str(setup_type or self._active_setup or "").strip().lower()
        active = str(self._active_setup or "").strip().lower()
        if setup and setup == active and self._active_profile:
            return dict(self._active_profile)
        for profile in self.setup_profiles:
            if str(profile.get("setup_type") or "").strip().lower() == setup:
                return dict(profile)
        return {}

    def _setup_binding(self, setup_type: str = "") -> dict:
        setup = str(setup_type or self._active_setup or "").strip().lower()
        return resolve_binding(setup, self._setup_overlay(setup))

    def _external_allows(self, structure: Dict, expected_bias: str, setup_type: str = "") -> bool:
        """Honor per-SETUP External alignment. Undetermined External does not block."""
        if expected_bias not in {"up", "down"}:
            return True
        binding = self._setup_binding(setup_type)
        if not bool(binding.get("require_external_alignment", False)):
            return True
        bias = self.structure_layers(structure).get("external")
        if bias not in {"up", "down"}:
            return True
        return bias == expected_bias

    def _binding_context(self, structure: Dict, setup_type: str = "") -> dict:
        binding = self._setup_binding(setup_type)
        direction_layer = binding["direction_layer"]
        entry_layer = binding["entry_layer"]
        box, box_layer = setup_box(structure, binding)
        return {
            **binding,
            "direction_pattern": layer_pattern(structure, direction_layer),
            "entry_pattern": layer_pattern(structure, entry_layer),
            "direction_event": layer_event(structure, direction_layer),
            "entry_event": layer_event(structure, entry_layer),
            "direction_state": layer_state(structure, direction_layer),
            "entry_state": layer_state(structure, entry_layer),
            "box": box,
            "box_layer": box_layer,
            "matched": binding_matches(structure, binding),
        }

    def _setup_owns_layer(self, setup_type: str, box_layer: str) -> bool:
        binding = self._setup_binding(setup_type)
        layer = str(box_layer or "").strip().lower()
        return layer in {binding["entry_layer"], binding["direction_layer"]}

    def _select_bound_event(self, structure: Dict):
        ranked = []
        for event_type, setup_type in (
            ("choch", "choch_reversal"),
            ("liquidity_sweep", "liquidity_sweep_reclaim"),
            ("bos", "trend_continuation"),
        ):
            binding = self._setup_binding(setup_type)
            if event_type != "choch" and not binding_matches(structure, binding):
                continue
            events = layer_events(structure, binding["entry_layer"])
            for event in reversed(events):
                if str(event.get("type") or "") != event_type:
                    continue
                index = int(event.get("confirmed_at", event.get("index", -1)) or -1)
                ranked.append((index, event, setup_type))
                break
        if not ranked:
            return None
        _index, event, setup_type = max(ranked, key=lambda item: item[0])
        return event, setup_type

    def _range_entry_mode(self) -> str:
        configured = str(self._param("entry_mode", "") or "").strip().lower()
        if configured in {"touch_or_near", "touch_and_reclaim"}:
            return configured
        return "touch_or_near"

    @staticmethod
    def _direction_bias(value) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"up", "bullish", "buy", "long"}:
            return "up"
        if normalized in {"down", "bearish", "sell", "short"}:
            return "down"
        return "undetermined"

    @classmethod
    def structure_layers(cls, structure: Optional[Dict] = None) -> Dict[str, str]:
        """Return Internal / Swing / External directional bias."""
        structure = structure or {}
        hierarchy = structure.get("structure_hierarchy") or {}
        return {
            "internal": cls._direction_bias(
                (hierarchy.get("internal") or {}).get("bias")
                or structure.get("internal_state")
            ),
            "swing": cls._direction_bias(
                (hierarchy.get("swing") or {}).get("bias")
                or structure.get("major_state")
                or structure.get("current_state")
            ),
            "external": cls._direction_bias(
                (hierarchy.get("external") or {}).get("bias")
                or structure.get("external_state")
            ),
        }

    @classmethod
    def background_bias(cls, structure: Optional[Dict] = None) -> str:
        """Swing is the permission layer for every symbol.

        External is too slow to gate entries. Internal never grants a fade.
        """
        swing = cls.structure_layers(structure)["swing"]
        if swing in {"up", "down"}:
            return swing
        return "sideways"

    @classmethod
    def slope_drift(cls, structure: Optional[Dict] = None) -> str:
        """Rising/falling channel even when Swing is still labelled sideways."""
        structure = structure or {}
        regime = str(structure.get("trend_regime") or "").strip().lower()
        if regime == "ascending_range":
            return "up"
        if regime == "descending_range":
            return "down"
        evidence = structure.get("trend_regime_evidence") or {}
        min_atr = max(0.05, _number(evidence.get("minimum_slope_atr"), 0.05))
        support_atr = _number(evidence.get("support_slope_atr"))
        resistance_atr = _number(evidence.get("resistance_slope_atr"))
        if (
            support_atr >= min_atr and resistance_atr >= min_atr
            and evidence.get("higher_highs") and evidence.get("higher_lows")
        ):
            return "up"
        if (
            support_atr <= -min_atr and resistance_atr <= -min_atr
            and evidence.get("lower_highs") and evidence.get("lower_lows")
        ):
            return "down"
        atr = max(_number(structure.get("atr")), 1e-9)
        box = structure.get("range") or {}
        min_slope = 0.05 * atr
        high_slope = _number(box.get("high_slope"))
        low_slope = _number(box.get("low_slope"))
        if high_slope >= min_slope and low_slope >= min_slope:
            return "up"
        if high_slope <= -min_slope and low_slope <= -min_slope:
            return "down"
        return ""

    @classmethod
    def counter_trend_reason(
        cls, direction: str, structure: Optional[Dict] = None,
        setup_type: str = "", config: Optional[Dict] = None,
    ) -> str:
        """Block shorts in a Swing uptrend and longs in a Swing downtrend."""
        direction = str(direction or "").strip().lower()
        if direction not in {"buy", "sell"}:
            return ""
        structure = structure or {}
        setup = str(setup_type or "").strip().lower()
        phase = str(structure.get("trend_phase") or "").strip().lower()
        if setup in {"choch_reversal", "range_false_breakout"} and phase == "failed":
            return ""
        layers = cls.structure_layers(structure)
        binding = resolve_binding(setup, config)
        layer_bias = layers.get(binding.get("direction_layer") or "swing")
        bias = layer_bias if layer_bias in {"up", "down"} else cls.background_bias(structure)
        drift = cls.slope_drift(structure) if setup == "liquidity_sweep_reclaim" else ""
        if bias == "sideways" and drift in {"up", "down"}:
            bias = drift
        detail = (
            f"Internal={layers['internal']}，Swing={layers['swing']}，"
            f"External={layers['external']}"
        )
        if bias == "up" and direction == "sell":
            if drift == "up" and layers["swing"] not in {"up", "down"}:
                return f"震荡上升通道，禁止扫高点开空（{detail}）"
            return f"Swing 上涨，禁止开空（{detail}）"
        if bias == "down" and direction == "buy":
            if drift == "down" and layers["swing"] not in {"up", "down"}:
                return f"震荡下降通道，禁止扫低点开多（{detail}）"
            return f"Swing 下跌，禁止开多（{detail}）"
        return ""

    def _triangle_breakout_confirmation(
        self, rows: List[Dict], structure: Dict, box: Dict,
        direction: str, atr: float,
    ) -> tuple[bool, Dict, str]:
        """Validate a triangle break using price displacement and hierarchy.

        The structure engine already distinguishes a close break from a wick
        sweep.  This second gate is deliberately stricter for executable plans:
        the breakout candle must have a meaningful body, close beyond the
        projected boundary, and agree with both Swing and External structure.
        """
        event = box.get("lifecycle_event") or {}
        index = int(event.get("confirmed_at", event.get("index", len(rows) - 1)) or 0)
        index = min(len(rows) - 1, max(0, index))
        bar = rows[index]
        open_price = _number(bar.get("open") or bar.get("open_price"))
        close_price = _number(bar.get("close") or bar.get("close_price"))
        body_atr = abs(close_price - open_price) / max(atr, 1e-9)

        if direction == "buy":
            slope = _number(box.get("high_slope"))
            intercept = _number(box.get("high_intercept"))
            boundary = intercept + slope * index if intercept else _number(
                box.get("breakout_level") or box.get("locked_top") or box.get("top")
            )
            extension = close_price - boundary
            expected_bias = "up"
        else:
            slope = _number(box.get("low_slope"))
            intercept = _number(box.get("low_intercept"))
            boundary = intercept + slope * index if intercept else _number(
                box.get("breakout_level") or box.get("locked_bottom") or box.get("bottom")
            )
            extension = boundary - close_price
            expected_bias = "down"
        extension_atr = extension / max(atr, 1e-9)

        hierarchy = structure.get("structure_hierarchy") or {}
        swing_bias = self._direction_bias(
            (hierarchy.get("swing") or {}).get("bias")
            or structure.get("major_state")
        )
        external_bias = self._direction_bias(
            (hierarchy.get("external") or {}).get("bias")
            or structure.get("external_state")
        )
        min_body = max(0.0, _number(
            self._param("triangle_breakout_min_body_atr", 0.5)
        ))
        min_extension = max(0.0, _number(
            self._param("triangle_breakout_min_close_extension_atr", 0.1)
        ))
        triangle_setup = (
            self._active_setup
            if self._active_setup in {"triangle_breakout", "triangle_breakout_watch"}
            else "triangle_breakout"
        )
        evidence = {
            "breakout_bar_index": index,
            "breakout_bar_time": _bar_time(bar),
            "open_price": round(open_price, 8),
            "close_price": round(close_price, 8),
            "boundary_price": round(boundary, 8),
            "body_atr": round(body_atr, 3),
            "minimum_body_atr": round(min_body, 3),
            "close_extension_atr": round(extension_atr, 3),
            "minimum_close_extension_atr": round(min_extension, 3),
            "swing_bias": swing_bias,
            "external_bias": external_bias,
            "expected_bias": expected_bias,
        }
        if body_atr < min_body:
            return False, evidence, (
                f"三角形突破K线实体仅 {body_atr:.2f} ATR，低于最低要求 {min_body:.2f} ATR"
            )
        if boundary <= 0 or extension_atr < min_extension:
            return False, evidence, (
                f"三角形突破收盘仅越过边界 {extension_atr:.2f} ATR，"
                f"低于最低要求 {min_extension:.2f} ATR"
            )
        if swing_bias != expected_bias:
            return False, evidence, (
                f"三角形突破方向与方向层不一致：突破={expected_bias}，Swing={swing_bias}"
            )
        if not self._external_allows(structure, expected_bias, triangle_setup):
            return False, evidence, (
                f"三角形突破方向与 External 不一致："
                f"突破={expected_bias}，External={external_bias}"
            )
        return True, evidence, ""

    @staticmethod
    def _layer_price(hierarchy: Dict, layer: str, name: str) -> float:
        return _number(((hierarchy.get(layer) or {}).get(name) or {}).get("price"))

    def _next_target(self, hierarchy: Dict, direction: str, price: float) -> float:
        return calculate_next_target(hierarchy, direction, price)

    def _protected_reference(self, hierarchy: Dict, direction: str, entry: float) -> float:
        """Return the nearest valid protected point from the current structure.

        Internal structure is preferred for a small-event stop, then swing and
        external structure are used as progressively safer fallbacks.  A point
        on the wrong side of the entry is ignored so a stale/invalid hierarchy
        cannot create an inverted stop.
        """
        return protected_reference(hierarchy, direction, entry)

    def _trend_entry_confirmation(
        self, rows: List[Dict], event: Dict, atr: float,
    ) -> tuple[str, float, Dict]:
        """Resolve a BOS entry as a retest or a sustained no-retest break."""
        level = _number(event.get("level"))
        direction = str(event.get("direction") or "")
        event_index = int(event.get("confirmed_at", event.get("index", -1)) or -1)
        evidence = {
            "confirmation_mode": "",
            "breakout_level": round(level, 8),
            "required_hold_bars": max(
                2, int(self._param("trend_continuation_hold_bars", 2))
            ),
        }
        if level <= 0 or direction not in {"up", "down"} or event_index < 0:
            return "", 0.0, evidence

        confirmation = str(event.get("confirmation") or "")
        retest_status = str(event.get("retest_status") or "")
        confirmation_index = int(
            event.get("confirmation_index", event_index) or event_index
        )
        confirmation_index = min(len(rows) - 1, max(0, confirmation_index))
        if confirmation in {"retest_confirmed"} or retest_status in {
            "touched_and_held", "retest_confirmed",
        }:
            evidence.update({
                "confirmation_mode": "retest",
                "confirmation_bar_index": confirmation_index,
            })
            return "breakout_retest", level, evidence
        if confirmation in {"continuation_confirmed"} or retest_status in {
            "held_without_touch", "continuation_confirmed",
        }:
            entry = _number(rows[confirmation_index].get("close") or rows[confirmation_index].get("close_price"))
            evidence.update({
                "confirmation_mode": "continuation_hold",
                "confirmation_bar_index": confirmation_index,
                "confirmation_close": round(entry, 8),
            })
            return "touch_or_near", entry, evidence

        tolerance = max(
            0.0, _number(self._param("trend_retest_tolerance_atr", 0.25))
        ) * max(atr, 1e-9)
        required_hold = evidence["required_hold_bars"]
        start_index = int(event.get("break_confirmed_at", event_index) or event_index)
        start_index = min(len(rows) - 1, max(0, start_index))
        breakout_close = _number(
            rows[start_index].get("close") or rows[start_index].get("close_price")
        )
        held_count = int(
            (direction == "up" and breakout_close >= level)
            or (direction == "down" and breakout_close <= level)
        )
        for index, row in enumerate(rows[start_index + 1:], start=start_index + 1):
            high = _number(row.get("high") or row.get("high_price"))
            low = _number(row.get("low") or row.get("low_price"))
            close = _number(row.get("close") or row.get("close_price"))
            touched_and_held = (
                direction == "up" and low <= level + tolerance and close >= level
            ) or (
                direction == "down" and high >= level - tolerance and close <= level
            )
            if touched_and_held:
                evidence.update({
                    "confirmation_mode": "retest",
                    "confirmation_bar_index": index,
                    "confirmation_close": round(close, 8),
                })
                return "breakout_retest", level, evidence
            held = (
                direction == "up" and close >= level
            ) or (
                direction == "down" and close <= level
            )
            held_count = held_count + 1 if held else 0
            if held_count >= required_hold:
                evidence.update({
                    "confirmation_mode": "continuation_hold",
                    "confirmation_bar_index": index,
                    "confirmation_close": round(close, 8),
                    "held_bars": held_count,
                })
                return "touch_or_near", close, evidence
        evidence["held_bars"] = held_count
        return "", 0.0, evidence

    def _higher_low_entry_confirmation(
        self, rows: List[Dict], event: Dict, atr: float, direction: str,
    ) -> tuple[str, float, Dict]:
        """Wait for a post-BOS pullback structure before entering.

        In mature or weakening trends the breakout level is only an
        observation anchor.  The actual entry is the break of the pullback
        swing high/low after a valid HL/LH has formed.
        """
        level = _number(event.get("level"))
        event_index = int(event.get("confirmed_at", event.get("index", -1)) or -1)
        if level <= 0 or event_index < 0 or event_index >= len(rows) - 1:
            return "", 0.0, {"confirmation_mode": "higher_low_pending"}
        minimum_pullback = max(0.25, _number(self._param("trend_hl_min_retrace_atr", 0.3))) * max(atr, 1e-9)
        level_tolerance = max(0.15, _number(self._param("trend_hl_level_tolerance_atr", 0.35))) * max(atr, 1e-9)
        confirmation_buffer = max(0.05, _number(self._param("trend_hl_confirmation_buffer_atr", 0.05))) * max(atr, 1e-9)
        post = rows[event_index:]
        extreme = _number(post[0].get("high") or post[0].get("high_price")) if direction == "up" else _number(post[0].get("low") or post[0].get("low_price"))
        pullback = None
        for offset, row in enumerate(post[1:], start=1):
            high = _number(row.get("high") or row.get("high_price"))
            low = _number(row.get("low") or row.get("low_price"))
            close = _number(row.get("close") or row.get("close_price"))
            if direction == "up":
                extreme = max(extreme, high)
                if pullback is None and extreme - low >= minimum_pullback and low >= level - level_tolerance:
                    pullback = {"index": event_index + offset, "price": low, "trigger": extreme}
                if pullback and close >= pullback["trigger"] + confirmation_buffer:
                    return "higher_low_breakout", pullback["trigger"], {
                        "confirmation_mode": "higher_low_breakout",
                        "confirmation_bar_index": event_index + offset,
                        "higher_low_index": pullback["index"],
                        "higher_low": round(pullback["price"], 8),
                        "pullback_trigger": round(pullback["trigger"], 8),
                    }
            else:
                extreme = min(extreme, low)
                if pullback is None and high - extreme >= minimum_pullback and high <= level + level_tolerance:
                    pullback = {"index": event_index + offset, "price": high, "trigger": extreme}
                if pullback and close <= pullback["trigger"] - confirmation_buffer:
                    return "lower_high_breakout", pullback["trigger"], {
                        "confirmation_mode": "lower_high_breakout",
                        "confirmation_bar_index": event_index + offset,
                        "lower_high_index": pullback["index"],
                        "lower_high": round(pullback["price"], 8),
                        "pullback_trigger": round(pullback["trigger"], 8),
                    }
        return "", 0.0, {"confirmation_mode": "higher_low_pending"}

    def _ascending_pullback_confirmation(
        self, rows: List[Dict], structure: Dict, atr: float, direction: str,
    ) -> tuple[str, float, Dict]:
        """Find a rising/falling structure pullback entry, not a new extreme."""
        hierarchy = structure.get("structure_hierarchy") or {}
        swing = hierarchy.get("swing") or {}
        pivots = [p for p in swing.get("pivots") or [] if p.get("kind") == ("low" if direction == "up" else "high")]
        expected_label = "HL" if direction == "up" else "LH"
        pivots = [p for p in pivots if p.get("label") == expected_label]
        if len(pivots) < 2 or not rows:
            return "", 0.0, {"confirmation_mode": "trend_pullback_pending"}
        previous, latest = pivots[-2], pivots[-1]
        previous_price = _number(previous.get("price"))
        latest_price = _number(latest.get("price"))
        spacing = abs(latest_price - previous_price)
        minimum_spacing = max(0.1, _number(self._param("trend_hl_min_spacing_atr", 0.5))) * max(atr, 1e-9)
        rising = latest_price > previous_price if direction == "up" else latest_price < previous_price
        if not rising or spacing < minimum_spacing:
            return "", 0.0, {"confirmation_mode": "trend_pullback_pending", "hl_spacing": round(spacing, 8)}
        current = _number(rows[-1].get("close") or rows[-1].get("close_price"))
        zone = max(0.1, _number(self._param("trend_pullback_zone_atr", 0.45))) * max(atr, 1e-9)
        # The entry is placed around the latest HL/LH. The tick gate later
        # requires price to actually revisit this zone before execution.
        near_support = (
            abs(current - latest_price) <= zone if direction == "up"
            else abs(current - latest_price) <= zone
        )
        internal = str(structure.get("internal_state") or "").lower()
        latest_event = (structure.get("internal_events") or [])[-1:]
        recovering = internal == direction or bool(
            latest_event and str(latest_event[0].get("direction") or "") == direction
        )
        if not near_support or not recovering:
            return "", 0.0, {
                "confirmation_mode": "trend_pullback_pending",
                "hl_spacing": round(spacing, 8),
                "pullback_level": round(latest_price, 8),
                "pullback_zone_atr": round(zone / max(atr, 1e-9), 3),
            }
        return "trend_pullback_reclaim", latest_price, {
            "confirmation_mode": "trend_pullback_reclaim",
            "pullback_level": round(latest_price, 8),
            "previous_pullback_level": round(previous_price, 8),
            "hl_spacing": round(spacing, 8),
            "pullback_zone_atr": round(zone / max(atr, 1e-9), 3),
        }

    def _location_reclaim_confirmation(
        self, rows: List[Dict], entry: float, direction: str, atr: float,
    ) -> tuple[bool, Dict, str]:
        """Require a directional, displaced close back beyond the HL/LH."""
        return location_reclaim_confirmation(
            rows, entry, direction, atr,
            min_body_atr=max(0.0, _number(
                self._param("location_reclaim_min_body_atr", 0.5)
            )),
            min_close_extension_atr=max(0.0, _number(
                self._param("location_reclaim_min_close_extension_atr", 0.2)
            )),
        )

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

    @staticmethod
    def _binding_snapshot(hierarchy: Dict) -> Dict:
        result = {}
        for layer in ("internal", "swing", "external"):
            item = hierarchy.get(layer) or {}
            detail = item.get("pattern_detail") if isinstance(item.get("pattern_detail"), dict) else {}
            result[layer] = {
                "bias": item.get("bias"),
                "pattern": item.get("pattern"),
                "pattern_phase": item.get("pattern_phase") or item.get("phase"),
                "phase": item.get("phase"),
                "event": item.get("event") or item.get("last_event"),
                "pattern_detail": {
                    key: detail.get(key)
                    for key in (
                        "pattern", "status", "top", "bottom", "breakout_direction",
                        "active", "start_index", "high_touches", "low_touches",
                        "width_atr", "score",
                    )
                    if key in detail
                },
            }
        return result

    def _exit_candidates(
        self, structure_snapshot: Dict, direction: str, entry: float,
    ) -> tuple[List[Dict], List[Dict]]:
        """Build ordered structural stop/target candidates for position control."""
        return exit_candidates(structure_snapshot, direction, entry, self._param)

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
        validation_evidence: Optional[Dict] = None,
    ) -> Dict:
        snapshot = structure_snapshot or {}
        evidence = validation_evidence or {}
        zone_revision = str(evidence.get("zone_revision") or "")
        group_scope = "group"
        identity_anchor = snapshot.get("structure_segment_id") or anchor
        cycle = 1
        family_id = ""
        group = _hash(source_id, symbol, period, identity_anchor, group_scope, cycle)
        plan_id = _hash(
            source_id, symbol, period, identity_anchor, setup_type, direction,
            "",
            cycle,
        )
        risk = abs(entry - stop_loss) if entry and stop_loss else 0.0
        reward = abs(take_profit - entry) if entry and take_profit else 0.0
        generated_at = int(time.time())
        safety_bars = max(1, int(_number(self._param("max_plan_lifetime_bars", 100))))
        plan_start = int(valid_from or time.time())
        safety_expiry = plan_start + PERIOD_SECONDS.get(str(period).upper(), 300) * safety_bars
        # A plan is an opportunity tied to one structure snapshot.  The
        # configured bar count remains a useful short-time fallback, but it
        # must never leave a stale waiting plan alive for days when Tick data
        # stops.  The repository also enforces this cap for already-persisted
        # plans, so this protects both new and legacy rows.
        safety_expiry = min(safety_expiry, plan_start + 24 * 60 * 60)
        payload = {
            "plan_id": plan_id, "plan_group_id": group,
            "setup_type": setup_type, "setup_family": self._setup_family(setup_type),
            **{k: v for k, v in self._setup_binding(setup_type).items() if k != "setup_type"},
            "direction": direction, "entry_mode": entry_mode, "status": status,
            "symbol": str(symbol), "period": str(period).upper(),
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
            "validation_evidence": evidence,
            "opportunity_family_id": family_id,
            "opportunity_cycle": cycle,
        }
        payload["structure_state"] = derive_structure_state(
            snapshot,
            setup_type=setup_type,
            direction=direction,
            entry_mode=entry_mode,
            event=evidence,
        )
        setup_family = self._setup_family(setup_type)
        box = snapshot.get("range") or {}
        pattern_type = box.get("pattern") or snapshot.get("current_pattern") or ""
        # The rolling closed-bar window moves ``anchor`` forward every candle.
        # Identity must follow the structural segment, not that window start,
        # otherwise the same breakout/retest is rewritten as a new plan.
        segment_id = snapshot.get("structure_segment_id") or _hash(
            symbol, period, snapshot.get("major_state"), pattern_type,
            round(_number(box.get("top")), 2), round(_number(box.get("bottom")), 2),
        )
        # A trade opportunity belongs to one structural segment, one zone,
        # one direction and one setup family.  The event's old zone-only ID is
        # deliberately ignored here: the same density bucket can reappear in
        # a later segment and must then be treated as a fresh opportunity.
        opportunity_segment = str(segment_id or "")
        opportunity_zone = str(evidence.get("zone_id") or "")
        family_id = _hash(
            source_id, symbol, period, opportunity_segment or identity_anchor,
            setup_type, direction, entry_mode,
        )
        payload["opportunity_family_id"] = family_id
        payload["opportunity_cycle"] = cycle
        payload["opportunity_id"] = str(
            evidence.get("opportunity_id") or _hash(family_id, cycle)
        )
        payload["plan_group_id"] = _hash("group", family_id, cycle)
        payload["plan_id"] = _hash(
            "plan", family_id, cycle,
            "",
        )
        payload["opportunity_stage"] = "single"
        plan_phase = {
            "close_breakout": "watching_breakout",
            "breakout_retest": "waiting_retest",
            "touch_and_reclaim": "waiting_reclaim",
            "touch_or_near": "waiting_touch",
        }.get(entry_mode, "active")
        plan_stage = stage_for(status, entry_mode)
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
        setup_type = str(kwargs.get("setup_type") or "")
        snapshot = kwargs.get("structure_snapshot") or {}
        blocked = self.counter_trend_reason(
            direction, snapshot, setup_type, self._setup_overlay(setup_type),
        )
        if blocked:
            self._reject(blocked)
            return None
        expected_bias = "up" if direction == "buy" else "down" if direction == "sell" else ""
        if expected_bias and not self._external_allows(snapshot, expected_bias, setup_type):
            layers = self.structure_layers(snapshot)
            self._reject(
                f"{setup_type or 'SETUP'} 要求 External 同向："
                f"计划={expected_bias}，External={layers.get('external')}"
            )
            return None
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

    def _stop_risk_atr(self, entry: float, stop_loss: float, atr: float) -> float:
        return abs(_number(entry) - _number(stop_loss)) / max(_number(atr), 1e-9)

    def _trend_stop_gate(
        self, *, entry: float, stop_loss: float, atr: float,
        entry_mode: str, breakout_level: float,
    ) -> tuple[str, float, Dict]:
        """Apply a graduated stop-distance gate without tightening structure SL.

        ``normal`` entries can trigger immediately.  A moderately distant
        continuation is converted to a breakout-retest plan at the original
        breakout level.  Larger distances are rejected until a new HL/LH is
        formed; the structural invalidation point is never moved closer just
        to satisfy the risk gate.
        """
        ratio = self._stop_risk_atr(entry, stop_loss, atr)
        normal = max(0.1, _number(self._param("trend_normal_stop_atr", 2.5)))
        retest = max(normal, _number(self._param("trend_retest_stop_atr", 4.0)))
        maximum = max(retest, _number(self._param("trend_max_stop_atr", 6.0)))
        evidence = {
            "stop_distance": round(abs(entry - stop_loss), 8),
            "stop_distance_atr": round(ratio, 3),
            "normal_stop_atr": round(normal, 3),
            "retest_stop_atr": round(retest, 3),
            "maximum_stop_atr": round(maximum, 3),
            "risk_tier": "normal",
        }
        if ratio <= normal:
            return entry_mode, entry, evidence
        if ratio > maximum:
            evidence["risk_tier"] = "rejected"
            return "rejected", 0.0, evidence
        if ratio > retest:
            evidence["risk_tier"] = "new_structure_required"
            return "new_structure_required", 0.0, evidence
        if entry_mode == "trend_pullback_reclaim":
            evidence["risk_tier"] = "trend_pullback"
            return entry_mode, entry, evidence
        if entry_mode == "breakout_retest":
            evidence["risk_tier"] = "retest"
            return entry_mode, entry, evidence
        level = _number(breakout_level)
        if level <= 0:
            evidence["risk_tier"] = "new_structure_required"
            return "new_structure_required", 0.0, evidence
        retest_ratio = self._stop_risk_atr(level, stop_loss, atr)
        evidence.update({
            "risk_tier": "retest",
            "original_entry": round(entry, 8),
            "retest_entry": round(level, 8),
            "retest_stop_distance": round(abs(level - stop_loss), 8),
            "retest_stop_distance_atr": round(retest_ratio, 3),
        })
        if retest_ratio > retest:
            evidence["risk_tier"] = "new_structure_required"
            return "new_structure_required", 0.0, evidence
        return "breakout_retest", level, evidence

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
            "primary_structure": structure.get("primary_structure") or "transition",
            "internal_state": structure.get("internal_state"),
            "external_state": structure.get("external_state"),
            "trend_phase": structure.get("trend_phase", "undetermined"),
            "trend_phase_evidence": structure.get("trend_phase_evidence") or {},
            "trend_regime": structure.get("trend_regime") or "",
            "trend_regime_evidence": structure.get("trend_regime_evidence") or {},
            "range": {key: box.get(key) for key in (
                "active", "pattern", "status", "top", "bottom", "start_index",
                "high_touches", "low_touches", "inside_ratio", "width_atr",
                "breakout_direction", "high_slope", "low_slope",
            )},
            "structure_levels": self._hierarchy_snapshot(hierarchy),
            "structure_hierarchy": self._binding_snapshot(hierarchy),
            "internal_events": list(structure.get("internal_events") or [])[-3:],
            "major_events": list(structure.get("major_events") or [])[-3:],
            "external_events": list(structure.get("external_events") or [])[-3:],
            "structure_segment_id": structure.get("structure_segment_id") or "",
            "structure_revision": structure.get("structure_revision") or "",
            "active_segment": structure.get("active_segment") or {},
        }
        snapshot["structure_state"] = derive_structure_state(snapshot)
        # Evaluate range, event and location SETUPs independently. A watching
        # box breakout must not hide a valid HL pullback on another layer.
        range_plans = self._filter_allowed(self._range_plans(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        ))
        event_plans = self._filter_allowed(self._event_plans(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        ))
        location_plans = self._filter_allowed(self._location_plans(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        ))
        plans = self._select_structure_plans(range_plans, event_plans, location_plans)
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
        binding = self._setup_binding("structure_location_pullback")
        if not binding_matches(structure, binding):
            self._reject(
                f"趋势回撤要求入场层 {binding['entry_layer']} 仍是趋势，"
                f"当前为 {layer_pattern(structure, binding['entry_layer'])}"
            )
            return []
        major = str(structure.get("major_state") or structure.get("current_state") or "")
        layer_bias = self.structure_layers(structure).get(binding["direction_layer"])
        if layer_bias in {"up", "down"}:
            major = layer_bias
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
        expected_bias = "up" if direction == "buy" else "down"
        swing_bias = self._direction_bias(
            (hierarchy.get("swing") or {}).get("bias")
        )
        external_bias = self._direction_bias(
            (hierarchy.get("external") or {}).get("bias")
        )
        internal_bias = self._direction_bias(
            (hierarchy.get("internal") or {}).get("bias")
            or structure.get("internal_state")
        )
        if swing_bias != expected_bias:
            self._reject(
                f"趋势回撤要求方向层同向：计划={expected_bias}，Swing={swing_bias}"
            )
            return []
        if not self._external_allows(structure, expected_bias):
            self._reject(
                f"趋势回撤要求 External 同向：计划={expected_bias}，"
                f"External={external_bias}"
            )
            return []
        if self._param("location_require_internal_confirmation", True) and (
            internal_bias != expected_bias
        ):
            self._reject(
                f"Internal 当前为 {internal_bias}，等待向 {expected_bias} 的 "
                "CHOCH/BOS 确认回撤结束"
            )
            return []
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
        proximity = atr * max(0.05, _number(self._param("location_proximity_atr", 0.4)))
        candidates = self._location_candidates(rows, structure, direction)
        if not candidates:
            self._reject("当前趋势没有可用的已确认 HL/LH，保护点仅用于止损和失效判断")
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
        # Once a completed candle has closed through the selected HL/LH, this
        # is no longer a valid pullback to that level.  Do not create a fresh
        # waiter from an already-broken location; a later rebound must wait
        # for a new structure plan.
        if (direction == "buy" and close < entry) or (
            direction == "sell" and close > entry
        ):
            self._reject(
                f"最新收盘已{'跌破' if direction == 'buy' else '突破'} "
                f"原始{'HL' if direction == 'buy' else 'LH'} {entry:.2f}，"
                "等待新的结构位置"
            )
            return []
        configured_entry_mode = str(self._param("entry_mode", "") or "").strip().lower()
        entry_mode = configured_entry_mode if configured_entry_mode in {
            "touch_or_near", "touch_and_reclaim"
        } else (
            "touch_and_reclaim"
            if self._param("require_location_reclaim", True) else "touch_or_near"
        )
        validation_evidence = {
            "expected_bias": expected_bias,
            "swing_bias": swing_bias,
            "external_bias": external_bias,
            "internal_bias": internal_bias,
            "entry_level_type": "HL" if direction == "buy" else "LH",
            "entry_level_source": level["source"],
            "location_entry_level": entry,
        }
        if entry_mode == "touch_and_reclaim":
            accepted, reclaim_evidence, rejection = self._location_reclaim_confirmation(
                rows, entry, direction, atr
            )
            validation_evidence["reclaim"] = reclaim_evidence
            validation_evidence["initial_reclaim_confirmed"] = accepted
            validation_evidence["initial_reclaim_rejection"] = rejection
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
        entry_text = (
            f"{entry:.5f}" if entry < 10 else f"{entry:.3f}" if entry < 1000 else f"{entry:.2f}"
        )
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
                f"{period} {'上涨' if direction == 'buy' else '下跌'}结构回撤："
                f"Swing/External 仍{'向上' if direction == 'buy' else '向下'}；"
                f"Internal 上次确认也还是{'上涨' if internal_bias == 'up' else '下跌' if internal_bias == 'down' else internal_bias}，"
                f"当前回踩 {level['source']} "
                f"{entry_text}，"
                f"收盘未破保护位所以还不算转{'空' if direction == 'buy' else '多'}；"
                f"等待收回后顺势{'买入' if direction == 'buy' else '卖出'}"
            ),
            valid_from=bar_time, expires_at=bar_time + seconds * valid_bars,
            invalidation_price=sl, structure_snapshot=snapshot,
            price_discovery=price_discovery,
            validation_evidence=validation_evidence,
        )
        return [plan] if plan else []

    def _range_plans(
        self, source_id, symbol, period, rows, structure, snapshot,
        bar_time, seconds,
    ) -> List[Dict]:
        box, box_layer = {}, ""
        false_box, false_layer = setup_box(structure, self._setup_binding("range_false_breakout"))
        if str(false_box.get("status") or "") == "failed_breakout":
            box, box_layer = false_box, false_layer
        else:
            for setup_type in (
                "range_breakout", "triangle_breakout",
                "range_lower_reversal", "triangle_prebreakout_pullback",
            ):
                candidate, layer = setup_box(structure, self._setup_binding(setup_type))
                if candidate:
                    box, box_layer = candidate, layer
                    break
        if not box:
            box = dict(structure.get("range") or {})
            box_layer = "swing" if box else ""
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

        if status == "failed_breakout" and self._param("enable_false_breakout", True) and self._setup_owns_layer("range_false_breakout", box_layer):
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
                validation_evidence={
                    "false_breakout_require_reclaim_close": bool(
                        self._param("false_breakout_require_reclaim_close", True)
                    ),
                    "false_breakout_confirmation_bars": max(
                        1, min(10, int(self._param("false_breakout_confirmation_bars", 1)))
                    ),
                    "false_breakout_min_reclaim_atr": max(
                        0.0, _number(self._param("false_breakout_min_reclaim_atr", 0.1))
                    ),
                },
            )
            return [plan] if plan else []

        if status == "breakout_confirmed" and self._param("enable_range_breakout", True) and (
            self._setup_owns_layer("range_breakout", box_layer)
            or self._setup_owns_layer("triangle_breakout", box_layer)
        ):
            direction = "buy" if box.get("breakout_direction") == "up" else "sell"
            is_triangle = "triangle" in pattern
            setup_type = "triangle_breakout" if is_triangle else "range_breakout"
            self._activate_setup(setup_type)
            validation_evidence = {}
            if is_triangle:
                accepted, validation_evidence, rejection = self._triangle_breakout_confirmation(
                    rows, structure, box, direction, atr,
                )
                if not accepted:
                    self._reject(rejection)
                    return []
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            entry = top if direction == "buy" else bottom
            stop_inside = atr * max(0.1, _number(self._param("breakout_stop_inside_atr", 0.3)))
            # A breakout-retest order is entered after the market has tested
            # the boundary. Protect the retest structure, not the original
            # breakout line, so a normal retest wick does not stop the trade.
            retest_bars = max(1, int(self._param("breakout_retest_valid_bars", 6)))
            retest_rows = rows[max(0, len(rows) - retest_bars):]
            retest_lows = [_number(item.get("low") or item.get("low_price")) for item in retest_rows]
            retest_highs = [_number(item.get("high") or item.get("high_price")) for item in retest_rows]
            retest_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            if direction == "buy" and any(value > 0 for value in retest_lows):
                sl = min(value for value in retest_lows if value > 0) - retest_buffer
            elif direction == "sell" and any(value > 0 for value in retest_highs):
                sl = max(value for value in retest_highs if value > 0) + retest_buffer
            else:
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
                setup_type=setup_type,
                direction=direction, entry_mode="breakout_retest", status="active",
                entry=entry, zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
                stop_loss=sl, take_profit=tp, confidence=min(95, confidence+5),
                reason=(
                    f"{period} {pattern}收盘确认向{('上' if direction == 'buy' else '下')}突破，"
                    + (
                        f"实体 {validation_evidence['body_atr']:.2f} ATR、"
                        f"收盘越界 {validation_evidence['close_extension_atr']:.2f} ATR，"
                        f"Swing/External 同向，等待回踩结构边界"
                        if is_triangle else "等待回踩结构边界"
                    )
                ),
                valid_from=bar_time,
                expires_at=bar_time + seconds * max(1, int(self._param("breakout_retest_valid_bars", 6))),
                invalidation_price=sl, structure_snapshot=snapshot,
                validation_evidence=validation_evidence,
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
        if pattern != "range" and str(structure.get("major_state") or structure.get("current_state")) in {"sideways", "range"} and self._param("enable_range_boundary", True) and self._setup_owns_layer("range_lower_reversal", box_layer):
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

        if self._param("enable_range_boundary", True) and self._setup_owns_layer("range_lower_reversal", box_layer):
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
        if self._param("enable_range_breakout", True) and self._setup_owns_layer("range_breakout_watch", box_layer):
            self._activate_setup("range_breakout_watch")
            for direction in ("buy", "sell"):
                expected_bias = "up" if direction == "buy" else "down"
                if self.counter_trend_reason(
                    direction, structure, "range_breakout_watch",
                    self._setup_overlay("range_breakout_watch"),
                ):
                    continue
                if not self._external_allows(structure, expected_bias, "range_breakout_watch"):
                    continue
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
        atr = max(1e-9, _number(structure.get("atr")))
        hierarchy = structure.get("structure_hierarchy") or {}
        swing = hierarchy.get("swing") or {}
        selected = self._select_bound_event(structure)
        if not selected:
            return []
        latest, event_setup = selected
        event_type = str(latest.get("type") or "")
        event_index = int(latest.get("confirmed_at", latest.get("index", -1)) or -1)
        age = len(rows)-1-event_index
        if event_type == "bos":
            swing_phase = str(swing.get("phase") or "")
            setup = (
                "structure_reversal"
                if swing_phase == "reversal_confirmed"
                else "trend_continuation"
            )
            self._activate_setup(setup)
            max_age_key = (
                "trend_max_event_age_bars_m1"
                if str(period).upper() == "M1"
                else "trend_max_event_age_bars_other"
            )
            max_age = max(1, int(self._param(
                max_age_key, 5 if str(period).upper() == "M1" else 3,
            )))
        else:
            setup = ""
            max_age = max(0, int(self._param("max_event_age_bars", 2)))
        if event_index < 0 or age < 0 or age > max_age:
            return []
        anchor = _bar_time(rows[event_index]) if event_index < len(rows) else bar_time

        if event_type == "choch" and self._param("enable_choch", True):
            self._activate_setup("choch_reversal")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            expires = bar_time + seconds * max(1, int(self._param("event_plan_valid_bars", 6)))
            direction_state = str(latest.get("direction") or "")
            if direction_state not in {"up", "down"}:
                self._reject("CHOCH 事件没有明确的反转方向")
                return []
            # A short-lived internal CHOCH inside a healthy higher-level up
            # trend is a pullback, not a tradable short reversal.  Requiring
            # the trend to fail (or the external structure to agree) avoids
            # selling every M1 retracement in an otherwise rising market.
            major_state = str(structure.get("major_state") or "").lower()
            trend_phase = str(structure.get("trend_phase") or "").lower()
            if (
                major_state in {"up", "down"}
                and direction_state != major_state
                and trend_phase in {"strong", "mature", "weakening"}
                and not self._external_allows(structure, direction_state, "choch_reversal")
            ):
                self._reject(
                    f"主结构仍为 {major_state}，当前 CHOCH={direction_state} 仅视为趋势内回撤，"
                    "等待主结构失效或更高层级确认后再做反转"
                )
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
            stop_ratio = self._stop_risk_atr(entry, sl, atr)
            max_stop_ratio = max(0.1, _number(
                self._param("choch_max_stop_atr", 3.0)
            ))
            if stop_ratio > max_stop_ratio:
                self._reject(
                    f"CHOCH 止损距离 {stop_ratio:.2f} ATR 超过上限 "
                    f"{max_stop_ratio:.2f} ATR，等待新的结构回踩"
                )
                return []
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
                validation_evidence={
                    "stop_distance": round(abs(entry - sl), 8),
                    "stop_distance_atr": round(stop_ratio, 3),
                    "maximum_stop_atr": round(max_stop_ratio, 3),
                    "risk_tier": "normal",
                },
            )
            return [plan] if plan else []

        if event_type == "liquidity_sweep" and self._param("enable_liquidity_sweep", True):
            self._activate_setup("liquidity_sweep_reclaim")
            entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
            stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
            target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
            expires = bar_time + seconds * max(1, int(self._param("event_plan_valid_bars", 6)))
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
            # 趋势和仍在抬高/走低的震荡通道里，扫单只做顺势回收：
            # 上涨或震荡上升只许扫低点买，下跌或震荡下降只许扫高点卖。
            blocked = self.counter_trend_reason(
                direction, structure, "liquidity_sweep_reclaim",
            )
            if blocked:
                self._reject(blocked)
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

        if event_type != "bos" or not self._param("enable_trend", True):
            return []
        direction_state = str(latest.get("direction") or "")
        major = str(structure.get("major_state") or "")
        trend_phase = str(structure.get("trend_phase") or "strong").lower()
        if self._param("trend_require_healthy_phase", True) and trend_phase == "failed":
            self._reject(
                f"趋势阶段为 {trend_phase}，推进力度衰减或保护点已失效，"
                "暂停趋势延续计划"
            )
            return []
        swing_bias = self._direction_bias(swing.get("bias") or major)
        if direction_state != major or swing_bias != major or major not in {"up", "down"}:
            self._reject("趋势延续要求主结构、Swing 与突破方向一致")
            return []
        displacement = _number(latest.get("displacement_atr"))
        period_key = "trend_min_breakout_displacement_atr_m1" if str(period).upper() == "M1" else "trend_min_breakout_displacement_atr_other"
        minimum = max(0.0, _number(self._param(
            period_key,
            self._param("min_breakout_displacement_atr", 0.6),
        )))
        if displacement < minimum:
            self._reject(f"趋势突破位移 {displacement:.2f} ATR 低于最低要求 {minimum:.2f} ATR")
            return []
        if str(latest.get("confirmation") or "") not in {
            "close_confirmed", "retest_confirmed", "continuation_confirmed",
        }:
            self._reject("趋势延续必须经过收盘突破确认")
            return []
        swing_pivots = [
            p for p in (swing.get("pivots") or [])
            if p.get("kind") == ("low" if direction_state == "up" else "high")
            and p.get("label") == ("HL" if direction_state == "up" else "LH")
        ]
        ascending_context = str(structure.get("trend_regime") or "").lower() == "ascending_range"
        if len(swing_pivots) >= 2:
            spacing = abs(
                _number(swing_pivots[-1].get("price"))
                - _number(swing_pivots[-2].get("price"))
            )
            pivot_ascending_context = (
                (
                    _number(swing_pivots[-1].get("price"))
                    > _number(swing_pivots[-2].get("price"))
                    if direction_state == "up" else
                    _number(swing_pivots[-1].get("price"))
                    < _number(swing_pivots[-2].get("price"))
                )
                and spacing >= max(0.1, _number(self._param("trend_hl_min_spacing_atr", 0.5))) * atr
            )
            ascending_context = ascending_context and pivot_ascending_context
        if trend_phase in {"mature", "weakening"} and ascending_context:
            entry_mode, entry, confirmation_evidence = self._ascending_pullback_confirmation(
                rows, structure, atr, direction_state,
            )
        else:
            # Flat/range background keeps the simpler BOS -> retest entry.
            entry_mode, entry, confirmation_evidence = self._trend_entry_confirmation(
                rows, latest, atr,
            )
        if not entry_mode or entry <= 0:
            self._reject("趋势延续尚未完成回踩确认或连续收盘站稳")
            return []
        mature_retest_only = bool(self._param("trend_mature_retest_only", True))
        chase_entry = entry_mode not in {"breakout_retest", "trend_pullback_reclaim"}
        if mature_retest_only and trend_phase in {"mature", "weakening"} and chase_entry:
            self._reject(
                f"趋势阶段为 {trend_phase}，已开启成熟趋势仅允许回踩，禁止突破后直接追入"
            )
            return []
        direction = "buy" if major == "up" else "sell"
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        expires = bar_time + seconds * max(1, int(self._param("event_plan_valid_bars", 6)))
        protected = self._protected_reference(hierarchy, direction, entry)
        if entry_mode == "trend_pullback_reclaim":
            if direction == "buy":
                protected = float(confirmation_evidence.get("pullback_level") or protected or 0)
            else:
                protected = float(confirmation_evidence.get("pullback_level") or protected or 0)
        if not protected or not entry:
            return []
        sl = protected-stop_buffer if direction == "buy" else protected+stop_buffer
        risk_entry_mode, risk_entry, risk_evidence = self._trend_stop_gate(
            entry=entry, stop_loss=sl, atr=atr, entry_mode=entry_mode,
            breakout_level=_number(latest.get("level")),
        )
        if risk_entry_mode == "rejected":
            self._reject(
                f"趋势延续止损距离 {risk_evidence['stop_distance_atr']:.2f} ATR "
                f"超过上限 {risk_evidence['maximum_stop_atr']:.2f} ATR，取消追价计划"
            )
            return []
        if risk_entry_mode == "new_structure_required":
            self._reject(
                f"趋势延续止损距离 {risk_evidence['stop_distance_atr']:.2f} ATR "
                f"过大，等待新的 HL/LH 后再生成计划"
            )
            return []
        if risk_entry_mode == "breakout_retest":
            entry_mode = risk_entry_mode
            entry = risk_entry
            entry_buffer = atr * max(
                0.0, _number(self._param("entry_zone_atr", 0.35))
            )
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        target = self._next_target(hierarchy, direction, entry)
        risk = abs(entry-sl)
        price_discovery = not bool(target)
        if price_discovery:
            target = entry+risk*2 if direction == "buy" else entry-risk*2
        elif direction == "buy":
            target -= target_buffer
        else:
            target += target_buffer
        confirmation_text = (
            "回踩突破位并守住"
            if entry_mode == "breakout_retest"
            else "回到上升/下降结构的 HL/LH 支撑区并完成方向回收"
            if entry_mode == "trend_pullback_reclaim"
            else f"连续 {confirmation_evidence.get('held_bars') or confirmation_evidence.get('required_hold_bars')} 根K线收在突破位外"
        )
        confirmation_evidence = dict(confirmation_evidence)
        confirmation_evidence["risk_gate"] = risk_evidence
        plan = self._tradable_plan(
            source_id=source_id, symbol=symbol, period=period, anchor=anchor,
            setup_type=setup, direction=direction, entry_mode=entry_mode,
            status="active", entry=entry,
            zone_lower=entry-entry_buffer, zone_upper=entry+entry_buffer,
            stop_loss=sl, take_profit=target,
            confidence=max(60, min(95, int(60+displacement*20))),
            reason=f"{period} {setup} BOS 已收盘确认，{confirmation_text}",
            valid_from=bar_time, expires_at=expires,
            invalidation_price=sl, structure_snapshot=snapshot,
            price_discovery=price_discovery,
            validation_evidence=confirmation_evidence,
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
        self._last_config_signature: Dict[tuple, str] = {}
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
            # Structure plans are generated from the canonical market-layer
            # config, not duplicated strategy parameters.  Strategy config is
            # only used later for execution filtering and risk management.
            resolved_config = resolve_structure_plan_config(symbol, period, "__builder__")
            setup_profiles = resolved_config.pop("_setup_profiles", []) if isinstance(resolved_config, dict) else []
            config_signature = hashlib.sha256(
                json.dumps(
                    {"config": resolved_config, "setup_profiles": setup_profiles},
                    sort_keys=True, ensure_ascii=False, default=str,
                ).encode("utf-8")
            ).hexdigest()
            if (self._last_bar.get(key) == bar_time
                    and self._last_config_signature.get(key) == config_signature):
                all_plans.extend(self._cache.get(key, []))
                continue
            result = structure or analyze(symbol, period, rows[-600:], resolved_config)
            plans = StructurePlanBuilder(
                resolved_config, setup_profiles=setup_profiles
            ).build(
                source_id, symbol, period, rows[-600:], result,
            )
            plans = self._resolve_plan_conflicts(plans)
            plans = self._apply_event_risk(plans, resolved_config, symbol, period, int(time.time()))
            close_price = _number(rows[-1].get("close") or rows[-1].get("close_price"))
            atr = _number((result or {}).get("atr"))
            dead_opportunity_ids = self._invalidate_stale_closed_plans(
                symbol, period, source_id, result, close_price, atr, plans,
            )
            if dead_opportunity_ids:
                # A frozen same-opportunity waiter can fail closed-bar checks
                # while the builder still emits the same opportunity_id. Drop
                # those ids so replace_scope cannot recreate the dead thesis.
                plans = [
                    plan for plan in plans
                    if str(plan.get("opportunity_id") or "") not in dead_opportunity_ids
                ]
            plans = self.repository.replace_scope(
                self.user_id, 0, "",
                source_id, symbol, period, plans, bar_time,
            ) or plans
            self._cache[key] = plans
            self._last_bar[key] = bar_time
            self._last_config_signature[key] = config_signature
            all_plans.extend(plans)
        return all_plans


    def _invalidate_stale_closed_plans(
        self, symbol: str, period: str, source_id: str, structure: Dict,
        close_price: float, atr: float, incoming_plans: List[Dict],
    ) -> set:
        """Retire waiting plans whose entry thesis died on this closed bar.

        Same-opportunity retention may keep plan_id/entry frozen, but every
        closed bar still re-checks protection, distance and entry thesis. Dead
        opportunity ids are returned so replace_scope cannot resurrect them.
        """
        current = self.repository.list_current(
            self.user_id, 0, "", source_id, symbol, period,
        )
        dead_opportunity_ids = set()
        period_name = str(period or "").upper()
        for plan in current:
            status = str(plan.get("status") or "")
            direction = str(plan.get("direction") or "")
            # Directional watching plans share the same retirement rules as
            # actionable waiters. Pure no_trade snapshots are observations only.
            if status not in {"active", "event_suppressed", "watching"}:
                continue
            if direction not in {"buy", "sell"}:
                continue
            plan_id = str(plan.get("plan_id") or "")
            opportunity_id = str(plan.get("opportunity_id") or "")
            plan = dict(plan)
            plan.setdefault("period", period_name)
            # Evaluate against the already-persisted outside streak, then decide
            # whether this close increments or clears it.
            probe = dict(plan)
            reason = close_invalidate_reason(probe, structure, close_price, atr)
            if not reason and not opportunity_still_valid(
                probe, structure, close_price, atr,
            ):
                reason = "买点已不再成立，取消等待中的结构计划"
            if reason:
                self.repository.invalidate_plan(plan_id, reason)
                if opportunity_id:
                    dead_opportunity_ids.add(opportunity_id)
                continue
            streak = next_outside_zone_closes(plan, close_price)
            changes = {"outside_zone_closes": streak, "period": period_name}
            if streak != int(plan.get("outside_zone_closes") or 0):
                plan.update(changes)
                self.repository.update_payload(plan_id, changes)
        return dead_opportunity_ids

    def _plans(self, symbol: str, strategy, config: Dict) -> List[Dict]:
        period = str(config.get("period") or "M5").upper()
        source_id = MARKET_STRUCTURE_PLAN_SOURCE_ID
        key = (source_id, str(symbol).upper(), period)
        # Every account/deployment owns a generator instance, while structure
        # plans are canonical for the user/source/symbol/period.  An instance
        # cache therefore cannot be authoritative: another account may refresh
        # the shared scope and supersede a plan that this generator previously
        # loaded.  Always pass through the repository's shared short TTL cache
        # so all engines converge on the same current plan set within seconds.
        plans = self.repository.list_current(
            self.user_id, 0, "",
            source_id, symbol, period,
        )
        # Historical density plans remain in the database for auditability,
        # but density is no longer an execution setup. Do not let legacy
        # rows reach tick triggering or account-level risk filters.
        plans = [
            plan for plan in plans
            if str(plan.get("setup_type") or "") not in LEGACY_NON_EXECUTION_SETUPS
        ]
        self._cache[key] = plans
        return plans

    def _latest_closed_bar(self, symbol: str, period: str, now: Optional[int] = None) -> Optional[Dict]:
        """Return the latest completed bar, never the currently forming bar."""
        rows = self.kline_store.get_all_klines(symbol, str(period or "M5").upper())
        if not rows:
            return None
        now = int(now or time.time())
        interval = PERIOD_SECONDS.get(str(period or "M5").upper(), 300)
        for row in reversed(rows):
            bar_time = _bar_time(row)
            if bar_time > 0 and bar_time + interval <= now:
                return row
        return None

    def _false_breakout_reclaim_confirmed(
        self, plan: Dict, closed_bar: Optional[Dict], effective_config: Dict,
    ) -> bool:
        """Require one or more completed closes clearly back inside the range."""
        evidence = plan.get("validation_evidence") or {}
        require_close = bool(effective_config.get(
            "false_breakout_require_reclaim_close",
            evidence.get("false_breakout_require_reclaim_close", True),
        ))
        if not require_close:
            return True
        if not closed_bar:
            return False
        direction = str(plan.get("direction") or "")
        entry = _number(plan.get("entry_price"))
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if direction not in {"buy", "sell"} or entry <= 0 or atr <= 0:
            return False
        close = _number(closed_bar.get("close") or closed_bar.get("close_price"))
        bar_time = _bar_time(closed_bar)
        if close <= 0 or bar_time <= 0:
            return False
        reclaim_distance = max(0.0, _number(effective_config.get(
            "false_breakout_min_reclaim_atr",
            evidence.get("false_breakout_min_reclaim_atr", 0.1),
        ))) * atr
        range_snapshot = (plan.get("structure_snapshot") or {}).get("range") or {}
        top = _number(range_snapshot.get("top"))
        bottom = _number(range_snapshot.get("bottom"))
        if direction == "sell":
            qualifies = close <= entry - reclaim_distance and (bottom <= 0 or close >= bottom)
        else:
            qualifies = close >= entry + reclaim_distance and (top <= 0 or close <= top)

        last_bar = int(plan.get("false_breakout_last_confirmation_bar") or 0)
        confirmed_bars = int(plan.get("false_breakout_confirmation_bars_seen") or 0)
        required_bars = max(1, min(10, int(effective_config.get(
            "false_breakout_confirmation_bars",
            evidence.get("false_breakout_confirmation_bars", 1),
        ))))
        if bar_time != last_bar:
            confirmed_bars = confirmed_bars + 1 if qualifies else 0
            changes = {
                "false_breakout_last_confirmation_bar": bar_time,
                "false_breakout_confirmation_bars_seen": confirmed_bars,
                "false_breakout_reclaim_confirmed": confirmed_bars >= required_bars,
            }
            plan.update(changes)
            self.repository.update_payload(plan.get("plan_id"), changes)
        return bool(plan.get("false_breakout_reclaim_confirmed"))

    def _location_entry_reclaim_confirmed(
        self, plan: Dict, closed_bar: Optional[Dict], effective_config: Dict,
    ) -> bool:
        """Re-check the latest completed reclaim candle at the actual entry."""
        if str(plan.get("entry_mode") or "") != "touch_and_reclaim":
            return True
        if not closed_bar:
            return False
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if entry <= 0 or atr <= 0 or direction not in {"buy", "sell"}:
            return False
        bar_time = _bar_time(closed_bar)
        if bar_time <= 0:
            return False
        last_bar = int(plan.get("location_entry_confirmation_bar") or 0)
        if bar_time == last_bar:
            return bool(plan.get("location_entry_reclaim_confirmed"))
        accepted, evidence, rejection = location_reclaim_confirmation(
            [closed_bar], entry, direction, atr,
            min_body_atr=max(0.0, _number(effective_config.get(
                "location_reclaim_min_body_atr", 0.5
            ))),
            min_close_extension_atr=max(0.0, _number(effective_config.get(
                "location_reclaim_min_close_extension_atr", 0.2
            ))),
        )
        changes = {
            "location_entry_confirmation_bar": bar_time,
            "location_entry_reclaim_confirmed": accepted,
            "location_entry_reclaim_evidence": evidence,
            "location_entry_reclaim_rejection": rejection,
        }
        plan.update(changes)
        self.repository.update_payload(plan.get("plan_id"), changes)
        return accepted

    def _triggered(
        self, plan: Dict, price: float, effective_config: Optional[Dict] = None,
        closed_bar: Optional[Dict] = None,
    ) -> bool:
        setup_type = str(plan.get("setup_type") or "")
        zone = plan.get("entry_zone") or {}
        lower, upper = _number(zone.get("lower")), _number(zone.get("upper"))
        if lower <= 0 or upper <= 0 or not lower <= price <= upper:
            reclaimed = str(plan.get("touch_state") or "") == "reclaimed" or str(
                plan.get("boundary_state") or ""
            ) == "triggered"
            far = bool(distance_invalidate_reason(plan, price))
            # A reclaim that has drifted well outside its zone must re-touch.
            if str(plan.get("entry_mode") or "") == "touch_and_reclaim" and (
                plan.get("touch_seen")
                or str(plan.get("touch_state") or "") in {"touched", "reclaimed"}
            ):
                if far:
                    changes = {
                        "touch_seen": False,
                        "touch_state": "unvisited",
                        "boundary_state": "left_boundary",
                    }
                    if setup_type == "range_false_breakout":
                        changes.update({
                            "false_breakout_last_confirmation_bar": 0,
                            "false_breakout_confirmation_bars_seen": 0,
                            "false_breakout_reclaim_confirmed": False,
                        })
                    elif setup_type == "structure_location_pullback":
                        changes.update({
                            "location_entry_confirmation_bar": 0,
                            "location_entry_reclaim_confirmed": False,
                            "location_entry_reclaim_evidence": {},
                            "location_entry_reclaim_rejection": "等待重新收盘确认",
                        })
                    plan.update(changes)
                    self._tick_state[str(plan.get("plan_id") or "")] = {"touched": False}
                    self.repository.update_payload(plan.get("plan_id"), changes)
                elif reclaimed and self._reclaim_fill_allowed(plan, price):
                    # Same-Tick reclaim may step just outside the zone.  Allow
                    # that overshoot only while price is still within 1 ATR of
                    # the sweep level; never chase a finished bounce.
                    return True
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") in {"touched", "reclaimed", "triggered"}:
                if price < lower or price > upper:
                    plan["boundary_state"] = "left_boundary"
                    self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "left_boundary"})
            return False
        mode = str(plan.get("entry_mode") or "")
        if mode in {"breakout_retest", "touch_or_near", "trend_pullback_reclaim"}:
            # Boundary state is progress metadata for the UI/lifecycle, not a
            # one-shot latch. A range plan must keep triggering while price
            # remains inside the entry zone until an account successfully
            # claims/orders it; otherwise a transient Tick marks it triggered
            # and every subsequent Tick refuses the same still-active plan.
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") != "triggered":
                plan["boundary_state"] = "triggered"
                self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "triggered"})
            return True
        if mode != "touch_and_reclaim":
            return False
        plan_id = str(plan.get("plan_id") or "")
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        if entry <= 0 or direction not in {"buy", "sell"}:
            return False
        # Persist touch progress on the plan itself. Process memory is only a
        # hot cache; restarts and multi-engine hosts must not lose "already
        # touched" evidence for an active reclaim waiter.
        memory = self._tick_state.setdefault(plan_id, {})
        touched = bool(
            memory.get("touched")
            or plan.get("touch_seen")
            or str(plan.get("touch_state") or "") in {"touched", "reclaimed"}
            or str(plan.get("boundary_state") or "") in {"touched", "reclaimed", "triggered"}
        )
        if direction == "buy" and price <= entry:
            touched = True
        elif direction == "sell" and price >= entry:
            touched = True
        if touched and not (
            memory.get("touched") or plan.get("touch_seen")
            or str(plan.get("touch_state") or "") in {"touched", "reclaimed"}
        ):
            changes = {
                "touch_seen": True,
                "touch_state": "touched",
                "touched_price": float(price),
                "boundary_state": "touched",
            }
            plan.update(changes)
            memory["touched"] = True
            self.repository.update_payload(plan_id, changes)
        else:
            memory["touched"] = bool(touched)
            plan["touch_seen"] = bool(touched)
        result = bool(touched and (
            (direction == "buy" and price >= entry)
            or (direction == "sell" and price <= entry)
        ))
        if result and setup_type == "range_false_breakout":
            if not self._false_breakout_reclaim_confirmed(
                plan, closed_bar, effective_config or {},
            ):
                return False
        if result and setup_type == "structure_location_pullback":
            if not self._location_entry_reclaim_confirmed(
                plan, closed_bar, effective_config or {},
            ):
                return False
        if result:
            changes = {
                "touch_seen": True,
                "touch_state": "reclaimed",
                "boundary_state": "triggered",
            }
            plan.update(changes)
            memory["touched"] = True
            self.repository.update_payload(plan_id, changes)
        return result

    @staticmethod
    def _resolve_plan_conflicts(plans: List[Dict]) -> List[Dict]:
        """Keep one direction when simultaneous active plans conflict."""
        return resolve_conflicts(plans)

    def _apply_event_risk(self, plans: List[Dict], config: Dict, symbol: str, period: str, now: int) -> List[Dict]:
        """Persist a visible pause instead of silently deleting reversal plans."""
        for plan in plans:
            event = active_event(config, symbol, period, str(plan.get("setup_type") or ""), now)
            if not event:
                if plan.get("status") == "event_suppressed":
                    restored = str(plan.get("status_before_event") or "active")
                    plan["status"] = restored if restored in {"active", "watching"} else "active"
                    plan.pop("event_risk", None)
                continue
            if plan.get("status") in {"active", "watching"}:
                plan["status_before_event"] = plan.get("status")
            plan["status"] = "event_suppressed"
            plan["plan_stage"] = "event_suppressed"
            plan["event_risk"] = event
            plan["reason"] = f"{plan.get('reason') or '结构交易计划'} · {event['reason']}，暂停触发"
        return plans

    def _event_invalidated(self, plan: Dict, price: float) -> str:
        """Evaluate cheap Tick-time invalidations from the persisted snapshot."""
        # invalidate_reason already includes the shared distance rule.
        return invalidate_reason(plan, price)


    def _reclaim_fill_allowed(self, plan: Dict, price: float) -> bool:
        """Allow a reclaim fill only near the swept level.

        In-zone fills stay valid.  A same-Tick step outside the zone is allowed
        up to 1 ATR from the frozen entry.  Without ATR, allow at most one
        entry-zone width so FX cannot fall back to a 0.8% chase.
        """
        entry = _number(plan.get("entry_price"))
        price = _number(price)
        if entry <= 0 or price <= 0:
            return False
        zone = plan.get("entry_zone") or {}
        lower, upper = _number(zone.get("lower")), _number(zone.get("upper"))
        if lower > 0 and upper > lower and lower <= price <= upper:
            return True
        distance = abs(price - entry)
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if atr > 0:
            return distance <= atr
        if lower > 0 and upper > lower:
            return distance <= abs(upper - lower)
        return False

    def _sweep_has_forward_target(self, plan: Dict, price: float) -> bool:
        """Reject a sweep reclaim that has already reached its nearest target."""
        direction = str(plan.get("direction") or "")
        price = _number(price)
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if direction not in {"buy", "sell"} or price <= 0:
            return True
        distances = []
        take_profit = _number(plan.get("take_profit"))
        if take_profit > 0:
            if direction == "buy" and take_profit > price:
                distances.append(take_profit - price)
            elif direction == "sell" and take_profit < price:
                distances.append(price - take_profit)
        for item in plan.get("target_candidates") or []:
            target = _number(item.get("price") if isinstance(item, dict) else 0)
            if direction == "buy" and target > price:
                distances.append(target - price)
            elif direction == "sell" and target < price:
                distances.append(price - target)
        if not distances:
            return True
        nearest = min(distances)
        if atr > 0:
            return nearest >= atr
        return nearest > 0

    def _tick_stop_gate(
        self, plan: Dict, price: float, effective_config: Optional[Dict] = None,
    ) -> tuple[bool, str]:
        """Re-check stop distance against the actual Tick trigger price."""
        setup = str(plan.get("setup_type") or "").strip().lower()
        snapshot = plan.get("structure_snapshot") or {}
        atr = _number(snapshot.get("atr"))
        stop = _number(plan.get("stop_loss"))
        price = _number(price)
        if price <= 0:
            return True, ""
        if setup == "liquidity_sweep_reclaim":
            if not self._reclaim_fill_allowed(plan, price):
                entry = _number(plan.get("entry_price"))
                distance = abs(price - entry) if entry > 0 else 0.0
                atr_text = f"{distance / atr:.2f} ATR" if atr > 0 else f"{distance:.5f}"
                return False, (
                    f"扫单回收触发价距计划入场 {atr_text}，"
                    "超过允许范围，等待重新回到入场区"
                )
            if not self._sweep_has_forward_target(plan, price):
                return False, "扫单回收前方结构目标不足 1 ATR，不在高点追入"
        if atr <= 0 or stop <= 0:
            return True, ""
        ratio = abs(price - stop) / atr
        effective_config = effective_config or {}
        risk_gate = (plan.get("validation_evidence") or {}).get("risk_gate") or {}
        if setup == "trend_continuation":
            normal = max(0.1, _number(
                risk_gate.get("normal_stop_atr",
                              effective_config.get("trend_normal_stop_atr", 2.5))
            ))
            maximum = max(3.0, _number(
                risk_gate.get("maximum_stop_atr",
                              effective_config.get("trend_max_stop_atr", 6.0))
            ))
            if ratio > maximum:
                return False, (
                    f"Tick触发价使趋势止损距离达到 {ratio:.2f} ATR，"
                    f"超过上限 {maximum:.2f} ATR"
                )
            if ratio > normal and str(plan.get("entry_mode") or "") != "breakout_retest":
                return False, (
                    f"Tick触发价距离结构保护点 {ratio:.2f} ATR，"
                    "趋势延续必须等待回踩确认"
                )
        elif setup == "choch_reversal":
            maximum = max(0.1, _number(
                risk_gate.get("maximum_stop_atr",
                              effective_config.get("choch_max_stop_atr", 3.0))
            ))
            if ratio > maximum:
                return False, (
                    f"Tick触发价使 CHOCH 止损距离达到 {ratio:.2f} ATR，"
                    f"超过上限 {maximum:.2f} ATR"
                )
        return True, ""

    def generate_signals_for_strategy(
        self, symbol: str, current_price: float, strategy,
    ) -> List[TradingSignal]:
        now = int(time.time())
        active, waiting = [], []
        seen_plan_ids = set()
        for config in strategy.get_signal_sources("structure_plan", enabled_only=True):
            params = dict(config.get("params") or {})
            period = str(config.get("period") or "M5").upper()
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
                setup_type = str(plan.get("setup_type") or "").strip().lower()
                effective_config = resolve_structure_plan_config(
                    symbol, period, setup_type
                ) if direction in {"buy", "sell"} else {}
                if direction in {"buy", "sell"}:
                    effective = effective_config
                    allowed_setups = {str(item).strip().lower() for item in (effective.get("allowed_setups") or []) if str(item).strip()}
                    effective_dirs = {str(item).strip().lower() for item in (effective.get("allowed_directions") or ["buy", "sell"]) if str(item).strip().lower() in {"buy", "sell"}}
                    blocked_setups = {str(item).strip().lower() for item in (effective.get("blocked_setups") or []) if str(item).strip()}
                    binding = resolve_binding(setup_type, effective)
                    snapshot = plan.get("structure_snapshot") or {}
                    if snapshot.get("structure_hierarchy") and not binding_matches(snapshot, binding):
                        continue
                    if (allowed_setups and setup_type not in allowed_setups) or setup_type in blocked_setups or not bool(effective.get("enabled", True)):
                        continue
                    if effective_dirs and direction not in effective_dirs:
                        continue
                    blocked_hours = {int(item) for item in (effective.get("blocked_hours") or []) if str(item).strip().isdigit()}
                    if blocked_hours:
                        beijing_hour = datetime.fromtimestamp(now, timezone.utc).astimezone(timezone(timedelta(hours=8))).hour
                        if beijing_hour in blocked_hours:
                            continue
                    event = active_event(effective, symbol, period, setup_type, now)
                    already_confirmed = (
                        str(plan.get("touch_state") or "") == "reclaimed"
                        or str(plan.get("boundary_state") or "") == "triggered"
                    )
                    if event and not already_confirmed:
                        suppress_plan = getattr(self.repository, "suppress_plan", None)
                        if suppress_plan:
                            suppress_plan(plan_id, event)
                        if plan.get("status") in {"active", "watching"}:
                            plan["status_before_event"] = plan.get("status")
                        plan["status"] = "event_suppressed"
                        plan["event_risk"] = event
                        waiting.append(plan)
                        continue
                    if plan.get("status") == "event_suppressed":
                        resume_plan = getattr(self.repository, "resume_plan", None)
                        restored = "active"
                        if resume_plan:
                            restored = resume_plan(plan_id) or "active"
                        else:
                            restored = str(plan.get("status_before_event") or "active")
                        plan["status"] = restored if restored in {"active", "watching"} else "active"
                        plan.pop("event_risk", None)
                if direction in {"buy", "sell"} and direction not in allowed_directions:
                    continue
                if direction not in {"buy", "sell"}:
                    continue
                blocked = StructurePlanBuilder.counter_trend_reason(
                    direction, plan.get("structure_snapshot") or {}, setup_type,
                )
                if blocked:
                    waiting.append(plan)
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
                stop_ok, stop_reason = self._tick_stop_gate(
                    plan, float(current_price), effective_config,
                )
                if not stop_ok:
                    if "超过上限" in stop_reason:
                        self.repository.invalidate_plan(plan_id, stop_reason)
                    else:
                        waiting.append(plan)
                    continue
                closed_bar = (
                    self._latest_closed_bar(symbol, period)
                    if setup_type in {"range_false_breakout", "structure_location_pullback"}
                    else None
                )
                if self._triggered(
                    plan, float(current_price), effective_config, closed_bar,
                ):
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
                trade_opportunity_id=str(plan.get("opportunity_id") or ""),
                trade_opportunity_stage=str(plan.get("opportunity_stage") or ""),
                trade_plan_valid_from=valid_from,
                trade_plan_expires_at=expires_at,
                created_at=datetime.now(),
                expires_at=datetime.fromtimestamp(expires_at) if expires_at else datetime.now()+timedelta(seconds=300),
            ))
            snapshot = plan.get("structure_snapshot") or {}
            signals[-1].atr = _number(snapshot.get("atr"))
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
