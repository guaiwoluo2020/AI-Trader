#!/usr/bin/env python3
"""Short-lived operational data retention for the SQLite deployment."""

import json
import shutil
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
    VACUUM_INTERVAL_SECONDS = 7 * 86400
    VACUUM_FREE_RATIO = 0.20
    VACUUM_MIN_RECLAIM_BYTES = 20 * 1024 * 1024

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
                run_id, trigger_type, started_at, db_size_before
            ) VALUES(?, ?, ?, ?)""",
            (run_id, trigger_type, current, before["db_size_bytes"]),
        )
        cleanup_result = {}
        checkpoint_status = ""
        vacuum_status = "not_due"
        vacuum_reason = "距离上次每周检查不足 7 天"
        error_message = ""
        status = "completed"
        try:
            cleanup_result = self.cleanup(current)
            checkpoint_status = self._checkpoint()
            checked_at = self._last_vacuum_check_at()
            if (
                trigger_type == "manual"
                or current - checked_at >= self.VACUUM_INTERVAL_SECONDS
            ):
                inspected = self.get_space_status()
                vacuum_status, vacuum_reason = self._maybe_vacuum(inspected)
                self._set_last_vacuum_check_at(current)
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
                "vacuum_interval_days": self.VACUUM_INTERVAL_SECONDS // 86400,
                "vacuum_free_ratio": self.VACUUM_FREE_RATIO,
                "vacuum_min_reclaim_bytes": self.VACUUM_MIN_RECLAIM_BYTES,
            },
            "latest": runs[0] if runs else None,
            "runs": runs,
        }

    def get_space_status(self) -> Dict:
        db_file = Path(self.storage.db_file)
        with self.storage._lock, self.storage._connect() as conn:
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            free_pages = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        return {
            "db_size_bytes": db_file.stat().st_size if db_file.exists() else 0,
            "page_count": page_count,
            "free_page_count": free_pages,
            "page_size": page_size,
            "reclaimable_bytes": free_pages * page_size,
            "free_ratio": round(free_pages / max(page_count, 1), 6),
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

    def _checkpoint(self) -> str:
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return f"busy={int(result[0])}, log={int(result[1])}, checkpointed={int(result[2])}"

    def _maybe_vacuum(self, space: Dict) -> tuple:
        active = self._active_heavy_workloads()
        if active:
            return "skipped", f"当前有 {active} 个回测或 Alpha 任务运行，已跳过"
        if space["free_ratio"] < self.VACUUM_FREE_RATIO:
            return "skipped", "空闲页比例未达到 20%"
        if space["reclaimable_bytes"] < self.VACUUM_MIN_RECLAIM_BYTES:
            return "skipped", "预计可回收空间不足 20 MB"
        disk_free = shutil.disk_usage(Path(self.storage.db_file).parent).free
        if disk_free < max(space["db_size_bytes"] * 2, 64 * 1024 * 1024):
            return "skipped", "磁盘可用空间不足，无法安全重建数据库"
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("VACUUM")
        return "executed", "达到空间回收阈值，VACUUM 执行完成"

    def _active_heavy_workloads(self) -> int:
        row = self.storage.fetchone(
            """SELECT
                (SELECT COUNT(*) FROM backtest_tasks WHERE status = 'running') +
                (SELECT COUNT(*) FROM alpha_research_runs WHERE status = 'running')
                AS total"""
        )
        return int(row["total"] if row else 0)

    def _last_vacuum_check_at(self) -> int:
        row = self.storage.fetchone(
            "SELECT value FROM app_meta WHERE key = 'data_vacuum_checked_at'"
        )
        return int(row["value"]) if row else 0

    def _set_last_vacuum_check_at(self, checked_at: int) -> None:
        self.storage.execute(
            """INSERT INTO app_meta(key, value) VALUES('data_vacuum_checked_at', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(checked_at),),
        )

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
                    f"""DELETE FROM {table} WHERE rowid IN (
                        SELECT rowid FROM {table} WHERE {where} LIMIT ?
                    )""",
                    (*params, self.BATCH_SIZE),
                )
                conn.commit()
                count = max(0, int(cursor.rowcount))
            deleted += count
            if count < self.BATCH_SIZE:
                return deleted
