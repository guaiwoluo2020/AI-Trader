"""用户资源配额与管理员白名单覆盖。"""

from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Dict, Optional

from membership import MEMBERSHIP_LIMITS, MembershipService
from sqlite_storage import get_storage


RESOURCE_LABELS = {
    "datasets": "历史行情数据集",
    "strategies": "策略",
    "signal_sources": "信号源",
}

DEFAULT_LIMITS = {
    key: MEMBERSHIP_LIMITS["silver"][key]
    for key in ("datasets", "strategies", "signal_sources")
}


class QuotaExceededError(ValueError):
    """用户试图创建超过其允许数量的资源。"""


class UserQuotaRepository:
    """管理用户的配额白名单覆盖值。"""

    FIELDS = tuple(DEFAULT_LIMITS)

    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    def get_overrides(self, user_id: int) -> Dict[str, Optional[int]]:
        row = self.storage.fetchone(
            """
            SELECT max_datasets, max_strategies, max_signal_sources
            FROM user_quota_overrides WHERE user_id = ?
            """,
            (int(user_id),),
        )
        if row is None:
            return {field: None for field in self.FIELDS}
        return {
            "datasets": row["max_datasets"],
            "strategies": row["max_strategies"],
            "signal_sources": row["max_signal_sources"],
        }

    def save_overrides(
        self, user_id: int, values: Dict[str, Optional[int]], updated_by: int,
    ) -> Dict[str, Optional[int]]:
        normalized = {}
        for field in self.FIELDS:
            value = values.get(field)
            if value is None:
                normalized[field] = None
                continue
            value = int(value)
            if not 0 <= value <= 1000:
                raise ValueError("配额须为 0-1000 的整数，或留空使用默认值")
            normalized[field] = value

        self.storage.execute(
            """
            INSERT INTO user_quota_overrides(
                user_id, max_datasets, max_strategies, max_signal_sources,
                updated_by, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                max_datasets = excluded.max_datasets,
                max_strategies = excluded.max_strategies,
                max_signal_sources = excluded.max_signal_sources,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id), normalized["datasets"], normalized["strategies"],
                normalized["signal_sources"], int(updated_by), int(time.time()),
            ),
        )
        return self.get_overrides(user_id)


class UserQuotaService:
    """在后端创建入口集中执行资源配额校验。"""

    def __init__(self, storage=None):
        self.storage = storage or get_storage()
        self.repository = UserQuotaRepository(self.storage)
        self.memberships = MembershipService(self.storage)

    @contextmanager
    def guarded(self):
        """将校验和紧随其后的写入串行化，防止并发请求绕过限额。"""
        with self.storage._lock:
            yield

    def get_usage(self, user_id: int) -> Dict[str, int]:
        datasets = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM backtest_datasets WHERE user_id = ?",
            (int(user_id),),
        )
        strategies = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM user_strategy_configs WHERE user_id = ?",
            (int(user_id),),
        )
        signal_sources = self.storage.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM user_strategy_configs, json_each(config_json, '$.signal_sources')
            WHERE user_id = ? AND json_valid(config_json)
              AND (
                json_extract(value, '$.source') != 'ai_entry'
                OR COALESCE(json_extract(value, '$.params.ai_signal_source_id'), '') = ''
              )
            """,
            (int(user_id),),
        )
        ai_signal_sources = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM ai_signal_sources WHERE user_id = ?",
            (int(user_id),),
        )
        return {
            "datasets": int(datasets["total"] if datasets else 0),
            "strategies": int(strategies["total"] if strategies else 0),
            "signal_sources": (
                int(signal_sources["total"] if signal_sources else 0)
                + int(ai_signal_sources["total"] if ai_signal_sources else 0)
            ),
        }

    def get_summary(self, user_id: int, role: str = "user") -> Dict:
        usage = self.get_usage(user_id)
        overrides = self.repository.get_overrides(user_id)
        admin = role == "admin"
        access = self.memberships.get_access(user_id)
        plan_limits = access["limits"]
        limits = {
            field: None if admin else (
                overrides[field] if overrides[field] is not None else plan_limits[field]
            )
            for field in DEFAULT_LIMITS
        }
        return {
            "usage": usage,
            "limits": limits,
            "overrides": overrides,
            "is_unlimited": admin,
            "membership": access,
        }

    def assert_capacity(
        self, user_id: int, role: str, resource: str, requested_total: int,
    ) -> None:
        if resource not in DEFAULT_LIMITS:
            raise ValueError(f"未知的配额资源: {resource}")
        if role == "admin":
            return
        summary = self.get_summary(user_id, role)
        limit = summary["limits"][resource]
        if limit is not None and requested_total > limit:
            raise QuotaExceededError(
                f"{RESOURCE_LABELS[resource]}已达上限（{summary['usage'][resource]}/{limit}），"
                "请删除不需要的配置或联系管理员扩容"
            )

    def assert_can_create(self, user_id: int, role: str, resource: str, amount: int = 1) -> None:
        usage = self.get_usage(user_id)
        self.assert_capacity(user_id, role, resource, usage[resource] + int(amount))

    def assert_strategy_sources(
        self, user_id: int, role: str, strategy_id: str, sources: list,
    ) -> None:
        current = self.storage.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM user_strategy_configs, json_each(config_json, '$.signal_sources')
            WHERE user_id = ? AND strategy_id != ? AND json_valid(config_json)
              AND (
                json_extract(value, '$.source') != 'ai_entry'
                OR COALESCE(json_extract(value, '$.params.ai_signal_source_id'), '') = ''
              )
            """,
            (int(user_id), str(strategy_id)),
        )
        independent_ai = self.storage.fetchone(
            "SELECT COUNT(*) AS total FROM ai_signal_sources WHERE user_id = ?",
            (int(user_id),),
        )
        requested_total = (
            int(current["total"] if current else 0)
            + sum(
                1 for source in (sources or [])
                if source.get("source") != "ai_entry"
                or not str((source.get("params") or {}).get("ai_signal_source_id") or "")
            )
            + int(independent_ai["total"] if independent_ai else 0)
        )
        self.assert_capacity(user_id, role, "signal_sources", requested_total)
