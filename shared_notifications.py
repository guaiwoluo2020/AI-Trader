#!/usr/bin/env python3
"""Best-effort email notifications for shared object reference changes."""

from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from typing import Dict, Iterable

from email_verification import SystemEmailConfigRepository


class SharedReferenceNotificationService:
    """Notify users when shared strategies, Alpha definitions or policies change."""

    def __init__(self):
        self.config_repository = SystemEmailConfigRepository()

    def notify(
        self, recipients: Iterable[Dict], subject: str, message: str,
    ) -> int:
        try:
            config = self.config_repository.get(include_password=True)
        except Exception as exc:
            print(f"[SharedNotify] 邮件配置读取失败: {exc}")
            return 0
        if (
            not config.get("enabled")
            or not config.get("sender_email")
            or not config.get("password")
        ):
            return 0
        sent = 0
        for recipient in recipients:
            email = str(recipient.get("email") or "").strip()
            if not email:
                continue
            try:
                self._send(config, email, subject, message)
                sent += 1
            except Exception as exc:
                print(f"[SharedNotify] 发送给 {email} 失败: {exc}")
        return sent

    @staticmethod
    def _send(config: Dict, target: str, subject: str, message: str) -> None:
        mail = EmailMessage()
        mail["From"] = f'{config["sender_name"]} <{config["sender_email"]}>'
        mail["To"] = target
        mail["Subject"] = subject
        mail.set_content(message)
        mail.add_alternative(
            "<div style='font-family:Arial,sans-serif;max-width:620px;margin:auto'>"
            "<h2 style='color:#176b4d'>AI Trader 共享内容变更提醒</h2>"
            f"<p>{html.escape(message).replace(chr(10), '<br>')}</p>"
            "<p style='color:#71827a'>共享内容为动态引用，原作者变更会同步影响你的配置。</p>"
            "</div>",
            subtype="html",
        )
        smtp_class = smtplib.SMTP_SSL if config["use_ssl"] else smtplib.SMTP
        kwargs = {
            "host": config["smtp_host"],
            "port": config["smtp_port"],
            "timeout": 15,
        }
        if config["use_ssl"]:
            kwargs["context"] = ssl.create_default_context()
        with smtp_class(**kwargs) as smtp:
            smtp.login(config["sender_email"], config["password"])
            smtp.send_message(mail)
