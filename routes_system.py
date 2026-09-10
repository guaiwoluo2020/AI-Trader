#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统相关的接口路由
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Optional
from auth import AuthUser, require_admin, require_auth
from market_tick_store import set_tick_persistence_enabled, tick_persistence_config
from status_payload import build_system_status_payload
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine
from infrastructure.storage_factory import healthcheck_storage


def create_system_routes(engine_manager: TradingEngineManager) -> APIRouter:
    """
    创建系统相关路由
    """
    router = APIRouter()
    protected_router = APIRouter()
    
    @router.get("/health")
    def health_check() -> Dict:
        """
        服务健康检查
        
        返回:
        ```json
        {
            "status": "ok"
        }
        ```
        """
        mysql_ok = healthcheck_storage()
        return {
            "status": "ok" if mysql_ok else "degraded",
            "ok": mysql_ok,
            "components": {"mysql": "ok" if mysql_ok else "unavailable"},
        }
    
    @protected_router.get("/status")
    async def get_status(
        account_id: Optional[int] = Query(None),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """
        获取服务状态
        
        返回:
        ```json
        {
            "status": "ok",
            "pending_instructions": 5,
            "statistics_records": 10,
            "symbols": ["EURUSD", "GBPUSD"]
        }
        ```
        """
        _, server = resolve_web_engine(engine_manager, user, account_id)
        pending_trades = server.get_all_pending_trades()
        total_instructions = sum(
            len(trades) for trades in pending_trades.values()
        )
        symbols = list(pending_trades.keys())
        latest_statistics = server.statistics_service.get_latest()

        return build_system_status_payload(
            pending_instructions=total_instructions,
            statistics_records=len(server.statistics_history),
            symbols=symbols,
            latest_statistics=latest_statistics,
        )

    @protected_router.get("/system/tasks")
    async def list_background_tasks(
        limit: int = Query(50, ge=1, le=200),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """查看统一后台队列中的 AI、复盘和维护任务状态。"""
        tasks = engine_manager.list_background_tasks(limit)
        # 管理任务不携带用户数据；普通用户只看到自己账户相关任务，
        # 以及全局系统任务的状态摘要。
        if user.role != "admin":
            tasks = [
                task for task in tasks
                if task.get("task_key", "").find(str(user.user_id)) >= 0
                or "system" in task.get("task_key", "")
                or "llm" in task.get("task_key", "")
            ]
        return {"status": "ok", "tasks": tasks, "count": len(tasks)}

    @protected_router.get("/system/tasks/{task_id}")
    async def get_background_task(
        task_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        task = engine_manager.get_background_task(task_id)
        if task is None:
            return {"status": "not_found", "task": None}
        return {"status": "ok", "task": task}

    @protected_router.get("/admin/system/tick-persistence")
    def get_tick_persistence(user: AuthUser = Depends(require_admin)) -> Dict:
        """查看本地 Tick 回放文件写入开关。"""
        return {"status": "ok", **tick_persistence_config()}

    @protected_router.put("/admin/system/tick-persistence")
    def update_tick_persistence(
        payload: Dict,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        """运行时切换 Tick 落盘；默认关闭，实时行情/交易不受影响。"""
        if "enabled" not in payload:
            raise HTTPException(status_code=400, detail="缺少 enabled 配置")
        raw_enabled = payload.get("enabled")
        if isinstance(raw_enabled, str):
            normalized = raw_enabled.strip().lower()
            if normalized not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                raise HTTPException(status_code=400, detail="enabled 必须是布尔值")
            raw_enabled = normalized in {"1", "true", "yes", "on"}
        enabled = set_tick_persistence_enabled(bool(raw_enabled))
        return {
            "status": "ok",
            "message": "Tick 本地落盘已开启" if enabled else "Tick 本地落盘已关闭",
            **tick_persistence_config(),
        }
    
    router.include_router(protected_router)
    return router
