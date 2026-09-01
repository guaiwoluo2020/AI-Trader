#!/usr/bin/env python3
"""Registration email verification and encrypted SMTP configuration."""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Dict, Optional

from cryptography.fernet import Fernet, InvalidToken

from mysql_repositories import MetaRepository, UserRepository, get_storage


ROOT_DIR = Path(__file__).resolve().parent
BLOCKLIST_PATH = ROOT_DIR / "resources" / "disposable_email_blocklist.conf"
CODE_TTL_SECONDS = 3 * 60
RESEND_INTERVAL_SECONDS = 60
MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 10


class EmailVerificationError(ValueError):
    """An email address or verification code cannot be accepted."""


class EmailDomainPolicy:
    """Validate addresses and reject disposable email domain suffixes."""

    def __init__(self, blocklist_path: Path = BLOCKLIST_PATH):
        self.blocklist_path = Path(blocklist_path)
        self._blocked_domains = self._load_blocklist()

    def _load_blocklist(self) -> set[str]:
        if not self.blocklist_path.is_file():
            raise RuntimeError(f"邮箱域名黑名单不存在: {self.blocklist_path}")
        return {
            line.strip().lower()
            for line in self.blocklist_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "//"))
        }

    def normalize(self, email: str) -> str:
        value = str(email or "").strip().lower()
        if len(value) > 254 or value.count("@") != 1:
            raise EmailVerificationError("请输入有效的邮箱地址")
        local, domain = value.rsplit("@", 1)
        if (
            not local or len(local) > 64 or not domain or "." not in domain
            or local.startswith(".") or local.endswith(".") or ".." in local
        ):
            raise EmailVerificationError("请输入有效的邮箱地址")
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789.!#$%&'*+/=?^_`{|}~-")
        if any(character not in allowed for character in local):
            raise EmailVerificationError("请输入有效的邮箱地址")
        try:
            domain = domain.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise EmailVerificationError("请输入有效的邮箱地址") from exc
        labels = domain.split(".")
        if any(
            not label or len(label) > 63 or label.startswith("-")
            or label.endswith("-") or not all(ch.isalnum() or ch == "-" for ch in label)
            for label in labels
        ):
            raise EmailVerificationError("请输入有效的邮箱地址")
        normalized = f"{local}@{domain}"
        if self.is_blocked(domain):
            raise EmailVerificationError("该邮箱域名属于临时邮箱，不能用于注册")
        return normalized

    def is_blocked(self, domain: str) -> bool:
        parts = str(domain or "").lower().strip(".").split(".")
        return any(".".join(parts[index:]) in self._blocked_domains for index in range(len(parts) - 1))


class SystemEmailConfigRepository:
    """Store global SMTP settings while keeping the password encrypted."""

    ENCRYPTION_KEY_META = "system_config_encryption_key"

    def __init__(self):
        self.storage = get_storage()
        self.meta = MetaRepository(self.storage)

    def _cipher(self) -> Fernet:
        key = self.meta.get(self.ENCRYPTION_KEY_META)
        if not key:
            key = Fernet.generate_key().decode("ascii")
            self.meta.set(self.ENCRYPTION_KEY_META, key)
        return Fernet(key.encode("ascii"))

    def get(self, include_password: bool = False) -> Dict:
        row = self.storage.fetchone("SELECT * FROM system_email_config WHERE id = 1")
        if row is None:
            return {
                "smtp_host": "smtp.qiye.aliyun.com",
                "smtp_port": 465,
                "use_ssl": True,
                "sender_email": "",
                "sender_name": "AI Trader",
                "password": "" if include_password else None,
                "password_set": False,
                "enabled": False,
                "updated_at": None,
            }
        encrypted = row["encrypted_password"] or ""
        password = ""
        if include_password and encrypted:
            try:
                password = self._cipher().decrypt(encrypted.encode("ascii")).decode("utf-8")
            except (InvalidToken, UnicodeError) as exc:
                raise RuntimeError("邮件服务密码无法解密，请管理员重新保存") from exc
        return {
            "smtp_host": row["smtp_host"],
            "smtp_port": int(row["smtp_port"]),
            "use_ssl": bool(row["use_ssl"]),
            "sender_email": row["sender_email"],
            "sender_name": row["sender_name"],
            "password": password if include_password else None,
            "password_set": bool(encrypted),
            "enabled": bool(row["enabled"]),
            "updated_at": int(row["updated_at"]),
        }

    def save(self, data: Dict, updated_by: int) -> Dict:
        current = self.get(include_password=False)
        host = str(data.get("smtp_host", current["smtp_host"]) or "").strip()
        port = int(data.get("smtp_port", current["smtp_port"]))
        sender_email = EmailDomainPolicy().normalize(
            data.get("sender_email", current["sender_email"])
        )
        sender_name = str(data.get("sender_name", current["sender_name"]) or "AI Trader").strip()
        if not host or not 1 <= port <= 65535:
            raise ValueError("SMTP 地址或端口无效")
        if len(sender_name) > 80 or "\n" in sender_name or "\r" in sender_name:
            raise ValueError("发件人名称无效")
        password = data.get("password")
        encrypted = self.storage.fetchone(
            "SELECT encrypted_password FROM system_email_config WHERE id = 1"
        )
        encrypted_password = encrypted["encrypted_password"] if encrypted else ""
        if password is not None and str(password):
            encrypted_password = self._cipher().encrypt(
                str(password).encode("utf-8")
            ).decode("ascii")
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO system_email_config(
                id, smtp_host, smtp_port, use_ssl, sender_email, sender_name,
                encrypted_password, enabled, updated_by, created_at, updated_at
            ) VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                smtp_host = excluded.smtp_host,
                smtp_port = excluded.smtp_port,
                use_ssl = excluded.use_ssl,
                sender_email = excluded.sender_email,
                sender_name = excluded.sender_name,
                encrypted_password = excluded.encrypted_password,
                enabled = excluded.enabled,
                updated_by = excluded.updated_by,
                updated_at = excluded.updated_at
            """,
            (
                host, port, int(bool(data.get("use_ssl", current["use_ssl"]))),
                sender_email, sender_name, encrypted_password,
                int(bool(data.get("enabled", current["enabled"]))),
                int(updated_by), now, now,
            ),
        )
        return self.get(include_password=False)


class EmailVerificationRepository:
    def __init__(self):
        self.storage = get_storage()

    def get(self, email: str):
        return self.storage.fetchone(
            "SELECT * FROM email_verification_codes WHERE email = ?", (email,)
        )

    def save(
        self, email: str, code_hash: str, code_salt: str, purpose: str,
    ) -> None:
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO email_verification_codes(
                email, code_hash, code_salt, purpose, expires_at, sent_at,
                attempts, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(email) DO UPDATE SET
                code_hash = excluded.code_hash,
                code_salt = excluded.code_salt,
                purpose = excluded.purpose,
                expires_at = excluded.expires_at,
                sent_at = excluded.sent_at,
                attempts = 0,
                created_at = excluded.created_at
            """,
            (
                email, code_hash, code_salt, purpose,
                now + CODE_TTL_SECONDS, now, now,
            ),
        )

    def increment_attempts(self, email: str) -> None:
        self.storage.execute(
            "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE email = ?",
            (email,),
        )

    def consume(self, email: str) -> None:
        self.storage.execute("DELETE FROM email_verification_codes WHERE email = ?", (email,))

    def enforce_requester_limit(self, requester: str) -> str:
        requester_hash = hashlib.sha256(
            str(requester or "unknown").encode("utf-8")
        ).hexdigest()
        cutoff = int(time.time()) - 3600
        self.storage.execute(
            "DELETE FROM email_verification_send_events WHERE sent_at < ?", (cutoff,)
        )
        row = self.storage.fetchone(
            """
            SELECT COUNT(*) AS total FROM email_verification_send_events
            WHERE requester_hash = ? AND sent_at >= ?
            """,
            (requester_hash, cutoff),
        )
        if row and int(row["total"]) >= MAX_SENDS_PER_HOUR:
            raise EmailVerificationError("验证码发送次数过多，请一小时后重试")
        return requester_hash

    def record_send(self, requester_hash: str) -> None:
        self.storage.execute(
            "INSERT INTO email_verification_send_events(requester_hash, sent_at) VALUES(?, ?)",
            (requester_hash, int(time.time())),
        )


class EmailVerificationService:
    def __init__(self):
        self.policy = EmailDomainPolicy()
        self.config_repository = SystemEmailConfigRepository()
        self.repository = EmailVerificationRepository()
        self.user_repository = UserRepository()

    @staticmethod
    def _hash_code(email: str, code: str, salt: str, purpose: str) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256",
            f"{email}:{purpose}:{code}".encode("utf-8"),
            salt.encode("ascii"),
            120_000,
        ).hex()

    def send_code(
        self, email: str, requester: str = "unknown",
        purpose: str = "registration",
    ) -> Dict:
        if purpose not in {"registration", "login"}:
            raise EmailVerificationError("不支持的验证码用途")
        normalized = self.policy.normalize(email)
        existing_user = self.user_repository.get_by_email(normalized)
        if purpose == "registration" and existing_user:
            raise EmailVerificationError("该邮箱已被注册，请直接登录")
        if purpose == "login" and not existing_user:
            raise EmailVerificationError("该邮箱尚未加入，请使用邀请链接注册")
        existing = self.repository.get(normalized)
        now = int(time.time())
        if existing and now - int(existing["sent_at"]) < RESEND_INTERVAL_SECONDS:
            wait = RESEND_INTERVAL_SECONDS - (now - int(existing["sent_at"]))
            raise EmailVerificationError(f"发送过于频繁，请 {wait} 秒后重试")
        requester_hash = self.repository.enforce_requester_limit(requester)

        config = self.config_repository.get(include_password=True)
        if not config["enabled"]:
            raise RuntimeError("注册邮件服务尚未启用，请联系管理员")
        if not config["sender_email"] or not config["password"]:
            raise RuntimeError("验证码邮件服务尚未配置，请联系管理员")

        code = f"{secrets.randbelow(1_000_000):06d}"
        self._send_message(config, normalized, code, purpose)
        salt = secrets.token_hex(16)
        self.repository.save(
            normalized,
            self._hash_code(normalized, code, salt, purpose),
            salt,
            purpose,
        )
        self.repository.record_send(requester_hash)
        return {"email": normalized, "expires_in": CODE_TTL_SECONDS, "resend_in": RESEND_INTERVAL_SECONDS}

    def assert_valid_code(
        self, email: str, code: str, purpose: str = "registration",
    ) -> str:
        normalized = self.policy.normalize(email)
        value = str(code or "").strip()
        if not value.isdigit() or len(value) != 6:
            raise EmailVerificationError("请输入 6 位邮箱验证码")
        row = self.repository.get(normalized)
        now = int(time.time())
        if row is None or int(row["expires_at"]) < now:
            raise EmailVerificationError("验证码不存在或已过期，请重新获取")
        if row["purpose"] != purpose:
            raise EmailVerificationError("验证码用途不匹配，请重新获取")
        if int(row["attempts"]) >= MAX_ATTEMPTS:
            raise EmailVerificationError("验证码尝试次数过多，请重新获取")
        expected = self._hash_code(
            normalized, value, row["code_salt"], purpose
        )
        if not hmac.compare_digest(row["code_hash"], expected):
            self.repository.increment_attempts(normalized)
            raise EmailVerificationError("邮箱验证码不正确")
        return normalized

    def consume(self, email: str) -> None:
        self.repository.consume(self.policy.normalize(email))

    def send_test(self, target_email: Optional[str] = None) -> Dict:
        config = self.config_repository.get(include_password=True)
        if not config["sender_email"] or not config["password"]:
            raise RuntimeError("请先保存发件邮箱和 SMTP 密码")
        target = self.policy.normalize(target_email or config["sender_email"])
        self._send_message(config, target, None, "test")
        return {"target_email": target}

    def send_admin_alert(self, subject: str, body: str) -> Dict:
        """向管理员发送系统告警，不触碰验证码发送频控记录。"""
        config = self.config_repository.get(include_password=True)
        if not config["enabled"] or not config["sender_email"] or not config["password"]:
            raise RuntimeError("邮件服务未启用或 SMTP 配置不完整")
        admins = [
            user for user in self.user_repository.list_users()
            if user.role == "admin" and user.email
        ]
        if not admins:
            raise RuntimeError("没有配置管理员邮箱")
        target = self.policy.normalize(admins[0].email)
        self._send_message(
            config, target, None, "alert",
            subject=str(subject or "AI Trader 系统告警")[:180],
            body=str(body or ""),
        )
        return {"target_email": target}

    @staticmethod
    def _send_message(
        config: Dict, target: str, code: Optional[str], purpose: str,
        subject: Optional[str] = None, body: Optional[str] = None,
    ) -> None:
        message = EmailMessage()
        message["From"] = f'{config["sender_name"]} <{config["sender_email"]}>'
        message["To"] = target
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message["Message-ID"] = make_msgid(domain=config["sender_email"].split("@", 1)[-1])
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"
        if subject is not None or body is not None:
            message["Subject"] = subject or "AI Trader 系统告警"
            message.set_content(body or "")
        elif code:
            action = "登录" if purpose == "login" else "注册"
            message["Subject"] = f"AI Trader {action}验证码 · {time.strftime('%H:%M:%S')}"
            message.set_content(
                f"你的 AI Trader {action}验证码是：{code}\n\n"
                "验证码 3 分钟内有效，请勿向任何人泄露。"
            )
            message.add_alternative(
                "<div style='font-family:Arial,sans-serif;max-width:560px;margin:auto'>"
                f"<h2 style='color:#176b4d'>AI Trader {action}验证</h2>"
                f"<p>你的{action}验证码是：</p>"
                f"<div style='font-size:32px;font-weight:700;letter-spacing:8px'>{html.escape(code)}</div>"
                "<p style='color:#71827a'>验证码 3 分钟内有效，请勿向任何人泄露。</p>"
                "</div>",
                subtype="html",
            )
        else:
            message["Subject"] = "AI Trader 邮件服务测试"
            message.set_content("邮件服务配置成功，注册验证码可以正常发送。")

        smtp_class = smtplib.SMTP_SSL if config["use_ssl"] else smtplib.SMTP
        kwargs = {"host": config["smtp_host"], "port": config["smtp_port"], "timeout": 15}
        if config["use_ssl"]:
            kwargs["context"] = ssl.create_default_context()
        try:
            with smtp_class(**kwargs) as smtp:
                smtp.login(config["sender_email"], config["password"])
                refused = smtp.send_message(message)
                if refused:
                    raise RuntimeError(f"SMTP 拒绝收件地址: {', '.join(refused)}")
        except smtplib.SMTPAuthenticationError as exc:
            raise RuntimeError(
                "SMTP 认证失败，请检查邮箱密码；若启用了三方客户端安全密码，"
                "请使用该安全密码"
            ) from exc
        except smtplib.SMTPException as exc:
            raise RuntimeError(f"SMTP 发送失败: {exc}") from exc
