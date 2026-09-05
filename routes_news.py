#!/usr/bin/env python3
"""Public market event ingestion, query, and realtime update routes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from auth import AuthUser, get_auth_manager, require_admin, require_auth
from market.utils.ws_manager import WebSocketManager
from market_event_repository import MarketEventRepository
from market.services.market_event_risk_service import (
    DEFAULT_EVENT_RISK_RULES,
    _calendar_timestamp,
    _major_us_event,
)


class MarketEventHub:
    """Authenticated WebSocket hub for market event page updates."""

    def __init__(self):
        self.ws_manager = WebSocketManager("market_events")

    async def broadcast(self, message: Dict) -> None:
        await self.ws_manager.broadcast(message)


_market_event_hub: Optional[MarketEventHub] = None


def get_market_event_hub() -> MarketEventHub:
    global _market_event_hub
    if _market_event_hub is None:
        _market_event_hub = MarketEventHub()
    return _market_event_hub


def _validate_day(value: object) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="date 必须是 YYYY-MM-DD 格式",
        ) from exc


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _normalize_importance(value: object) -> int:
    try:
        return max(0, min(int(value or 0), 3))
    except (TypeError, ValueError):
        return 0


_REVERSAL_SETUP_LABELS = {
    "range_lower_reversal": "箱体下沿反转",
    "range_upper_reversal": "箱体上沿反转",
    "range_false_breakout": "箱体假突破",
    "structure_location_pullback": "结构位置回撤",
    "liquidity_sweep_reclaim": "流动性扫单回收",
    "choch_reversal": "CHOCH 反转",
    "structure_reversal": "结构反转",
}


def _risk_calendar_item(rule: Dict, event_time: int, source: str, label: str,
                        level: str, before: int, after: int, event_type: str,
                        affected_setups: List[str]) -> Dict:
    start = event_time - before * 60
    end = event_time + after * 60
    beijing = ZoneInfo("Asia/Shanghai")
    return {
        "id": str(rule.get("id") or f"risk:{event_type}:{event_time}"),
        "label": label,
        "event_type": event_type,
        "level": level,
        "source": source,
        "event_timestamp": event_time,
        "event_time_beijing": datetime.fromtimestamp(event_time, timezone.utc).astimezone(beijing).isoformat(),
        "suppress_from": start,
        "suppress_until": end,
        "suppress_from_beijing": datetime.fromtimestamp(start, timezone.utc).astimezone(beijing).isoformat(),
        "suppress_until_beijing": datetime.fromtimestamp(end, timezone.utc).astimezone(beijing).isoformat(),
        "affected_setups": affected_setups,
        "affected_setup_labels": [_REVERSAL_SETUP_LABELS.get(item, item) for item in affected_setups],
    }


def _normalize_symbols(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(symbol).strip() for symbol in value if str(symbol).strip()
    ))


def _require_items(payload: Dict, field: str) -> List[Dict]:
    items = payload.get(field)
    if items is None:
        items = payload.get("data")
    if not isinstance(items, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field}（或 data）必须是数组",
        )
    if len(items) > 2000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单次最多上报 2000 条 {field}",
        )
    if not all(isinstance(item, dict) for item in items):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field} 中的每一项必须是对象",
        )
    return items


def _normalize_calendar(day: str, items: List[Dict]) -> List[Dict]:
    result = []
    for index, item in enumerate(items):
        name = str(item.get("name") or item.get("title") or "").strip()
        if not name:
            raise HTTPException(400, f"events[{index}] 缺少 name")
        event_time = str(
            item.get("publish_time") or item.get("event_time")
            or item.get("time") or ""
        ).strip()
        raw_timestamp = item.get("event_timestamp", item.get("timestamp"))
        try:
            event_timestamp = int(raw_timestamp) if raw_timestamp not in (None, "") else None
        except (TypeError, ValueError):
            event_timestamp = None
        event_time_utc = ""
        event_time_beijing = ""
        if event_timestamp is not None and event_timestamp > 0:
            instant = datetime.fromtimestamp(event_timestamp, tz=timezone.utc)
            event_time_utc = instant.isoformat().replace("+00:00", "Z")
            event_time_beijing = instant.astimezone(
                ZoneInfo("Asia/Shanghai")
            ).isoformat()
        event_id = str(item.get("id") or "").strip() or _stable_id(
            "calendar", day, event_time, name, item.get("currency")
        )
        result.append({
            **item,
            "id": event_id,
            "name": name,
            "event_time": event_time,
            "publish_time": event_time,
            "event_timestamp": event_timestamp,
            "event_time_utc": event_time_utc,
            "event_time_beijing": event_time_beijing,
            "broker_server_time": str(item.get("broker_server_time") or event_time),
            "broker_utc_offset_seconds": item.get("broker_utc_offset_seconds"),
            "forecast": item.get("forecast", item.get("consensus", "")),
            "importance": _normalize_importance(
                item.get("importance", item.get("star", 0))
            ),
            "symbols": _normalize_symbols(item.get("symbols")),
        })
    return result


def _normalize_key_events(day: str, items: List[Dict]) -> List[Dict]:
    result = []
    for index, item in enumerate(items):
        title = str(
            item.get("title") or item.get("name") or item.get("content") or ""
        ).strip()
        if not title:
            raise HTTPException(400, f"events[{index}] 缺少 title")
        event_time = str(
            item.get("event_time") or item.get("publish_time")
            or item.get("time") or ""
        ).strip()
        event_id = str(item.get("id") or "").strip() or _stable_id(
            "key", day, event_time, title, item.get("category")
        )
        result.append({
            **item,
            "id": event_id,
            "title": title,
            "event_time": event_time,
            "importance": _normalize_importance(
                item.get("importance", item.get("star", 0))
            ),
            "symbols": _normalize_symbols(item.get("symbols")),
        })
    return result


def _normalize_flash_news(items: List[Dict]) -> List[Dict]:
    result = []
    for index, item in enumerate(items):
        content = str(item.get("content") or item.get("title") or "").strip()
        if not content:
            raise HTTPException(400, f"items[{index}] 缺少 content")
        published_at = str(
            item.get("published_at") or item.get("time")
            or item.get("create_time") or datetime.now().isoformat()
        ).strip()
        news_id = str(item.get("id") or "").strip() or _stable_id(
            "flash", published_at, content
        )
        result.append({
            **item,
            "id": news_id,
            "content": content,
            "published_at": published_at,
            "importance": _normalize_importance(
                item.get("importance", item.get("star", 0))
            ),
            "symbols": _normalize_symbols(
                item.get("symbols") or item.get("related_symbols")
            ),
        })
    return result


def create_news_routes():
    """Create the shared market event service routes."""
    router = APIRouter(prefix="/news", tags=["市场事件"])
    repository = MarketEventRepository()
    hub = get_market_event_hub()

    @router.post("/calendar/daily")
    async def replace_calendar_day(
        request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        payload = await request.json()
        day = _validate_day(payload.get("date"))
        source = str(payload.get("source") or "external").strip()
        events = _normalize_calendar(day, _require_items(payload, "events"))
        count = repository.replace_calendar_day(day, events, source)
        return {
            "status": "ok",
            "message": f"{day} 财经日历已覆盖",
            "date": day,
            "count": count,
        }

    @router.get("/calendar")
    async def get_calendar(
        date_value: Optional[str] = Query(None, alias="date"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        day = _validate_day(date_value) if date_value else None
        events = repository.list_calendar(day)
        return {"status": "ok", "date": day, "count": len(events), "data": events}

    @router.post("/key-events/daily")
    async def replace_key_event_day(
        request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        payload = await request.json()
        day = _validate_day(payload.get("date"))
        source = str(payload.get("source") or "external").strip()
        events = _normalize_key_events(day, _require_items(payload, "events"))
        count = repository.replace_key_event_day(day, events, source)
        return {
            "status": "ok",
            "message": f"{day} 关键事件已覆盖",
            "date": day,
            "count": count,
        }

    @router.get("/key-events")
    async def get_key_events(
        date_value: Optional[str] = Query(None, alias="date"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        day = _validate_day(date_value) if date_value else None
        events = repository.list_key_events(day)
        return {"status": "ok", "date": day, "count": len(events), "data": events}

    @router.post("/flash")
    async def upsert_flash_news(
        request: Request,
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        payload = await request.json()
        source = str(payload.get("source") or "external").strip()
        items = _normalize_flash_news(_require_items(payload, "items"))
        for item in items:
            item["source"] = source
        count = repository.upsert_flash_news(items, source)
        await hub.broadcast({
            "type": "market_flash_news_updated",
            "count": count,
            "items": items,
            "updated_at": int(datetime.now().timestamp()),
        })
        return {"status": "ok", "message": "市场快讯已写入", "count": count}

    @router.get("/flash")
    async def get_flash_news(
        limit: int = Query(100, ge=1, le=500),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        items = repository.list_flash_news(limit)
        return {"status": "ok", "count": len(items), "data": items}

    @router.get("/risk-calendar")
    async def get_risk_calendar(
        date_value: Optional[str] = Query(None, alias="date"),
        user: AuthUser = Depends(require_auth),
    ) -> Dict:
        """Return one Beijing day of macro/opening risk windows and affected setups."""
        day = _validate_day(date_value) if date_value else date.today().isoformat()
        target = date.fromisoformat(day)
        beijing = ZoneInfo("Asia/Shanghai")
        day_start = int(datetime(target.year, target.month, target.day, tzinfo=beijing).timestamp())
        day_end = day_start + 24 * 60 * 60
        items: List[Dict] = []

        # Recurring market opens are evaluated in their native time zones. This
        # automatically produces the correct Beijing time through DST changes.
        for raw in DEFAULT_EVENT_RISK_RULES:
            tz = ZoneInfo(str(raw.get("timezone") or "Asia/Shanghai"))
            hour, minute = (int(part) for part in str(raw.get("time", "00:00")).split(":", 1))
            occurrence = datetime(target.year, target.month, target.day, hour, minute, tzinfo=tz)
            if raw.get("weekdays") and occurrence.weekday() not in {int(x) for x in raw["weekdays"]}:
                continue
            event_time = int(occurrence.timestamp())
            affected = list(raw.get("affect_setups") or _REVERSAL_SETUP_LABELS.keys())
            items.append(_risk_calendar_item(
                raw, event_time, "system", str(raw.get("label") or "市场开盘"),
                str(raw.get("level") or "L2"), int(raw.get("before_minutes") or 0),
                int(raw.get("after_minutes") or 0), str(raw.get("event_type") or "market_open"), affected,
            ))

        # Stored macro events use a timestamp and are already normalized to UTC.
        for event in repository.list_calendar(day) + repository.list_key_events(day):
            event_time = _calendar_timestamp(event)
            if not event_time or not day_start <= event_time < day_end:
                continue
            major = _major_us_event(event)
            importance = _normalize_importance(event.get("importance"))
            if not major and importance < 3:
                continue
            before, after = (45, 90) if major else (30, 45)
            label = str(event.get("name") or event.get("title") or "财经日历高影响事件")
            items.append(_risk_calendar_item(
                event, event_time, str(event.get("source") or "calendar"), label,
                "L4" if major else "L3", before, after, major or "economic_calendar",
                list(_REVERSAL_SETUP_LABELS.keys()),
            ))
        # The same MT5 event may be present in both calendar and key-event
        # tables. Collapse it for the risk view while retaining the strongest
        # severity and the most specific label.
        deduped = {}
        for item in items:
            normalized_label = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", item["label"].lower())
            key = (item["event_type"], item["event_timestamp"], normalized_label)
            previous = deduped.get(key)
            if previous is None or item["level"] > previous["level"]:
                deduped[key] = item
        items = sorted(deduped.values(), key=lambda item: (item["event_timestamp"], item["level"], item["label"]))
        return {"status": "ok", "date": day, "count": len(items), "data": items}

    @router.get("/status")
    async def get_status(user: AuthUser = Depends(require_auth)) -> Dict:
        return {
            "status": "ok",
            "data": {
                **repository.get_status(),
                "websocket_clients": hub.ws_manager.get_client_count(),
            },
        }

    @router.websocket("/ws")
    async def market_event_websocket(websocket: WebSocket):
        await websocket.accept()
        authenticated = False
        try:
            auth_text = await asyncio.wait_for(websocket.receive_text(), timeout=10)
            auth_message = json.loads(auth_text)
            if auth_message.get("type") != "auth" or not auth_message.get("token"):
                await websocket.close(code=1008, reason="请先登录")
                return
            user = get_auth_manager().verify_token(auth_message["token"])
            hub.ws_manager.add_client(websocket)
            authenticated = True
            await websocket.send_json({
                "type": "connected",
                "message": "已连接到公共市场事件服务",
                "user_id": user.user_id,
            })
            while True:
                message = json.loads(await websocket.receive_text())
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except asyncio.TimeoutError:
            await websocket.close(code=1008, reason="登录超时")
        except (HTTPException, json.JSONDecodeError):
            await websocket.close(code=1008, reason="登录凭证无效")
        except WebSocketDisconnect:
            pass
        finally:
            if authenticated:
                hub.ws_manager.remove_client(websocket)

    return router
