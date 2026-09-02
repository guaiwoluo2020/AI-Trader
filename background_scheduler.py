#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程级共享后台任务调度器。"""

import queue
import threading
import time
import uuid
import json
from dataclasses import dataclass, asdict
from typing import Callable, Hashable, Dict, Optional, Any


@dataclass
class TaskRecord:
    task_id: str
    task_key: str
    status: str = "queued"
    attempts: int = 0
    max_retries: int = 0
    submitted_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: str = ""
    result: Any = None


class SharedTaskScheduler:
    """使用单一调度线程和受限工作池执行账户后台任务。"""

    def __init__(
        self,
        tick_callback: Callable,
        interval_seconds: float = 1.0,
        max_workers: int = 4,
        storage=None,
    ):
        self._tick_callback = tick_callback
        self._interval_seconds = max(float(interval_seconds), 0.05)
        self._max_workers = max(int(max_workers), 1)
        self._queue = queue.Queue()
        self._busy_keys = set()
        self._tasks: Dict[str, TaskRecord] = {}
        self._task_by_key: Dict[Hashable, str] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._workers = []
        self._storage = storage

    def _persist(self, record: TaskRecord) -> None:
        if self._storage is None:
            return
        try:
            now = int(time.time())
            self._storage.execute(
                """INSERT INTO background_tasks(
                    task_id,task_key,status,attempts,max_retries,submitted_at,
                    started_at,finished_at,lease_until,error_message,result_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,attempts=excluded.attempts,
                    started_at=excluded.started_at,finished_at=excluded.finished_at,
                    lease_until=excluded.lease_until,error_message=excluded.error_message,
                    result_json=excluded.result_json,updated_at=excluded.updated_at""",
                (record.task_id, record.task_key, record.status, record.attempts,
                 record.max_retries, int(record.submitted_at),
                 int(record.started_at or 0), int(record.finished_at or 0),
                 int((time.time() + 60) if record.status == "running" else 0),
                 record.error, json.dumps(record.result, ensure_ascii=False, default=str), now),
            )
        except Exception as exc:
            print(f"[SharedTaskScheduler] 任务状态持久化失败: {exc}")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="account-scheduler",
            )
            self._workers = [
                threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name=f"account-task-{index + 1}",
                )
                for index in range(self._max_workers)
            ]
            for worker in self._workers:
                worker.start()
            self._thread.start()

    def submit(self, task_key: Hashable, callback: Callable, *, max_retries: int = 0) -> bool:
        """提交后台任务；同一 key 在执行期间去重，并支持有限重试。"""
        with self._lock:
            if task_key in self._busy_keys or self._stop_event.is_set():
                return False
            self._busy_keys.add(task_key)
            task_id = uuid.uuid4().hex
            self._task_by_key[task_key] = task_id
            self._tasks[task_id] = TaskRecord(
                task_id=task_id, task_key=repr(task_key),
                max_retries=max(0, int(max_retries)), submitted_at=time.time(),
            )
            self._persist(self._tasks[task_id])
            self._queue.put((task_id, task_key, callback))
            return True

    def get_task(self, task_id: str) -> Optional[Dict]:
        """返回任务状态，供管理界面和异步接口轮询。"""
        with self._lock:
            record = self._tasks.get(str(task_id))
            return asdict(record) if record else None

    def list_tasks(self, limit: int = 50) -> list:
        with self._lock:
            records = sorted(self._tasks.values(), key=lambda item: item.submitted_at, reverse=True)
            result = [asdict(item) for item in records[:max(1, min(int(limit), 200))]]
        if self._storage is not None and len(result) < max(1, min(int(limit), 200)):
            try:
                rows = self._storage.fetchall(
                    "SELECT task_id,task_key,status,attempts,max_retries,submitted_at,"
                    "started_at,finished_at,lease_until,error_message,result_json "
                    "FROM background_tasks ORDER BY submitted_at DESC LIMIT ?",
                    (max(1, min(int(limit), 200)),),
                )
                known = {item["task_id"] for item in result}
                for row in rows:
                    if row["task_id"] in known:
                        continue
                    item = dict(row)
                    try:
                        item["result"] = json.loads(item.pop("result_json") or "null")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        item["result"] = None
                    item["error"] = item.pop("error_message", "")
                    result.append(item)
            except Exception as exc:
                print(f"[SharedTaskScheduler] 任务历史读取失败: {exc}")
        return result[:max(1, min(int(limit), 200))]

    def recover_expired_leases(self) -> int:
        """将崩溃遗留的 running 任务标记为可重试。"""
        if self._storage is None:
            return 0
        now = int(time.time())
        try:
            return int(self._storage.execute(
                "UPDATE background_tasks SET status='queued', lease_until=NULL, "
                "updated_at=? WHERE status='running' AND lease_until>0 AND lease_until<?",
                (now, now),
            ) or 0)
        except Exception as exc:
            print(f"[SharedTaskScheduler] 任务租约恢复失败: {exc}")
            return 0

    def is_busy(self, key_prefix: Hashable) -> bool:
        with self._lock:
            return any(
                self._matches_prefix(task_key, key_prefix)
                for task_key in self._busy_keys
            )

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                task_id, task_key, _ = item
                with self._lock:
                    self._busy_keys.discard(task_key)
                    self._task_by_key.pop(task_key, None)
                    record = self._tasks.get(task_id)
                    if record:
                        record.status = "canceled"
                        record.finished_at = time.time()
            self._queue.task_done()
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join(timeout=2)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick_callback(time.monotonic(), self)
            except Exception as exc:
                print(f"[SharedTaskScheduler] 调度异常: {exc}")
            self._stop_event.wait(self._interval_seconds)

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            task_id, task_key, callback = item
            with self._lock:
                record = self._tasks.get(task_id)
                if record:
                    record.status = "running"
                    record.started_at = time.time()
                    self._persist(record)
            try:
                while True:
                    with self._lock:
                        record = self._tasks.get(task_id)
                        if record:
                            record.attempts += 1
                            attempt = record.attempts
                            max_retries = record.max_retries
                    try:
                        result = callback()
                        with self._lock:
                            if record:
                                record.status = "succeeded"
                                record.result = result
                                record.finished_at = time.time()
                                self._persist(record)
                        break
                    except Exception as exc:
                        if attempt <= max_retries and not self._stop_event.is_set():
                            time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
                            continue
                        with self._lock:
                            if record:
                                record.status = "failed"
                                record.error = str(exc)[:1000]
                                record.finished_at = time.time()
                                self._persist(record)
                        print(f"[SharedTaskScheduler] 任务 {task_key} 执行失败: {exc}")
                        break
            finally:
                with self._lock:
                    self._busy_keys.discard(task_key)
                    self._task_by_key.pop(task_key, None)
                self._queue.task_done()

    @staticmethod
    def _matches_prefix(task_key: Hashable, key_prefix: Hashable) -> bool:
        if isinstance(task_key, tuple):
            return bool(task_key) and task_key[0] == key_prefix
        return task_key == key_prefix
