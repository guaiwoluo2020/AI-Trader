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
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from fastapi import Header, HTTPException, status


DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 12


@dataclass
class AuthUser:
    """认证后的用户"""
    username: str


class AuthManager:
    """本地文件认证管理器"""

    def __init__(self, auth_file: Optional[str] = None):
        root = Path(__file__).resolve().parent
        self.auth_file = Path(
            auth_file
            or os.getenv("AI_TRADER_AUTH_FILE")
            or root / ".auth_users.json"
        )
        self.default_username = os.getenv("AI_TRADER_DEFAULT_ADMIN_USERNAME", "admin")
        self.default_password = os.getenv("AI_TRADER_DEFAULT_ADMIN_PASSWORD", "admin123456")
        self.token_ttl_seconds = int(
            os.getenv("AI_TRADER_AUTH_TOKEN_TTL", str(DEFAULT_TOKEN_TTL_SECONDS))
        )
        self._lock = threading.RLock()
        self._ensure_store()

    def _ensure_store(self) -> None:
        with self._lock:
            if self.auth_file.exists():
                data = self._read_store()
                if data.get("users"):
                    return
            self._write_store(
                {
                    "secret": secrets.token_urlsafe(32),
                    "users": [self._build_user_record(self.default_username, self.default_password)],
                }
            )

    def _build_user_record(self, username: str, password: str) -> Dict:
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        now = int(time.time())
        return {
            "username": username,
            "password_hash": password_hash,
            "salt": salt,
            "created_at": now,
            "updated_at": now,
        }

    def _read_store(self) -> Dict:
        if not self.auth_file.exists():
            return {}
        return json.loads(self.auth_file.read_text(encoding="utf-8"))

    def _write_store(self, data: Dict) -> None:
        self.auth_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        )
        return digest.hex()

    def authenticate(self, username: str, password: str) -> Optional[AuthUser]:
        with self._lock:
            store = self._read_store()
            users = store.get("users", [])
            for user in users:
                if user.get("username") != username:
                    continue
                expected = user.get("password_hash", "")
                actual = self._hash_password(password, user.get("salt", ""))
                if hmac.compare_digest(expected, actual):
                    return AuthUser(username=username)
        return None

    def create_token(self, user: AuthUser) -> str:
        with self._lock:
            store = self._read_store()
            secret = store["secret"]

        payload = {
            "sub": user.username,
            "exp": int(time.time()) + self.token_ttl_seconds,
            "nonce": secrets.token_hex(8),
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
            store = self._read_store()
            secret = store.get("secret", "")

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
        return AuthUser(username=username)


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
