#!/usr/bin/env python3
"""历史回测行情数据集接口。"""

import time
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from auth import AuthUser, require_auth
from backtest_data import (
    BacktestDatasetRepository,
    BacktestDatasetService,
    DatasetReferencedError,
)
from ea_auth import EAIdentity, require_ea_auth
from sqlite_storage import StrategyConfigRepository, TradingAccountRepository
from trading_engine_manager import TradingEngineManager
from user_quotas import UserQuotaService


def create_backtest_data_routes(
    engine_manager: TradingEngineManager,
) -> APIRouter:
    router = APIRouter()
    repository = BacktestDatasetRepository()
    service = BacktestDatasetService(repository)
    account_repository = TradingAccountRepository()
    strategy_repository = StrategyConfigRepository()
    quota_service = UserQuotaService()

    @router.get("/backtest/datasets")
    async def list_datasets(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        datasets = repository.list_for_user(user.user_id)
        return {
            "status": "ok",
            "count": len(datasets),
            "datasets": datasets,
            "quota": quota_service.get_summary(user.user_id, user.role),
        }

    @router.get("/backtest/datasets/context")
    async def get_dataset_context(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        accounts = [
            account for account in account_repository.list_for_user(user.user_id)
            if account.account_type == "mt5" and account.status == "active"
        ]
        account = accounts[0] if accounts else None
        engine = engine_manager.get_engine_for_user(user.user_id)
        symbols = set(engine.kline_service.get_symbols())
        symbols.update(
            strategy.symbol
            for strategy in strategy_repository.get_all_strategies(user.user_id)
        )
        return {
            "status": "ok",
            "account": (
                {
                    "account_id": account.account_id,
                    "account_name": account.account_name,
                    "mt5_login": account.mt5_login,
                    "mt5_server": account.mt5_server,
                    "ea_version": account.ea_version,
                    "last_seen_at": account.last_seen_at,
                    "enabled": account.enabled,
                }
                if account else None
            ),
            "accounts": [
                {
                    "account_id": item.account_id,
                    "account_name": item.account_name,
                    "mt5_login": item.mt5_login,
                    "mt5_server": item.mt5_server,
                    "ea_version": item.ea_version,
                    "last_seen_at": item.last_seen_at,
                    "connected": bool(
                        item.last_seen_at
                        and time.time() - item.last_seen_at <= 120
                    ),
                    "enabled": item.enabled,
                }
                for item in accounts
            ],
            "symbols": sorted(symbol for symbol in symbols if symbol),
            "required_ea_version": "2.0.7",
        }

    @router.post("/backtest/datasets")
    async def create_dataset(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            account_id = int(payload.get("account_id", 0))
            account = account_repository.get_by_id(user.user_id, account_id)
            if (
                account is None or account.account_type != "mt5"
                or account.status != "active" or account.activated_at is None
            ):
                raise ValueError("请先下载并连接 MT5 EA")
            with quota_service.guarded():
                quota_service.assert_can_create(user.user_id, user.role, "datasets")
                dataset = service.create_dataset(
                    user_id=user.user_id,
                    account_id=account.account_id,
                    dataset_name=payload.get("dataset_name", ""),
                    symbol=payload.get("symbol", ""),
                    requested_start=int(payload.get("requested_start", 0)),
                    requested_end=int(payload.get("requested_end", 0)),
                    warmup_days=int(payload.get("warmup_days", 1)),
                    visibility=str(payload.get("visibility", "shared")),
                )
                quota_service.usage_repository.rebuild_user(user.user_id)
            return {
                "status": "ok",
                "message": "历史数据集已创建，等待 MT5 EA 领取任务",
                "dataset": dataset,
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @router.get("/backtest/datasets/{dataset_id}")
    async def get_dataset(
        dataset_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        dataset = repository.get_visible(user.user_id, dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="历史数据集不存在")
        return {"status": "ok", "dataset": dataset}

    @router.patch("/backtest/datasets/{dataset_id}/visibility")
    async def update_dataset_visibility(
        dataset_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            dataset = repository.update_visibility(
                user.user_id,
                dataset_id,
                str(payload.get("visibility", "")),
            )
            if dataset is None:
                raise HTTPException(status_code=403, detail="只有创建者可以修改数据集")
            return {
                "status": "ok",
                "message": "数据集已设为共享" if dataset["visibility"] == "shared" else "数据集已设为私有",
                "dataset": dataset,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/backtest/datasets/{dataset_id}/copy")
    async def copy_dataset(
        dataset_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        with quota_service.guarded():
            quota_service.assert_can_create(user.user_id, user.role, "datasets")
            dataset = repository.copy(user.user_id, dataset_id)
            quota_service.usage_repository.rebuild_user(user.user_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="历史数据集不存在或未共享")
        return {
            "status": "ok",
            "message": "已复制为新的私有历史行情数据集",
            "dataset": dataset,
        }

    @router.post("/backtest/datasets/{dataset_id}/cancel")
    async def cancel_dataset(
        dataset_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        if not repository.cancel(user.user_id, dataset_id):
            raise HTTPException(
                status_code=400,
                detail="数据集不存在或当前状态不能取消",
            )
        return {"status": "ok", "message": "历史数据任务已取消"}

    @router.delete("/backtest/datasets/{dataset_id}")
    async def delete_dataset(
        dataset_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            if not service.delete_dataset(user.user_id, dataset_id):
                raise HTTPException(status_code=404, detail="历史数据集不存在")
            quota_service.usage_repository.rebuild_user(user.user_id)
            return {"status": "ok", "message": "历史数据集已删除"}
        except DatasetReferencedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/ea/backtest-data/tasks/next")
    async def get_next_ea_task(
        symbol: str = Query(..., min_length=1),
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        task = service.get_next_task(identity.account_id, symbol)
        return {"status": "ok", "task": task}

    @router.post("/ea/backtest-data/tasks/{dataset_id}/chunks")
    async def upload_ea_chunk(
        dataset_id: str,
        request: Request,
        identity: EAIdentity = Depends(require_ea_auth),
    ) -> Dict:
        try:
            payload = await request.json()
            result = service.accept_chunk(
                identity.account_id, dataset_id, payload
            )
            dataset = result["dataset"]
            return {
                "status": "ok",
                "result": result["result"],
                "dataset_status": dataset["status"],
                "progress": dataset["progress"],
                "received_bars": dataset["received_bars"],
                "quality_score": dataset["quality_score"],
            }
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    return router
