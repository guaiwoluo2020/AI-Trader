"""Durable, best-effort account notifications for operational events."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from shared_notifications import SharedReferenceNotificationService

CHINA_TZ = timezone(timedelta(hours=8))


class AccountNotificationService:
    def __init__(self, storage, mailer=None):
        self.storage = storage
        self.mailer = mailer or SharedReferenceNotificationService()

    @staticmethod
    def _reason_key(reason: str) -> str:
        text = str(reason or "未分类风控拦截").strip()
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    def _claim(self, user_id: int, account_id: int, business_date: str,
               notification_type: str, reason_key: str, subject: str,
               message: str) -> bool:
        now = int(datetime.now(timezone.utc).timestamp())
        sql = """INSERT IGNORE INTO account_notification_deliveries
               (user_id, account_id, business_date, notification_type, reason_key,
                status, subject, message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)"""
        params = (int(user_id), int(account_id), business_date, notification_type,
                  reason_key, subject, message, now, now)
        if hasattr(self.storage, "_connect"):
            with self.storage._lock, self.storage._connect() as conn:
                result = conn.execute(sql, params)
                rowcount = int(getattr(result, "rowcount", 0) or 0)
        else:
            result = self.storage.execute(sql, params)
            rowcount = int(getattr(result, "rowcount", 0) or 0)
        return rowcount == 1

    def _send(self, user_id: int, account_id: int, business_date: str,
              notification_type: str, reason_key: str, subject: str,
              message: str) -> bool:
        user = self.storage.fetchone("SELECT email FROM users WHERE id = ?", (int(user_id),))
        email = str((user or {}).get("email") or "").strip()
        if not email or not self._claim(user_id, account_id, business_date,
                                         notification_type, reason_key, subject, message):
            return False
        try:
            sent = self.mailer.notify([{"email": email}], subject, message)
            ok = bool(sent)
            self.storage.execute(
                "UPDATE account_notification_deliveries SET status=?, sent_at=?, updated_at=? WHERE account_id=? AND business_date=? AND notification_type=? AND reason_key=?",
                ("sent" if ok else "skipped", int(datetime.now(timezone.utc).timestamp()) if ok else None,
                 int(datetime.now(timezone.utc).timestamp()), int(account_id), business_date,
                 notification_type, reason_key),
            )
            return ok
        except Exception as exc:
            self.storage.execute(
                "UPDATE account_notification_deliveries SET status='failed', error_message=?, updated_at=? WHERE account_id=? AND business_date=? AND notification_type=? AND reason_key=?",
                (str(exc)[:2000], int(datetime.now(timezone.utc).timestamp()), int(account_id),
                 business_date, notification_type, reason_key),
            )
            print(f"[AccountNotify] 邮件发送失败 account={account_id}: {exc}")
            return False

    def notify_flatten(self, user_id: int, account_id: int, account_name: str,
                       result: Dict, business_date: Optional[str] = None) -> bool:
        date = business_date or datetime.now(CHINA_TZ).date().isoformat()
        errors = "；".join(str(x) for x in (result.get("errors") or [])) or "无"
        message = (f"账户：{account_name}（ID {account_id}）\n"
                   f"北京时间业务日：{date}\n"
                   f"持仓数量：{int(result.get('position_count', 0))}\n"
                   f"已提交平仓：{int(result.get('closed_count', 0))}\n"
                   f"成功/失败：{int(result.get('closed_count', 0))} / {int(result.get('failed_count', 0))}\n"
                   f"失败明细：{errors}")
        return self._send(user_id, account_id, date, "daily_flatten", "completed",
                          f"AI Trader 每日清仓明细 - {account_name}", message)

    def notify_risk_block(self, user_id: int, account_id: int, account_name: str,
                          symbol: str, direction: str, volume: float, reason: str,
                          business_date: Optional[str] = None, strategy: str = "") -> bool:
        date = business_date or datetime.now(CHINA_TZ).date().isoformat()
        reason_key = self._reason_key(reason)
        message = (f"账户：{account_name}（ID {account_id}）\n策略：{strategy or '未指定'}\n"
                   f"品种/方向/手数：{symbol} / {direction} / {volume}\n"
                   f"风控原因：{reason}\n北京时间业务日：{date}")
        return self._send(user_id, account_id, date, "risk_block", reason_key,
                          f"AI Trader 风控拦截提醒 - {account_name}", message)
