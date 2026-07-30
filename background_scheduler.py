#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""进程级共享后台任务调度器。"""

import queue
import threading
import time
from typing import Callable, Hashable


class SharedTaskScheduler:
    """使用单一调度线程和受限工作池执行账户后台任务。"""

    def __init__(
        self,
        tick_callback: Callable,
        interval_seconds: float = 1.0,
        max_workers: int = 4,
    ):
        self._tick_callback = tick_callback
        self._interval_seconds = max(float(interval_seconds), 0.05)
        self._max_workers = max(int(max_workers), 1)
        self._queue = queue.Queue()
        self._busy_keys = set()
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._workers = []

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

    def submit(self, task_key: Hashable, callback: Callable) -> bool:
        """同一任务尚未完成时不重复提交。"""
        with self._lock:
            if task_key in self._busy_keys or self._stop_event.is_set():
                return False
            self._busy_keys.add(task_key)
            self._queue.put((task_key, callback))
            return True

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
                task_key, _ = item
                with self._lock:
                    self._busy_keys.discard(task_key)
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
            task_key, callback = item
            try:
                callback()
            except Exception as exc:
                print(f"[SharedTaskScheduler] 任务 {task_key} 执行失败: {exc}")
            finally:
                with self._lock:
                    self._busy_keys.discard(task_key)
                self._queue.task_done()

    @staticmethod
    def _matches_prefix(task_key: Hashable, key_prefix: Hashable) -> bool:
        if isinstance(task_key, tuple):
            return bool(task_key) and task_key[0] == key_prefix
        return task_key == key_prefix
