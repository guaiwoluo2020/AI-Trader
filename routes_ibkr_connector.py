"""Inbound WebSocket endpoint for standalone IBKR Gateway connectors."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from auth import AuthUser, require_auth
from system_event_log import SystemEventLogRepository
from mysql_repositories import RuntimeStateRepository
from repositories.accounts import TradingAccountRepository
from market.services.market_tick_ingress import MarketTickIngress

logger = logging.getLogger(__name__)
_connectors: Dict[str, Dict] = {}
_connector_sockets: Dict[str, WebSocket] = {}


def _admin_user_id(account_repository: TradingAccountRepository) -> int:
    """Resolve the owner for a Gateway that has not been manually assigned."""
    row = account_repository.storage.fetchone(
        "SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
    )
    return int(row["id"]) if row else 0


def _bind_discovered_ibkr_accounts(
    connector_state: Dict,
    hello: Dict,
    accounts: list,
    account_repository: TradingAccountRepository,
) -> Dict:
    """Bind Gateway-discovered broker accounts to the configured AI Trader user.

    A fresh connector does not need a pre-filled IBKR account number: IBKR's
    managedAccounts callback is authoritative.  Until a dedicated owner is
    supplied, this installation deliberately binds it to the admin user.
    """
    user_id = int(hello.get("user_id") or connector_state.get("user_id") or 0)
    if user_id <= 0:
        user_id = _admin_user_id(account_repository)
    if user_id <= 0:
        raise RuntimeError("未找到 admin 用户，无法自动绑定 IBKR Gateway")

    normalized = [str(value).strip().upper() for value in accounts if str(value).strip()]
    bound = [account_repository.ensure_ibkr_account(user_id, value) for value in normalized]
    requested_id = int(hello.get("trading_account_id") or 0)
    primary = next((item for item in bound if item.account_id == requested_id), None)
    primary = primary or (bound[0] if bound else None)
    connector_state.update({
        "user_id": user_id,
        "ibkr_accounts": normalized,
        "trading_account_id": primary.account_id if primary else 0,
        "primary_ibkr_account": primary.mt5_login if primary else "",
        "bound_accounts": [
            {"ibkr_account": item.mt5_login, "trading_account_id": item.account_id}
            for item in bound
        ],
    })
    return {
        "user_id": user_id,
        "trading_account_id": primary.account_id if primary else 0,
        "accounts": [
            {"ibkr_account": item.mt5_login, "trading_account_id": item.account_id}
            for item in bound
        ],
    }


def _record_connector_event(connector_id: str, payload: Dict) -> None:
    """Persist low-volume broker lifecycle events; quotes stay hot-path only."""
    event_name = str(payload.get("event") or "")
    if event_name == "quote":
        return
    detail = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    try:
        SystemEventLogRepository().add({
            "event_id": payload.get("event_id"),
            "occurred_at": payload.get("occurred_at"),
            "level": "error" if event_name == "error" else "info",
            "category": "integration",
            "event_type": f"ibkr_{event_name or 'event'}",
            "event_name": f"IBKR {event_name or 'event'}",
            "actor_type": "ibkr_connector",
            "actor_id": connector_id,
            "entity_type": "ibkr_order" if event_name in {"order_status", "execution"} else "ibkr_connector",
            "entity_id": str(detail.get("order_id") or detail.get("exec_id") or connector_id),
            "symbol": str(detail.get("symbol") or ""),
            "message": str(detail.get("message") or detail.get("status") or event_name),
            "status": str(detail.get("status") or ""),
            "detail": detail,
        })
    except Exception:
        # Audit persistence must not disconnect a live broker connector.
        logger.exception("failed to persist IBKR connector event: %s", event_name)


def create_ibkr_connector_routes(engine_manager=None) -> APIRouter:
    router = APIRouter()
    tick_ingress = MarketTickIngress(engine_manager) if engine_manager is not None else None
    account_repository = TradingAccountRepository()

    @router.websocket("/ws/ibkr")
    async def ibkr_connector(websocket: WebSocket):
        expected = os.getenv("IBKR_CONNECTOR_TOKEN", "").strip()
        supplied = websocket.headers.get("authorization", "")
        supplied = supplied[7:].strip() if supplied.lower().startswith("bearer ") else supplied
        if not expected or not hmac.compare_digest(supplied, expected):
            await websocket.close(code=1008, reason="Connector 凭证无效")
            return
        await websocket.accept()
        connector_id = "unknown"
        try:
            hello = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
            if hello.get("type") != "hello" or hello.get("connector") != "ibkr":
                await websocket.close(code=1008, reason="无效的 Connector 握手")
                return
            connector_id = f"{hello.get('account') or 'unknown'}:{hello.get('client_id') or 'unknown'}"
            _connectors[connector_id] = {
                "account": hello.get("account", ""),
                "client_id": hello.get("client_id"),
                "read_only": bool(hello.get("read_only", True)),
                "user_id": int(hello.get("user_id") or 0),
                "trading_account_id": int(hello.get("trading_account_id") or 0),
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "last_event_at": None,
                "account_financials": {},
            }
            _connector_sockets[connector_id] = websocket
            await websocket.send_json({"type": "connected", "connector_id": connector_id,
                                       "read_only": _connectors[connector_id]["read_only"]})
            config = RuntimeStateRepository(0, 0).get_entity("ibkr_market_config", "default") or {}
            await websocket.send_json({"type": "market_config", "symbols": config.get("symbols", [])})
            async for message in websocket.iter_text():
                payload = json.loads(message)
                if payload.get("type") == "event":
                    _connectors[connector_id]["last_event_at"] = datetime.now(timezone.utc).isoformat()
                    if payload.get("event") == "accounts":
                        detail = payload.get("payload") or {}
                        try:
                            binding = _bind_discovered_ibkr_accounts(
                                _connectors[connector_id], hello,
                                detail.get("accounts") or [], account_repository,
                            )
                            _connectors[connector_id]["bound_accounts"] = binding.get("accounts", [])
                            await websocket.send_json({"type": "binding", **binding})
                        except Exception:
                            logger.exception("failed to auto-bind IBKR accounts: %s", connector_id)
                    if payload.get("event") == "account_summary":
                        detail = payload.get("payload") or {}
                        broker_account = str(detail.get("account") or "").strip().upper()
                        tag = str(detail.get("tag") or "").strip()
                        try:
                            value = float(detail.get("value"))
                        except (TypeError, ValueError):
                            value = None
                        binding = _connectors.get(connector_id, {})
                        user_id = int(binding.get("user_id") or hello.get("user_id") or 0)
                        account_id = next((
                            int(item.get("trading_account_id") or 0)
                            for item in (binding.get("bound_accounts") or [])
                            if str(item.get("ibkr_account") or "").upper() == broker_account
                        ), 0)
                        if user_id > 0 and account_id > 0 and broker_account and tag and value is not None:
                            cache = binding.setdefault("account_financials", {}).setdefault(broker_account, {})
                            cache[tag] = value
                            account = account_repository.get_by_id(user_id, account_id)
                            if account is not None:
                                account_repository.update_financial_snapshot(
                                    account_id,
                                    balance=cache.get("TotalCashValue", account.balance),
                                    equity=cache.get("NetLiquidation", account.equity),
                                    free_margin=cache.get("AvailableFunds", account.free_margin),
                                    margin=cache.get("MaintMarginReq", account.margin),
                                )
                    if payload.get("event") == "quote" and engine_manager is not None:
                        detail = payload.get("payload") or {}
                        binding = _connectors.get(connector_id, {})
                        user_id = int(binding.get("user_id") or hello.get("user_id") or 0)
                        symbol = str(detail.get("symbol") or "").strip()
                        price = float(detail.get("price") or 0)
                        bid = float(detail.get("bid") or price)
                        ask = float(detail.get("ask") or price)
                        trading_account_id = int(binding.get("trading_account_id") or hello.get("trading_account_id") or 0)
                        if user_id > 0 and symbol and price > 0 and tick_ingress is not None:
                            try:
                                tick_ingress.ingest(
                                    user_id=user_id, symbol=symbol, price=price,
                                    account_ids=(trading_account_id,),
                                    source="ibkr", bid=bid, ask=ask,
                                )
                            except Exception:
                                logger.exception("failed to ingest IBKR quote: %s", symbol)
                    _record_connector_event(connector_id, payload)
                    logger.info("IBKR event connector=%s event=%s", connector_id, payload.get("event"))
                elif payload.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except (asyncio.TimeoutError, WebSocketDisconnect, json.JSONDecodeError):
            pass
        finally:
            _connector_sockets.pop(connector_id, None)
            _connectors.pop(connector_id, None)

    @router.get("/admin/ibkr/connectors")
    async def ibkr_connectors(user: AuthUser = Depends(require_auth)):
        if user.role != "admin":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅管理员可查看 IBKR Connector")
        return {"items": list(_connectors.values()), "count": len(_connectors)}

    @router.get("/admin/ibkr/market-config")
    async def get_ibkr_market_config(user: AuthUser = Depends(require_auth)):
        if user.role != "admin":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅管理员可配置 IBKR 行情")
        return RuntimeStateRepository(0, 0).get_entity("ibkr_market_config", "default") or {"symbols": []}

    @router.put("/admin/ibkr/market-config")
    async def put_ibkr_market_config(payload: Dict, user: AuthUser = Depends(require_auth)):
        if user.role != "admin":
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅管理员可配置 IBKR 行情")
        raw = payload.get("symbols") or []
        symbols, seen = [], set()
        for item in raw:
            if isinstance(item, dict):
                symbol = str(item.get("symbol") or "").strip()
                con_id = int(item.get("con_id") or 0)
                if not symbol or con_id <= 0:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=400, detail="完整合约必须包含 symbol 和正数 con_id")
                normalized = {
                    "symbol": symbol,
                    "con_id": con_id,
                    "sec_type": str(item.get("sec_type") or "STK").upper(),
                    "exchange": str(item.get("exchange") or "SMART").upper(),
                    "currency": str(item.get("currency") or "USD").upper(),
                }
                if item.get("expiry"):
                    normalized["expiry"] = str(item["expiry"])
                key = (symbol, con_id)
            else:
                value = str(item).strip()
                if not value:
                    continue
                normalized, key = value, (value, 0)
            if key not in seen:
                seen.add(key)
                symbols.append(normalized)
        if len(symbols) > 200:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="最多配置 200 个 IBKR 品种")
        config = {"symbols": symbols, "updated_by": int(user.user_id),
                  "updated_at": datetime.now(timezone.utc).isoformat()}
        RuntimeStateRepository(0, 0).upsert_entity("ibkr_market_config", "default", config, status="active")
        for connector in list(_connector_sockets.values()):
            try:
                await connector.send_json({"type": "market_config", "symbols": symbols})
            except Exception:
                pass
        return config

    return router
