#!/usr/bin/env python3
"""Authenticated Alpha research API."""

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from alpha_research import AlphaResearchService
from auth import AuthUser, require_auth
from llm_governance import LLMQuotaExceeded
from repositories.strategy_config import StrategyConfigRepository


def create_alpha_research_routes() -> APIRouter:
    router = APIRouter()
    service = AlphaResearchService()
    strategy_repo = StrategyConfigRepository()

    @router.get("/alpha-research/context")
    async def get_context(user: AuthUser = Depends(require_auth)) -> Dict:
        return {"status": "ok", **service.context(user.user_id)}

    @router.get("/alpha-research/runs")
    async def list_runs(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        runs, total = service.repository.list_page(user.user_id, page, page_size)
        return {"status": "ok", "count": len(runs), "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total, "runs": runs}

    @router.post("/alpha-research/candidates")
    async def generate_candidates(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            candidates = service.candidates.generate(user.user_id, await request.json())
        except LLMQuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "status": "ok",
            "message": f"已生成 {len(candidates)} 个 Alpha 候选",
            "candidates": candidates,
        }

    @router.post("/alpha-research/runs")
    async def create_run(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            run = service.create(user.user_id, await request.json())
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "ok",
            "message": "Alpha 研究任务已提交，将按 LLM 结构迭代与 Optuna 参数搜索执行",
            "run": run,
        }

    @router.get("/alpha-research/runs/{run_id}")
    async def get_run(
        run_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        run = service.repository.get(user.user_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Alpha 研究任务不存在")
        if run["status"] == "completed":
            run["admission"] = service.library.admission_report(run["result"])
        return {"status": "ok", "run": run}

    @router.post("/alpha-research/runs/{run_id}/publish")
    async def publish_run(
        run_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        run = service.repository.get(user.user_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Alpha 研究任务不存在")
        try:
            payload = await request.json()
            alpha = service.library.publish_run(
                user.user_id, run, str(payload.get("visibility", "private"))
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "message": "Alpha 已进入因子库", "alpha": alpha}

    @router.get("/alpha-library")
    async def list_alpha_library(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        items, total = service.library.list_visible_page(user.user_id, page, page_size)
        return {"status": "ok", "count": len(items), "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total, "items": items}

    @router.post("/alpha-library/{alpha_id}/retire")
    async def retire_alpha(
        alpha_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        if strategy_repo.list_alpha_references(alpha_id):
            raise HTTPException(
                status_code=409,
                detail="该共享 Alpha 已被策略应用，不能停用；请复制为新 Alpha 后再调整",
            )
        if not service.library.retire(user.user_id, alpha_id):
            raise HTTPException(status_code=404, detail="Alpha 不存在或无权停用")
        return {"status": "ok", "message": "Alpha 已停用"}

    @router.post("/alpha-library/{alpha_id}/copy")
    async def copy_alpha(
        alpha_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        alpha = service.library.copy(user.user_id, alpha_id)
        if alpha is None:
            raise HTTPException(status_code=404, detail="Alpha 不存在或未共享")
        return {"status": "ok", "message": "已复制为新的私有 Alpha", "alpha": alpha}

    @router.post("/alpha-research/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        if not service.repository.request_cancel(user.user_id, run_id):
            raise HTTPException(status_code=400, detail="任务不存在或当前状态不能终止")
        return {"status": "ok", "message": "已提交终止请求"}

    return router
