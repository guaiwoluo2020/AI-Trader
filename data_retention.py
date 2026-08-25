#!/usr/bin/env python3
"""Short-lived operational data retention for the MySQL deployment."""

import json
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from sqlite_storage import SQLiteStorage, get_storage


class DataRetentionService:
    LLM_CALL_DAYS = 3
    PAPER_HEARTBEAT_DAYS = 3
    BACKTEST_DETAIL_DAYS = 7
    ALPHA_SIGNAL_DAYS = 7
    BATCH_SIZE = 5000

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.storage.initialize()

    def cleanup(self, now: Optional[int] = None) -> Dict[str, int]:
        current = int(now or time.time())
        terminal = ("completed", "failed", "canceled")
        results = {
            "llm_call_logs": self._delete_in_batches(
                "llm_call_logs",
                "created_at < ?",
                (current - self.LLM_CALL_DAYS * 86400,),
            ),
            "paper_heartbeat_logs": self._delete_in_batches(
                "paper_runtime_logs",
                "event_type = 'heartbeat' AND created_at < ?",
                (current - self.PAPER_HEARTBEAT_DAYS * 86400,),
            ),
            "backtest_orders": self._delete_task_details(
                "backtest_orders", current, terminal
            ),
            "backtest_replay_bars": self._delete_task_details(
                "backtest_replay_bars", current, terminal
            ),
            "alpha_research_signals": self._delete_alpha_signals(
                current, terminal
            ),
            "historical_klines": self._delete_in_batches(
                "historical_klines", "timestamp < ?", (current - 7 * 86400,)
            ),
        }
        return results

    def run_maintenance(
        self, trigger_type: str = "scheduled", now: Optional[int] = None
    ) -> Dict:
        current = int(now or time.time())
        started = time.monotonic()
        run_id = uuid.uuid4().hex
        before = self.get_space_status()
        self.storage.execute(
            """INSERT INTO data_maintenance_runs(
                run_id, trigger_type, status, started_at, db_size_before,
                checkpoint_status, vacuum_status, vacuum_reason, error_message
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, trigger_type, "running", current, before["db_size_bytes"],
                "not_required", "managed_by_rds", "MySQL InnoDB 空间维护由 RDS 管理", "",
            ),
        )
        cleanup_result = {}
        checkpoint_status = "not_required"
        vacuum_status = "managed_by_rds"
        vacuum_reason = "MySQL InnoDB 空间维护由 RDS 管理"
        error_message = ""
        status = "completed"
        try:
            cleanup_result = self.cleanup(current)
        except Exception as exc:
            status = "failed"
            error_message = str(exc)[:1000]
        after = self.get_space_status()
        completed_at = int(time.time())
        duration_ms = int((time.monotonic() - started) * 1000)
        self.storage.execute(
            """UPDATE data_maintenance_runs SET
                status = ?, completed_at = ?, duration_ms = ?,
                db_size_after = ?, page_count = ?, free_page_count = ?,
                reclaimable_bytes = ?, free_ratio = ?, checkpoint_status = ?,
                vacuum_status = ?, vacuum_reason = ?, cleanup_json = ?,
                error_message = ? WHERE run_id = ?""",
            (
                status, completed_at, duration_ms, after["db_size_bytes"],
                after["page_count"], after["free_page_count"],
                after["reclaimable_bytes"], after["free_ratio"],
                checkpoint_status, vacuum_status, vacuum_reason,
                json.dumps(cleanup_result, ensure_ascii=False), error_message,
                run_id,
            ),
        )
        return self.get_run(run_id) or {}

    def get_status(self) -> Dict:
        runs = self.list_runs(30)
        return {
            "space": self.get_space_status(),
            "policy": {
                "llm_call_days": self.LLM_CALL_DAYS,
                "paper_heartbeat_days": self.PAPER_HEARTBEAT_DAYS,
                "backtest_detail_days": self.BACKTEST_DETAIL_DAYS,
                "alpha_signal_days": self.ALPHA_SIGNAL_DAYS,
                "vacuum_interval_days": None,
                "vacuum_free_ratio": None,
                "vacuum_min_reclaim_bytes": None,
            },
            "latest": runs[0] if runs else None,
            "runs": runs,
        }

    def get_space_status(self) -> Dict:
        row = self.storage.fetchone(
            """SELECT COALESCE(SUM(data_length + index_length), 0) AS db_size_bytes
               FROM information_schema.tables
               WHERE table_schema = DATABASE()"""
        ) or {}
        return {
            "db_size_bytes": int(row.get("db_size_bytes") or 0),
            "page_count": 0,
            "free_page_count": 0,
            "page_size": 0,
            "reclaimable_bytes": 0,
            "free_ratio": 0,
        }

    def list_runs(self, limit: int = 30) -> list:
        rows = self.storage.fetchall(
            """SELECT * FROM data_maintenance_runs
               ORDER BY started_at DESC LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        )
        return [self._run_to_dict(row) for row in rows]

    def get_run(self, run_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT * FROM data_maintenance_runs WHERE run_id = ?", (run_id,)
        )
        return self._run_to_dict(row) if row else None

    def _active_heavy_workloads(self) -> int:
        row = self.storage.fetchone(
            """SELECT
                (SELECT COUNT(*) FROM backtest_tasks WHERE status = 'running') +
                (SELECT COUNT(*) FROM alpha_research_runs WHERE status = 'running')
                AS total"""
        )
        return int(row["total"] if row else 0)

    @staticmethod
    def _run_to_dict(row) -> Dict:
        data = dict(row)
        data["cleanup"] = json.loads(data.pop("cleanup_json") or "{}")
        return data

    def _delete_task_details(
        self, table: str, now: int, terminal: tuple
    ) -> int:
        cutoff = now - self.BACKTEST_DETAIL_DAYS * 86400
        placeholders = ",".join("?" for _ in terminal)
        return self._delete_in_batches(
            table,
            f"""task_id IN (
                SELECT task_id FROM backtest_tasks
                WHERE status IN ({placeholders}) AND completed_at < ?
            )""",
            (*terminal, cutoff),
        )

    def _delete_alpha_signals(
        self, now: int, terminal: tuple
    ) -> int:
        cutoff = now - self.ALPHA_SIGNAL_DAYS * 86400
        placeholders = ",".join("?" for _ in terminal)
        return self._delete_in_batches(
            "alpha_research_signals",
            f"""run_id IN (
                SELECT run_id FROM alpha_research_runs
                WHERE status IN ({placeholders}) AND completed_at < ?
            )""",
            (*terminal, cutoff),
        )

    def _delete_in_batches(
        self, table: str, where: str, params: tuple
    ) -> int:
        deleted = 0
        while True:
            with self.storage._lock, self.storage._connect() as conn:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE {where} LIMIT ?",
                    (*params, self.BATCH_SIZE),
                )
                conn.commit()
                count = max(0, int(cursor.rowcount))
            deleted += count
            if count < self.BATCH_SIZE:
                return deleted
