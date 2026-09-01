#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EA 请求身份认证。"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Query, Request, status

from mysql_repositories import TradingAccountRepository


MINIMUM_EA_VERSION = "2.0.7"


def _ea_version_tuple(value: str) -> tuple[int, int, int]:
    text = str(value or "").strip()
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts) or len(parts) > 3:
        raise ValueError("EA 版本格式无效")
    numbers = [int(part) for part in parts]
    # Historical MT5 display versions used 2.07 for semantic 2.0.7.
    if len(numbers) == 2:
        numbers = [numbers[0], 0, numbers[1]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def ensure_supported_ea_version(value: str) -> str:
    version = str(value or "").strip()
    try:
        supported = _ea_version_tuple(version) >= _ea_version_tuple(
            MINIMUM_EA_VERSION
        )
    except ValueError:
        supported = False
    if not supported:
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail={
                "code": "ea_upgrade_required",
                "message": f"EA 版本过低，请升级到 {MINIMUM_EA_VERSION} 或更高版本",
                "current_version": version or None,
                "required_version": MINIMUM_EA_VERSION,
            },
            headers={"Upgrade": f"AI-Trader-EA/{MINIMUM_EA_VERSION}"},
        )
    return version


@dataclass
class EAIdentity:
    user_id: int
    account_id: int
    account_key: str
    ea_version: str


def require_ea_auth(
    request: Request,
    user_id: Optional[int] = Query(default=None, description="Web 登录用户 ID"),
    ea_token: Optional[str] = Query(default=None, description="EA 账户凭证"),
    x_ea_user_id: Optional[int] = Header(default=None, alias="X-EA-User-ID"),
    x_ea_token: Optional[str] = Header(default=None, alias="X-EA-Token"),
    x_ea_version: Optional[str] = Header(default=None, alias="X-EA-Version"),
) -> EAIdentity:
    """允许 EA 通过查询参数或请求头提交账户凭证。"""
    ea_version = ensure_supported_ea_version(x_ea_version or "")
    resolved_user_id = (
        user_id
        if isinstance(user_id, int)
        else x_ea_user_id if isinstance(x_ea_user_id, int) else None
    )
    resolved_token = (
        ea_token
        if isinstance(ea_token, str) and ea_token
        else x_ea_token if isinstance(x_ea_token, str) and x_ea_token else None
    )
    if resolved_user_id is None or not resolved_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="EA 请求缺少 user_id 或 ea_token",
        )

    account = TradingAccountRepository().authenticate(
        resolved_user_id,
        resolved_token,
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="EA 账户凭证无效",
        )

    identity = EAIdentity(
        user_id=account.user_id,
        account_id=account.account_id,
        account_key=account.account_key,
        ea_version=ea_version,
    )
    request.state.ea_identity = identity
    return identity
