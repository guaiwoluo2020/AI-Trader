"""Durable cooldowns for account/deployment/plan execution decisions."""
from __future__ import annotations

import time
from typing import Optional


class StrategyDecisionCooldownRepository:
    def __init__(self, storage):
        self.storage = storage

    def get_active_until(self, cooldown_key: str, now: Optional[int] = None) -> int:
        now = int(time.time() if now is None else now)
        row = self.storage.fetchone(
            "SELECT cooldown_until FROM strategy_decision_cooldowns "
            "WHERE cooldown_key=? AND cooldown_until>? LIMIT 1",
            (str(cooldown_key), now),
        )
        return int(row["cooldown_until"] or 0) if row else 0

    def set_cooldown(
        self, cooldown_key: str, cooldown_until: int,
        *, user_id: int = 0, account_id: int = 0,
        deployment_id: str = "", strategy_id: str = "", plan_id: str = "",
        plan_stage: str = "", direction: str = "", now: Optional[int] = None,
    ) -> None:
        now = int(time.time() if now is None else now)
        self.storage.execute(
            """INSERT INTO strategy_decision_cooldowns(
                   cooldown_key,user_id,account_id,deployment_id,strategy_id,
                   plan_id,plan_stage,direction,cooldown_until,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)
               ON DUPLICATE KEY UPDATE
                   cooldown_until=VALUES(cooldown_until),updated_at=VALUES(updated_at)""",
            (
                str(cooldown_key), int(user_id), int(account_id),
                str(deployment_id), str(strategy_id), str(plan_id),
                str(plan_stage), str(direction), int(cooldown_until), now,
            ),
        )
