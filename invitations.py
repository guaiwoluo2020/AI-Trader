"""Private invitation codes used to gate account registration."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Dict, Optional

from mysql_repositories import MySQLStorage, get_storage


class InvitationError(ValueError):
    """The invitation is missing, expired, disabled, or exhausted."""


class InvitationService:
    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_storage()

    @staticmethod
    def normalize(code: str) -> str:
        return "".join(str(code or "").strip().upper().split())

    @classmethod
    def _hash(cls, code: str) -> str:
        return hashlib.sha256(cls.normalize(code).encode("utf-8")).hexdigest()

    def create(
        self, created_by: int, label: str = "", max_uses: int = 1,
        expires_days: Optional[int] = 7,
    ) -> Dict:
        max_uses = int(max_uses)
        if not 1 <= max_uses <= 1000:
            raise InvitationError("邀请码使用次数须为 1-1000")
        if expires_days is not None:
            expires_days = int(expires_days)
            if not 1 <= expires_days <= 365:
                raise InvitationError("邀请码有效期须为 1-365 天")
        clean_label = str(label or "").strip()[:80]
        code = secrets.token_hex(6).upper()
        now = int(time.time())
        expires_at = now + expires_days * 86400 if expires_days else None
        invitation_id = uuid.uuid4().hex[:16]
        self.storage.execute(
            """
            INSERT INTO invitation_codes(
                invitation_id, code_hash, code_prefix, label, max_uses,
                expires_at, created_by, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invitation_id, self._hash(code), code[:4], clean_label,
                max_uses, expires_at, int(created_by), now, now,
            ),
        )
        return {**self.get(invitation_id), "code": code}

    def get(self, invitation_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT * FROM invitation_codes WHERE invitation_id = ?",
            (str(invitation_id),),
        )
        return self._to_dict(row) if row else None

    def list_all(self) -> list[Dict]:
        return [
            self._to_dict(row)
            for row in self.storage.fetchall(
                "SELECT * FROM invitation_codes ORDER BY created_at DESC"
            )
        ]

    def assert_available(self, code: str) -> Dict:
        normalized = self.normalize(code)
        if not normalized:
            raise InvitationError("请输入邀请码或通过邀请链接访问")
        row = self.storage.fetchone(
            "SELECT * FROM invitation_codes WHERE code_hash = ?",
            (self._hash(normalized),),
        )
        if row is None:
            raise InvitationError("邀请码无效")
        now = int(time.time())
        if not bool(row["active"]):
            raise InvitationError("邀请码已停用")
        if row["expires_at"] is not None and int(row["expires_at"]) < now:
            raise InvitationError("邀请码已过期")
        if int(row["used_count"]) >= int(row["max_uses"]):
            raise InvitationError("邀请码使用次数已达上限")
        return self._to_dict(row)

    def claim(self, code: str) -> str:
        invitation = self.assert_available(code)
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE invitation_codes
                SET used_count = used_count + 1, updated_at = ?
                WHERE invitation_id = ? AND active = 1
                  AND used_count < max_uses
                  AND (expires_at IS NULL OR expires_at >= ?)
                """,
                (now, invitation["invitation_id"], now),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise InvitationError("邀请码已被使用或不可用")
        return invitation["invitation_id"]

    def release(self, invitation_id: str) -> None:
        self.storage.execute(
            """
            UPDATE invitation_codes
            SET used_count = MAX(0, used_count - 1), updated_at = ?
            WHERE invitation_id = ?
            """,
            (int(time.time()), str(invitation_id)),
        )

    def set_active(self, invitation_id: str, active: bool) -> Dict:
        self.storage.execute(
            """
            UPDATE invitation_codes SET active = ?, updated_at = ?
            WHERE invitation_id = ?
            """,
            (int(bool(active)), int(time.time()), str(invitation_id)),
        )
        invitation = self.get(invitation_id)
        if invitation is None:
            raise InvitationError("邀请码不存在")
        return invitation

    @staticmethod
    def _to_dict(row) -> Dict:
        now = int(time.time())
        expires_at = int(row["expires_at"]) if row["expires_at"] is not None else None
        available = bool(
            row["active"] and int(row["used_count"]) < int(row["max_uses"])
            and (expires_at is None or expires_at >= now)
        )
        return {
            "invitation_id": row["invitation_id"],
            "code_prefix": row["code_prefix"],
            "label": row["label"],
            "max_uses": int(row["max_uses"]),
            "used_count": int(row["used_count"]),
            "expires_at": expires_at,
            "active": bool(row["active"]),
            "available": available,
            "created_by": int(row["created_by"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }
