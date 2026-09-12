"""Per-account scheduled flattening with durable daily idempotency."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from mysql_repositories import MySQLStorage, TradingAccountRepository, get_storage
from account_notification_service import AccountNotificationService

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
        self.notifications = AccountNotificationService(self.storage)

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
            result = self._flatten_account(row, run["run_id"])
            self._finish(run["run_id"], result)
            summary["completed" if not result["failed_count"] else "failed"] += 1
        self._fail_expired(now)
        self._record_missed_windows(now)
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

    def _flatten_account(self, row, run_id: str = "") -> Dict:
        account_id, user_id = int(row["id"]), int(row["user_id"])
        if str(row["account_type"]).lower() == "paper":
            return dict(self.paper_trading.flatten_account_positions(user_id, account_id), asynchronous=False)
        engine = self.engine_manager.get_engine(user_id, account_id)
        positions = list(engine.position_service.get_positions() or [])
        closed, errors = 0, []
        for position in positions:
            try:
                symbol = str(position.get("symbol") or "").strip()
                ticket = int(position.get("ticket") or position.get("position_id") or 0)
                if symbol and ticket:
                    instruction_id = f"flatten-{run_id}-{ticket}"
                    self.storage.execute(
                        "INSERT IGNORE INTO account_flatten_items "
                        "(instruction_id,run_id,account_id,user_id,symbol,ticket,status,requested_at,reason) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (instruction_id, run_id, account_id, user_id, symbol, ticket,
                         "pending", int(datetime.now(timezone.utc).timestamp()), "scheduled_flatten"),
                    )
                    engine.add_close_position_instruction(symbol, ticket, instruction_id, run_id)
                    self._audit(user_id, account_id, symbol, instruction_id, "position_close_requested",
                                "已生成定时清仓平仓指令", run_id, {"ticket": ticket})
                    closed += 1
            except Exception as exc:  # one bad position must not block others
                errors.append(str(exc))
        return {"position_count": len(positions), "closed_count": closed,
                "failed_count": len(errors), "errors": errors, "asynchronous": True}

    def _finish(self, run_id: str, result: Dict) -> None:
        now = int(datetime.now(timezone.utc).timestamp())
        # MT5 requests are asynchronous: a queued command is not a completed
        # close.  Completion is finalized by the EA execution receipt.
        status = "failed" if result.get("failed_count") else (
            "waiting_execution" if result.get("asynchronous") and int(result.get("closed_count", 0))
            else "skipped_no_positions" if not int(result.get("position_count", 0)) else "completed"
        )
        errors = "; ".join(result.get("errors") or [])[:2000]
        self.storage.execute(
            "UPDATE account_flatten_runs SET status=?, position_count=?, closed_count=?, "
            "failed_count=?, error_message=?, updated_at=? WHERE run_id=?",
            (status, int(result.get("position_count", 0)), int(result.get("closed_count", 0)),
             int(result.get("failed_count", 0)), errors, now, run_id),
        )
        self._audit_from_result(run_id, result, status)
        try:
            run = self.storage.fetchone(
                "SELECT r.user_id, r.account_id, r.business_date, a.account_name "
                "FROM account_flatten_runs r JOIN trading_accounts a ON a.id=r.account_id "
                "WHERE r.run_id = ?", (run_id,)
            )
            if run and status in {"completed", "failed", "skipped_no_positions"}:
                self.notifications.notify_flatten(
                    int(run["user_id"]), int(run["account_id"]),
                    str(run["account_name"] or run["account_id"]), result,
                    business_date=str(run["business_date"]),
                )
        except Exception as exc:
            print(f"[Flatten] 清仓邮件通知失败 run={run_id}: {exc}")

    def _audit(self, user_id, account_id, symbol, entity_id, event_type, message, run_id, detail=None):
        try:
            from system_event_log import SystemEventLogRepository
            SystemEventLogRepository(self.storage).add({
                "user_id": user_id, "account_id": account_id, "symbol": symbol,
                "event_type": event_type, "event_name": message, "category": "trading",
                "entity_type": "account_flatten", "entity_id": entity_id,
                "correlation_id": run_id, "message": message, "status": "info",
                "detail": detail or {},
            })
        except Exception as exc:
            print(f"[Flatten] 审计写入失败: {exc}")

    def _audit_from_result(self, run_id, result, status):
        row = self.storage.fetchone("SELECT user_id,account_id FROM account_flatten_runs WHERE run_id=?", (run_id,))
        if row:
            self._audit(row["user_id"], row["account_id"], "", run_id,
                        "triggered", f"定时清仓已触发，状态={status}", run_id, result)

    def _fail_expired(self, now: datetime) -> None:
        now_ts = int(now.timestamp())
        expired = self.storage.fetchall(
            "SELECT instruction_id,run_id,user_id,account_id,symbol FROM account_flatten_items "
            "WHERE status IN ('pending','delivered') AND run_id IN "
            "(SELECT run_id FROM account_flatten_runs WHERE window_ended_at <= ?)",
            (now_ts,),
        )
        for item in expired:
            self.storage.execute(
                "UPDATE account_flatten_items SET status='timeout',reported_at=?,reason=? "
                "WHERE instruction_id=? AND status IN ('pending','delivered')",
                (now_ts, "清仓窗口内未收到EA回执", item["instruction_id"]),
            )
            self._audit(item["user_id"], item["account_id"], item["symbol"], item["instruction_id"],
                        "position_close_timeout", "定时清仓平仓超时", item["run_id"], {})
        self.storage.execute(
            "UPDATE account_flatten_runs SET status='failed', "
            "error_message='清仓窗口已结束', updated_at=? "
            "WHERE status IN ('pending','running') AND window_ended_at <= ?",
            (now_ts, now_ts),
        )

    def _record_missed_windows(self, now: datetime) -> None:
        """Create an auditable missed-window record when the worker was down."""
        current = now.astimezone(CHINA_TZ)
        for row in self.storage.fetchall(
            "SELECT id,user_id,auto_flatten_time FROM trading_accounts "
            "WHERE status='active' AND enabled=1 AND auto_flatten_enabled=1 "
            "AND auto_flatten_time IS NOT NULL AND auto_flatten_time<>''"
        ):
            try:
                hour, minute = (int(x) for x in str(row["auto_flatten_time"]).split(":", 1))
                scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if current < scheduled + timedelta(minutes=5):
                    continue
                day = scheduled.date().isoformat()
                existing = self.storage.fetchone(
                    "SELECT status FROM account_flatten_runs WHERE account_id=? AND business_date=?",
                    (int(row["id"]), day),
                )
                if existing:
                    continue
                run_id = f"missed-{int(row['id'])}-{day}"
                now_ts = int(now.timestamp())
                self.storage.execute(
                    "INSERT IGNORE INTO account_flatten_runs "
                    "(run_id,account_id,user_id,business_date,scheduled_time,window_started_at,window_ended_at,status,error_message,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, int(row["id"]), int(row["user_id"]), day, str(row["auto_flatten_time"]),
                     int(scheduled.timestamp()), int((scheduled + timedelta(minutes=5)).timestamp()),
                     "missed_window", "服务未在清仓窗口运行", now_ts, now_ts),
                )
                self._audit(int(row["user_id"]), int(row["id"]), "", run_id,
                            "missed_window", "定时清仓窗口已错过", run_id, {"scheduled_time": str(row["auto_flatten_time"])})
            except (TypeError, ValueError):
                continue
