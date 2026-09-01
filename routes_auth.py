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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
    EmailLoginRequest,
    InvitationCreateRequest,
    InvitationStatusRequest,
    LoginResponse,
    RegisterRequest,
    SendEmailCodeRequest,
    SystemEmailConfigRequest,
    TestSystemEmailRequest,
    UserMembershipUpdateRequest,
    UserQuotaOverrideRequest,
)
from invitations import InvitationError, InvitationService
from email_verification import (
    EmailVerificationError,
    EmailVerificationService,
    SystemEmailConfigRepository,
)
from membership import MembershipService
from mysql_repositories import EAActivationRepository, TradingAccountRepository, UserRepository
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
    membership_service = MembershipService()
    user_repository = UserRepository()
    invitation_service = InvitationService()

    def login_response(user: AuthUser) -> LoginResponse:
        auth_manager = get_auth_manager()
        user = auth_manager.start_session(user)
        return LoginResponse(
            status="ok",
            token=auth_manager.create_token(user),
            expires_in=auth_manager.token_ttl_seconds,
            user=_user_info(user),
            next_path=(
                "/" if EAActivationRepository().has_downloaded(user.user_id)
                else "/mt5-setup"
            ),
        )

    @router.post("/email-code")
    async def send_registration_email_code(
        payload: SendEmailCodeRequest,
        request: Request,
    ):
        try:
            invitation_service.assert_available(payload.invitation_code or "")
            requester = request.client.host if request.client else "unknown"
            result = await asyncio.to_thread(
                email_service.send_code, payload.email, requester, "registration"
            )
            return {"status": "ok", "message": "验证码已发送，请检查邮箱", **result}
        except (EmailVerificationError, InvitationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except (RuntimeError, OSError, smtplib.SMTPException) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"验证码发送失败: {exc}",
            ) from exc

    @router.post("/login/email-code")
    async def send_login_email_code(
        payload: SendEmailCodeRequest,
        request: Request,
    ):
        try:
            requester = request.client.host if request.client else "unknown"
            result = await asyncio.to_thread(
                email_service.send_code, payload.email, requester, "login"
            )
            return {"status": "ok", "message": "登录验证码已发送", **result}
        except EmailVerificationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @router.post("/login/email", response_model=LoginResponse)
    async def login_with_email(payload: EmailLoginRequest) -> LoginResponse:
        auth_manager = get_auth_manager()
        try:
            email = email_service.assert_valid_code(
                payload.email, payload.verification_code, "login"
            )
            user = auth_manager.get_user_by_email(email)
            if user is None:
                raise EmailVerificationError("该邮箱尚未加入")
            email_service.consume(email)
            return login_response(user)
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

    @router.post(
        "/register",
        response_model=LoginResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def register(payload: RegisterRequest) -> LoginResponse:
        auth_manager = get_auth_manager()
        invitation_id = None
        try:
            if not payload.accepted_private_use_terms:
                raise ValueError("请先阅读并同意私人技术验证使用协议")
            invitation_service.assert_available(payload.invitation_code)
            email = email_service.assert_valid_code(
                payload.email, payload.verification_code, "registration"
            )
            invitation_id = invitation_service.claim(payload.invitation_code)
            user = auth_manager.register_passwordless(payload.username, email)
            email_service.consume(email)
        except (UsernameAlreadyExistsError, InvitationError) as exc:
            if invitation_id:
                invitation_service.release(invitation_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except (EmailVerificationError, ValueError) as exc:
            if invitation_id:
                invitation_service.release(invitation_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return login_response(user)

    @router.get("/admin/invitations")
    async def list_invitations(user: AuthUser = Depends(require_admin)):
        return {"status": "ok", "invitations": invitation_service.list_all()}

    @router.post("/admin/invitations", status_code=status.HTTP_201_CREATED)
    async def create_invitation(
        payload: InvitationCreateRequest,
        user: AuthUser = Depends(require_admin),
    ):
        try:
            invitation = invitation_service.create(
                user.user_id, payload.label, payload.max_uses, payload.expires_days
            )
            invitation["invite_path"] = f'/register?invite={invitation["code"]}'
            return {"status": "ok", "invitation": invitation}
        except InvitationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.patch("/admin/invitations/{invitation_id}")
    async def update_invitation_status(
        invitation_id: str,
        payload: InvitationStatusRequest,
        user: AuthUser = Depends(require_admin),
    ):
        try:
            invitation = invitation_service.set_active(invitation_id, payload.active)
            return {"status": "ok", "invitation": invitation}
        except InvitationError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
    async def list_user_quotas(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_admin),
    ):
        users = []
        records, total = user_repository.list_users_page(page, page_size)
        for record in records:
            summary = quota_service.get_summary(record.user_id, record.role)
            users.append({
                "user_id": record.user_id,
                "username": record.username,
                "email": record.email,
                "role": record.role,
                "membership_level": record.membership_level,
                "live_trading_enabled": record.live_trading_enabled,
                **summary,
            })
        return {"status": "ok", "users": users, "total": total,
                "page": page, "page_size": page_size,
                "has_more": page * page_size < total}

    @router.post("/admin/users/{user_id}/view-token")
    async def create_user_view_token(
        user_id: int,
        user: AuthUser = Depends(require_admin),
    ):
        """管理员生成一小时只读用户视图 Token，不改变目标用户登录状态。"""
        target = user_repository.get_by_id(user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        auth_manager = get_auth_manager()
        target_user = auth_manager._to_auth_user(target)
        token = auth_manager.create_view_token(user, target_user)
        return {
            "status": "ok",
            "token": token,
            "expires_in": min(auth_manager.token_ttl_seconds, 60 * 60),
            "user": {
                **_user_info(target_user).model_dump(),
                "view_only": True,
                "impersonated_by": user.user_id,
            },
        }

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

    @router.put("/admin/users/{user_id}/membership")
    async def save_user_membership(
        user_id: int,
        payload: UserMembershipUpdateRequest,
        user: AuthUser = Depends(require_admin),
    ):
        try:
            access = membership_service.update_user(
                user_id,
                payload.membership_level,
                payload.live_trading_enabled,
                user.user_id,
            )
            if not access["can_live_trade"] and engine_manager is not None:
                engine_manager.suspend_user_live_orders(user_id)
            return {
                "status": "ok",
                "message": "用户会员等级和实盘权限已更新",
                "membership": access,
            }
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/me")
    async def me(user: AuthUser = Depends(require_auth)):
        return {
            "status": "ok",
            "user": _user_info(user).model_dump(),
        }

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
        membership_level=user.membership_level,
        live_trading_enabled=user.live_trading_enabled,
    )


def _ea_artifact_path() -> Path:
    configured = os.getenv("AI_TRADER_MT5_EA_EX5")
    return Path(configured).expanduser() if configured else DEFAULT_EA_ARTIFACT


def _public_server_url() -> str:
    return os.getenv(
        "AI_TRADER_PUBLIC_BASE_URL",
        "http://127.0.0.1:8000",
    ).rstrip("/")
