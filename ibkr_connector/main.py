"""Run the IBKR connector as a long-lived Linux process.

The connector is an outbound client: it owns the local Gateway socket and
reconnects the server WebSocket with exponential backoff.  It never enables
order submission while ``IBKR_READ_ONLY=true``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import aiohttp

from .config import ConnectorConfig
from .gateway_client import IBGatewayClient
from .protocol import event, execution_report, hello

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("ibkr_connector")


async def run(config: ConnectorConfig) -> None:
    if not config.connector_token:
        raise RuntimeError("IBKR_CONNECTOR_TOKEN 未配置")
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        processed_commands = set()
        delay = config.reconnect_seconds
        while True:
            try:
                headers = {"Authorization": f"Bearer {config.connector_token}"}
                async with session.ws_connect(config.server_ws_url, headers=headers,
                                              heartbeat=config.heartbeat_seconds) as ws:
                    await ws.send_json(hello(config))
                    loop = asyncio.get_running_loop()
                    def publish(name, payload):
                        # An empty IBKR position snapshot is valid but has no
                        # position callback from which to derive the account.
                        # Preserve the configured account in both envelope and
                        # payload so the server can still clear stale state.
                        if name == "positions_snapshot" and not payload.get("account"):
                            payload = dict(payload)
                            payload["account"] = config.account
                        msg = event(name, payload, account=config.account)
                        future = asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
                        def _report_send_failure(done):
                            if done.cancelled():
                                return
                            exc = done.exception()
                            if exc:
                                logger.warning("IBKR event send failed event=%s: %s", name, exc)
                        future.add_done_callback(_report_send_failure)
                    gateway = IBGatewayClient(config.gateway_host, config.gateway_port,
                                              config.client_id, publish)
                    gateway.connect_and_run()
                    # The server sends the persisted K-line cursors after the
                    # websocket handshake.  Subscribe to quotes immediately,
                    # but wait for that command before requesting history so a
                    # reconnect does not download the same two days twice.
                    gateway.subscribe_symbols(config.symbols)
                    delay = config.reconnect_seconds
                    gateway_watchdog = asyncio.create_task(
                        _watch_gateway_connection(ws, gateway, config.heartbeat_seconds)
                    )
                    try:
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                command = json.loads(message.data)
                                if command.get("type") == "market_config":
                                    symbols = tuple(x for x in command.get("symbols", [])
                                                     if isinstance(x, dict) or str(x).strip())
                                    if symbols:
                                        gateway.subscribe_symbols(
                                            symbols, command.get("kline_cursors") or {}
                                        )
                                elif command.get("type") == "ping":
                                    await ws.send_json({"type": "pong"})
                                elif command.get("type") == "shutdown":
                                    return
                                elif command.get("type") == "order":
                                    command_id = str(command.get("command_id") or "")
                                    if not command_id or command_id in processed_commands:
                                        continue
                                    processed_commands.add(command_id)
                                    if config.read_only or not bool(command.get("live", False)):
                                        await ws.send_json(execution_report(
                                            "order_rejected", {"reason": "connector_read_only"},
                                            account=config.account, command_id=command_id))
                                        continue
                                    try:
                                        ib_order_id = gateway.place_market_order(command)
                                        await ws.send_json(execution_report(
                                            "order_accepted", {"ibkr_order_id": ib_order_id},
                                            account=config.account, command_id=command_id))
                                    except Exception as exc:
                                        await ws.send_json(execution_report(
                                            "order_rejected", {"reason": str(exc)},
                                            account=config.account, command_id=command_id))
                                elif command.get("type") == "order" and config.read_only:
                                    await ws.send_json(event("order_rejected", {
                                        "reason": "connector_read_only"}, account=config.account))
                    finally:
                        gateway_watchdog.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await gateway_watchdog
                        gateway.close()
                    # A clean server-side close is still a disconnect; avoid a
                    # tight reconnect loop when the service is being restarted.
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("IBKR connector disconnected; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(60.0, delay * 2)


async def _watch_gateway_connection(ws, gateway: IBGatewayClient, heartbeat_seconds: float) -> None:
    """Force the outer reconnect loop when the local Gateway drops.

    ``ibapi`` runs its reader in a background thread.  Previously a Gateway
    disconnect only invoked ``connectionClosed`` in that thread while the
    WebSocket receive loop kept waiting forever, leaving the systemd process
    healthy-looking but no longer publishing account or quote events.
    Closing the server WebSocket makes ``run`` leave its inner context and
    recreate both connections.
    """
    interval = max(2.0, min(float(heartbeat_seconds), 10.0))
    # Give the API client a short grace period to complete its initial socket
    # handshake before treating a transient false value as a disconnect.
    await asyncio.sleep(interval)
    position_refresh_at = asyncio.get_running_loop().time()
    while True:
        if not gateway.ready:
            logger.warning("IBKR Gateway API not ready; forcing connector reconnect")
            with contextlib.suppress(Exception):
                await ws.close(code=1011, message=b"IBKR Gateway disconnected")
            return
        now = asyncio.get_running_loop().time()
        if now >= position_refresh_at:
            gateway.request_positions()
            position_refresh_at = now + 60.0
        await asyncio.sleep(interval)


def main() -> None:
    asyncio.run(run(ConnectorConfig.from_env()))


if __name__ == "__main__":
    main()
