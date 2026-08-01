#!/usr/bin/env python3
"""Web 页面显式账户上下文解析。"""

from typing import Optional, Tuple

from fastapi import HTTPException, status

from auth import AuthUser
from sqlite_storage import TradingAccountRecord, TradingAccountRepository
from trading_engine_manager import TradingEngineManager


def resolve_web_engine(
    engine_manager: TradingEngineManager,
    user: AuthUser,
    account_id: Optional[int],
) -> Tuple[TradingAccountRecord, object]:
    repository = TradingAccountRepository()
    account = (
        repository.get_by_id(user.user_id, int(account_id))
        if account_id is not None
        else repository.get_primary_mt5(user.user_id)
    )
    if account is None or account.account_type != "mt5":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MT5 账户不存在或不属于当前用户",
        )
    return account, engine_manager.get_engine(
        account.user_id, account.account_id
    )
