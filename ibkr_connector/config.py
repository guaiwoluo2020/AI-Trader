"""Environment based configuration for the standalone IBKR connector."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Tuple


def _csv(value: str) -> Tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class ConnectorConfig:
    server_ws_url: str = "ws://127.0.0.1:8000/ws/ibkr"
    connector_token: str = ""
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 4002
    client_id: int = 207
    symbols: Tuple[Any, ...] = field(default_factory=tuple)
    account: str = ""
    user_id: int = 0
    trading_account_id: int = 0
    read_only: bool = True
    reconnect_seconds: float = 5.0
    heartbeat_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "ConnectorConfig":
        return cls(
            server_ws_url=os.getenv("IBKR_SERVER_WS_URL", cls.server_ws_url),
            connector_token=os.getenv("IBKR_CONNECTOR_TOKEN", ""),
            gateway_host=os.getenv("IBKR_GATEWAY_HOST", cls.gateway_host),
            gateway_port=int(os.getenv("IBKR_GATEWAY_PORT", str(cls.gateway_port))),
            client_id=int(os.getenv("IBKR_CLIENT_ID", str(cls.client_id))),
            symbols=_csv(os.getenv("IBKR_SYMBOLS", "")),
            account=os.getenv("IBKR_ACCOUNT", ""),
            user_id=int(os.getenv("IBKR_USER_ID", "0") or 0),
            trading_account_id=int(os.getenv("IBKR_TRADING_ACCOUNT_ID", "0") or 0),
            read_only=os.getenv("IBKR_READ_ONLY", "true").lower() not in {"0", "false", "no"},
            reconnect_seconds=max(1.0, float(os.getenv("IBKR_RECONNECT_SECONDS", "5"))),
            heartbeat_seconds=max(5.0, float(os.getenv("IBKR_HEARTBEAT_SECONDS", "20"))),
        )
