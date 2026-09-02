import unittest

from market.services.outbox_dispatcher import OutboxDispatcher


class FakeOutbox:
    def __init__(self, events):
        self.events = events
        self.published = []
        self.failed = []

    def recover_stale(self, lease_seconds):
        return 2

    def claim_pending(self, limit):
        return self.events[:limit]

    def mark_published(self, event_id):
        self.published.append(event_id)

    def mark_failed(self, event_id, error, retry_seconds=60):
        self.failed.append((event_id, error, retry_seconds))


class OutboxDispatcherTest(unittest.TestCase):
    def test_dispatches_success_and_retries_unknown_handler(self):
        repo = FakeOutbox([
            {"event_id": "1", "event_name": "ok", "payload": {"x": 1}},
            {"event_id": "2", "event_name": "unknown", "payload": {}},
        ])
        received = []
        dispatcher = OutboxDispatcher(repo, handlers={"ok": received.append})
        stats = dispatcher.dispatch_once(limit=10)
        self.assertEqual(stats.recovered, 2)
        self.assertEqual(stats.published, 1)
        self.assertEqual(stats.failed, 1)
        self.assertEqual(received[0]["event_id"], "1")
        self.assertEqual(repo.published, ["1"])
        self.assertEqual(repo.failed[0][0], "2")


if __name__ == "__main__":
    unittest.main()
