"""Persistent cooldowns for key-level signal generation."""

from __future__ import annotations

import time
from typing import Optional


class KeyLevelCooldownRepository:
    """Store directional key-level cooldowns in MySQL.

    The generator keeps a local cache for the hot path, while this repository
    is the durable source of truth used after process restarts and by other
    workers evaluating the same account.
    """

    def __init__(self, storage, user_id: int = 0, account_id: int = 0):
        self.storage = storage
        self.user_id = int(user_id or 0)
        self.account_id = int(account_id or 0)

    def _scoped_id(self, cooldown_id: str) -> str:
        return f"{self.user_id}|{self.account_id}|{str(cooldown_id)}"

    def get_active_until(
        self, cooldown_id: str, now: Optional[int] = None,
    ) -> Optional[int]:
        now = int(time.time() if now is None else now)
        scoped_id = self._scoped_id(cooldown_id)
        row = self.storage.fetchone(
            """SELECT cooldown_until
               FROM key_level_signal_cooldowns
               WHERE cooldown_id=? AND user_id=? AND account_id=?
                 AND cooldown_until>?""",
            (scoped_id, self.user_id, self.account_id, now),
        )
        if not row:
            return None
        try:
            return int(row.get("cooldown_until") or 0)
        except (TypeError, ValueError):
            return None

    def set_cooldown(
        self, cooldown_id: str, cooldown_until: int, now: Optional[int] = None,
    ) -> None:
        now = int(time.time() if now is None else now)
        scoped_id = self._scoped_id(cooldown_id)
        self.storage.execute(
            """INSERT INTO key_level_signal_cooldowns(
                   cooldown_id,user_id,account_id,cooldown_until,updated_at
               ) VALUES(?,?,?,?,?)
               ON DUPLICATE KEY UPDATE
                   cooldown_until=VALUES(cooldown_until),
                   updated_at=VALUES(updated_at)""",
            (
                scoped_id, self.user_id, self.account_id,
                int(cooldown_until), now,
            ),
        )

    def claim_cooldown(
        self, cooldown_id: str, cooldown_seconds: int,
        now: Optional[int] = None,
    ) -> bool:
        """Atomically claim a cooldown if no active row exists."""
        now = int(time.time() if now is None else now)
        scoped_id = self._scoped_id(cooldown_id)
        cooldown_until = now + max(0, int(cooldown_seconds))
        # The storage lock serializes connection checkout in this process;
        # SELECT ... FOR UPDATE serializes concurrent workers at the row level.
        self.storage.initialize()
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """SELECT cooldown_until
                   FROM key_level_signal_cooldowns
                   WHERE cooldown_id=? AND user_id=? AND account_id=?
                   FOR UPDATE""",
                (scoped_id, self.user_id, self.account_id),
            ).fetchone()
            if row:
                try:
                    existing_until = int(row.get("cooldown_until") or 0)
                except (TypeError, ValueError):
                    existing_until = 0
                if existing_until > now:
                    return False
                conn.execute(
                    """UPDATE key_level_signal_cooldowns
                       SET cooldown_until=?, updated_at=?
                       WHERE cooldown_id=? AND user_id=? AND account_id=?""",
                    (
                        cooldown_until, now, scoped_id,
                        self.user_id, self.account_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO key_level_signal_cooldowns(
                           cooldown_id,user_id,account_id,cooldown_until,updated_at
                       ) VALUES(?,?,?,?,?)""",
                    (
                        scoped_id, self.user_id, self.account_id,
                        cooldown_until, now,
                    ),
                )
        return True
