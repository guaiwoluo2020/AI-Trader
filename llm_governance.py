#!/usr/bin/env python3
"""Central model catalog, scene routing, quota enforcement and LLM audit."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from sqlite_storage import LLMAccessRepository, LLMConfigRepository, SQLiteStorage, get_storage
from system_event_log import SystemEventLogRepository


AI_SIGNAL_ANALYSIS = "ai_signal_analysis"
BACKTEST_REPORT_ANALYSIS = "backtest_report_analysis"
ALPHA_CANDIDATE_GENERATION = "alpha_candidate_generation"
ALPHA_ITERATIVE_REFINEMENT = "alpha_iterative_refinement"

SCENE_DEFAULTS = (
    (AI_SIGNAL_ANALYSIS, "AI 行情与交易信号", "high", 1, 1),
    (BACKTEST_REPORT_ANALYSIS, "回测报告分析", "low", 0, 0),
    (ALPHA_CANDIDATE_GENERATION, "Alpha 候选生成", "low", 0, 0),
    (ALPHA_ITERATIVE_REFINEMENT, "Alpha 迭代优化", "low", 0, 0),
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
        for code, name, frequency, requires_access, selectable in SCENE_DEFAULTS:
            self.storage.execute(
                """
                INSERT OR IGNORE INTO llm_scene_policies(
                    scene_code, display_name, frequency_class, requires_access,
                    enabled, default_model_id, allow_user_selection, updated_at
                ) VALUES(?, ?, ?, ?, 1, '', ?, ?)
                """,
                (code, name, frequency, requires_access, selectable, now),
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
            scenes.append(item)
        return scenes

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
        self.storage.execute(
            """
            UPDATE llm_scene_policies SET enabled = ?, default_model_id = ?,
                allow_user_selection = ?, updated_by = ?, updated_at = ?
            WHERE scene_code = ?
            """,
            (int(bool(data.get("enabled", True))), default_model,
             int(bool(data.get("allow_user_selection", False))), admin_user_id,
             int(time.time()), scene_code),
        )
        self.storage.execute("DELETE FROM llm_scene_models WHERE scene_code = ?", (scene_code,))
        for model_id in model_ids:
            self.storage.execute(
                "INSERT INTO llm_scene_models(scene_code, model_id) VALUES(?, ?)",
                (scene_code, model_id),
            )
        return next(item for item in self.list_scenes() if item["scene_code"] == scene_code)

    def quota_status(self, user_id: int) -> Dict:
        start = datetime.now(CHINA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        start_ts = int(start.timestamp())
        low_codes = tuple(code for code, _, frequency, _, _ in SCENE_DEFAULTS if frequency == "low")
        placeholders = ",".join("?" for _ in low_codes)
        row = self.storage.fetchone(
            f"SELECT COUNT(*) AS used FROM llm_call_logs WHERE user_id = ? AND created_at >= ? AND scene_code IN ({placeholders})",
            (user_id, start_ts, *low_codes),
        )
        used = int(row["used"] if row else 0)
        return {"limit": FREE_DAILY_LIMIT, "used": used, "remaining": max(0, FREE_DAILY_LIMIT - used)}

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
                code for code, _, frequency, _, _ in SCENE_DEFAULTS
                if frequency == "low"
            )
            placeholders = ",".join("?" for _ in low_codes)
            with self.storage._lock, self.storage._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                used = conn.execute(
                    f"SELECT COUNT(*) AS used FROM llm_call_logs WHERE user_id = ? AND created_at >= ? AND scene_code IN ({placeholders})",
                    (user_id, int(start.timestamp()), *low_codes),
                ).fetchone()["used"]
                if int(used) >= FREE_DAILY_LIMIT:
                    raise LLMQuotaExceeded(
                        f"今日免费大模型调用额度（{FREE_DAILY_LIMIT}次）已用完，明日可继续使用"
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
        return {
            "models": self.list_models(),
            "scenes": self.list_scenes(),
            "free_daily_limit": FREE_DAILY_LIMIT,
        }
