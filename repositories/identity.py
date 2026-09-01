"""User identity and application metadata repositories."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from infrastructure.storage_factory import get_mysql_storage
from mysql_storage import MySQLStorage


def _now_ts() -> int:
    return int(time.time())


def _default_admin_username() -> str:
    return os.getenv("AI_TRADER_DEFAULT_ADMIN_USERNAME", "admin")


def _default_admin_password() -> str:
    return os.getenv("AI_TRADER_DEFAULT_ADMIN_PASSWORD", "admin123456")


def _default_admin_email() -> str:
    return os.getenv(
        "AI_TRADER_DEFAULT_ADMIN_EMAIL", "xingxing.wxx@foxmail.com"
    ).strip().lower()


def _runtime_username() -> str:
    return os.getenv("AI_TRADER_RUNTIME_USERNAME", _default_admin_username())


@dataclass
class UserRecord:
    user_id: int
    username: str
    email: Optional[str]
    password_hash: str
    salt: str
    role: str
    membership_level: str
    live_trading_enabled: bool
    token_version: int
    created_at: int
    updated_at: int


class MetaRepository:
    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()

    def get(self, key: str) -> Optional[str]:
        row = self.storage.fetchone("SELECT value FROM app_meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        self.storage.execute(
            """
            INSERT INTO app_meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


class UserRepository:
    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_mysql_storage()

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        return self._row_to_user(self.storage.fetchone(
            """SELECT id, username, email, password_hash, salt, role,
                      membership_level, live_trading_enabled, token_version,
                      created_at, updated_at FROM users WHERE username = ?""",
            (username,),
        ))

    def get_by_id(self, user_id: int) -> Optional[UserRecord]:
        return self._row_to_user(self.storage.fetchone(
            """SELECT id, username, email, password_hash, salt, role,
                      membership_level, live_trading_enabled, token_version,
                      created_at, updated_at FROM users WHERE id = ?""",
            (user_id,),
        ))

    def get_by_email(self, email: str) -> Optional[UserRecord]:
        return self._row_to_user(self.storage.fetchone(
            """SELECT id, username, email, password_hash, salt, role,
                      membership_level, live_trading_enabled, token_version,
                      created_at, updated_at FROM users WHERE email = ?""",
            (email,),
        ))

    def create_user(self, username: str, password_hash: str, salt: str,
                    role: str = "user", email: Optional[str] = None,
                    membership_level: str = "silver",
                    live_trading_enabled: bool = False) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """INSERT INTO users(username, email, password_hash, salt, role,
                   membership_level, live_trading_enabled, token_version,
                   created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (username, email, password_hash, salt, role, membership_level,
             int(live_trading_enabled), now, now),
        )
        user = self.get_by_username(username)
        if user is None:
            raise RuntimeError(f"创建用户失败: {username}")
        return user

    def update_password(self, user_id: int, password_hash: str,
                        salt: str) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """UPDATE users SET password_hash = ?, salt = ?,
               token_version = token_version + 1, updated_at = ? WHERE id = ?""",
            (password_hash, salt, now, user_id),
        )
        user = self.get_by_id(user_id)
        if user is None:
            raise RuntimeError("更新密码后未找到用户")
        return user

    def rotate_token_version(self, user_id: int) -> UserRecord:
        now = _now_ts()
        self.storage.execute(
            """UPDATE users SET token_version = token_version + 1,
               updated_at = ? WHERE id = ?""",
            (now, int(user_id)),
        )
        user = self.get_by_id(user_id)
        if user is None:
            raise RuntimeError("刷新登录会话后未找到用户")
        return user

    def count(self) -> int:
        row = self.storage.fetchone("SELECT COUNT(*) AS total FROM users")
        return int(row["total"]) if row else 0

    def list_users(self) -> List[UserRecord]:
        rows = self.storage.fetchall(
            """SELECT id, username, email, password_hash, salt, role,
               membership_level, live_trading_enabled, token_version,
               created_at, updated_at FROM users ORDER BY created_at, id"""
        )
        return [user for row in rows if (user := self._row_to_user(row)) is not None]

    def list_users_page(self, page: int = 1, page_size: int = 20):
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        total = self.storage.fetchone("SELECT COUNT(*) AS total FROM users")
        rows = self.storage.fetchall(
            """SELECT id, username, email, password_hash, salt, role,
               membership_level, live_trading_enabled, token_version,
               created_at, updated_at FROM users
               ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (page_size, (page - 1) * page_size),
        )
        return (
            [user for row in rows if (user := self._row_to_user(row)) is not None],
            int(total["total"] if total else 0),
        )

    def ensure_runtime_user(self, password_hash_builder) -> UserRecord:
        username = _runtime_username()
        user = self.get_by_username(username)
        if user:
            return user
        salt, password_hash = password_hash_builder(_default_admin_password())
        role = "admin" if username.strip().lower() == _default_admin_username().strip().lower() else "user"
        email = _default_admin_email() if role == "admin" else None
        return self.create_user(username, password_hash, salt, role=role, email=email)

    @staticmethod
    def _row_to_user(row: Optional[Dict]) -> Optional[UserRecord]:
        if row is None:
            return None
        return UserRecord(
            user_id=int(row["id"]), username=row["username"], email=row["email"],
            password_hash=row["password_hash"], salt=row["salt"], role=row["role"],
            membership_level=row["membership_level"],
            live_trading_enabled=bool(row["live_trading_enabled"]),
            token_version=int(row["token_version"]),
            created_at=int(row["created_at"]), updated_at=int(row["updated_at"]),
        )
