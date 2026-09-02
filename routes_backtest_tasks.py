#!/usr/bin/env python3
"""回测模板、批次和任务接口。"""

from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from auth import AuthUser, require_auth
from backtest_ai_analysis import BacktestAIAnalysisService
from llm_governance import LLMQuotaExceeded
from backtest_tasks import BacktestTemplateService
from market_tick_store import list_tick_files


def create_backtest_task_routes() -> APIRouter:
    router = APIRouter()
    service = BacktestTemplateService()
    ai_analysis_service = BacktestAIAnalysisService(service.storage)

    @router.get("/backtest/templates/context")
    async def get_template_context(
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        context = service.get_context(user.user_id)
        # Tick files are local market-source data and are exposed separately
        # from uploaded K-line datasets for the replay selector.
        context["tick_files"] = list_tick_files()
        return {"status": "ok", **context}

    @router.get("/backtest/templates")
    async def list_templates(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        templates, total = service.list_templates_page(user.user_id, page, page_size)
        return {"status": "ok", "count": len(templates), "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total, "templates": templates}

    @router.post("/backtest/templates")
    async def create_template(
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            template = service.create_template(user.user_id, await request.json())
            return {"status": "ok", "message": "回测模板已创建", "template": template}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/backtest/templates/{template_id}")
    async def update_template(
        template_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            template = service.update_template(
                user.user_id, template_id, await request.json()
            )
            if template is None:
                raise HTTPException(status_code=404, detail="回测模板不存在")
            return {"status": "ok", "message": "回测模板已更新", "template": template}
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/backtest/templates/{template_id}")
    async def delete_template(
        template_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        if not service.delete_template(user.user_id, template_id):
            raise HTTPException(status_code=404, detail="回测模板不存在")
        return {"status": "ok", "message": "回测模板已删除，历史批次仍然保留"}

    @router.post("/backtest/templates/{template_id}/run")
    async def run_template(
        template_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            batch = service.run_template(user.user_id, template_id)
            return {
                "status": "ok",
                "message": f"已生成 {batch['task_count']} 个回测任务",
                "batch": batch,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/backtest/batches")
    async def list_batches(
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        batches, total = service.list_batches_page(user.user_id, page, page_size)
        return {"status": "ok", "count": len(batches), "total": total, "page": page, "page_size": page_size, "has_more": page * page_size < total, "batches": batches}

    @router.get("/backtest/batches/{batch_id}")
    async def get_batch(
        batch_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        batch = service.get_batch(user.user_id, batch_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="回测批次不存在",
            )
        return {"status": "ok", "batch": batch}

    @router.post("/backtest/batches/{batch_id}/cancel")
    async def cancel_batch(
        batch_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        batch = service.cancel_batch(user.user_id, batch_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="回测批次不存在",
            )
        return {
            "status": "ok",
            "message": "已提交批次停止请求",
            "batch": batch,
        }

    @router.post("/backtest/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        task = service.cancel_task(user.user_id, task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="回测任务不存在",
            )
        return {
            "status": "ok",
            "message": "已提交任务停止请求",
            "task": task,
        }

    @router.get("/backtest/tasks/{task_id}/ledger")
    async def get_task_ledger(
        task_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        ledger = service.get_task_ledger(user.user_id, task_id)
        if ledger is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="回测任务不存在",
            )
        return {"status": "ok", "ledger": ledger}

    @router.get("/backtest/tasks/{task_id}/ai-analysis")
    async def get_task_ai_analysis(
        task_id: str,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        analysis = ai_analysis_service.get_analysis(user.user_id, task_id)
        if analysis is None:
            raise HTTPException(status_code=404, detail="回测任务不存在")
        return {"status": "ok", "analysis": analysis}

    @router.post("/backtest/tasks/{task_id}/ai-analysis")
    async def start_task_ai_analysis(
        task_id: str,
        request: Request,
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        try:
            analysis = ai_analysis_service.start_analysis(
                user.user_id,
                user.role,
                task_id,
                regenerate=bool(data.get("regenerate", False)),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LLMQuotaExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "ok",
            "message": "回测 AI 分析任务已提交",
            "analysis": analysis,
        }

    return router
