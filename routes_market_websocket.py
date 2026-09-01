"""WebSocket routes for market and system-log streams."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from auth import get_auth_manager
from market.system_log import get_system_log_broadcaster
from mysql_repositories import TradingAccountRepository
from web_account_context import resolve_web_engine


def create_market_websocket_routes(engine_manager) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/market")
    async def websocket_market(websocket: WebSocket):
        await websocket.accept()
        engine = None
        try:
            auth_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            message = json.loads(auth_text)
            if message.get("type") != "auth" or not message.get("token"):
                await websocket.close(code=1008, reason="请先登录"); return
            user = get_auth_manager().verify_token(message["token"])
            _, engine = resolve_web_engine(engine_manager, user, message.get("account_id"))
            engine.add_ws_client(websocket)
            engine.system_log.add_log("websocket_connect", message="行情 WebSocket 已连接")
            await websocket.send_text(json.dumps({"type": "connected", "message": "已连接到账户行情监控服务", "user_id": user.user_id, "account_id": engine.account_id}))
            while True:
                try:
                    payload = json.loads(await websocket.receive_text())
                    if payload.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                except WebSocketDisconnect:
                    break
        except asyncio.TimeoutError:
            await websocket.close(code=1008, reason="登录超时")
        except (HTTPException, json.JSONDecodeError):
            await websocket.close(code=1008, reason="登录凭证无效")
        finally:
            if engine is not None:
                engine.system_log.add_log("websocket_disconnect", message="行情 WebSocket 已断开")
                engine.remove_ws_client(websocket)

    @router.websocket("/ws/system-logs")
    async def websocket_system_logs(websocket: WebSocket):
        await websocket.accept()
        broadcaster = get_system_log_broadcaster()
        subscribed = False
        try:
            message = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
            if message.get("type") != "auth" or not message.get("token"):
                await websocket.close(code=1008, reason="请先登录"); return
            user = get_auth_manager().verify_token(message["token"])
            account_id = message.get("account_id")
            if account_id is not None:
                account_id = int(account_id)
            if user.role != "admin" and account_id and TradingAccountRepository().get_by_id(user.user_id, account_id) is None:
                await websocket.close(code=1008, reason="交易账户不存在"); return
            broadcaster.add(websocket, user.user_id, account_id, user.role == "admin")
            subscribed = True
            await websocket.send_text(json.dumps({"type": "connected", "message": "日志实时流已连接"}))
            while True:
                payload = json.loads(await websocket.receive_text())
                if payload.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
        except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            if subscribed:
                broadcaster.remove(websocket)

    return router
