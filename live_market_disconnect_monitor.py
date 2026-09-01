"""Notify users when a live MT5 symbol stops publishing market data."""

from __future__ import annotations

import threading
import time
from typing import Dict

from shared_notifications import SharedReferenceNotificationService
from sqlite_storage import get_storage


class LiveMarketDisconnectMonitor:
    """Periodic, deduplicated monitor for live-account symbol data freshness."""

    def __init__(self, interval_seconds: int = 60, timeout_seconds: int = 600):
        self.interval_seconds = max(30, int(interval_seconds))
        self.timeout_seconds = max(60, int(timeout_seconds))
        self.storage = get_storage()
        self.notifications = SharedReferenceNotificationService()
        self._stop = threading.Event()
        self._thread = None
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.storage.execute("""
                CREATE TABLE IF NOT EXISTS live_market_disconnect_alerts (
                    user_id BIGINT NOT NULL,
                    account_id BIGINT NOT NULL,
                    symbol VARCHAR(255) NOT NULL,
                    alerted_at BIGINT NOT NULL DEFAULT 0,
                    recovered_at BIGINT NULL,
                    PRIMARY KEY (account_id, symbol)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="market-disconnect-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception as exc:
                print(f"[MarketDisconnect] 检查失败: {exc}")
            self._stop.wait(self.interval_seconds)

    def check_once(self) -> None:
        now = int(time.time())
        rows = self.storage.fetchall("""
            SELECT a.id AS account_id, a.user_id, a.account_name, a.mt5_login,
                   d.symbol, MAX(k.updated_at) AS last_market_at,
                   u.username, u.email
            FROM trading_accounts a
            JOIN strategy_deployments d ON d.account_id = a.id AND d.status = 'active'
            LEFT JOIN historical_klines k
              ON k.user_id = a.user_id AND k.symbol = d.symbol
            JOIN users u ON u.id = a.user_id
            WHERE a.account_type = 'mt5' AND d.execution_mode = 'live'
            GROUP BY a.id, a.user_id, a.account_name, a.mt5_login, d.symbol,
                     u.username, u.email
        """)
        for row in rows:
            stale = not row.get("last_market_at") or now - int(row["last_market_at"]) > self.timeout_seconds
            key = (int(row["account_id"]), str(row["symbol"]))
            prior = self.storage.fetchone(
                "SELECT alerted_at FROM live_market_disconnect_alerts WHERE account_id = ? AND symbol = ?",
                key,
            )
            if stale and not (prior and int(prior.get("alerted_at") or 0)):
                self.notifications.notify(
                    [row],
                    f"实盘行情中断提醒：{row['symbol']}",
                    f"账户 {row['account_name']}（MT5 {row['mt5_login'] or '-'}）的实盘品种 {row['symbol']} 已超过 10 分钟没有行情上报。\n"
                    "相关实盘策略可能不会生成或执行新订单，请检查 EA 图表、品种名称和网络连接。",
                )
                self.storage.execute("""INSERT INTO live_market_disconnect_alerts
                        (user_id, account_id, symbol, alerted_at, recovered_at)
                        VALUES (?, ?, ?, ?, NULL)
                        ON DUPLICATE KEY UPDATE alerted_at = VALUES(alerted_at), recovered_at = NULL""",
                        (row["user_id"], row["account_id"], row["symbol"], now))
            elif not stale and prior and int(prior.get("alerted_at") or 0):
                self.storage.execute("UPDATE live_market_disconnect_alerts SET recovered_at = ? WHERE account_id = ? AND symbol = ?",
                                    (now, row["account_id"], row["symbol"]))
