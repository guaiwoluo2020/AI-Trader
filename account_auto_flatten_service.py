"""Per-account scheduled flattening with durable daily idempotency."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from mysql_repositories import MySQLStorage, TradingAccountRepository, get_storage

CHINA_TZ = timezone(timedelta(hours=8))


def scheduled_window(time_text: str, now: Optional[datetime] = None) -> Optional[Tuple[datetime, datetime]]:
    """Return today's Beijing five-minute execution window when ``now`` is inside it."""
    text = str(time_text or "").strip()
    try:
        hour, minute = (int(item) for item in text.split(":", 1))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    except (TypeError, ValueError):
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(CHINA_TZ)
    scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window_end = scheduled + timedelta(minutes=5)
    if scheduled <= current < window_end:
        return scheduled, window_end
    return None


class AccountAutoFlattenService:
    def __init__(self, account_repository: TradingAccountRepository, paper_trading,
                 engine_manager, storage: Optional[MySQLStorage] = None):
        self.accounts = account_repository
        self.paper_trading = paper_trading
        self.engine_manager = engine_manager
        self.storage = storage or account_repository.storage or get_storage()

    def run_due_accounts(self) -> Dict:
        now = datetime.now(timezone.utc)
        rows = self.storage.fetchall(
            "SELECT id, user_id, account_type, auto_flatten_time FROM trading_accounts "
            "WHERE status = 'active' AND enabled = 1 AND auto_flatten_enabled = 1 "
            "AND auto_flatten_time IS NOT NULL AND auto_flatten_time <> ''"
        )
        summary = {"checked": len(rows), "started": 0, "completed": 0, "failed": 0, "skipped": 0}
        for row in rows:
            window = scheduled_window(row["auto_flatten_time"], now)
            if window is None:
                continue
            run = self._claim(row, window)
            if run is None:
                summary["skipped"] += 1
                continue
            summary["started"] += 1
            result = self._flatten_account(row)
            self._finish(run["run_id"], result)
            summary["completed" if not result["failed_count"] else "failed"] += 1
        self._fail_expired(now)
        return summary

    def _claim(self, row, window):
        scheduled, window_end = window
        business_date = scheduled.date().isoformat()
        run_id = uuid.uuid4().hex
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with self.storage._lock, self.storage._connect() as conn:
            existing = conn.execute(
                "SELECT run_id, status, window_ended_at FROM account_flatten_runs "
                "WHERE account_id = ? AND business_date = ? FOR UPDATE",
                (int(row["id"]), business_date),
            ).fetchone()
            if existing is not None:
                if str(existing["status"]) == "failed" and int(existing["window_ended_at"]) > now_ts:
                    conn.execute(
                        "UPDATE account_flatten_runs SET status='running', "
                        "error_message='', updated_at=? WHERE run_id=?",
                        (now_ts, existing["run_id"]),
                    )
                    conn.commit()
                    return {"run_id": existing["run_id"]}
                return None
            cursor = conn.execute(
                "INSERT IGNORE INTO account_flatten_runs "
                "(run_id, account_id, user_id, business_date, scheduled_time, "
                "window_started_at, window_ended_at, status, error_message, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', '', ?, ?)",
                (run_id, int(row["id"]), int(row["user_id"]), business_date,
                 str(row["auto_flatten_time"]), int(scheduled.timestamp()),
                 int(window_end.timestamp()), now_ts, now_ts),
            )
            conn.commit()
            if getattr(cursor, "rowcount", 0) != 1:
                return None
        return {"run_id": run_id}

    def _flatten_account(self, row) -> Dict:
        account_id, user_id = int(row["id"]), int(row["user_id"])
        if str(row["account_type"]).lower() == "paper":
            return self.paper_trading.flatten_account_positions(user_id, account_id)
        engine = self.engine_manager.get_engine(user_id, account_id)
        positions = list(engine.position_service.get_positions() or [])
        closed, errors = 0, []
        for position in positions:
            try:
                symbol = str(position.get("symbol") or "").strip()
                ticket = int(position.get("ticket") or position.get("position_id") or 0)
                if symbol and ticket:
                    engine.add_close_position_instruction(symbol, ticket)
                    closed += 1
            except Exception as exc:  # one bad position must not block others
                errors.append(str(exc))
        return {"position_count": len(positions), "closed_count": closed,
                "failed_count": len(errors), "errors": errors}

    def _finish(self, run_id: str, result: Dict) -> None:
        now = int(datetime.now(timezone.utc).timestamp())
        status = "failed" if result.get("failed_count") else "completed"
        errors = "; ".join(result.get("errors") or [])[:2000]
        self.storage.execute(
            "UPDATE account_flatten_runs SET status=?, position_count=?, closed_count=?, "
            "failed_count=?, error_message=?, updated_at=? WHERE run_id=?",
            (status, int(result.get("position_count", 0)), int(result.get("closed_count", 0)),
             int(result.get("failed_count", 0)), errors, now, run_id),
        )

    def _fail_expired(self, now: datetime) -> None:
        now_ts = int(now.timestamp())
        self.storage.execute(
            "UPDATE account_flatten_runs SET status='failed', "
            "error_message='清仓窗口已结束', updated_at=? "
            "WHERE status IN ('pending','running') AND window_ended_at <= ?",
            (now_ts, now_ts),
        )
