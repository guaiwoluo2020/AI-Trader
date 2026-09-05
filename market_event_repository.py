#!/usr/bin/env python3
"""MySQL-backed public market event data repository."""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from mysql_repositories import MySQLStorage, get_storage


class MarketEventRepository:
    """Store shared calendar events, key events, and flash news."""

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_storage()
        self.storage.initialize()

    def replace_calendar_day(
        self, event_date: str, events: List[Dict], source: str
    ) -> int:
        return self._replace_day(
            "market_calendar_events", event_date, events, source
        )

    def replace_key_event_day(
        self, event_date: str, events: List[Dict], source: str
    ) -> int:
        return self._replace_day(
            "market_key_events", event_date, events, source
        )

    def _replace_day(
        self,
        table: str,
        event_date: str,
        events: List[Dict],
        source: str,
    ) -> int:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            # Each provider owns only its own slice of a day.  An MT5 upload
            # must not erase official BLS/FOMC events (and vice versa).
            conn.execute(
                f"DELETE FROM {table} WHERE event_date = ? AND source = ?",
                (event_date, source),
            )
            for event in events:
                conn.execute(
                    f"""
                    INSERT INTO {table}(
                        event_date, event_id, event_time, importance, source,
                        payload_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_date,
                        event["id"],
                        event.get("event_time", ""),
                        int(event.get("importance", 0)),
                        source,
                        json.dumps(event, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            conn.commit()
        return len(events)

    def list_calendar(self, event_date: Optional[str] = None) -> List[Dict]:
        return self._list_daily("market_calendar_events", event_date)

    def list_key_events(self, event_date: Optional[str] = None) -> List[Dict]:
        return self._list_daily("market_key_events", event_date)

    def _list_daily(self, table: str, event_date: Optional[str]) -> List[Dict]:
        if event_date:
            rows = self.storage.fetchall(
                f"""
                SELECT event_date, source, payload_json, updated_at
                FROM {table}
                WHERE event_date = ?
                ORDER BY event_time, event_id
                """,
                (event_date,),
            )
        else:
            rows = self.storage.fetchall(
                f"""
                SELECT event_date, source, payload_json, updated_at
                FROM {table}
                ORDER BY event_date DESC, event_time DESC, event_id
                LIMIT 1000
                """
            )
        return [self._daily_row(row) for row in rows]

    @staticmethod
    def _daily_row(row) -> Dict:
        payload = json.loads(row["payload_json"])
        payload["event_date"] = row["event_date"]
        payload["source"] = row["source"]
        payload["updated_at"] = int(row["updated_at"])
        return payload

    def upsert_flash_news(self, items: List[Dict], source: str) -> int:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO market_flash_news(
                        news_id, published_at, importance, source,
                        payload_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(news_id) DO UPDATE SET
                        published_at = excluded.published_at,
                        importance = excluded.importance,
                        source = excluded.source,
                        payload_json = excluded.payload_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item["id"],
                        item.get("published_at", ""),
                        int(item.get("importance", 0)),
                        source,
                        json.dumps(item, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            conn.commit()
        return len(items)

    def list_flash_news(self, limit: int = 100) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT source, payload_json, updated_at
            FROM market_flash_news
            ORDER BY published_at DESC, updated_at DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 500)),),
        )
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["source"] = row["source"]
            payload["updated_at"] = int(row["updated_at"])
            result.append(payload)
        return result

    def get_status(self) -> Dict:
        calendar = self.storage.fetchone(
            "SELECT COUNT(*) AS count, MAX(updated_at) AS updated_at FROM market_calendar_events"
        )
        key_events = self.storage.fetchone(
            "SELECT COUNT(*) AS count, MAX(updated_at) AS updated_at FROM market_key_events"
        )
        flash = self.storage.fetchone(
            "SELECT COUNT(*) AS count, MAX(updated_at) AS updated_at FROM market_flash_news"
        )
        return {
            "calendar_count": int(calendar["count"]),
            "calendar_updated_at": calendar["updated_at"],
            "key_event_count": int(key_events["count"]),
            "key_event_updated_at": key_events["updated_at"],
            "flash_news_count": int(flash["count"]),
            "flash_news_updated_at": flash["updated_at"],
        }
