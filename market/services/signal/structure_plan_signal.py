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
from ..structure_events import (
    collect_structure_events, latest_event_for, decide_event_observations,
)
from ..structure_observation import advance_observation_state, build_observation_plans


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
    "confirmation_bars": 3,
    "confirmation_include_first_bar": True,
    "confirmation_higher_lows": True,
    "confirmation_lower_highs": True,
    "confirmation_min_body_atr": 0.5,
    "confirmation_min_close_extension_atr": 0.2,
    "confirmation_max_distance_atr": 0.6,
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
    "location_reclaim_confirmation_bars": 3,
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
    "choch_retest_confirmation_bars": 3,
    "choch_retest_min_body_atr": 0.2,
    "choch_retest_min_close_extension_atr": 0.05,
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



LEGACY_SETUP_NAMES = {
    "structure_location_pullback", "range_lower_reversal", "range_upper_reversal",
    "range_breakout", "range_false_breakout", "triangle_breakout",
    "triangle_breakout_watch", "triangle_prebreakout_pullback", "choch_reversal",
    "liquidity_sweep_reclaim", "trend_continuation", "structure_reversal",
    "range_breakout_watch", "pressure_reversal", "pressure_zone_breakout",
}


def _allowed_plan_types(raw) -> set:
    """Ignore retired SETUP whitelists so event-driven plan types can trade."""
    allowed = {str(item).strip().lower() for item in (raw or []) if str(item).strip()}
    if allowed and allowed <= LEGACY_SETUP_NAMES and len(allowed) >= 8:
        return set()
    return allowed


def setup_is_allowed(symbol: str, period: str, setup_type: str) -> bool:
    """Return the effective symbol/period/setup trading gate."""
    config = resolve_structure_plan_config(symbol, period, setup_type)
    allowed = _allowed_plan_types(config.get("allowed_setups"))
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
        allowed = _allowed_plan_types(self._base_params.get("allowed_setups"))
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

    def _trade_direction(self, direction: str) -> str:
        value = str(direction or "").strip().lower()
        if value in {"up", "buy", "long", "bullish"}:
            return "buy"
        if value in {"down", "sell", "short", "bearish"}:
            return "sell"
        return ""

    def _price_from_observation(
        self, observation: Dict, source_id, symbol, period, rows, structure, snapshot,
        bar_time, seconds,
    ) -> Optional[Dict]:
        """Price one accepted observation using the current structure snapshot."""
        plan_type = str(observation.get("plan_type") or "")
        event_type = str(observation.get("event_type") or "")
        layer = str(observation.get("event_layer") or "")
        direction = self._trade_direction(observation.get("direction"))
        if direction not in {"buy", "sell"}:
            return None
        events = snapshot.get("structure_events") or []
        event = next((item for item in events if str(item.get("event_id") or "") == str(observation.get("source_event_id") or "")), {})
        parent = next((item for item in events if str(item.get("event_id") or "") == str(observation.get("parent_event_id") or "")), {})
        level = _number(event.get("level") or parent.get("level"))
        if level <= 0:
            return None
        atr = max(1e-9, _number(structure.get("atr") or snapshot.get("atr")))
        hierarchy = structure.get("structure_hierarchy") or {}
        entry_buffer = atr * max(0.0, _number(self._param("entry_zone_atr", 0.35)))
        stop_buffer = atr * max(0.0, _number(self._param("stop_buffer_atr", 0.25)))
        target_buffer = atr * max(0.0, _number(self._param("target_buffer_atr", 0.1)))
        protected = self._protected_reference(hierarchy, direction, level)
        if direction == "buy":
            sl = (min(level, protected) if protected else level) - stop_buffer
        else:
            sl = (max(level, protected) if protected else level) + stop_buffer
        target = self._next_target(hierarchy, direction, level)
        price_discovery = not bool(target)
        risk = abs(level - sl)
        if price_discovery:
            target = level + risk if direction == "buy" else level - risk
        else:
            target = target - target_buffer if direction == "buy" else target + target_buffer
        confirmation = str(observation.get("required_confirmation") or "none")
        entry_mode = (
            "touch_and_reclaim" if "reclaim" in confirmation or event_type in {"reclaim", "liquidity_sweep"}
            else "breakout_retest" if event_type in {"bos", "choch"}
            else "touch_or_near"
        )
        setup_type = plan_type or "swing_pullback"
        self._activate_setup(setup_type)
        valid_bars = max(1, int(self._param("event_plan_valid_bars", 6)))
        plan = self._tradable_plan(
            source_id=source_id, symbol=symbol, period=period, anchor=bar_time,
            setup_type=setup_type, direction=direction, entry_mode=entry_mode,
            status="active", entry=level,
            zone_lower=level-entry_buffer, zone_upper=level+entry_buffer,
            stop_loss=sl, take_profit=target,
            min_risk_reward_override=self._param("trend_min_real_risk_reward", 0.5),
            confidence=72 if layer == "swing" else 66,
            reason=(
                f"{period} {layer.upper()} {event_type.upper()} 观察计划："
                f"{'买入' if direction == 'buy' else '卖出'} {level:.2f}，"
                f"后续确认 {confirmation}"
            ),
            valid_from=bar_time, expires_at=bar_time + seconds * valid_bars,
            invalidation_price=sl, structure_snapshot=snapshot,
            price_discovery=price_discovery,
            validation_evidence={
                "observation_plan_id": observation.get("observation_plan_id"),
                "event_chain": list(observation.get("event_chain") or []),
                "event_type": event_type,
                "event_layer": layer,
                "required_confirmation": confirmation,
            },
        )
        if not plan:
            return None
        plan["plan_type"] = plan_type
        plan["observation_plan_id"] = observation.get("observation_plan_id")
        plan["parent_event_id"] = observation.get("parent_event_id")
        plan["event_chain"] = list(observation.get("event_chain") or [])
        plan["required_confirmation"] = confirmation
        plan["confirmation_period"] = observation.get("confirmation_period")
        plan["source_event_id"] = observation.get("source_event_id")
        return plan

    def _plans_from_observations(
        self, source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
    ) -> List[Dict]:
        """Create executable plans only from accepted observation records."""
        ranked = []
        rank = {
            "create_plan": 0,
            "confirm_signal": 1,
            "update_plan": 2,
        }
        event_rank = {
            "choch": 0, "liquidity_sweep": 1, "bos": 2,
            "reclaim": 3, "hl_confirmed": 4, "lh_confirmed": 4,
            "retest": 5, "hl_support_touched": 6, "lh_press_touched": 6,
        }
        for observation in snapshot.get("observation_plans") or []:
            action = str(observation.get("matrix_action") or "")
            if action not in rank:
                continue
            ranked.append((
                rank[action],
                event_rank.get(str(observation.get("event_type") or ""), 9),
                0 if str(observation.get("event_layer") or "") == "swing" else 1,
                observation,
            ))
        selected = []
        seen = set()
        for _action, _event, _layer, observation in sorted(ranked, key=lambda item: item[:3]):
            family = str(observation.get("plan_type") or "")
            if family == "event_confirmation":
                family = "pullback"
            elif family.endswith("pullback"):
                family = "pullback"
            elif family in {"structure_reversal", "early_reversal"}:
                family = "reversal"
            key = str(observation.get("event_layer") or "")
            if key in seen:
                continue
            plan = self._price_from_observation(
                observation, source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
            )
            if not plan:
                continue
            seen.add(key)
            selected.append(plan)
        return selected

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
        if setup in {
            "choch_reversal", "range_false_breakout", "liquidity_sweep_reclaim",
            "early_reversal", "structure_reversal", "liquidity_reversal",
        }:
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
            confirmation_bars=max(1, int(self._param("location_reclaim_confirmation_bars", 3))),
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
        # RETEST and RECLAIM are the canonical observation categories.  The
        # legacy setup label is retained only inside this execution adapter so
        # existing position management can finish its migration without
        # creating a second concept in the event model.
        payload["observation_type"] = (
            "reclaim" if setup_type in {
                "range_false_breakout", "range_lower_reversal", "range_upper_reversal",
            } else "retest" if setup_type in {
                "structure_location_pullback", "triangle_prebreakout_pullback",
                "range_breakout", "triangle_breakout",
            } else "event"
        )
        payload["observation_state"] = (
            "watching" if status in {"active", "watching", "event_suppressed"} else
            str(status or "watching")
        )
        binding = self._setup_binding(setup_type)
        event_layer = str(binding.get("event_layer") or binding.get("entry_layer") or "").lower()
        event_type = str(binding.get("bind_event") or "").lower()
        source_event = latest_event_for(
            (structure_snapshot or {}).get("structure_events") or [],
            layer=event_layer, event_type=event_type,
        )
        source_event_id = str((source_event or {}).get("event_id") or "")
        event_decision = next(
            (item for item in (structure_snapshot or {}).get("event_decisions") or []
             if str(item.get("event_id") or "") == source_event_id),
            {},
        )
        observation_plan = next(
            (item for item in (structure_snapshot or {}).get("observation_plans") or []
             if str(item.get("plan_type") or "") == str(event_decision.get("plan_type") or "")
             and str(item.get("direction") or "") in {"", str(direction or "")}),
            {},
        )
        legacy_plan_types = {
            "structure_location_pullback": "swing_pullback",
            "range_lower_reversal": "range_reclaim",
            "range_upper_reversal": "range_reclaim",
            "range_false_breakout": "liquidity_reversal",
            "triangle_prebreakout_pullback": "swing_pullback",
            "range_breakout": "trend_continuation",
            "triangle_breakout": "trend_continuation",
            "choch_reversal": "structure_reversal",
            "structure_reversal": "structure_reversal",
            "trend_continuation": "trend_continuation",
        }
        canonical_plan_type = str(
            event_decision.get("plan_type")
            or legacy_plan_types.get(setup_type)
            or payload.get("observation_type")
            or setup_type
        )
        payload.update({
            "event_layer": event_layer,
            "event_type": event_type,
            "direction_layer": str(binding.get("direction_layer") or "").lower(),
            "entry_layer": str(binding.get("entry_layer") or event_layer).lower(),
            "source_event_id": source_event_id,
            "source_event": source_event or {},
            "plan_type": canonical_plan_type,
            "matrix_action": str(event_decision.get("matrix_action") or "create_plan"),
            "required_confirmation": str(event_decision.get("required_confirmation") or "none"),
            "observation_plan_id": str(observation_plan.get("observation_plan_id") or ""),
            "parent_event_id": str(observation_plan.get("parent_event_id") or ""),
            "confirmation_type": str(observation_plan.get("required_confirmation") or event_decision.get("required_confirmation") or "none"),
            "confirmation_period": str(observation_plan.get("confirmation_period") or period).upper(),
            "confirmation_bars_required": max(1, int(self._param("confirmation_bars", 3))),
            "confirmation_bars_seen": 0,
            "event_chain": [
                f"{event_layer}:{event_type}" if event_layer and event_type else setup_type,
            ],
        })
        if observation_plan.get("event_chain"):
            payload["event_chain"] = list(observation_plan["event_chain"])
        if setup_type in {"structure_location_pullback", "range_lower_reversal", "range_upper_reversal", "range_breakout", "triangle_breakout"}:
            reference = str((evidence or {}).get("entry_level_type") or "").upper()
            if reference in {"HL", "LH"}:
                payload["event_chain"] = [
                    f"{event_layer}:{'hl_confirmed' if reference == 'HL' else 'lh_confirmed'}",
                    f"{event_layer}:{'hl_support_touched' if reference == 'HL' else 'lh_press_touched'}",
                    f"{event_layer}:retest",
                ]
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
        if "liquidity" in setup_type or "sweep" in setup_type:
            return "liquidity"
        if "reversal" in setup_type:
            return "reversal"
        if "pullback" in setup_type:
            return "pullback"
        if setup_type == "event_confirmation":
            return "pullback"
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
        if setup_type in {
            "structure_location_pullback", "trend_continuation", "structure_reversal",
            "early_reversal", "swing_pullback", "internal_pullback", "liquidity_reversal",
            "internal_momentum",
        }:
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
        skip_external = setup_type in {
            "early_reversal", "structure_reversal", "liquidity_reversal",
            "internal_momentum", "range_reclaim", "event_confirmation",
        }
        if expected_bias and not skip_external and not self._external_allows(snapshot, expected_bias, setup_type):
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
            "structure_events": collect_structure_events({
                **structure,
                "latest_close": _number(rows[-1].get("close") or rows[-1].get("close_price")),
                "retest_proximity_atr": self._param("location_proximity_atr", 0.4),
            }, symbol, period),
            "structure_segment_id": structure.get("structure_segment_id") or "",
            "structure_revision": structure.get("structure_revision") or "",
            "active_segment": structure.get("active_segment") or {},
            "event_matrix": self._param("event_matrix", []),
        }
        snapshot["event_decisions"] = decide_event_observations(
            snapshot, snapshot["structure_events"],
            require_external_alignment=bool(self._param("require_external_alignment", True)),
            matrix_overrides={
                str(item.get("event_key")): item for item in snapshot.get("event_matrix") or []
                if isinstance(item, dict) and item.get("event_key")
            },
        )
        snapshot["observation_plans"] = build_observation_plans(
            snapshot["structure_events"], snapshot["event_decisions"], period=period,
        )
        snapshot["structure_state"] = derive_structure_state(snapshot)
        plans = self._filter_allowed(self._plans_from_observations(
            source_id, symbol, period, rows, structure, snapshot, bar_time, seconds,
        ))
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
            structure_events = collect_structure_events(result, symbol, period)
            replace_events = getattr(self.repository, "replace_events", None)
            if replace_events:
                replace_events(self.user_id, symbol, period, structure_events)
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

    def _latest_closed_bars(
        self, symbol: str, period: str, count: int, now: Optional[int] = None,
    ) -> List[Dict]:
        """Return completed bars in chronological order, excluding the forming bar."""
        rows = self.kline_store.get_all_klines(symbol, str(period or "M5").upper())
        if not rows:
            return []
        now = int(now or time.time())
        interval = PERIOD_SECONDS.get(str(period or "M5").upper(), 300)
        completed = []
        for row in reversed(rows):
            bar_time = _bar_time(row)
            if bar_time > 0 and bar_time + interval <= now:
                completed.append(row)
                if len(completed) >= count:
                    break
        return list(reversed(completed))

    def _latest_closed_bar(self, symbol: str, period: str, now: Optional[int] = None) -> Optional[Dict]:
        rows = self._latest_closed_bars(symbol, period, 1, now)
        return rows[-1] if rows else None

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
        self, plan: Dict, closed_bars: List[Dict], effective_config: Dict,
    ) -> bool:
        """Re-check the completed reclaim sequence at the actual entry."""
        if not closed_bars:
            return False
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if entry <= 0 or atr <= 0 or direction not in {"buy", "sell"}:
            return False
        bar_time = _bar_time(closed_bars[-1])
        if bar_time <= 0:
            return False
        required_bars = max(1, int(
            plan.get("confirmation_bars_required")
            or effective_config.get("confirmation_bars")
            or effective_config.get("location_reclaim_confirmation_bars", 3)
        ))
        last_bar = int(plan.get("location_entry_confirmation_bar") or 0)
        if (bar_time == last_bar and int(plan.get(
            "location_entry_confirmation_bars_required") or 0
        ) == required_bars):
            return bool(plan.get("location_entry_reclaim_confirmed"))
        accepted, evidence, rejection = location_reclaim_confirmation(
            closed_bars, entry, direction, atr,
            min_body_atr=max(0.0, _number(effective_config.get(
                "confirmation_min_body_atr", effective_config.get("location_reclaim_min_body_atr", 0.5)
            ))),
            min_close_extension_atr=max(0.0, _number(effective_config.get(
                "confirmation_min_close_extension_atr", effective_config.get("location_reclaim_min_close_extension_atr", 0.2)
            ))),
            confirmation_bars=required_bars,
            require_touch=False,
        )
        changes = {
            "location_entry_confirmation_bar": bar_time,
            "location_entry_confirmation_bars_required": required_bars,
            "location_entry_reclaim_confirmed": accepted,
            "location_entry_reclaim_evidence": evidence,
            "location_entry_reclaim_rejection": rejection,
            "confirmation_type": "confirmation_sequence",
            "confirmation_bars_required": required_bars,
            "confirmation_bars_seen": int(evidence.get("confirmation_bars_seen") or 0),
            "confirmation_evidence": evidence,
            "confirmation_rejection": rejection,
        }
        plan.update(changes)
        self.repository.update_payload(plan.get("plan_id"), changes)
        return accepted

    def _choch_entry_retest_confirmed(
        self, plan: Dict, closed_bars: List[Dict], effective_config: Dict,
    ) -> bool:
        """Require a CHOCH retest and two directional closes before entry."""
        if not closed_bars:
            return False
        entry = _number(plan.get("entry_price"))
        direction = str(plan.get("direction") or "")
        atr = _number((plan.get("structure_snapshot") or {}).get("atr"))
        if entry <= 0 or atr <= 0 or direction not in {"buy", "sell"}:
            return False
        required = max(1, int(effective_config.get(
            "choch_retest_confirmation_bars", 3
        )))
        bar_time = _bar_time(closed_bars[-1])
        if bar_time <= 0:
            return False
        last_bar = int(plan.get("choch_retest_confirmation_bar") or 0)
        if (bar_time == last_bar and int(plan.get(
            "choch_retest_confirmation_bars_required") or 0
        ) == required):
            return bool(plan.get("choch_retest_confirmed"))
        accepted, evidence, rejection = location_reclaim_confirmation(
            closed_bars, entry, direction, atr,
            min_body_atr=max(0.0, _number(effective_config.get(
                "choch_retest_min_body_atr", 0.2
            ))),
            min_close_extension_atr=max(0.0, _number(effective_config.get(
                "choch_retest_min_close_extension_atr", 0.05
            ))),
            confirmation_bars=required,
            require_touch=True,
        )
        changes = {
            "choch_retest_confirmation_bar": bar_time,
            "choch_retest_confirmation_bars_required": required,
            "choch_retest_confirmed": accepted,
            "choch_retest_evidence": evidence,
            "choch_retest_rejection": rejection,
        }
        plan.update(changes)
        self.repository.update_payload(plan.get("plan_id"), changes)
        return accepted

    def _triggered(
        self, plan: Dict, price: float, effective_config: Optional[Dict] = None,
        closed_bar: Optional[Dict | List[Dict]] = None,
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
                        "observation_state": "watching",
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
                            "location_entry_confirmation_bars_required": 0,
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
                    if setup_type == "structure_location_pullback" and not self._location_entry_reclaim_confirmed(
                        plan, closed_bar if isinstance(closed_bar, list) else [closed_bar] if closed_bar else [],
                        effective_config or {},
                    ):
                        return False
                    return True
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") in {"touched", "reclaimed", "triggered"}:
                if price < lower or price > upper:
                    plan["boundary_state"] = "left_boundary"
                    self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "left_boundary"})
            return False
        mode = str(plan.get("entry_mode") or "")
        if mode in {"breakout_retest", "touch_or_near", "trend_pullback_reclaim"} and setup_type != "choch_reversal":
            # Boundary state is progress metadata for the UI/lifecycle, not a
            # one-shot latch. A range plan must keep triggering while price
            # remains inside the entry zone until an account successfully
            # claims/orders it; otherwise a transient Tick marks it triggered
            # and every subsequent Tick refuses the same still-active plan.
            if str(plan.get("setup_type") or "").startswith("range_") and str(plan.get("boundary_state") or "") != "triggered":
                plan["boundary_state"] = "triggered"
                self.repository.update_payload(plan.get("plan_id"), {"boundary_state": "triggered"})
            confirmation = str(plan.get("required_confirmation") or plan.get("confirmation_type") or "")
            plan_type = str(plan.get("plan_type") or setup_type)
            needs_sequence = confirmation in {
                "confirmation_sequence", "hl_retest", "lh_retest",
                "retest_or_reclaim", "retest_or_sequence",
            } or plan_type in {
                "swing_pullback", "internal_pullback", "early_reversal",
                "structure_reversal", "liquidity_reversal", "event_confirmation",
                "range_reclaim", "internal_momentum", "trend_continuation",
                "range_breakout",
            } or setup_type in {
                "range_false_breakout", "structure_location_pullback", "choch_reversal",
            }
            if not needs_sequence:
                return True
            bars = closed_bar if isinstance(closed_bar, list) else [closed_bar] if closed_bar else []
            if not self._location_entry_reclaim_confirmed(plan, bars, effective_config or {}):
                return False
            return True
        if mode not in {"touch_and_reclaim", "breakout_retest"}:
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
            advance_observation_state(plan, touched=True)
            changes["observation_state"] = plan["observation_state"]
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
        plan_type = str(plan.get("plan_type") or setup_type)
        confirmation = str(plan.get("required_confirmation") or plan.get("confirmation_type") or "")
        needs_sequence = confirmation in {
            "confirmation_sequence", "hl_retest", "lh_retest",
            "retest_or_reclaim", "retest_or_sequence",
        } or plan_type in {
            "swing_pullback", "internal_pullback", "early_reversal",
            "structure_reversal", "liquidity_reversal", "event_confirmation",
            "range_reclaim", "internal_momentum", "trend_continuation",
            "range_breakout",
        } or setup_type in {
            "range_false_breakout", "structure_location_pullback", "choch_reversal",
        }
        if result and needs_sequence:
            bars = closed_bar if isinstance(closed_bar, list) else [closed_bar] if closed_bar else []
            if plan_type == "range_reclaim" or setup_type == "range_false_breakout":
                if not self._false_breakout_reclaim_confirmed(plan, bars[-1] if bars else None, effective_config or {}):
                    return False
            elif not self._location_entry_reclaim_confirmed(plan, bars, effective_config or {}):
                return False
        if result:
            changes = {
                "touch_seen": True,
                "touch_state": "reclaimed",
                "boundary_state": "triggered",
            }
            advance_observation_state(plan, confirmed=True, triggered=True)
            changes["observation_state"] = plan["observation_state"]
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
                    allowed_setups = _allowed_plan_types(effective.get("allowed_setups"))
                    effective_dirs = {str(item).strip().lower() for item in (effective.get("allowed_directions") or ["buy", "sell"]) if str(item).strip().lower() in {"buy", "sell"}}
                    blocked_setups = {str(item).strip().lower() for item in (effective.get("blocked_setups") or []) if str(item).strip()}
                    binding = resolve_binding(setup_type, effective)
                    snapshot = plan.get("structure_snapshot") or {}
                    event_driven = bool(
                        plan.get("event_chain")
                        or plan.get("observation_plan_id")
                        or plan.get("required_confirmation")
                    )
                    if (not event_driven and snapshot.get("structure_hierarchy")
                            and not binding_matches(snapshot, binding)):
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
                closed_bar = None
                confirmation = str(plan.get("required_confirmation") or plan.get("confirmation_type") or "")
                plan_type = str(plan.get("plan_type") or setup_type)
                needs_sequence = confirmation in {
                    "confirmation_sequence", "hl_retest", "lh_retest",
                    "retest_or_reclaim", "retest_or_sequence",
                } or plan_type in {
                    "swing_pullback", "internal_pullback", "early_reversal",
                    "structure_reversal", "liquidity_reversal", "event_confirmation",
                    "range_reclaim", "internal_momentum", "trend_continuation",
                    "range_breakout",
                } or setup_type in {
                    "structure_location_pullback", "choch_reversal",
                    "range_false_breakout",
                }
                if needs_sequence:
                    required = max(1, int(plan.get("confirmation_bars_required") or effective_config.get(
                        "confirmation_bars", effective_config.get("location_reclaim_confirmation_bars", 3)
                    )))
                    closed_bar = self._latest_closed_bars(symbol, period, required)
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
                setup_type=str(plan.get("plan_type") or plan.get("setup_type") or "structure_plan"),
                plan_type=str(plan.get("plan_type") or plan.get("setup_type") or ""),
                event_chain=list(plan.get("event_chain") or []),
                event_layer=str(plan.get("event_layer") or ""),
                event_type=str(plan.get("event_type") or ""),
                pattern=str(((plan.get("structure_snapshot") or {}).get("event_decisions") or [{}])[-1].get("pattern") or ((plan.get("validation_evidence") or {}).get("pattern") or "")),
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
