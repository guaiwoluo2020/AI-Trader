"""Monitor missing live ticks during broker-reported trading sessions."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone


class TickGapMonitor:
    THRESHOLD_SECONDS = 300

    def __init__(self, storage, notifications):
        self.storage = storage
        self.notifications = notifications
        self._last_tick = {}
        self._alerted = set()

    def record_tick(self, account_id: int, symbol: str, now: int | None = None) -> None:
        key = (int(account_id), str(symbol or '').strip())
        if not key[1]:
            return
        self._last_tick[key] = int(now or time.time())
        self._alerted.discard(key)

    @staticmethod
    def _in_session(raw: str, now: datetime) -> bool:
        try:
            sessions = json.loads(raw or '[]')
        except (TypeError, ValueError):
            return False
        if not isinstance(sessions, list):
            return False
        # MQL5 ENUM_DAY_OF_WEEK uses Sunday=0; Python datetime uses Monday=0.
        weekday = (now.weekday() + 1) % 7
        for item in sessions:
            if int(item.get('day', -1)) != weekday:
                continue
            offset = int(item.get('offset', 0) or 0)
            broker_now = now.astimezone(timezone(timedelta(minutes=offset)))
            broker_minute = broker_now.hour * 60 + broker_now.minute
            start, end = int(item.get('from', 0)), int(item.get('to', 0))
            if start <= broker_minute < end or (end == 0 and broker_minute >= start):
                return True
        return False

    def check(self, now: int | None = None) -> int:
        current = int(now or time.time())
        rows = self.storage.fetchall(
            "SELECT a.id,a.user_id,a.account_name,s.symbol,s.trade_sessions_json "
            "FROM trading_accounts a JOIN account_instrument_specs s ON s.account_id=a.id "
            "WHERE a.account_type='mt5' AND a.status='active' AND a.enabled=1 "
            "AND s.trade_sessions_json IS NOT NULL AND s.trade_sessions_json<>''"
        ) or []
        alerted = 0
        utc_now = datetime.fromtimestamp(current, timezone.utc)
        for row in rows:
            key = (int(row['id']), str(row['symbol']))
            last = self._last_tick.get(key)
            if last is None or current - last < self.THRESHOLD_SECONDS:
                continue
            if not self._in_session(row['trade_sessions_json'], utc_now) or key in self._alerted:
                continue
            self._alerted.add(key)
            self.notifications.notify_tick_gap(
                int(row['user_id']), int(row['id']), str(row['account_name'] or row['id']),
                str(row['symbol']), current - last,
            )
            alerted += 1
        return alerted
