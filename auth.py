#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量认证模块
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlite_storage import MetaRepository, UserRepository, bootstrap_runtime_storage


DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 12


@dataclass
class AuthUser:
    """认证后的用户"""
    user_id: int
    username: str
    email: Optional[str] = None
    role: str = "user"
    token_version: int = 1


class UsernameAlreadyExistsError(ValueError):
    """注册用户名已存在。"""


class AuthManager:
    """SQLite 认证管理器"""

    def __init__(self, auth_file: Optional[str] = None):
        self.legacy_auth_file = Path(
            auth_file
            or os.getenv("AI_TRADER_AUTH_FILE")
            or Path(__file__).resolve().parent / ".auth_users.json"
        )
        self.default_username = os.getenv("AI_TRADER_DEFAULT_ADMIN_USERNAME", "admin")
        self.default_password = os.getenv("AI_TRADER_DEFAULT_ADMIN_PASSWORD", "admin123456")
        self.token_ttl_seconds = int(
            os.getenv("AI_TRADER_AUTH_TOKEN_TTL", str(DEFAULT_TOKEN_TTL_SECONDS))
        )
        self._lock = threading.RLock()
        self.meta_repo = MetaRepository()
        self.user_repo = UserRepository()
        self._ensure_store()

    def _ensure_store(self) -> None:
        with self._lock:
            bootstrap_runtime_storage(self._build_password_credentials)

    def _build_password_credentials(self, password: str):
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        return salt, password_hash

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return digest.hex()

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 8 or len(password) > 128:
            raise ValueError("密码长度需为 8-128 位")
        if not any(ch.isalpha() for ch in password) or not any(
            ch.isdigit() for ch in password
        ):
            raise ValueError("密码必须同时包含字母和数字")

    @staticmethod
    def _to_auth_user(record) -> AuthUser:
        return AuthUser(
            user_id=record.user_id,
            username=record.username,
            email=record.email,
            role=record.role,
            token_version=record.token_version,
        )

    def authenticate(self, username: str, password: str) -> Optional[AuthUser]:
        with self._lock:
            requested_username = username.strip()
            user = self.user_repo.get_by_username(requested_username)
            if user is None and requested_username != requested_username.lower():
                user = self.user_repo.get_by_username(requested_username.lower())
            if user:
                actual = self._hash_password(password, user.salt)
                if hmac.compare_digest(user.password_hash, actual):
                    return self._to_auth_user(user)
        return None

    def register(self, username: str, password: str, email: str) -> AuthUser:
        normalized_username = username.strip().lower()
        normalized_email = email.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{3,32}", normalized_username):
            raise ValueError("用户名需为 3-32 位，仅支持字母、数字、下划线和短横线")
        self._validate_password(password)

        with self._lock:
            if self.user_repo.get_by_username(normalized_username):
                raise UsernameAlreadyExistsError("用户名已被注册")
            if self.user_repo.get_by_email(normalized_email):
                raise UsernameAlreadyExistsError("该邮箱已被注册")

            salt, password_hash = self._build_password_credentials(password)
            try:
                record = self.user_repo.create_user(
                    normalized_username,
                    password_hash,
                    salt,
                    role="user",
                    email=normalized_email,
                )
            except sqlite3.IntegrityError as exc:
                raise UsernameAlreadyExistsError("用户名或邮箱已被注册") from exc

        return self._to_auth_user(record)

    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> AuthUser:
        self._validate_password(new_password)

        with self._lock:
            record = self.user_repo.get_by_id(user_id)
            if record is None:
                raise ValueError("用户不存在")

            actual = self._hash_password(current_password, record.salt)
            if not hmac.compare_digest(record.password_hash, actual):
                raise ValueError("当前密码不正确")

            if hmac.compare_digest(
                record.password_hash,
                self._hash_password(new_password, record.salt),
            ):
                raise ValueError("新密码不能与当前密码相同")

            salt, password_hash = self._build_password_credentials(new_password)
            updated = self.user_repo.update_password(
                user_id,
                password_hash,
                salt,
            )

        return self._to_auth_user(updated)

    def create_token(self, user: AuthUser) -> str:
        with self._lock:
            secret = self.meta_repo.get("auth_secret") or ""

        payload = {
            "sub": user.username,
            "exp": int(time.time()) + self.token_ttl_seconds,
            "nonce": secrets.token_hex(8),
            "ver": user.token_version,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
        signature = hmac.new(
            secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded_payload}.{signature}"

    def verify_token(self, token: str) -> AuthUser:
        try:
            encoded_payload, signature = token.split(".", 1)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的登录凭证",
            ) from exc

        with self._lock:
            secret = self.meta_repo.get("auth_secret") or ""

        expected_signature = hmac.new(
            secret.encode("utf-8"),
            encoded_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录凭证校验失败",
            )

        padding = "=" * (-len(encoded_payload) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(f"{encoded_payload}{padding}").decode("utf-8")
        )

        if payload.get("exp", 0) < int(time.time()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录已过期，请重新登录",
            )

        username = payload.get("sub")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的登录凭证",
            )

        user = self.user_repo.get_by_username(username)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在或已被删除",
            )
        if int(payload.get("ver", 1)) != user.token_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录状态已失效，请重新登录",
            )
        return self._to_auth_user(user)


_AUTH_MANAGER: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _AUTH_MANAGER
    if _AUTH_MANAGER is None:
        _AUTH_MANAGER = AuthManager()
    return _AUTH_MANAGER


def reset_auth_manager() -> None:
    global _AUTH_MANAGER
    _AUTH_MANAGER = None


def require_auth(authorization: Optional[str] = Header(default=None)) -> AuthUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )

    return get_auth_manager().verify_token(token)


def require_admin(user: AuthUser = Depends(require_auth)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可以执行此操作",
        )
    return user
