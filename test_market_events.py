#!/usr/bin/env python3
"""Public market event service tests."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from auth import AuthUser, reset_auth_manager
from market_event_repository import MarketEventRepository
import routes_news
from routes_news import create_news_routes, get_market_event_hub
from mysql_repositories import MySQLStorage, reset_storage


class _Request:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class _WebSocketClient:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


class MarketEventRepositoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MySQLStorage(str(Path(self.temp_dir.name) / "events.db"))
        self.storage.initialize()
        self.repository = MarketEventRepository(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_daily_upload_replaces_only_the_requested_day(self):
        self.repository.replace_calendar_day(
            "2026-08-09",
            [{"id": "old", "name": "Old", "event_time": "09:00"}],
            "jin10",
        )
        self.repository.replace_calendar_day(
            "2026-08-10",
            [{"id": "other", "name": "Other", "event_time": "10:00"}],
            "jin10",
        )

        self.repository.replace_calendar_day(
            "2026-08-09",
            [{"id": "new", "name": "New", "event_time": "11:00"}],
            "jin10",
        )

        self.assertEqual(
            [item["id"] for item in self.repository.list_calendar("2026-08-09")],
            ["new"],
        )
        self.assertEqual(
            [item["id"] for item in self.repository.list_calendar("2026-08-10")],
            ["other"],
        )

    def test_flash_news_upsert_is_idempotent(self):
        self.repository.upsert_flash_news(
            [{"id": "100", "content": "first", "published_at": "2026-08-09T10:00:00"}],
            "jin10",
        )
        self.repository.upsert_flash_news(
            [{"id": "100", "content": "updated", "published_at": "2026-08-09T10:00:00"}],
            "jin10",
        )

        items = self.repository.list_flash_news()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "updated")
        self.assertEqual(items[0]["source"], "jin10")


class MarketEventRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_DB_FILE"] = str(
            Path(self.temp_dir.name) / "routes.db"
        )
        reset_storage()
        reset_auth_manager()
        routes_news._market_event_hub = None
        self.router = create_news_routes()
        self.admin = AuthUser(user_id=1, username="admin", role="admin")

    def tearDown(self):
        routes_news._market_event_hub = None
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    def _endpoint(self, path, method):
        for route in self.router.routes:
            if route.path == path and method in getattr(route, "methods", set()):
                return route.endpoint
        self.fail(f"missing route {method} {path}")

    def test_jin10_aliases_are_normalized_and_only_flash_is_broadcast(self):
        client = _WebSocketClient()
        hub = get_market_event_hub()
        hub.ws_manager.add_client(client)

        calendar_endpoint = self._endpoint("/news/calendar/daily", "POST")
        calendar_result = asyncio.run(calendar_endpoint(
            _Request({
                "date": "2026-08-09",
                "source": "jin10",
                "data": [{
                    "id": 10,
                    "name": "美国CPI年率",
                    "time": "2026-08-09T20:30:00+08:00",
                    "star": 3,
                    "consensus": "2.8%",
                    "previous": "2.7%",
                }],
            }),
            self.admin,
        ))
        self.assertEqual(calendar_result["count"], 1)
        self.assertEqual(client.messages, [])

        flash_endpoint = self._endpoint("/news/flash", "POST")
        flash_result = asyncio.run(flash_endpoint(
            _Request({
                "source": "jin10",
                "data": [{
                    "id": 99,
                    "content": "美联储官员发表讲话",
                    "time": "2026-08-09T21:00:00+08:00",
                    "importance": 2,
                    "keywords": ["美联储"],
                }],
            }),
            self.admin,
        ))

        self.assertEqual(flash_result["count"], 1)
        self.assertEqual(len(client.messages), 1)
        self.assertEqual(client.messages[0]["type"], "market_flash_news_updated")
        self.assertEqual(client.messages[0]["items"][0]["source"], "jin10")
        hub.ws_manager.remove_client(client)


if __name__ == "__main__":
    unittest.main()
