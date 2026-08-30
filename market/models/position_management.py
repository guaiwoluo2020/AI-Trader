#!/usr/bin/env python3
"""Position management policy and runtime result models."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


RULE_TYPES = {
    "signal", "pivot", "atr", "fixed_points", "fixed_percent",
    "risk_reward", "none",
}
MANAGEMENT_RULE_TYPES = {
    "break_even", "pivot_trailing", "trailing_stop",
    "structure_trailing",
    "partial_take_profit", "reverse_signal", "max_holding_bars",
}
PERIODS = {"M1", "M5", "M15", "H1", "H4"}
SETUP_FAMILIES = {
    "range", "reversal", "breakout", "trend", "trend_follow",
    "pullback", "mean_reversion", "factor", "manual", "generic",
}
SIGNAL_SOURCES = {
    "ai_entry", "pivot", "key_level", "moving_average",
    "alpha_factor", "structure_plan", "manual",
}


def default_position_management_config() -> Dict:
    return {
        "management_mode": "standard",
        "multi_level_exit": {
            "disaster_stop_buffer_atr": 0.50,
            "stop_close_percent": {
                "internal": 30, "swing": 40, "external": 100,
            },
            "take_profit_close_percent": {
                "internal": 30, "swing": 30, "external": 100,
            },
        },
        "initial_stop_rules": [
            {"type": "pivot", "period": "M5", "selection": "nearest",
             "max_age_bars": 100,
             "buffer": {"type": "fixed_points", "value": 0}},
            {"type": "fixed_percent", "value": 0.003},
        ],
        "initial_take_profit_rules": [
            {"type": "risk_reward", "value": 2.0},
        ],
        "management_rules": [
            {"type": "break_even", "activation_r": 1.0, "offset_r": 0.0},
            {"type": "pivot_trailing", "period": "M5",
             "buffer": {"type": "fixed_points", "value": 0}},
            {"type": "structure_trailing", "structure_layer": "swing",
             "buffer_type": "atr", "buffer_value": 0.15,
             "min_improvement_atr": 0.10, "confirm_bars": 1,
             "cooldown_seconds": 30},
            {"type": "trailing_stop", "activation_r": 1.0,
             "distance_r": 0.8},
            {"type": "partial_take_profit", "levels": [
                {"trigger_r": 1.0, "close_percent": 30,
                 "move_sl": "break_even"},
                {"trigger_r": 2.0, "close_percent": 30,
                 "move_sl": "trail"},
            ]},
        ],
        "min_risk_reward": 1.0,
        "min_stop_percent": 0.1,
        "max_stop_percent": 0.7,
        "min_stop_distance": 0.0,
        "max_stop_distance": 0.0,
        # Per deployment circuit breaker: completed losing positions only.
        # It blocks fresh entries, never existing-position protection.
        "loss_streak_circuit_breaker_enabled": True,
        "loss_streak_limit": 3,
        # 默认连续亏损熔断暂停 10 分钟；可在持仓管理方案中调整。
        "loss_streak_pause_minutes": 10,
        "setup_profiles": [],
    }


def _positive(value, label: str, allow_zero: bool = False) -> float:
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{label}必须大于{'等于' if allow_zero else ''}0")
    return number


def normalize_position_management_config(config: Optional[Dict]) -> Dict:
    normalized = copy.deepcopy(config or default_position_management_config())
    mode = str(normalized.get("management_mode") or "standard").strip().lower()
    normalized["management_mode"] = (
        "multi_level_exit" if mode == "multi_level_exit" else "standard"
    )
    multi = copy.deepcopy(normalized.get("multi_level_exit") or {})
    multi["disaster_stop_buffer_atr"] = _positive(
        multi.get("disaster_stop_buffer_atr", 0.50), "灾难止损ATR缓冲", True
    )
    for key, defaults in (
        ("stop_close_percent", {"internal": 30, "swing": 40, "external": 100}),
        ("take_profit_close_percent", {"internal": 30, "swing": 30, "external": 100}),
    ):
        values = copy.deepcopy(multi.get(key) or {})
        for layer, default in defaults.items():
            values[layer] = min(100.0, max(
                0.0, float(values.get(layer, default) or 0)
            ))
        multi[key] = values
    normalized["multi_level_exit"] = multi
    stop_rules = list(normalized.get("initial_stop_rules") or [])
    take_rules = list(normalized.get("initial_take_profit_rules") or [])
    management_rules = list(normalized.get("management_rules") or [])
    if not stop_rules:
        raise ValueError("至少需要一条初始止损规则")
    if not take_rules:
        raise ValueError("至少需要一条初始止盈规则")

    for group, rules in (("初始止损", stop_rules), ("初始止盈", take_rules)):
        for rule in rules:
            rule_type = str(rule.get("type", "")).strip()
            if rule_type not in RULE_TYPES:
                raise ValueError(f"{group}包含不支持的规则: {rule_type}")
            rule["type"] = rule_type
            if rule_type == "pivot":
                period = str(rule.get("period", "M5")).upper()
                if period not in PERIODS:
                    raise ValueError(f"转折点周期无效: {period}")
                rule["period"] = period
                rule["max_age_bars"] = max(1, int(rule.get("max_age_bars", 100)))
            if rule_type in {"fixed_points", "fixed_percent", "atr", "risk_reward"}:
                rule["value"] = _positive(rule.get("value", 0), f"{group}规则值")

    for rule in management_rules:
        rule_type = str(rule.get("type", "")).strip()
        if rule_type not in MANAGEMENT_RULE_TYPES:
            raise ValueError(f"包含不支持的持仓管理规则: {rule_type}")
        rule["type"] = rule_type
        rule["enabled"] = bool(rule.get("enabled", True))
        if rule_type == "pivot_trailing":
            period = str(rule.get("period", "M5")).upper()
            if period not in PERIODS:
                raise ValueError(f"转折点周期无效: {period}")
            rule["period"] = period
        elif rule_type == "structure_trailing":
            rule["structure_layer"] = str(rule.get("structure_layer") or "swing").lower()
            if rule["structure_layer"] not in {"internal", "swing", "external"}:
                raise ValueError("结构移动止损层级无效")
            rule["buffer_type"] = str(rule.get("buffer_type") or "atr").lower()
            if rule["buffer_type"] not in {"atr", "fixed_points", "fixed_percent"}:
                raise ValueError("结构移动止损缓冲类型无效")
            rule["buffer_value"] = _positive(rule.get("buffer_value", 0.15), "结构止损缓冲")
            rule["min_improvement_atr"] = _positive(rule.get("min_improvement_atr", 0.10), "结构止损最小改善")
            rule["confirm_bars"] = max(1, min(10, int(rule.get("confirm_bars", 1))))
            rule["cooldown_seconds"] = max(0, min(86400, int(rule.get("cooldown_seconds", 30))))
        elif rule_type == "break_even":
            rule["activation_r"] = _positive(rule.get("activation_r", 1), "保本启动R")
            rule["offset_r"] = _positive(rule.get("offset_r", 0), "保本偏移R", True)
        elif rule_type == "trailing_stop":
            rule["activation_r"] = _positive(rule.get("activation_r", 1.0), "移动止损启动R")
            rule["distance_r"] = _positive(rule.get("distance_r", 0.8), "移动止损距离R")
        elif rule_type == "partial_take_profit":
            levels = []
            for index, level in enumerate(rule.get("levels") or [], start=1):
                trigger_r = _positive(
                    level.get("trigger_r", index), "分批止盈触发R"
                )
                close_percent = _positive(
                    level.get("close_percent", 30), "分批止盈比例"
                )
                if close_percent > 100:
                    raise ValueError("分批止盈比例不能超过100%")
                move_sl = str(level.get("move_sl", "none")).strip()
                if move_sl not in {"none", "break_even", "trail"}:
                    move_sl = "none"
                levels.append({
                    "level_id": str(level.get("level_id") or f"tp{index}"),
                    "trigger_r": trigger_r,
                    "close_percent": close_percent,
                    "move_sl": move_sl,
                })
            rule["levels"] = sorted(levels, key=lambda item: item["trigger_r"])
        elif rule_type == "max_holding_bars":
            rule["period"] = str(rule.get("period", "M1")).upper()
            rule["bars"] = max(1, int(rule.get("bars", 1)))

    normalized["initial_stop_rules"] = stop_rules
    normalized["initial_take_profit_rules"] = take_rules
    normalized["management_rules"] = management_rules
    normalized["min_risk_reward"] = _positive(
        normalized.get("min_risk_reward", 1), "最小盈亏比", True
    )
    normalized["min_stop_percent"] = _positive(
        normalized.get("min_stop_percent", 0.1), "最小止损比例", True
    )
    normalized["max_stop_percent"] = _positive(
        normalized.get("max_stop_percent", 0.7), "最大止损比例", True
    )
    normalized["min_stop_distance"] = _positive(
        normalized.get("min_stop_distance", 0), "最小止损距离", True
    )
    normalized["max_stop_distance"] = _positive(
        normalized.get("max_stop_distance", 0), "最大止损距离", True
    )
    normalized["loss_streak_circuit_breaker_enabled"] = bool(
        normalized.get("loss_streak_circuit_breaker_enabled", True)
    )
    normalized["loss_streak_limit"] = max(
        1, min(20, int(normalized.get("loss_streak_limit", 3) or 3))
    )
    normalized["loss_streak_pause_minutes"] = max(
        1, min(24 * 60, int(normalized.get("loss_streak_pause_minutes", 10) or 10))
    )
    if "signal_take_profit_close_percent" in normalized:
        normalized["signal_take_profit_close_percent"] = min(
            100.0,
            max(0.0, float(normalized.get("signal_take_profit_close_percent", 0))),
        )
    if (normalized["max_stop_percent"] > 0
            and normalized["max_stop_percent"] < normalized["min_stop_percent"]):
        raise ValueError("最大止损距离不能小于最小止损距离")
    profiles = []
    base_config = {
        key: copy.deepcopy(value)
        for key, value in normalized.items() if key != "setup_profiles"
    }
    for index, raw in enumerate((config or {}).get("setup_profiles") or []):
        if not isinstance(raw, dict):
            continue
        match = raw.get("match") or {}
        setup_types = sorted({
            str(item).strip().lower() for item in match.get("setup_types") or []
            if str(item).strip()
        })
        setup_families = sorted({
            str(item).strip().lower() for item in match.get("setup_families") or []
            if str(item).strip().lower() in SETUP_FAMILIES
        })
        signal_sources = sorted({
            str(item).strip().lower() for item in match.get("signal_sources") or []
            if str(item).strip().lower() in SIGNAL_SOURCES
        })
        if not setup_types and not setup_families and not signal_sources:
            raise ValueError(f"场景规则 {index + 1} 至少需要一个匹配条件")
        overrides = copy.deepcopy(raw.get("overrides") or {})
        allowed_override_keys = {
            "initial_stop_rules", "initial_take_profit_rules",
            "management_rules", "min_risk_reward",
            "min_stop_percent", "max_stop_percent",
            "min_stop_distance", "max_stop_distance",
            "management_mode", "multi_level_exit",
        }
        overrides = {
            key: value for key, value in overrides.items()
            if key in allowed_override_keys
        }
        merged = copy.deepcopy(base_config)
        merged.update(overrides)
        validated = normalize_position_management_config({
            **merged, "setup_profiles": [],
        })
        normalized_overrides = {
            key: copy.deepcopy(validated[key]) for key in overrides
        }
        profiles.append({
            "profile_id": str(raw.get("profile_id") or uuid.uuid4().hex[:12]),
            "name": str(raw.get("name") or f"场景规则 {index + 1}").strip(),
            "enabled": bool(raw.get("enabled", True)),
            "priority": max(0, min(1000, int(raw.get("priority", 100)))),
            "inherit_default": bool(raw.get("inherit_default", True)),
            "parameter_mode": (
                "custom" if str(raw.get("parameter_mode") or "").lower() == "custom"
                else "recommended"
            ),
            "match": {
                "setup_types": setup_types,
                "setup_families": setup_families,
                "signal_sources": signal_sources,
            },
            "overrides": normalized_overrides,
        })
    normalized["setup_profiles"] = profiles
    return normalized


def resolve_position_management_config(
    config: Dict, setup_context: Optional[Dict] = None,
) -> tuple[Dict, Optional[Dict]]:
    """Resolve exact setup, family, source, then the unchanged default rules."""
    normalized = normalize_position_management_config(config)
    context = setup_context or {}
    setup_type = str(context.get("setup_type") or "generic_entry").lower()
    setup_family = str(context.get("setup_family") or "generic").lower()
    signal_source = str(context.get("signal_source") or "").lower()
    candidates = []
    for profile in normalized.get("setup_profiles") or []:
        if not profile.get("enabled", True):
            continue
        match = profile.get("match") or {}
        rank = 0
        if setup_type in match.get("setup_types", []):
            rank = 300
        elif setup_family in match.get("setup_families", []):
            rank = 200
        elif signal_source and signal_source in match.get("signal_sources", []):
            rank = 100
        if rank:
            candidates.append((rank, int(profile.get("priority", 0)), profile))
    selected = max(candidates, key=lambda item: (item[0], item[1]))[2] if candidates else None
    resolved = {
        key: copy.deepcopy(value)
        for key, value in normalized.items() if key != "setup_profiles"
    }
    if selected:
        if not selected.get("inherit_default", True):
            # Full-independent profiles were validated against the base for
            # required fields; fields explicitly supplied remain authoritative.
            resolved = {
                key: copy.deepcopy(value)
                for key, value in normalized.items() if key != "setup_profiles"
            }
        resolved.update(copy.deepcopy(selected.get("overrides") or {}))
    return resolved, copy.deepcopy(selected)


@dataclass
class PositionManagementPolicy:
    name: str
    user_id: int
    config: Dict = field(default_factory=default_position_management_config)
    policy_id: str = ""
    version: int = 1
    enabled: bool = True
    visibility: str = "private"
    source_policy_id: str = ""
    source_owner_user_id: int = 0
    source_owner_username: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValueError("持仓管理方案名称不能为空")
        self.policy_id = self.policy_id or uuid.uuid4().hex[:12]
        self.visibility = "shared" if self.visibility == "shared" else "private"
        self.source_policy_id = str(self.source_policy_id or "")
        self.source_owner_user_id = int(self.source_owner_user_id or 0)
        self.source_owner_username = str(self.source_owner_username or "")
        self.config = normalize_position_management_config(self.config)
        self.created_at = self.created_at or datetime.now()
        self.updated_at = self.updated_at or self.created_at

    def to_dict(self) -> Dict:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "user_id": self.user_id,
            "name": self.name,
            "enabled": self.enabled,
            "visibility": self.visibility,
            "is_shared": self.visibility == "shared",
            "readonly_reference": bool(self.source_owner_user_id),
            "source_policy_id": self.source_policy_id,
            "source_owner_user_id": self.source_owner_user_id,
            "source_owner_username": self.source_owner_username,
            "config": copy.deepcopy(self.config),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "PositionManagementPolicy":
        def parse(value):
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        return cls(
            policy_id=str(data.get("policy_id", "")),
            version=max(1, int(data.get("version", 1))),
            user_id=int(data.get("user_id", 0)),
            name=data.get("name", ""),
            enabled=bool(data.get("enabled", True)),
            visibility=(
                "shared"
                if data.get("visibility") == "shared" or data.get("is_shared")
                else "private"
            ),
            source_policy_id=str(data.get("source_policy_id", "")),
            source_owner_user_id=int(data.get("source_owner_user_id") or 0),
            source_owner_username=str(data.get("source_owner_username") or ""),
            config=data.get("config") or {},
            created_at=parse(data.get("created_at")),
            updated_at=parse(data.get("updated_at")),
        )


@dataclass
class PositionPlan:
    stop_loss: float
    take_profit: float
    initial_risk: float
    risk_reward: float
    policy_id: str
    policy_snapshot: Dict
    stop_rule: Dict
    take_profit_rule: Dict
    explanation: List[str] = field(default_factory=list)
    # When the signal-provided stop is tighter than the policy floor, the
    # runtime widens it to the floor and records the change for audit/UI.
    stop_adjustment: Optional[Dict] = None
    exit_levels: List[Dict] = field(default_factory=list)
    disaster_stop_loss: float = 0.0
    reference_take_profit: float = 0.0


@dataclass
class PositionAction:
    action: str = "none"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    close_percent: float = 0.0
    close_volume: float = 0.0
    level_id: str = ""
    level_ids: List[str] = field(default_factory=list)
    reason: str = ""
    events: List[Dict] = field(default_factory=list)
