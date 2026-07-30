#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EA 请求身份认证。"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Header, HTTPException, Query, Request, status

from sqlite_storage import TradingAccountRepository


@dataclass
class EAIdentity:
    user_id: int
    account_id: int
    account_key: str


def require_ea_auth(
    request: Request,
    user_id: Optional[int] = Query(default=None, description="Web 登录用户 ID"),
    ea_token: Optional[str] = Query(default=None, description="EA 账户凭证"),
    x_ea_user_id: Optional[int] = Header(default=None, alias="X-EA-User-ID"),
    x_ea_token: Optional[str] = Header(default=None, alias="X-EA-Token"),
) -> EAIdentity:
    """允许 EA 通过查询参数或请求头提交账户凭证。"""
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
    )
    request.state.ea_identity = identity
    return identity
