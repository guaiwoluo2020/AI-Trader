import unittest
from account_notification_service import AccountNotificationService


class FakeStorage:
    def __init__(self): self.claimed = set(); self.updated = []
    def fetchone(self, sql, params): return {"email": "user@example.com"}
    def execute(self, sql, params):
        if "INSERT IGNORE" in sql:
            key = tuple(params[1:5]); n = 1 if key not in self.claimed else 0; self.claimed.add(key)
            return type("R", (), {"rowcount": n})()
        self.updated.append((sql, params)); return type("R", (), {"rowcount": 1})()


class AccountNotificationTests(unittest.TestCase):
    def test_flatten_is_idempotent_and_contains_summary(self):
        storage = FakeStorage(); sent = []
        mailer = type("Mailer", (), {"notify": lambda self, r, s, m: sent.append((r, s, m)) or 1})()
        service = AccountNotificationService(storage, mailer)
        result = {"position_count": 3, "closed_count": 2, "failed_count": 1, "errors": ["BTCUSD 无报价"]}
        self.assertTrue(service.notify_flatten(7, 11, "Paper", result, "2026-09-12"))
        self.assertFalse(service.notify_flatten(7, 11, "Paper", result, "2026-09-12"))
        self.assertEqual(len(sent), 1)
        self.assertIn("持仓数量：3", sent[0][2]); self.assertIn("成功/失败：2 / 1", sent[0][2])

    def test_risk_reason_dedup_allows_different_reasons(self):
        storage = FakeStorage(); sent = []
        mailer = type("Mailer", (), {"notify": lambda self, r, s, m: sent.append(m) or 1})()
        service = AccountNotificationService(storage, mailer)
        self.assertTrue(service.notify_risk_block(7, 11, "Paper", "BTCUSD", "buy", .1, "订单上限", "2026-09-12"))
        self.assertFalse(service.notify_risk_block(7, 11, "Paper", "BTCUSD", "buy", .1, "订单上限", "2026-09-12"))
        self.assertTrue(service.notify_risk_block(7, 11, "Paper", "BTCUSD", "buy", .1, "保证金不足", "2026-09-12"))
        self.assertEqual(len(sent), 2)

    def test_mail_failure_is_swallowed(self):
        storage = FakeStorage()
        mailer = type("Mailer", (), {"notify": lambda self, *a: (_ for _ in ()).throw(RuntimeError("smtp down"))})()
        self.assertFalse(AccountNotificationService(storage, mailer).notify_risk_block(7, 11, "MT5", "GOLD", "sell", 1, "熔断", "2026-09-12"))


if __name__ == '__main__': unittest.main()
