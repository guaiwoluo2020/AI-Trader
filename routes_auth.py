#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证相关路由
"""

from fastapi import APIRouter, Depends, HTTPException, status

from auth import AuthUser, get_auth_manager, require_auth
from models import AuthUserInfo, LoginRequest, LoginResponse


def create_auth_routes() -> APIRouter:
    """创建认证路由"""
    router = APIRouter(prefix="/auth", tags=["认证"])

    @router.post("/login", response_model=LoginResponse)
    async def login(payload: LoginRequest) -> LoginResponse:
        auth_manager = get_auth_manager()
        user = auth_manager.authenticate(payload.username, payload.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

        return LoginResponse(
            status="ok",
            token=auth_manager.create_token(user),
            expires_in=auth_manager.token_ttl_seconds,
            user=AuthUserInfo(username=user.username),
        )

    @router.get("/me")
    async def me(user: AuthUser = Depends(require_auth)):
        return {
            "status": "ok",
            "user": AuthUserInfo(username=user.username).model_dump(),
        }

    return router
