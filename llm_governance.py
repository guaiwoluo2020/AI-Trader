#!/usr/bin/env python3
"""Central model catalog, scene routing, quota enforcement and LLM audit."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from sqlite_storage import LLMAccessRepository, LLMConfigRepository, SQLiteStorage, get_storage
from membership import MEMBERSHIP_LIMITS
from system_event_log import SystemEventLogRepository


AI_SIGNAL_ANALYSIS = "ai_signal_analysis"
BACKTEST_REPORT_ANALYSIS = "backtest_report_analysis"
ALPHA_CANDIDATE_GENERATION = "alpha_candidate_generation"
ALPHA_ITERATIVE_REFINEMENT = "alpha_iterative_refinement"

DEFAULT_SYSTEM_PROMPT = (
    "你是一位专业的金融分析师，擅长技术分析和趋势判断。"
    "请用JSON格式输出分析结果，不要有任何额外的文字说明。"
)

DEFAULT_ANALYSIS_PROMPT_TEMPLATE = """你是一位专业的金融分析师。请分析以下交易品种的K线数据，并给出趋势判断和交易建议。

## 分析要求

1. 对策略启用的每个周期判断趋势类型、置信度(0-100)和理由
2. 给出整体趋势方向、强度(0-100)和总结
3. 根据K线数据分别给出3个关键支撑位和压力位
4. 交易建议必须覆盖策略启用的全部AI周期，period只能是H4、H1、M15、M5、M1之一
5. 每条建议的止盈止损必须满足关联策略中最高的最低盈亏比要求

趋势类型可选：单边上涨、单边下跌、区间震荡、震荡上升、震荡下跌、震荡收窄、震荡扩大。

## 策略约束

{{strategy_context}}

## K线数据

{{market_data}}

## 输出格式

必须输出纯JSON，不要包含Markdown代码块或其他说明：
{
  "品种": {
    "trend_analysis": {
      "策略启用周期": {"trend": "趋势类型", "confidence": 置信度, "reason": "判断理由"}
    },
    "overall_trend": {"direction": "方向", "strength": 强度, "summary": "总结"},
    "key_levels": {"resistance": [压力位1, 压力位2, 压力位3], "support": [支撑位1, 支撑位2, 支撑位3]},
    "trade_suggestions": [{
      "strategy_id": "策略ID", "signal_source_id": "信号源实例ID",
      "period": "策略启用周期",
      "direction": "buy或sell", "confidence": 置信度,
      "entry_price": 入场价格, "stop_loss": 止损价格, "take_profit": 止盈价格,
      "reason": "交易理由"
    }]
  }
}
"""

BACKTEST_REPORT_SYSTEM_PROMPT = (
    "你是一名严谨的量化策略研究员。只输出合法 JSON，不输出 Markdown。"
    "请区分统计证据与推测，不承诺收益。"
)

BACKTEST_REPORT_PROMPT_TEMPLATE = """请分析下面的回测数据，找出策略表现、风险和参数设置中的问题，并提出可以通过下一轮回测验证的优化建议。

约束：
1. 只依据提供的数据，不虚构行情、成交或统计指标。
2. 区分统计证据与推测；样本不足时明确说明，不能承诺收益。
3. strategy_snapshot.signal_sources 只包含本次回测实际启用的信号源；只能针对其中列出的信号源和参数提出建议，不得补充或假设其他信号源。
4. 已取消任务的数据可能不完整，必须在结论中说明终止进度带来的偏差。
5. 不要建议直接上线实盘；每项参数修改都应给出独立回测验证方法。
6. 必须输出纯 JSON，不要包含 Markdown。

输出结构：
{
  "executive_summary": "总体结论",
  "data_quality": {"level": "high|medium|low", "notes": ["说明"]},
  "diagnosis": [{"area": "收益|风险|交易|信号源|数据", "severity": "high|medium|low", "finding": "发现", "evidence": "数据证据"}],
  "optimization_suggestions": [{"priority": 1, "target": "参数路径或优化对象", "current_value": "当前值", "suggested_value": "建议值或范围", "reason": "原因", "expected_impact": "预期方向", "validation_plan": "下一轮如何验证"}],
  "risk_warnings": ["风险提示"],
  "next_backtest_plan": {"changes": ["一次只改少量变量"], "datasets": ["建议的数据范围"], "acceptance_criteria": ["验收指标"]}
}

回测数据：
{{backtest_snapshot}}"""

ALPHA_SYSTEM_PROMPT = (
    "你是量化研究助手。只输出合法 JSON，不输出 Markdown。"
    "候选必须使用提供的技术分析或平台原生时段因子，解释研究假设，不承诺盈利。"
)

ALPHA_CANDIDATE_PROMPT_TEMPLATE = """研究目标：{{research_description}}
分析周期：{{timeframe}}
预测未来：{{prediction_horizon}} 根 K 线
请生成 {{candidate_count}} 个结构有差异的 Alpha 候选。

可用因子：
{{factor_catalog}}

返回结构：
{
  "candidates": [
    {
      "name": "候选名称",
      "theme": "趋势/动量/波动/统计/量价/时段/形态/周期/收益",
      "hypothesis": "可检验的研究假设",
      "buy_logic": "买入方向含义",
      "sell_logic": "卖出方向含义",
      "factors": [
        {"name": "ema", "length_min": 5, "length_max": 30,
          "weight_min": 0.2, "weight_max": 1.5}
      ]
    }
  ]
}
每个候选使用 1-5 个因子；周期范围 2-500；权重范围 -3 到 3；
候选之间不能只是参数不同，必须体现不同研究假设。"""

ALPHA_REFINEMENT_PROMPT_TEMPLATE = """研究目标：{{research_description}}
分析周期：{{timeframe}}
预测未来：{{prediction_horizon}} 根 K 线

当前候选：
{{current_candidate}}

已完成研究轮次（仅训练集与验证集，未包含隐藏测试）：
{{iteration_history}}

可用因子：
{{factor_catalog}}

请按因子研究漏斗诊断：重点检查 IC、Rank IC、滚动 IC、IC_IR、
Rank IC_IR、多周期 Decay、分组单调性与训练/验证过拟合差距。
独立评估用于判断单因子是否具有预测信息；残差 Rank IC 用于判断该因子
在剔除其他因子解释部分后是否仍提供增量信息。优先替换独立评估失败、
残差信息弱或与其他因子高度重复的因子。
输出一个下一轮候选。优先做有理由的结构调整，可保留、增加、删除或替换因子；
不要只复制失败结构并微调参数。参数精调将由 Optuna 完成。

只返回：
{
  "candidate": {
    "name": "候选名称",
    "theme": "研究分类",
    "hypothesis": "修订后的可检验假设",
    "buy_logic": "买入方向含义",
    "sell_logic": "卖出方向含义",
    "factors": [
      {"name": "ema", "length_min": 5, "length_max": 30,
        "weight_min": 0.2, "weight_max": 1.5}
    ]
  },
  "diagnosis": "为何这样调整",
  "changes": ["结构变化摘要"]
}
每个候选使用 1-5 个因子；周期范围 2-500；权重范围 -3 到 3。"""

SCENE_DEFAULTS = (
    (
        AI_SIGNAL_ANALYSIS, "AI 行情与交易信号", "high", 1, 1,
        DEFAULT_SYSTEM_PROMPT, DEFAULT_ANALYSIS_PROMPT_TEMPLATE,
    ),
    (
        BACKTEST_REPORT_ANALYSIS, "回测报告分析", "low", 0, 0,
        BACKTEST_REPORT_SYSTEM_PROMPT, BACKTEST_REPORT_PROMPT_TEMPLATE,
    ),
    (
        ALPHA_CANDIDATE_GENERATION, "Alpha 候选生成", "low", 0, 0,
        ALPHA_SYSTEM_PROMPT, ALPHA_CANDIDATE_PROMPT_TEMPLATE,
    ),
    (
        ALPHA_ITERATIVE_REFINEMENT, "Alpha 迭代优化", "low", 0, 0,
        ALPHA_SYSTEM_PROMPT, ALPHA_REFINEMENT_PROMPT_TEMPLATE,
    ),
)
FREE_DAILY_LIMIT = 30
CHINA_TZ = timezone(timedelta(hours=8))


class LLMGovernanceError(ValueError):
    pass


class LLMQuotaExceeded(LLMGovernanceError):
    pass


class LLMGovernanceService:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.configs = LLMConfigRepository(self.storage)
        self.access = LLMAccessRepository(self.storage)
        self._seed()

    def _seed(self) -> None:
        now = int(time.time())
        for (
            code, name, frequency, requires_access, selectable,
            system_prompt, user_prompt_template,
        ) in SCENE_DEFAULTS:
            self.storage.execute(
                """
                INSERT OR IGNORE INTO llm_scene_policies(
                    scene_code, display_name, frequency_class, requires_access,
                    enabled, default_model_id, allow_user_selection,
                    system_prompt, user_prompt_template, updated_at
                ) VALUES(?, ?, ?, ?, 1, '', ?, ?, ?, ?)
                """,
                (
                    code, name, frequency, requires_access, selectable,
                    system_prompt, user_prompt_template, now,
                ),
            )
            self.storage.execute(
                """
                UPDATE llm_scene_policies
                SET system_prompt = CASE WHEN system_prompt = '' THEN ? ELSE system_prompt END,
                    user_prompt_template = CASE WHEN user_prompt_template = '' THEN ? ELSE user_prompt_template END
                WHERE scene_code = ?
                """,
                (system_prompt, user_prompt_template, code),
            )
            if code == AI_SIGNAL_ANALYSIS:
                prompt_count = self.storage.fetchone(
                    "SELECT COUNT(*) AS total FROM llm_scene_prompts WHERE scene_code = ?",
                    (code,),
                )
                if int(prompt_count["total"] if prompt_count else 0) == 0:
                    policy = self.storage.fetchone(
                        "SELECT system_prompt, user_prompt_template FROM llm_scene_policies WHERE scene_code = ?",
                        (code,),
                    )
                    self.storage.execute(
                        """
                        INSERT INTO llm_scene_prompts(
                            prompt_id, scene_code, prompt_name, system_prompt,
                            user_prompt_template, is_default, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            f"{code}:default", code, "默认提示词",
                            policy["system_prompt"], policy["user_prompt_template"],
                            now, now,
                        ),
                    )

    def _bootstrap_model(self, model_id: str) -> None:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO llm_models(model_id, display_name, available, enabled,
                                   discovered_at, last_seen_at)
            VALUES(?, ?, 1, 1, ?, ?)
            ON CONFLICT(model_id) DO NOTHING
            """,
            (model_id, model_id, now, now),
        )
        for code, *_ in SCENE_DEFAULTS:
            count = self.storage.fetchone(
                "SELECT COUNT(*) AS total FROM llm_scene_models WHERE scene_code = ?",
                (code,),
            )
            if int(count["total"]) == 0:
                self.storage.execute(
                    "INSERT OR IGNORE INTO llm_scene_models(scene_code, model_id) VALUES(?, ?)",
                    (code, model_id),
                )
                self.storage.execute(
                    "UPDATE llm_scene_policies SET default_model_id = ? WHERE scene_code = ?",
                    (model_id, code),
                )

    def _admin_config(self):
        admin = self.storage.fetchone(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
        )
        if not admin:
            raise LLMGovernanceError("系统尚未创建管理员账号")
        config = self.configs.get_config(int(admin["id"]))
        if not config.enabled:
            raise LLMGovernanceError("管理员尚未配置可用的大模型服务")
        return config

    def _admin_user_id(self) -> int:
        admin = self.storage.fetchone(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
        )
        if not admin:
            raise LLMGovernanceError("系统尚未创建管理员账号")
        return int(admin["id"])

    def sync_models(self) -> List[Dict]:
        config = self._admin_config()
        response = requests.get(
            f"{config.api_base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=30,
        )
        if response.status_code != 200:
            raise LLMGovernanceError(
                f"模型列表同步失败，接口返回 HTTP {response.status_code}"
            )
        payload = response.json()
        ids = sorted({
            str(item.get("id") or "").strip()
            for item in payload.get("data", []) if isinstance(item, dict)
        } - {""})
        if not ids:
            raise LLMGovernanceError("BASE URL 未返回可用模型")
        now = int(time.time())
        self.storage.execute("UPDATE llm_models SET available = 0")
        for model_id in ids:
            self.storage.execute(
                """
                INSERT INTO llm_models(model_id, display_name, available, enabled,
                                       discovered_at, last_seen_at)
                VALUES(?, ?, 1, 0, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    display_name = excluded.display_name, available = 1,
                    last_seen_at = excluded.last_seen_at
                """,
                (model_id, model_id, now, now),
            )
        return self.list_models()

    def list_models(self) -> List[Dict]:
        return [dict(row) | {
            "available": bool(row["available"]), "enabled": bool(row["enabled"]),
        } for row in self.storage.fetchall(
            "SELECT * FROM llm_models ORDER BY available DESC, enabled DESC, model_id"
        )]

    def set_model_enabled(self, model_id: str, enabled: bool) -> None:
        row = self.storage.fetchone(
            "SELECT available FROM llm_models WHERE model_id = ?", (model_id,)
        )
        if row is None:
            raise LLMGovernanceError("模型不存在，请先同步模型列表")
        if enabled and not bool(row["available"]):
            raise LLMGovernanceError("该模型已不在 BASE URL 返回列表中")
        self.storage.execute(
            "UPDATE llm_models SET enabled = ? WHERE model_id = ?",
            (int(enabled), model_id),
        )

    def list_scenes(self) -> List[Dict]:
        scenes = []
        for row in self.storage.fetchall(
            "SELECT * FROM llm_scene_policies ORDER BY frequency_class, scene_code"
        ):
            item = dict(row)
            item.update({
                "requires_access": bool(row["requires_access"]),
                "enabled": bool(row["enabled"]),
                "allow_user_selection": bool(row["allow_user_selection"]),
                "model_ids": [r["model_id"] for r in self.storage.fetchall(
                    "SELECT model_id FROM llm_scene_models WHERE scene_code = ? ORDER BY model_id",
                    (row["scene_code"],),
                )],
            })
            if row["scene_code"] == AI_SIGNAL_ANALYSIS:
                item["prompt_profiles"] = [dict(prompt) | {
                    "is_default": bool(prompt["is_default"]),
                } for prompt in self.storage.fetchall(
                    """
                    SELECT prompt_id, prompt_name, system_prompt, user_prompt_template,
                           is_default, created_at, updated_at
                    FROM llm_scene_prompts
                    WHERE scene_code = ?
                    ORDER BY is_default DESC, updated_at DESC, prompt_name
                    """,
                    (row["scene_code"],),
                )]
            else:
                item["prompt_profiles"] = []
            scenes.append(item)
        return scenes

    def scene_model_warnings(self) -> List[Dict]:
        enabled = {
            item["model_id"] for item in self.list_models()
            if item["enabled"] and item["available"]
        }
        warnings = []
        for scene in self.list_scenes():
            selected = set(scene.get("model_ids") or [])
            invalid = sorted(selected - enabled)
            default_invalid = (
                bool(scene.get("default_model_id"))
                and scene.get("default_model_id") not in enabled
            )
            if invalid or default_invalid:
                warnings.append({
                    "scene_code": scene["scene_code"],
                    "display_name": scene["display_name"],
                    "invalid_model_ids": invalid,
                    "default_model_invalid": default_invalid,
                    "message": (
                        f"{scene['display_name']} 的场景模型不在当前有效模型列表中，"
                        "请重新选择并保存。"
                    ),
                })
        return warnings

    def _validate_scene_prompt(
        self, scene_code: str, system_prompt: str, user_prompt_template: str,
    ) -> None:
        if not system_prompt or not user_prompt_template:
            raise LLMGovernanceError("场景提示词不能为空")
        if scene_code == AI_SIGNAL_ANALYSIS and (
            "{{strategy_context}}" not in user_prompt_template
            or "{{market_data}}" not in user_prompt_template
        ):
            raise LLMGovernanceError("AI行情分析提示词必须保留 {{strategy_context}} 和 {{market_data}}")
        if scene_code == BACKTEST_REPORT_ANALYSIS and "{{backtest_snapshot}}" not in user_prompt_template:
            raise LLMGovernanceError("回测报告提示词必须保留 {{backtest_snapshot}}")
        if scene_code == ALPHA_CANDIDATE_GENERATION:
            for token in (
                "{{research_description}}", "{{timeframe}}",
                "{{prediction_horizon}}", "{{candidate_count}}",
                "{{factor_catalog}}",
            ):
                if token not in user_prompt_template:
                    raise LLMGovernanceError(f"Alpha候选提示词必须保留 {token}")
        if scene_code == ALPHA_ITERATIVE_REFINEMENT:
            for token in (
                "{{research_description}}", "{{timeframe}}",
                "{{prediction_horizon}}", "{{current_candidate}}",
                "{{iteration_history}}", "{{factor_catalog}}",
            ):
                if token not in user_prompt_template:
                    raise LLMGovernanceError(f"Alpha迭代提示词必须保留 {token}")

    def _normalize_ai_prompt_profiles(self, data: Dict) -> List[Dict]:
        profiles = data.get("prompt_profiles") or []
        if not isinstance(profiles, list) or not profiles:
            raise LLMGovernanceError("AI行情分析至少需要保留一条提示词")
        normalized = []
        for profile in profiles:
            if not isinstance(profile, dict):
                raise LLMGovernanceError("提示词配置格式不正确")
            prompt_name = str(profile.get("prompt_name") or "").strip()
            if not prompt_name:
                raise LLMGovernanceError("请填写提示词名称")
            system_prompt = str(profile.get("system_prompt") or "").strip()
            user_prompt_template = str(profile.get("user_prompt_template") or "").strip()
            self._validate_scene_prompt(
                AI_SIGNAL_ANALYSIS, system_prompt, user_prompt_template,
            )
            normalized.append({
                "prompt_id": str(profile.get("prompt_id") or uuid.uuid4().hex),
                "prompt_name": prompt_name[:100],
                "system_prompt": system_prompt,
                "user_prompt_template": user_prompt_template,
                "is_default": bool(profile.get("is_default")),
            })
        if len({profile["prompt_id"] for profile in normalized}) != len(normalized):
            raise LLMGovernanceError("提示词标识重复，请删除后重新添加")
        if sum(profile["is_default"] for profile in normalized) != 1:
            raise LLMGovernanceError("AI行情分析必须且只能选择一条默认提示词")
        return normalized

    def save_scene(self, scene_code: str, data: Dict, admin_user_id: int) -> Dict:
        current = self.storage.fetchone(
            "SELECT * FROM llm_scene_policies WHERE scene_code = ?", (scene_code,)
        )
        if current is None:
            raise LLMGovernanceError("未知的大模型调用场景")
        model_ids = list(dict.fromkeys(str(v).strip() for v in data.get("model_ids", [])))
        enabled_models = {row["model_id"] for row in self.storage.fetchall(
            "SELECT model_id FROM llm_models WHERE enabled = 1 AND available = 1"
        )}
        if not model_ids or any(model not in enabled_models for model in model_ids):
            raise LLMGovernanceError("场景至少需要选择一个已启用且可用的模型")
        default_model = str(data.get("default_model_id") or "").strip()
        if default_model not in model_ids:
            raise LLMGovernanceError("默认模型必须包含在场景可用模型中")
        prompt_profiles = (
            self._normalize_ai_prompt_profiles(data)
            if scene_code == AI_SIGNAL_ANALYSIS else []
        )
        default_prompt = next(
            (profile for profile in prompt_profiles if profile["is_default"]), None,
        )
        system_prompt = (
            default_prompt["system_prompt"] if default_prompt
            else str(data.get("system_prompt") or "").strip()
        )
        user_prompt_template = (
            default_prompt["user_prompt_template"] if default_prompt
            else str(data.get("user_prompt_template") or "").strip()
        )
        if scene_code != AI_SIGNAL_ANALYSIS:
            self._validate_scene_prompt(
                scene_code, system_prompt, user_prompt_template,
            )
        self.storage.execute(
            """
            UPDATE llm_scene_policies SET enabled = ?, default_model_id = ?,
                allow_user_selection = ?, system_prompt = ?,
                user_prompt_template = ?, updated_by = ?, updated_at = ?
            WHERE scene_code = ?
            """,
            (int(bool(data.get("enabled", True))), default_model,
             int(bool(data.get("allow_user_selection", False))), system_prompt,
             user_prompt_template, admin_user_id, int(time.time()), scene_code),
        )
        self.storage.execute("DELETE FROM llm_scene_models WHERE scene_code = ?", (scene_code,))
        for model_id in model_ids:
            self.storage.execute(
                "INSERT INTO llm_scene_models(scene_code, model_id) VALUES(?, ?)",
                (scene_code, model_id),
            )
        if scene_code == AI_SIGNAL_ANALYSIS:
            now = int(time.time())
            self.storage.execute(
                "DELETE FROM llm_scene_prompts WHERE scene_code = ?", (scene_code,)
            )
            for profile in prompt_profiles:
                self.storage.execute(
                    """
                    INSERT INTO llm_scene_prompts(
                        prompt_id, scene_code, prompt_name, system_prompt,
                        user_prompt_template, is_default, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile["prompt_id"], scene_code, profile["prompt_name"],
                        profile["system_prompt"], profile["user_prompt_template"],
                        int(profile["is_default"]), now, now,
                    ),
                )
        return next(item for item in self.list_scenes() if item["scene_code"] == scene_code)

    def quota_status(self, user_id: int) -> Dict:
        start = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = int(start.timestamp())
        low_codes = tuple(code for code, _, frequency, *_ in SCENE_DEFAULTS if frequency == "low")
        placeholders = ",".join("?" for _ in low_codes)
        row = self.storage.fetchone(
            f"SELECT COUNT(*) AS used FROM llm_call_logs WHERE user_id = ? AND created_at >= ? AND scene_code IN ({placeholders})",
            (user_id, start_ts, *low_codes),
        )
        used = int(row["used"] if row else 0)
        limit = self._daily_limit(user_id)
        return {"limit": limit, "used": used, "remaining": max(0, limit - used)}

    def _daily_limit(self, user_id: int) -> int:
        row = self.storage.fetchone(
            "SELECT role, membership_level FROM users WHERE id = ?", (user_id,)
        )
        if row and row["role"] == "admin":
            return MEMBERSHIP_LIMITS["diamond"]["low_llm_daily"]
        level = str(row["membership_level"] if row else "silver")
        plan = MEMBERSHIP_LIMITS.get(level, MEMBERSHIP_LIMITS["silver"])
        return int(plan["low_llm_daily"])

    def scene_options(self, user_id: int, scene_code: str) -> Dict:
        scene = next((item for item in self.list_scenes() if item["scene_code"] == scene_code), None)
        if scene is None:
            raise LLMGovernanceError("未知的大模型调用场景")
        enabled = {item["model_id"] for item in self.list_models() if item["enabled"] and item["available"]}
        scene["models"] = [model for model in scene.pop("model_ids") if model in enabled]
        scene["quota"] = self.quota_status(user_id) if scene["frequency_class"] == "low" else None
        return scene

    def reserve_call(
        self, user_id: int, scene_code: str, requested_model: Optional[str] = None,
        object_type: str = "", object_id: str = "",
    ) -> Dict:
        scene = self.scene_options(user_id, scene_code)
        if not scene["enabled"]:
            raise LLMGovernanceError(f"{scene['display_name']}场景已被管理员停用")
        role = self.storage.fetchone("SELECT role FROM users WHERE id = ?", (user_id,))
        is_admin = bool(role and role["role"] == "admin")
        if scene["requires_access"]:
            if not self.access.get_status(user_id)["access_granted"]:
                raise PermissionError("大模型行情分析功能尚未开通")
        models = scene["models"]
        if not models:
            raise LLMGovernanceError(f"管理员尚未给{scene['display_name']}配置可用模型")
        model = str(requested_model or "").strip() or scene["default_model_id"]
        if model not in models:
            raise LLMGovernanceError("所选模型不在当前场景的可用模型列表中")
        config = self._admin_config()
        call_id = uuid.uuid4().hex
        now = int(time.time())
        if scene["frequency_class"] == "low" and not is_admin:
            start = datetime.now(CHINA_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            low_codes = tuple(
                code for code, _, frequency, *_ in SCENE_DEFAULTS
                if frequency == "low"
            )
            placeholders = ",".join("?" for _ in low_codes)
            with self.storage._lock, self.storage._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                used = conn.execute(
                    f"SELECT COUNT(*) AS used FROM llm_call_logs WHERE user_id = ? AND created_at >= ? AND scene_code IN ({placeholders})",
                    (user_id, int(start.timestamp()), *low_codes),
                ).fetchone()["used"]
                daily_limit = self._daily_limit(user_id)
                if int(used) >= daily_limit:
                    raise LLMQuotaExceeded(
                        f"今日免费大模型调用额度（{daily_limit}次）已用完，明日可继续使用"
                    )
                conn.execute(
                    """
                    INSERT INTO llm_call_logs(call_id, user_id, scene_code, model_id,
                                              object_type, object_id, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (call_id, user_id, scene_code, model, object_type, object_id, now),
                )
                conn.commit()
        else:
            self.storage.execute(
                """
                INSERT INTO llm_call_logs(call_id, user_id, scene_code, model_id,
                                          object_type, object_id, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (call_id, user_id, scene_code, model, object_type, object_id, now),
            )
        return {
            "call_id": call_id, "user_id": user_id, "scene_code": scene_code,
            "config": config, "model": model, "started_at": time.monotonic(),
            "system_prompt": scene.get("system_prompt") or "",
            "user_prompt_template": scene.get("user_prompt_template") or "",
        }

    def finish_call(self, reservation: Dict, status: str, usage: Optional[Dict] = None, error: str = "") -> None:
        usage = usage or {}
        duration_ms = int((time.monotonic() - reservation["started_at"]) * 1000)
        self.storage.execute(
            """
            UPDATE llm_call_logs SET status = ?, duration_ms = ?, prompt_tokens = ?,
                completion_tokens = ?, total_tokens = ?, error_message = ?, completed_at = ?
            WHERE call_id = ?
            """,
            (status, duration_ms,
             usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"),
             str(error)[:500], int(time.time()), reservation["call_id"]),
        )
        SystemEventLogRepository(self.storage).add({
            "user_id": reservation["user_id"],
            "level": "error" if status == "failed" else "info",
            "category": "ai",
            "event_type": f"llm_call_{status}",
            "event_name": "大模型调用失败" if status == "failed" else "大模型调用完成",
            "entity_type": "llm_call", "entity_id": reservation["call_id"],
            "correlation_id": reservation["call_id"], "status": status,
            "message": (
                str(error)[:300] if status == "failed"
                else f"{reservation['scene_code']} 使用 {reservation['model']} 完成调用"
            ),
            "detail": {
                "scene_code": reservation["scene_code"],
                "model": reservation["model"], "duration_ms": duration_ms,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            },
        })

    def overview(self) -> Dict:
        admin_user_id = self._admin_user_id()
        providers = self.configs.list_provider_configs(admin_user_id)
        return {
            "providers": providers,
            "active_provider": next(
                (provider for provider in providers if provider["active"]), None
            ),
            "models": self.list_models(),
            "scenes": self.list_scenes(),
            "scene_model_warnings": self.scene_model_warnings(),
            "free_daily_limit": FREE_DAILY_LIMIT,
        }
