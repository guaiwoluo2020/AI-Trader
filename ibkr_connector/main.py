"""Run the IBKR connector as a long-lived Linux process.

The connector is an outbound client: it owns the local Gateway socket and
reconnects the server WebSocket with exponential backoff.  It never enables
order submission while ``IBKR_READ_ONLY=true``.
"""
from __future__ import annotations

import asyncio
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
                        msg = event(name, payload, account=config.account)
                        asyncio.run_coroutine_threadsafe(ws.send_json(msg), loop)
                    gateway = IBGatewayClient(config.gateway_host, config.gateway_port,
                                              config.client_id, publish)
                    gateway.connect_and_run()
                    gateway.subscribe_symbols(config.symbols)
                    delay = config.reconnect_seconds
                    try:
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                command = json.loads(message.data)
                                if command.get("type") == "market_config":
                                    symbols = tuple(x for x in command.get("symbols", [])
                                                     if isinstance(x, dict) or str(x).strip())
                                    if symbols:
                                        gateway.subscribe_symbols(symbols)
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


def main() -> None:
    asyncio.run(run(ConnectorConfig.from_env()))


if __name__ == "__main__":
    main()
