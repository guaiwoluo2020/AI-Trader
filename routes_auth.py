#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
认证相关路由
"""

import asyncio
import os
import smtplib
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from auth import (
    AuthUser,
    UsernameAlreadyExistsError,
    get_auth_manager,
    require_admin,
    require_auth,
)
from models import (
    AuthUserInfo,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    SendEmailCodeRequest,
    SystemEmailConfigRequest,
    TestSystemEmailRequest,
    UserQuotaOverrideRequest,
)
from email_verification import (
    EmailVerificationError,
    EmailVerificationService,
    SystemEmailConfigRepository,
)
from sqlite_storage import EAActivationRepository, TradingAccountRepository, UserRepository
from trading_engine_manager import TradingEngineManager
from user_quotas import UserQuotaService


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_EA_ARTIFACT = ROOT_DIR / "dist" / "mt5TerminalEA.ex5"
EA_ACTIVATION_TTL_SECONDS = 10 * 60


def create_auth_routes(
    engine_manager: TradingEngineManager = None,
) -> APIRouter:
    """创建认证路由"""
    router = APIRouter(prefix="/auth", tags=["认证"])
    email_service = EmailVerificationService()
    email_config_repository = SystemEmailConfigRepository()
    quota_service = UserQuotaService()
    user_repository = UserRepository()

    @router.post("/email-code")
    async def send_registration_email_code(
        payload: SendEmailCodeRequest,
        request: Request,
    ):
        try:
            requester = request.client.host if request.client else "unknown"
            result = await asyncio.to_thread(
                email_service.send_code, payload.email, requester
            )
            return {"status": "ok", "message": "验证码已发送，请检查邮箱", **result}
        except EmailVerificationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except (RuntimeError, OSError, smtplib.SMTPException) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"验证码发送失败: {exc}",
            ) from exc

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
            user=_user_info(user),
            next_path=(
                "/"
                if EAActivationRepository().has_downloaded(user.user_id)
                else "/mt5-setup"
            ),
        )

    @router.post(
        "/register",
        response_model=LoginResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(payload: RegisterRequest) -> LoginResponse:
        auth_manager = get_auth_manager()
        try:
            email = email_service.assert_valid_code(
                payload.email, payload.verification_code
            )
            user = auth_manager.register(payload.username, payload.password, email)
            email_service.consume(email)
        except UsernameAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        return LoginResponse(
            status="ok",
            token=auth_manager.create_token(user),
            expires_in=auth_manager.token_ttl_seconds,
            user=_user_info(user),
            next_path="/mt5-setup",
        )

    @router.get("/admin/email-config")
    async def get_system_email_config(
        user: AuthUser = Depends(require_admin),
    ):
        return {"status": "ok", "config": email_config_repository.get()}

    @router.put("/admin/email-config")
    async def save_system_email_config(
        payload: SystemEmailConfigRequest,
        user: AuthUser = Depends(require_admin),
    ):
        try:
            config = email_config_repository.save(payload.model_dump(), user.user_id)
            return {"status": "ok", "message": "邮件服务配置已加密保存", "config": config}
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @router.post("/admin/email-config/test")
    async def test_system_email_config(
        payload: TestSystemEmailRequest,
        user: AuthUser = Depends(require_admin),
    ):
        try:
            result = await asyncio.to_thread(email_service.send_test, payload.target_email)
            return {"status": "ok", "message": "测试邮件已发送", **result}
        except (EmailVerificationError, RuntimeError, OSError, smtplib.SMTPException) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"测试邮件发送失败: {exc}",
            ) from exc

    @router.get("/quota")
    async def get_my_quota(user: AuthUser = Depends(require_auth)):
        return {
            "status": "ok",
            "quota": quota_service.get_summary(user.user_id, user.role),
        }

    @router.get("/admin/user-quotas")
    async def list_user_quotas(user: AuthUser = Depends(require_admin)):
        users = []
        for record in user_repository.list_users():
            summary = quota_service.get_summary(record.user_id, record.role)
            users.append({
                "user_id": record.user_id,
                "username": record.username,
                "email": record.email,
                "role": record.role,
                **summary,
            })
        return {"status": "ok", "users": users}

    @router.put("/admin/users/{user_id}/quota")
    async def save_user_quota(
        user_id: int,
        payload: UserQuotaOverrideRequest,
        user: AuthUser = Depends(require_admin),
    ):
        target = user_repository.get_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        try:
            overrides = quota_service.repository.save_overrides(
                user_id,
                {
                    "datasets": payload.max_datasets,
                    "strategies": payload.max_strategies,
                    "signal_sources": payload.max_signal_sources,
                },
                user.user_id,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "status": "ok",
            "message": "用户配额白名单已保存",
            "quota": {
                **quota_service.get_summary(target.user_id, target.role),
                "overrides": overrides,
            },
        }

    @router.get("/me")
    async def me(user: AuthUser = Depends(require_auth)):
        return {
            "status": "ok",
            "user": _user_info(user).model_dump(),
        }

    @router.post(
        "/change-password",
        response_model=ChangePasswordResponse,
    )
    async def change_password(
        payload: ChangePasswordRequest,
        user: AuthUser = Depends(require_auth),
    ) -> ChangePasswordResponse:
        auth_manager = get_auth_manager()
        try:
            updated_user = auth_manager.change_password(
                user.user_id,
                payload.current_password,
                payload.new_password,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        return ChangePasswordResponse(
            status="ok",
            message="密码修改成功",
            token=auth_manager.create_token(updated_user),
            expires_in=auth_manager.token_ttl_seconds,
            user=_user_info(updated_user),
        )

    @router.get("/mt5-binding")
    async def get_mt5_binding(user: AuthUser = Depends(require_auth)):
        repository = TradingAccountRepository()
        account = (
            repository.get_primary_mt5(user.user_id)
            or repository.get_default(user.user_id)
        )
        return {
            "status": "ok",
            "binding": _account_payload(account) if account else None,
        }

    @router.post("/mt5-binding")
    async def create_or_rotate_mt5_binding(
        user: AuthUser = Depends(require_auth),
    ):
        account, ea_token = TradingAccountRepository().create_or_rotate_default(
            user.user_id
        )
        if engine_manager is not None:
            engine_manager.bind_account(user.user_id, account.account_id)
        return {
            "status": "ok",
            "binding": {
                **_account_payload(account),
                "ea_token": ea_token,
            },
        }

    @router.get("/mt5-ea/status")
    async def get_mt5_ea_status(
        account_id: Optional[int] = None,
        user: AuthUser = Depends(require_auth),
    ):
        repository = TradingAccountRepository()
        account = (
            repository.get_by_id(user.user_id, account_id)
            if account_id is not None
            else repository.get_primary_mt5(user.user_id)
        )
        if account_id is not None and account is None:
            raise HTTPException(status_code=404, detail="MT5 账户不存在")
        if account is not None and account.account_type != "mt5":
            raise HTTPException(status_code=404, detail="MT5 账户不存在")
        last_seen_at = account.last_seen_at if account else None
        connected = bool(last_seen_at and int(time.time()) - last_seen_at <= 120)
        artifact_path = _ea_artifact_path()
        return {
            "status": "ok",
            "connection": "connected" if connected else "offline",
            "connected": connected,
            "binding": _account_payload(account) if account else None,
            "server_url": _public_server_url(),
            "artifact_available": artifact_path.is_file(),
            "artifact_name": "mt5TerminalEA.ex5",
        }

    @router.post("/mt5-ea/download")
    async def download_mt5_ea(
        user: AuthUser = Depends(require_auth),
    ):
        artifact_path = _ea_artifact_path()
        if not artifact_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MT5 EA 尚未编译发布，请先生成 dist/mt5TerminalEA.ex5",
            )

        activation_code, expires_at = EAActivationRepository().create(
            user.user_id,
            ttl_seconds=EA_ACTIVATION_TTL_SECONDS,
        )
        filename = f"mt5TerminalEA_{activation_code}.ex5"
        return FileResponse(
            path=artifact_path,
            filename=filename,
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-EA-Filename": filename,
                "X-EA-Activation-Expires-At": str(expires_at),
            },
        )

    return router


def _account_payload(account):
    return {
        "account_id": account.account_id,
        "user_id": account.user_id,
        "account_key": account.account_key,
        "account_name": account.account_name,
        "account_type": account.account_type,
        "environment": account.environment,
        "currency": account.currency,
        "initial_balance": account.initial_balance,
        "balance": account.balance,
        "equity": account.equity,
        "free_margin": account.free_margin,
        "margin": account.margin,
        "account_status": account.status,
        "financial_updated_at": account.financial_updated_at,
        "enabled": account.enabled,
        "trading_enabled": account.trading_enabled,
        "auto_trading_enabled": account.auto_trading_enabled,
        "max_total_positions": account.max_total_positions,
        "max_single_volume": account.max_single_volume,
        "daily_loss_limit": account.daily_loss_limit,
        "daily_order_limit": account.daily_order_limit,
        "archived_at": account.archived_at,
        "last_seen_at": account.last_seen_at,
        "mt5_login": account.mt5_login,
        "mt5_server": account.mt5_server,
        "ea_version": account.ea_version,
        "activated_at": account.activated_at,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def _user_info(user: AuthUser) -> AuthUserInfo:
    return AuthUserInfo(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        role=user.role,
    )


def _ea_artifact_path() -> Path:
    configured = os.getenv("AI_TRADER_MT5_EA_EX5")
    return Path(configured).expanduser() if configured else DEFAULT_EA_ARTIFACT


def _public_server_url() -> str:
    return os.getenv(
        "AI_TRADER_PUBLIC_BASE_URL",
        "http://127.0.0.1:8000",
    ).rstrip("/")
