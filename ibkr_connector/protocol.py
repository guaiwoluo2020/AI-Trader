"""Versioned wire envelopes exchanged with the AI-Trader server."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict


PROTOCOL_VERSION = 1


def event(name: str, payload: Dict[str, Any], *, account: str = "") -> Dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": "event",
        "event": name,
        "event_id": uuid.uuid4().hex,
        "occurred_at": int(time.time()),
        "account": account,
        "payload": payload,
    }


def hello(config) -> Dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "type": "hello",
        "connector": "ibkr",
        "client_id": config.client_id,
        "account": config.account,
        "user_id": config.user_id,
        "trading_account_id": config.trading_account_id,
        "read_only": config.read_only,
    }


def execution_report(event_name: str, payload: Dict[str, Any], *, account: str = "",
                     command_id: str = "") -> Dict[str, Any]:
    """Create a receipt envelope for the server's unified execution path."""
    message = event(event_name, payload, account=account)
    message["command_id"] = command_id
    return message
