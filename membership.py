"""会员等级、资源权益与实盘授权。"""

from __future__ import annotations

import time
from typing import Dict, Optional

from mysql_repositories import MySQLStorage, get_storage


MEMBERSHIP_LEVELS = ("normal", "silver", "gold", "diamond")
MEMBERSHIP_LABELS = {
    "normal": "普通用户",
    "silver": "白银会员",
    "gold": "黄金会员",
    "diamond": "钻石会员",
}
MEMBERSHIP_LIMITS = {
    "normal": {
        "datasets": 3, "strategies": 1, "signal_sources": 3,
        "paper_accounts": 1, "live_accounts": 0, "low_llm_daily": 5,
    },
    "silver": {
        "datasets": 10, "strategies": 5, "signal_sources": 10,
        "paper_accounts": 2, "live_accounts": 0, "low_llm_daily": 30,
    },
    "gold": {
        "datasets": 30, "strategies": 20, "signal_sources": 50,
        "paper_accounts": 5, "live_accounts": 1, "low_llm_daily": 100,
    },
    "diamond": {
        "datasets": 100, "strategies": 100, "signal_sources": 300,
        "paper_accounts": 20, "live_accounts": 5, "low_llm_daily": 500,
    },
}


class MembershipError(ValueError):
    """会员权益不足或会员配置无效。"""


class MembershipService:
    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def normalize_level(level: str) -> str:
        normalized = str(level or "").strip().lower()
        if normalized not in MEMBERSHIP_LEVELS:
            raise MembershipError("会员等级必须是普通、白银、黄金或钻石")
        return normalized

    def get_access(self, user_id: int) -> Dict:
        row = self.storage.fetchone(
            """
            SELECT id, role, membership_level, live_trading_enabled
            FROM users WHERE id = ?
            """,
            (int(user_id),),
        )
        if row is None:
            raise MembershipError("用户不存在")
        level = self.normalize_level(row["membership_level"] or "silver")
        is_admin = row["role"] == "admin"
        limits = {
            key: None if is_admin else value
            for key, value in MEMBERSHIP_LIMITS[level].items()
        }
        live_enabled = bool(is_admin or row["live_trading_enabled"])
        live_eligible = bool(is_admin or MEMBERSHIP_LIMITS[level]["live_accounts"] > 0)
        return {
            "membership_level": level,
            "membership_label": MEMBERSHIP_LABELS[level],
            "live_trading_enabled": live_enabled,
            "live_trading_eligible": live_eligible,
            "can_live_trade": live_enabled and live_eligible,
            "limits": limits,
            "is_admin": is_admin,
        }

    def update_user(
        self, user_id: int, membership_level: str,
        live_trading_enabled: bool, updated_by: int,
    ) -> Dict:
        level = self.normalize_level(membership_level)
        target = self.storage.fetchone(
            "SELECT id, role FROM users WHERE id = ?", (int(user_id),)
        )
        if target is None:
            raise MembershipError("用户不存在")
        if target["role"] == "admin":
            raise MembershipError("管理员账号不需要设置会员等级")
        enabled = bool(live_trading_enabled)
        if enabled and MEMBERSHIP_LIMITS[level]["live_accounts"] <= 0:
            raise MembershipError("只有黄金或钻石会员可以开通实盘交易")

        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET membership_level = ?, live_trading_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (level, int(enabled), now, int(user_id)),
            )
            if not enabled:
                conn.execute(
                    """
                    DELETE FROM runtime_entities
                    WHERE user_id = ?
                      AND entity_type IN ('pending_order', 'trading_instruction')
                    """,
                    (int(user_id),),
                )
            conn.commit()
        from system_event_log import SystemEventLogRepository
        SystemEventLogRepository(self.storage).add({
            "user_id": int(updated_by),
            "level": "info",
            "category": "security",
            "event_type": "user_membership_updated",
            "event_name": "用户会员权益已更新",
            "entity_type": "user",
            "entity_id": str(user_id),
            "status": "completed",
            "message": (
                f"用户 {user_id} 调整为 {MEMBERSHIP_LABELS[level]}，"
                f"实盘授权={'开启' if enabled else '关闭'}"
            ),
            "detail": {
                "membership_level": level,
                "live_trading_enabled": enabled,
            },
        })
        return self.get_access(user_id)

    def assert_live_trading(self, user_id: int, account_id: Optional[int] = None) -> Dict:
        access = self.get_access(user_id)
        if not access["can_live_trade"]:
            if not access["live_trading_eligible"]:
                raise MembershipError("实盘交易仅向黄金和钻石会员开放")
            raise MembershipError("实盘交易尚未由管理员授权")

        limit = access["limits"]["live_accounts"]
        if limit is not None and account_id is not None:
            rows = self.storage.fetchall(
                """
                SELECT DISTINCT account_id FROM strategy_deployments
                WHERE user_id = ? AND execution_mode = 'live' AND status = 'active'
                """,
                (int(user_id),),
            )
            active_accounts = {int(row["account_id"]) for row in rows}
            if int(account_id) not in active_accounts and len(active_accounts) >= limit:
                raise MembershipError(f"当前会员等级最多运行 {limit} 个实盘账户")
        return access

    def paper_account_limit(self, user_id: int) -> Optional[int]:
        return self.get_access(user_id)["limits"]["paper_accounts"]
