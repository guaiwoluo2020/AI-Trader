#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易策略数据模型
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import json
import os
import uuid


class ConsistencyRequirement:
    """一致性要求"""
    ANY = "any"                  # 任一信号即可
    MAJORITY = "majority"        # 多数信号一致
    ALL = "all"                  # 所有信号一致


class ConflictResolution:
    """冲突解决策略"""
    HIGHEST_CONFIDENCE = "highest_confidence"    # 最高置信度
    HIGHEST_WEIGHT = "highest_weight"            # 最高权重
    SKIP = "skip"                                # 跳过冲突


class VolumeMode:
    """手数模式"""
    FIXED = "fixed"                          # 固定手数
    RISK_PERCENT = "risk_percent"            # 风险百分比


class StopLossMode:
    """止损模式"""
    SIGNAL = "signal"                        # 使用信号建议
    FIXED_POINTS = "fixed_points"            # 固定点数
    ATR_PERCENT = "atr_percent"              # ATR百分比


class TakeProfitMode:
    """止盈模式"""
    SIGNAL = "signal"                        # 使用信号建议
    FIXED_POINTS = "fixed_points"            # 固定点数
    RISK_REWARD = "risk_reward"              # 风险回报比


class PositionConflict:
    """持仓冲突处理"""
    ALLOW_OPPOSITE = "allow_opposite"        # 允许反向
    ALLOW_SAME = "allow_same"                # 允许同向
    ALLOW_BOTH = "allow_both"                # 都允许
    BLOCK = "block"                          # 有持仓则阻止


class StrategyLifecycle:
    """策略从研发到实盘的生命周期状态。"""

    DRAFT = "draft"
    BACKTESTING = "backtesting"
    BACKTEST_PASSED = "backtest_passed"
    PAPER_TRADING = "paper_trading"
    PRODUCTION = "production"
    RETIRED = "retired"

    LABELS = {
        DRAFT: "草稿",
        BACKTESTING: "回测中",
        BACKTEST_PASSED: "回测通过",
        PAPER_TRADING: "模拟盘验证",
        PRODUCTION: "可用于实盘",
        RETIRED: "已停用",
    }

    TRANSITIONS = {
        DRAFT: {BACKTESTING},
        BACKTESTING: {DRAFT, BACKTEST_PASSED},
        BACKTEST_PASSED: {BACKTESTING, PAPER_TRADING},
        PAPER_TRADING: {BACKTEST_PASSED, PRODUCTION},
        PRODUCTION: {PAPER_TRADING, RETIRED},
        RETIRED: set(),
    }

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls.LABELS

    @classmethod
    def can_transition(cls, current: str, target: str) -> bool:
        return target in cls.TRANSITIONS.get(current, set())


SIGNAL_PERIODS = ("M1", "M5", "M15", "H1", "H4")
SIGNAL_PERIOD_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H4": 240,
}

def signal_source_defaults(source: str, period: str = "M5") -> Dict:
    """创建一条可独立配置的信号源实例。"""
    period = period if period in SIGNAL_PERIODS else "M5"
    params = {}
    if source == "key_level":
        params = {
            "level_mode": "automatic",
            "levels": [],
            "expression": "",
            "proximity_threshold": 0.0008,
            "order_distance": 0.0008,
            "upward_approach_sell": True,
            "downward_approach_buy": True,
            "upward_breakout_buy": True,
            "downward_breakout_sell": True,
            "cooldown_seconds": 180,
        }
    elif source == "ai_entry":
        params = {
            "analysis_mode": "self_analysis",
            "analysis_interval_minutes": max(5, SIGNAL_PERIOD_MINUTES[period]),
            "kline_count": 100,
            "min_confidence": 70,
            "entry_threshold": 0.0008,
            "model": "",
            "system_prompt": "",
            "analysis_prompt_template": "",
            "reference_runtime_ids": [],
            "reference_market_data": [],
            "shared_runtime_id": "",
        }
    elif source == "moving_average":
        params = {
            "fast_period": 5,
            "slow_period": 20,
            "ma_type": "sma",
            "min_confidence": 70,
            "cooldown_seconds": 180,
        }
    elif source == "pivot":
        strength_by_period = {"M1": 6, "M5": 4, "M15": 3, "H1": 3, "H4": 3}
        threshold_by_period = {
            "M1": 0.0002, "M5": 0.0005, "M15": 0.0015,
            "H1": 0.0015, "H4": 0.0015,
        }
        params = {
            "confirmation_strength": strength_by_period[period],
            "signal_type": "both",
            "proximity_threshold": threshold_by_period[period],
            "merge_distance": 0.0004,
            "stop_buffer_ratio": 0.0005,
            "risk_reward_ratio": 2.0,
            "max_age_bars": 120,
            "recency_half_life_bars": 30,
            "candidate_limit": 10,
            "min_confirmation_count": 1,
            "min_pivot_score": 0,
            "cooldown_seconds": 180,
        }
    elif source == "alpha_factor":
        params = {
            "alpha_id": "",
            "alpha_owner_user_id": 0,
            "alpha_version": 1,
            "alpha_name": "",
            "alpha_snapshot": {},
            "min_confidence": 60,
            "cooldown_seconds": 180,
        }
    return {
        "signal_source_id": uuid.uuid4().hex[:12],
        "source": source,
        "enabled": True,
        "period": period,
        "weight": 30,
        "params": params,
    }


def migrate_signal_config(
    signal_config: Dict, strategy_id: str = "legacy", min_confidence: int = 70,
) -> List[Dict]:
    """将旧版多周期配置拆成单周期信号源实例。"""
    migrated = []
    for source in ("key_level", "ai_entry"):
        config = (signal_config or {}).get(source, {})
        if source == "key_level":
            if config.get("enabled", True) and int(config.get("weight", 0)) > 0:
                item = signal_source_defaults(source, "M1")
                item.update({
                    "signal_source_id": f"{strategy_id}-key-level-m1",
                    "weight": int(config.get("weight", 40)),
                })
                migrated.append(item)
            continue
        for period, period_config in (config.get("periods") or {}).items():
            if not config.get("enabled", True) or not period_config.get("enabled"):
                continue
            weight = int(period_config.get("weight", 0))
            if weight <= 0:
                continue
            item = signal_source_defaults(source, period)
            item.update({
                "signal_source_id": f"{strategy_id}-{source}-{period.lower()}",
                "weight": weight,
            })
            if source == "ai_entry":
                item["params"]["min_confidence"] = int(min_confidence)
            migrated.append(item)
    return migrated


def normalize_signal_sources(
    items: Optional[List[Dict]], enforce_mutex: bool = False,
) -> Optional[List[Dict]]:
    """校验实例唯一性并补全类型专属默认值。"""
    if items is None:
        return None
    if enforce_mutex:
        sources = {
            str((raw or {}).get("source", "")).strip()
            for raw in items
        }
        if "key_level" in sources and (
            "ai_entry" in sources or "moving_average" in sources
            or "alpha_factor" in sources or "pivot" in sources
        ):
            raise ValueError(
                "关键点位信号源不能和AI/均线信号源同时存在，也不能和Alpha信号源同时存在"
            )
    normalized = []
    occupied = set()
    source_ids = set()
    for raw in items:
        raw = raw or {}
        source = str(raw.get("source", "")).strip()
        # 多周期信号源已下线；旧配置在读取时直接丢弃，避免阻断策略加载。
        if source == "multi_timeframe":
            continue
        period = str(raw.get("period", "")).upper()
        if source not in {"key_level", "ai_entry", "moving_average", "alpha_factor", "pivot"}:
            raise ValueError(f"不支持的信号源类型: {source}")
        if source == "key_level":
            period = "M1"
        if period not in SIGNAL_PERIODS:
            raise ValueError(f"不支持的信号周期: {period}")
        raw_params = raw.get("params") or {}
        occupied_key = (
            source if source == "key_level"
            else (source, period, str(raw_params.get("alpha_id", "")))
            if source == "alpha_factor"
            else (source, period)
        )
        if occupied_key in occupied:
            if source == "key_level":
                raise ValueError("关键点位信号源不能重复添加")
            raise ValueError(f"{source} 的 {period} 周期不能重复添加")
        occupied.add(occupied_key)

        source_id = str(raw.get("signal_source_id") or uuid.uuid4().hex[:12])
        if source_id in source_ids:
            raise ValueError(f"信号源实例ID重复: {source_id}")
        source_ids.add(source_id)
        item = signal_source_defaults(source, period)
        item.update({
            "signal_source_id": source_id,
            "enabled": bool(raw.get("enabled", True)),
            "weight": max(0, min(100, int(raw.get("weight", 30)))),
        })
        item["params"].update(raw.get("params") or {})
        params = item["params"]
        if source == "key_level":
            if params["level_mode"] not in {"automatic", "levels", "expression"}:
                raise ValueError("关键点位来源配置无效")
            raw_levels = params.get("levels") or []
            if isinstance(raw_levels, str):
                raw_levels = raw_levels.replace("，", ",").split(",")
            params["levels"] = sorted({
                float(value) for value in raw_levels
                if str(value).strip() and float(value) > 0
            })
            params["expression"] = str(params.get("expression") or "").strip()[:200]
            params["order_distance"] = max(
                0.0,
                min(0.1, float(params.get(
                    "order_distance",
                    params.get("proximity_threshold", 0.0008),
                ))),
            )
            params["proximity_threshold"] = max(
                0.0,
                min(0.1, float(params.get(
                    "proximity_threshold",
                    params["order_distance"],
                ))),
            )
            params.pop("stop_loss_distance", None)
            for flag in (
                "upward_approach_sell", "downward_approach_buy",
                "upward_breakout_buy", "downward_breakout_sell",
            ):
                params[flag] = bool(params.get(flag, True))
            params["cooldown_seconds"] = max(
                0, min(86400, int(params["cooldown_seconds"]))
            )
        elif source == "pivot":
            params["confirmation_strength"] = max(
                1, min(20, int(params.get("confirmation_strength", 3)))
            )
            params["signal_type"] = str(
                params.get("signal_type") or "both"
            ).strip().lower()
            if params["signal_type"] not in {"near", "breakout", "both"}:
                raise ValueError("转折点触发方式无效")
            for field, fallback, upper in (
                ("proximity_threshold", 0.001, 0.05),
                ("merge_distance", 0.0004, 0.05),
                ("stop_buffer_ratio", 0.0005, 0.05),
            ):
                params[field] = max(
                    0.0, min(upper, float(params.get(field, fallback)))
                )
            params["risk_reward_ratio"] = max(
                1.0, min(10.0, float(params.get("risk_reward_ratio", 2.0)))
            )
            params["max_age_bars"] = max(
                1, min(5000, int(params.get("max_age_bars", 120)))
            )
            params["recency_half_life_bars"] = max(
                1, min(
                    params["max_age_bars"],
                    int(params.get("recency_half_life_bars", 30)),
                )
            )
            params["candidate_limit"] = max(
                1, min(100, int(params.get("candidate_limit", 10)))
            )
            params["min_confirmation_count"] = max(
                1, min(20, int(params.get("min_confirmation_count", 1)))
            )
            params["min_pivot_score"] = max(
                0, min(100, int(params.get("min_pivot_score", 0)))
            )
            params["cooldown_seconds"] = max(
                0, min(86400, int(params.get("cooldown_seconds", 180)))
            )
        elif source == "ai_entry":
            params["analysis_mode"] = str(
                params.get("analysis_mode") or "self_analysis"
            ).strip()
            if params["analysis_mode"] not in {
                "self_analysis", "shared_reference"
            }:
                raise ValueError("AI入场信号运行方式无效")
            params["analysis_interval_minutes"] = max(
                SIGNAL_PERIOD_MINUTES[period],
                min(1440, int(params["analysis_interval_minutes"])),
            )
            params["kline_count"] = max(10, min(500, int(params["kline_count"])))
            params["min_confidence"] = max(
                0, min(100, int(params["min_confidence"]))
            )
            params["entry_threshold"] = max(
                0.0, min(0.1, float(params["entry_threshold"]))
            )
            params["model"] = str(params.get("model") or "").strip()
            if len(params["model"]) > 200:
                raise ValueError("AI入场信号选择的大模型无效")
            params["system_prompt"] = str(
                params.get("system_prompt") or ""
            ).strip()
            params["analysis_prompt_template"] = str(
                params.get("analysis_prompt_template") or ""
            ).strip()
            if len(params["system_prompt"]) > 10000:
                raise ValueError("AI系统提示词不能超过10000个字符")
            if len(params["analysis_prompt_template"]) > 50000:
                raise ValueError("AI分析提示词不能超过50000个字符")
            if (
                params["analysis_prompt_template"]
                and "{{market_data}}" not in params["analysis_prompt_template"]
            ):
                raise ValueError("AI分析提示词必须包含 {{market_data}}")
            # Runtime-data sharing belongs to the standalone AI signal source,
            # not to a strategy's serialised source binding.
            params.pop("share_runtime_data", None)
            references = params.get("reference_runtime_ids") or []
            if not isinstance(references, list):
                raise ValueError("共享AI运行数据引用格式无效")
            params["reference_runtime_ids"] = list(dict.fromkeys(
                str(value).strip() for value in references if str(value).strip()
            ))[:10]
            references = params.get("reference_market_data") or []
            if not isinstance(references, list):
                raise ValueError("参考行情配置格式无效")
            if len(references) > 5:
                raise ValueError("每个 AI 信号源最多配置 5 条参考行情")
            normalized_references = []
            occupied_references = set()
            for reference in references:
                if not isinstance(reference, dict):
                    raise ValueError("参考行情配置项格式无效")
                reference_symbol = str(reference.get("symbol") or "").strip()
                reference_period = str(reference.get("period") or "").strip().upper()
                if not reference_symbol or reference_period not in SIGNAL_PERIODS:
                    raise ValueError("参考行情必须填写有效品种和周期")
                if reference_symbol == str(raw.get("symbol") or "").strip() and reference_period == period:
                    raise ValueError("参考行情不能与主行情使用相同的品种和周期")
                key = (reference_symbol.upper(), reference_period)
                if key in occupied_references:
                    raise ValueError("同一个品种和周期不能重复添加参考行情")
                occupied_references.add(key)
                role = str(reference.get("role") or "market_context").strip()
                if role not in {"higher_timeframe", "lower_timeframe", "related_symbol", "market_context"}:
                    raise ValueError("参考行情类型无效")
                normalized_references.append({
                    "symbol": reference_symbol,
                    "period": reference_period,
                    "kline_count": max(10, min(500, int(reference.get("kline_count", 100) or 100))),
                    "role": role,
                })
            params["reference_market_data"] = normalized_references
            params["shared_runtime_id"] = str(
                params.get("shared_runtime_id") or ""
            ).strip()
            if params["analysis_mode"] == "shared_reference":
                if not params["shared_runtime_id"]:
                    raise ValueError("共享引用模式必须选择一条共享AI运行数据")
                params["reference_runtime_ids"] = []
        elif source == "moving_average":
            params["fast_period"] = max(1, min(500, int(params["fast_period"])))
            params["slow_period"] = max(2, min(1000, int(params["slow_period"])))
            if params["fast_period"] >= params["slow_period"]:
                raise ValueError("均线快线周期必须小于慢线周期")
            params["ma_type"] = str(params["ma_type"]).lower()
            if params["ma_type"] not in {"sma", "ema"}:
                raise ValueError("均线类型必须是 sma 或 ema")
            params["min_confidence"] = max(
                0, min(100, int(params.get("min_confidence", 70)))
            )
            for obsolete in (
                "stop_loss_pct", "risk_reward_ratio", "exit_mode",
                "trailing_activation_r", "trailing_distance_r",
                "min_gap_ratio",
            ):
                params.pop(obsolete, None)
            params["cooldown_seconds"] = max(
                0, min(86400, int(params["cooldown_seconds"]))
            )
        else:
            params["alpha_id"] = str(params.get("alpha_id") or "").strip()
            if not params["alpha_id"]:
                raise ValueError("请选择已验证 Alpha")
            params["alpha_owner_user_id"] = int(params.get("alpha_owner_user_id") or 0)
            params["alpha_version"] = max(1, int(params.get("alpha_version", 1)))
            params["alpha_name"] = str(params.get("alpha_name") or "").strip()[:100]
            params["alpha_snapshot"] = (
                params.get("alpha_snapshot")
                if isinstance(params.get("alpha_snapshot"), dict)
                else {}
            )
            params["min_confidence"] = max(
                0, min(100, int(params.get("min_confidence", 60)))
            )
            params["cooldown_seconds"] = max(
                0, min(86400, int(params.get("cooldown_seconds", 180)))
            )
        normalized.append(item)
    return normalized


@dataclass
class TradingStrategy:
    """交易策略 - 绑定品种，配置信号权重和决策规则"""

    # ==================== 基本信息 ====================
    symbol: str                       # 绑定的品种
    strategy_name: str = ""           # 策略名称
    visibility: str = "private"       # private/shared，共享后其他用户只能复制

    # 策略不再承担运行开关；是否运行由账户部署关系和账户交易开关控制。
    enabled: bool = True

    # 直接构造策略保持历史兼容；通过 StrategyStore 创建的新策略会显式设为草稿。
    lifecycle_status: str = StrategyLifecycle.PRODUCTION
    lifecycle_updated_at: datetime = None
    lifecycle_history: List[Dict] = field(default_factory=list)

    # ==================== 信号源配置（新版：支持周期级别控制）====================
    # 信号源配置结构：
    # {
    #   "key_level": {"enabled": true, "weight": 40},  # key_level 不区分周期
    #   "ai_entry": {
    #     "enabled": true,
    #     "periods": {"M5": {"enabled": true, "weight": 20}, ...}
    #   }
    # }
    signal_config: Dict = field(default_factory=lambda: {
        "key_level": {
            "enabled": True,
            "weight": 40
        },
        "ai_entry": {
            "enabled": True,
            "periods": {
                "M1": {"enabled": False, "weight": 15},
                "M5": {"enabled": True, "weight": 20},
                "M15": {"enabled": True, "weight": 30},
                "H1": {"enabled": True, "weight": 25},
                "H4": {"enabled": False, "weight": 20}
            }
        }
    })

    # 新版结构：用户可重复添加同类信号源，但同类信号源的周期必须唯一。
    # None 表示历史数据尚未迁移，[] 表示用户明确删除了全部信号源。
    signal_sources: Optional[List[Dict]] = None

    # ==================== 信号权重配置（兼容旧版，已废弃）===================
    signal_weights: Dict[str, int] = field(default_factory=lambda: {
        "key_level": 40,
        "ai_entry": 30,
    })

    period_weights: Dict[str, int] = field(default_factory=lambda: {
        "H4": 20,
        "H1": 20,
        "M15": 25,
        "M5": 20,
        "M1": 15,
    })

    # ==================== 信号过滤规则 ====================
    min_confidence: int = 50
    consistency_requirement: str = ConsistencyRequirement.MAJORITY
    conflict_resolution: str = ConflictResolution.HIGHEST_WEIGHT

    # ==================== 仓位管理 ====================
    fixed_volume: float = 0.01
    volume_mode: str = VolumeMode.FIXED
    risk_percent: float = 1.0
    max_positions: int = 3
    max_same_direction: int = 2

    # ==================== 独立持仓管理 ====================
    position_management_policy_id: str = ""

    # ==================== 过滤条件 ====================
    min_risk_reward: float = 1.0
    max_risk_reward: float = 5.0

    # ==================== 时间过滤 ====================
    trading_hours: Dict = field(default_factory=lambda: {
        "start": "00:00",
        "end": "23:59",
        "exclude_hours": []
    })

    # ==================== 持仓冲突处理 ====================
    position_conflict: str = PositionConflict.ALLOW_OPPOSITE

    # ==================== 自动生成字段 ====================
    strategy_id: str = ""
    source_strategy_id: str = ""
    source_owner_user_id: int = 0
    source_owner_username: str = ""
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.visibility not in {"private", "shared"}:
            self.visibility = "private"
        self.source_owner_user_id = int(self.source_owner_user_id or 0)
        if not self.strategy_id:
            self.strategy_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()
        if not self.updated_at:
            self.updated_at = self.created_at
        if not StrategyLifecycle.is_valid(self.lifecycle_status):
            self.lifecycle_status = StrategyLifecycle.DRAFT
        if not self.lifecycle_updated_at:
            self.lifecycle_updated_at = self.created_at
        self.enabled = True
        if not self.strategy_name:
            self.strategy_name = f"Strategy_{self.symbol}"
        self.signal_sources = normalize_signal_sources(self.signal_sources)
        if self.signal_sources is None:
            self.signal_sources = migrate_signal_config(
                self.signal_config, self.strategy_id, self.min_confidence
            )

    def is_runnable(self) -> bool:
        """只有已发布的策略可以生成实盘决策；部署状态负责运行开关。"""
        return self.lifecycle_status == StrategyLifecycle.PRODUCTION

    def is_runnable_for(self, execution_mode: str = "live") -> bool:
        """按执行环境判断策略是否可运行，模拟盘不依赖实盘启用开关。"""
        if execution_mode == "backtest":
            return self.lifecycle_status != StrategyLifecycle.RETIRED
        if execution_mode == "paper":
            return self.lifecycle_status in {
                StrategyLifecycle.BACKTEST_PASSED,
                StrategyLifecycle.PAPER_TRADING,
                StrategyLifecycle.PRODUCTION,
            }
        return self.is_runnable()

    def transition_lifecycle(self, target_status: str, reason: str = "") -> None:
        """按状态机推进策略生命周期。"""
        if not StrategyLifecycle.is_valid(target_status):
            raise ValueError(f"未知的策略生命周期状态: {target_status}")
        if not StrategyLifecycle.can_transition(
            self.lifecycle_status, target_status
        ):
            current_label = StrategyLifecycle.LABELS[self.lifecycle_status]
            target_label = StrategyLifecycle.LABELS[target_status]
            raise ValueError(
                f"不允许从“{current_label}”直接转换为“{target_label}”"
            )

        now = datetime.now()
        previous_status = self.lifecycle_status
        self.lifecycle_status = target_status
        self.lifecycle_updated_at = now
        self.updated_at = now
        self.lifecycle_history.append({
            "from_status": previous_status,
            "to_status": target_status,
            "changed_at": now.isoformat(),
            "reason": str(reason or "").strip(),
        })

    @staticmethod
    def _config_value(value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        return value

    def update(self, data: Dict) -> None:
        """更新配置"""
        material_fields = {
            "signal_config", "signal_sources", "signal_weights", "period_weights",
            "min_confidence", "consistency_requirement",
            "conflict_resolution", "fixed_volume", "volume_mode",
            "risk_percent", "max_positions",
            "max_same_direction", "position_management_policy_id",
            "min_risk_reward", "max_risk_reward", "trading_hours",
            "position_conflict",
        }
        before = {
            field_name: self._config_value(getattr(self, field_name))
            for field_name in material_fields
            if field_name in data
        }
        if (
            self.lifecycle_status == StrategyLifecycle.RETIRED
            and any(
                before[field_name] != self._config_value(data[field_name])
                for field_name in before
            )
        ):
            raise ValueError("已停用策略不可修改，请创建新策略重新验证")
        if "strategy_name" in data:
            self.strategy_name = str(data["strategy_name"]).strip()
        if "visibility" in data or "is_shared" in data:
            visibility = data.get("visibility")
            if visibility is None:
                visibility = "shared" if data.get("is_shared") else "private"
            self.visibility = "shared" if visibility == "shared" else "private"
        # 策略级 enabled 已废弃，统一由账户部署关系控制。
        self.enabled = True
        if "signal_config" in data:
            self.signal_config = data["signal_config"]
            if "signal_sources" not in data:
                self.signal_sources = migrate_signal_config(
                    self.signal_config, self.strategy_id, self.min_confidence
                )
        if "signal_sources" in data:
            self.signal_sources = normalize_signal_sources(
                data["signal_sources"], enforce_mutex=True
            )
        if "signal_weights" in data:
            self.signal_weights = data["signal_weights"]
        if "period_weights" in data:
            self.period_weights = data["period_weights"]
        if "min_confidence" in data:
            self.min_confidence = int(data["min_confidence"])
        if "consistency_requirement" in data:
            self.consistency_requirement = data["consistency_requirement"]
        if "conflict_resolution" in data:
            self.conflict_resolution = data["conflict_resolution"]
        if "fixed_volume" in data:
            self.fixed_volume = float(data["fixed_volume"])
        if "volume_mode" in data:
            self.volume_mode = data["volume_mode"]
        if "risk_percent" in data:
            self.risk_percent = float(data["risk_percent"])
        if "max_positions" in data:
            self.max_positions = int(data["max_positions"])
        if "max_same_direction" in data:
            self.max_same_direction = int(data["max_same_direction"])
        if "position_management_policy_id" in data:
            self.position_management_policy_id = str(
                data["position_management_policy_id"] or ""
            ).strip()
        if "min_risk_reward" in data:
            self.min_risk_reward = float(data["min_risk_reward"])
        if "max_risk_reward" in data:
            self.max_risk_reward = float(data["max_risk_reward"])
        if "position_conflict" in data:
            self.position_conflict = data["position_conflict"]
        if "trading_hours" in data:
            self.trading_hours = data["trading_hours"]

        material_changed = any(
            before[field_name]
            != self._config_value(getattr(self, field_name))
            for field_name in before
        )
        if (
            material_changed
            and self.lifecycle_status != StrategyLifecycle.DRAFT
        ):
            now = datetime.now()
            previous_status = self.lifecycle_status
            self.lifecycle_status = StrategyLifecycle.DRAFT
            self.lifecycle_updated_at = now
            self.lifecycle_history.append({
                "from_status": previous_status,
                "to_status": StrategyLifecycle.DRAFT,
                "changed_at": now.isoformat(),
                "reason": "策略参数已修改，需要重新验证",
            })
        self.updated_at = datetime.now()

        self.updated_at = datetime.now()

    def get_signal_sources(
        self, source: str = None, enabled_only: bool = False,
    ) -> List[Dict]:
        return [
            item for item in (self.signal_sources or [])
            if (source is None or item.get("source") == source)
            and (not enabled_only or (
                item.get("enabled", True) and int(item.get("weight", 0)) > 0
            ))
        ]

    def get_signal_weight(
        self, source: str, period: str = None, signal_source_id: str = "",
    ) -> int:
        """
        获取信号源权重（支持周期级别）

        Args:
            source: 信号源 (key_level/ai_entry/moving_average)
            period: 周期 (M1/M5/M15/H1/H4)，key_level 不需要周期

        Returns:
            权重值
        """
        if self.signal_sources is not None:
            candidates = self.get_signal_sources(source, enabled_only=True)
            if signal_source_id:
                candidates = [
                    item for item in candidates
                    if item.get("signal_source_id") == signal_source_id
                ]
            if period:
                candidates = [
                    item for item in candidates if item.get("period") == period
                ]
            return max(
                (int(item.get("weight", 0)) for item in candidates), default=0
            )

        # 兼容旧版 signal_config
        if self.signal_config and source in self.signal_config:
            config = self.signal_config[source]
            if not config.get("enabled", True):
                return 0

            # key_level 不区分周期
            if source == "key_level":
                return config.get("weight", 0)

            # 其他信号源区分周期
            if period and "periods" in config:
                period_config = config["periods"].get(period, {})
                if not period_config.get("enabled", False):
                    return 0
                return period_config.get("weight", 0)

            # 如果没有 period 配置，返回 0
            return 0

        # 兼容旧版 signal_weights
        return self.signal_weights.get(source, 0)

    def is_signal_enabled(
        self, source: str, period: str = None, signal_source_id: str = "",
    ) -> bool:
        """
        检查信号源是否启用

        Args:
            source: 信号源
            period: 周期（key_level 不需要）

        Returns:
            是否启用
        """
        if self.signal_sources is not None:
            return self.get_signal_weight(source, period, signal_source_id) > 0

        if not self.signal_config or source not in self.signal_config:
            # 兼容旧版：signal_weights 中有配置就认为启用
            return source in self.signal_weights and self.signal_weights[source] > 0

        config = self.signal_config[source]
        if not config.get("enabled", True):
            return False

        # key_level 不区分周期
        if source == "key_level":
            return True

        # 其他信号源需要检查周期
        if period and "periods" in config:
            period_config = config["periods"].get(period, {})
            return period_config.get("enabled", False)

        return False

    def get_period_weight(self, period: str) -> int:
        """获取周期权重（兼容旧版）"""
        return self.period_weights.get(period, 0)

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "visibility": self.visibility,
            "is_shared": self.visibility == "shared",
            "enabled": True,
            "lifecycle_status": self.lifecycle_status,
            "lifecycle_label": StrategyLifecycle.LABELS[self.lifecycle_status],
            "lifecycle_updated_at": (
                self.lifecycle_updated_at.isoformat()
                if self.lifecycle_updated_at else None
            ),
            "lifecycle_history": self.lifecycle_history,
            "signal_config": self.signal_config,
            "signal_sources": self.signal_sources,
            "signal_weights": self.signal_weights,
            "period_weights": self.period_weights,
            "min_confidence": self.min_confidence,
            "consistency_requirement": self.consistency_requirement,
            "conflict_resolution": self.conflict_resolution,
            "fixed_volume": self.fixed_volume,
            "volume_mode": self.volume_mode,
            "risk_percent": self.risk_percent,
            "max_positions": self.max_positions,
            "max_same_direction": self.max_same_direction,
            "position_management_policy_id": self.position_management_policy_id,
            "min_risk_reward": self.min_risk_reward,
            "max_risk_reward": self.max_risk_reward,
            "trading_hours": self.trading_hours,
            "position_conflict": self.position_conflict,
            "source_strategy_id": self.source_strategy_id,
            "source_owner_user_id": self.source_owner_user_id,
            "source_owner_username": self.source_owner_username,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingStrategy':
        """从字典创建"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = data.get('updated_at')
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        lifecycle_updated_at = data.get('lifecycle_updated_at')
        if isinstance(lifecycle_updated_at, str):
            lifecycle_updated_at = datetime.fromisoformat(
                lifecycle_updated_at
            )

        # 历史配置没有生命周期字段，按原有行为视为已经可用于实盘。
        lifecycle_status = data.get(
            'lifecycle_status', StrategyLifecycle.PRODUCTION
        )

        # 默认 signal_config
        default_signal_config = {
            "key_level": {
                "enabled": True,
                "weight": 40
            },
            "ai_entry": {
                "enabled": True,
                "periods": {
                    "M1": {"enabled": False, "weight": 15},
                    "M5": {"enabled": True, "weight": 20},
                    "M15": {"enabled": True, "weight": 30},
                    "H1": {"enabled": True, "weight": 25},
                    "H4": {"enabled": False, "weight": 20}
                }
            }
        }

        return cls(
            symbol=data.get('symbol', ''),
            strategy_name=data.get('strategy_name', ''),
            visibility=(
                'shared'
                if data.get('is_shared') and not data.get('visibility')
                else data.get('visibility', 'private')
            ),
            enabled=True,
            lifecycle_status=lifecycle_status,
            lifecycle_updated_at=lifecycle_updated_at,
            lifecycle_history=data.get('lifecycle_history', []),
            signal_config=data.get('signal_config', default_signal_config),
            signal_sources=data.get('signal_sources'),
            signal_weights=data.get('signal_weights', {"key_level": 40, "ai_entry": 30}),
            period_weights=data.get('period_weights', {"H4": 20, "H1": 20, "M15": 25, "M5": 20, "M1": 15}),
            min_confidence=data.get('min_confidence', 50),
            consistency_requirement=data.get('consistency_requirement', ConsistencyRequirement.MAJORITY),
            conflict_resolution=data.get('conflict_resolution', ConflictResolution.HIGHEST_WEIGHT),
            fixed_volume=data.get('fixed_volume', 0.01),
            volume_mode=data.get('volume_mode', VolumeMode.FIXED),
            risk_percent=data.get('risk_percent', 1.0),
            max_positions=data.get('max_positions', 3),
            max_same_direction=data.get('max_same_direction', 2),
            position_management_policy_id=data.get(
                'position_management_policy_id', ''
            ),
            min_risk_reward=data.get('min_risk_reward', 1.0),
            max_risk_reward=data.get('max_risk_reward', 5.0),
            trading_hours=data.get('trading_hours', {"start": "00:00", "end": "23:59", "exclude_hours": []}),
            position_conflict=data.get('position_conflict', PositionConflict.ALLOW_OPPOSITE),
            strategy_id=data.get('strategy_id', ''),
            source_strategy_id=data.get('source_strategy_id', ''),
            source_owner_user_id=data.get('source_owner_user_id', 0),
            source_owner_username=data.get('source_owner_username', ''),
            created_at=created_at,
            updated_at=updated_at,
        )


@dataclass
class TradingDecision:
    """交易决策 - 策略层输出"""

    # ==================== 基本信息 ====================
    symbol: str                       # 品种
    strategy_id: str                  # 来源策略ID
    strategy_name: str = ""           # 来源策略名称
    auto_executed: bool = False       # 是否已自动生成 EA 指令
    execution_mode: str = "live"     # live / paper

    # ==================== 决策结果 ====================
    action: str = ""                  # buy/sell/none
    decision_type: str = ""           # signal_combined / single_signal / manual

    # ==================== 信号汇总 ====================
    signals: List[Dict] = field(default_factory=list)
    signal_summary: Dict = field(default_factory=dict)

    # ==================== 执行参数 ====================
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    volume: float = 0.01

    risk_points: float = 0.0
    reward_points: float = 0.0
    risk_reward_ratio: float = 0.0

    # ==================== 决策理由 ====================
    decision_reason: str = ""
    confidence_score: float = 0.0

    # ==================== 检查结果 ====================
    position_check: Dict = field(default_factory=dict)
    risk_check: Dict = field(default_factory=dict)

    # ==================== 状态 ====================
    decision_id: str = ""
    status: str = "pending"           # pending/confirmed/rejected/expired
    created_at: datetime = None
    # Waiting decisions are aggregated in memory and deliberately never saved.
    observation_count: int = 1
    first_observed_at: datetime = None
    last_observed_at: datetime = None

    # ==================== 关联 ====================
    order_id: Optional[str] = None

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "auto_executed": self.auto_executed,
            "execution_mode": self.execution_mode,
            "action": self.action,
            "decision_type": self.decision_type,
            "signals": self.signals,
            "signal_summary": self.signal_summary,
            "entry_price": self.entry_price,
            "sl": self.sl,
            "tp": self.tp,
            "volume": self.volume,
            "risk_points": self.risk_points,
            "reward_points": self.reward_points,
            "risk_reward_ratio": self.risk_reward_ratio,
            "decision_reason": self.decision_reason,
            "confidence_score": self.confidence_score,
            "position_check": self.position_check,
            "risk_check": self.risk_check,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "observation_count": self.observation_count,
            "first_observed_at": (
                self.first_observed_at.isoformat()
                if self.first_observed_at else None
            ),
            "last_observed_at": (
                self.last_observed_at.isoformat()
                if self.last_observed_at else None
            ),
            "order_id": self.order_id,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'TradingDecision':
        """从字典创建"""
        created_at = data.get('created_at')
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        first_observed_at = data.get('first_observed_at')
        if isinstance(first_observed_at, str):
            first_observed_at = datetime.fromisoformat(first_observed_at)
        last_observed_at = data.get('last_observed_at')
        if isinstance(last_observed_at, str):
            last_observed_at = datetime.fromisoformat(last_observed_at)

        return cls(
            symbol=data.get('symbol', ''),
            strategy_id=data.get('strategy_id', ''),
            strategy_name=data.get('strategy_name', ''),
            auto_executed=data.get('auto_executed', False),
            execution_mode=data.get('execution_mode', 'live'),
            action=data.get('action', ''),
            decision_type=data.get('decision_type', ''),
            signals=data.get('signals', []),
            signal_summary=data.get('signal_summary', {}),
            entry_price=data.get('entry_price', 0.0),
            sl=data.get('sl', 0.0),
            tp=data.get('tp', 0.0),
            volume=data.get('volume', 0.01),
            risk_points=data.get('risk_points', 0.0),
            reward_points=data.get('reward_points', 0.0),
            risk_reward_ratio=data.get('risk_reward_ratio', 0.0),
            decision_reason=data.get('decision_reason', ''),
            confidence_score=data.get('confidence_score', 0.0),
            position_check=data.get('position_check', {}),
            risk_check=data.get('risk_check', {}),
            decision_id=data.get('decision_id', ''),
            status=data.get('status', 'pending'),
            created_at=created_at,
            observation_count=int(data.get('observation_count', 1) or 1),
            first_observed_at=first_observed_at,
            last_observed_at=last_observed_at,
            order_id=data.get('order_id'),
        )
