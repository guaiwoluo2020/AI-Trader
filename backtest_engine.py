#!/usr/bin/env python3
"""Deterministic M1 replay engine with synchronous, cached LLM barriers."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from backtest_tasks import BacktestTaskStatus
from market.models import (
    PositionManagementPolicy, SignalSource, TradingSignal, TradingStrategy,
)
from market.services.position_manager import PositionManager
from market.services.llm_service import LLMService
from market.services.signal.signal_rules import (
    build_ai_entry_signal,
    build_key_level_state_signal,
    build_moving_average_state_signal,
    direction_action,
    evaluate_moving_average_state,
    extract_ai_trend_state,
    valid_exits as shared_valid_exits,
)
from market.services.signal.key_level_signal import evaluate_key_level_expression
from market.services.signal.alpha_factor_signal import AlphaRuntimeExecutor
from market.services.strategy.strategy_service import StrategyService
from market.store.llm_store import LLMStore
from sqlite_storage import SQLiteStorage, get_storage


ENGINE_VERSION = "direction-consensus-2"
PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
PERIOD_LIMITS = {"M1": 60, "M5": 48, "M15": 40, "H1": 30, "H4": 30}


class BacktestEngineError(RuntimeError):
    """A task cannot produce a trustworthy backtest result."""


class BacktestCanceled(BacktestEngineError):
    """A user requested a cooperative stop of the current replay."""


class BacktestTaskRepository:
    """Atomically claims tasks and keeps task/batch status consistent."""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def recover_stale(self, stale_seconds: int = 600) -> int:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            canceled_batches = [
                row["batch_id"] for row in conn.execute(
                    """
                    SELECT DISTINCT batch_id FROM backtest_tasks
                    WHERE status = ? AND cancel_requested = 1
                    """,
                    (BacktestTaskStatus.RUNNING,),
                ).fetchall()
            ]
            conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, completed_at = ?, heartbeat_at = ?, worker_id = ''
                WHERE status = ? AND cancel_requested = 1
                """,
                (
                    BacktestTaskStatus.CANCELED, now, now,
                    BacktestTaskStatus.RUNNING,
                ),
            )
            cursor = conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, worker_id = '', error_message = '', heartbeat_at = NULL
                WHERE status = ? AND cancel_requested = 0
                  AND COALESCE(heartbeat_at, started_at, 0) < ?
                """,
                (BacktestTaskStatus.QUEUED, BacktestTaskStatus.RUNNING, now - stale_seconds),
            )
            for batch_id in canceled_batches:
                self._refresh_batch(conn, batch_id, now)
            conn.commit()
            return cursor.rowcount

    def claim_next(self, worker_id: str) -> Optional[Dict]:
        now = int(time.time())
        self.storage.initialize()
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT t.*, b.strategy_snapshot_json, b.strategy_snapshot_hash,
                       b.template_snapshot_json
                FROM backtest_tasks t
                JOIN backtest_batches b ON b.batch_id = t.batch_id
                WHERE t.status = ? AND t.cancel_requested = 0
                ORDER BY t.created_at, t.task_id
                LIMIT 1
                """,
                (BacktestTaskStatus.QUEUED,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            cursor = conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, progress = 0, started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?, worker_id = ?, engine_version = ?,
                    error_message = '', cancel_requested = 0,
                    llm_analysis_count = 0, llm_call_count = 0,
                    llm_cache_hits = 0
                WHERE task_id = ? AND status = ? AND cancel_requested = 0
                """,
                (
                    BacktestTaskStatus.RUNNING,
                    now,
                    now,
                    worker_id,
                    ENGINE_VERSION,
                    row["task_id"],
                    BacktestTaskStatus.QUEUED,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE backtest_batches
                SET status = ?, started_at = COALESCE(started_at, ?)
                WHERE batch_id = ? AND status = ?
                """,
                (
                    BacktestTaskStatus.RUNNING,
                    now,
                    row["batch_id"],
                    BacktestTaskStatus.QUEUED,
                ),
            )
            conn.commit()
            return self._decode_task(dict(row))

    def heartbeat(self, task_id: str, progress: float) -> bool:
        with self.storage._lock, self.storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE backtest_tasks SET progress = ?, heartbeat_at = ?
                WHERE task_id = ? AND status = ? AND cancel_requested = 0
                """,
                (
                    max(0.0, min(99.9, float(progress))),
                    int(time.time()),
                    task_id,
                    BacktestTaskStatus.RUNNING,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1

    def is_cancel_requested(self, task_id: str) -> bool:
        row = self.storage.fetchone(
            "SELECT status, cancel_requested FROM backtest_tasks WHERE task_id = ?",
            (task_id,),
        )
        return row is None or row["status"] != BacktestTaskStatus.RUNNING or bool(
            row["cancel_requested"]
        )

    def record_llm_analysis(self, task_id: str, cache_hit: bool) -> None:
        """Record live LLM usage without rewriting the simulated account ledger."""
        self.storage.execute(
            """
            UPDATE backtest_tasks
            SET llm_analysis_count = llm_analysis_count + 1,
                llm_call_count = llm_call_count + ?,
                llm_cache_hits = llm_cache_hits + ?,
                heartbeat_at = ?
            WHERE task_id = ? AND status = ?
            """,
            (
                0 if cache_hit else 1,
                1 if cache_hit else 0,
                int(time.time()),
                task_id,
                BacktestTaskStatus.RUNNING,
            ),
        )

    def checkpoint(self, task_id: str, progress: float, ledger: Dict) -> bool:
        """Persist a throttled live account snapshot for the running task."""
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id FROM backtest_tasks
                WHERE task_id = ? AND status = ? AND cancel_requested = 0
                """,
                (task_id, BacktestTaskStatus.RUNNING),
            ).fetchone()
            if row is None:
                return False
            self._persist_ledger(conn, task_id, int(row["user_id"]), ledger, now)
            conn.execute(
                """
                UPDATE backtest_tasks SET progress = ?, heartbeat_at = ?
                WHERE task_id = ? AND status = ? AND cancel_requested = 0
                """,
                (
                    max(0.0, min(99.9, float(progress))), now, task_id,
                    BacktestTaskStatus.RUNNING,
                ),
            )
            conn.commit()
            return True

    def request_cancel_task(self, user_id: int, task_id: str) -> Optional[Dict]:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id, batch_id, status, cancel_requested
                FROM backtest_tasks WHERE task_id = ? AND user_id = ?
                """,
                (task_id, user_id),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == BacktestTaskStatus.QUEUED:
                conn.execute(
                    """
                    UPDATE backtest_tasks
                    SET status = ?, cancel_requested = 1, completed_at = ?,
                        heartbeat_at = ?, worker_id = ''
                    WHERE task_id = ? AND status = ?
                    """,
                    (
                        BacktestTaskStatus.CANCELED, now, now, task_id,
                        BacktestTaskStatus.QUEUED,
                    ),
                )
            elif row["status"] == BacktestTaskStatus.RUNNING:
                conn.execute(
                    "UPDATE backtest_tasks SET cancel_requested = 1 WHERE task_id = ?",
                    (task_id,),
                )
            self._refresh_batch(conn, row["batch_id"], now)
            conn.commit()
            updated = conn.execute(
                """
                SELECT task_id, batch_id, status, progress, cancel_requested
                FROM backtest_tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            return dict(updated)

    def request_cancel_batch(self, user_id: int, batch_id: str) -> Optional[Dict]:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            batch = conn.execute(
                "SELECT batch_id FROM backtest_batches WHERE batch_id = ? AND user_id = ?",
                (batch_id, user_id),
            ).fetchone()
            if batch is None:
                return None
            conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, cancel_requested = 1, completed_at = ?,
                    heartbeat_at = ?, worker_id = ''
                WHERE batch_id = ? AND status = ?
                """,
                (
                    BacktestTaskStatus.CANCELED, now, now, batch_id,
                    BacktestTaskStatus.QUEUED,
                ),
            )
            conn.execute(
                """
                UPDATE backtest_tasks SET cancel_requested = 1
                WHERE batch_id = ? AND status = ?
                """,
                (batch_id, BacktestTaskStatus.RUNNING),
            )
            self._refresh_batch(conn, batch_id, now)
            conn.commit()
            return {"batch_id": batch_id}

    def cancel(self, task_id: str, ledger: Optional[Dict] = None) -> None:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                "SELECT batch_id, user_id FROM backtest_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return
            if ledger:
                self._persist_ledger(conn, task_id, int(row["user_id"]), ledger, now)
            conn.execute(
                """
                UPDATE backtest_accounts SET status = 'canceled', updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )
            conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, cancel_requested = 1, completed_at = ?,
                    heartbeat_at = ?, worker_id = ''
                WHERE task_id = ?
                """,
                (BacktestTaskStatus.CANCELED, now, now, task_id),
            )
            self._refresh_batch(conn, row["batch_id"], now)
            conn.commit()

    def complete(self, task_id: str, result: Dict) -> None:
        now = int(time.time())
        public_result = dict(result)
        ledger = public_result.pop("_ledger", None)
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """
                SELECT batch_id, user_id, cancel_requested
                FROM backtest_tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return
            if row["cancel_requested"]:
                if ledger:
                    ledger["account"]["status"] = "canceled"
                    self._persist_ledger(
                        conn, task_id, int(row["user_id"]), ledger, now
                    )
                conn.execute(
                    """
                    UPDATE backtest_tasks
                    SET status = ?, completed_at = ?, heartbeat_at = ?, worker_id = ''
                    WHERE task_id = ?
                    """,
                    (BacktestTaskStatus.CANCELED, now, now, task_id),
                )
                self._refresh_batch(conn, row["batch_id"], now)
                conn.commit()
                return
            if ledger:
                self._persist_ledger(
                    conn, task_id, int(row["user_id"]), ledger, now
                )
            conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, progress = 100, result_json = ?, completed_at = ?,
                    heartbeat_at = ?, error_message = '', worker_id = ''
                WHERE task_id = ?
                """,
                (
                    BacktestTaskStatus.COMPLETED,
                    json.dumps(public_result, ensure_ascii=False, separators=(",", ":")),
                    now,
                    now,
                    task_id,
                ),
            )
            self._refresh_batch(conn, row["batch_id"], now)
            conn.commit()

    @staticmethod
    def _persist_ledger(conn, task_id: str, user_id: int, ledger: Dict, now: int) -> None:
        conn.execute("PRAGMA foreign_keys=ON")
        for table in (
            "backtest_trades", "backtest_positions", "backtest_orders",
            "backtest_equity_points", "backtest_replay_bars",
            "backtest_accounts",
        ):
            conn.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))

        account = ledger["account"]
        conn.execute(
            """
            INSERT INTO backtest_accounts(
                task_id, user_id, initial_balance, balance, equity,
                free_margin, margin, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id, user_id, account["initial_balance"], account["balance"],
                account["equity"], account["free_margin"], account.get("margin", 0),
                account.get("status", "completed"), now, now,
            ),
        )
        conn.executemany(
            """
            INSERT INTO backtest_orders(
                order_id, task_id, user_id, strategy_id, symbol, direction,
                status, requested_volume, filled_volume, requested_price,
                filled_price, stop_loss, take_profit, signal_source,
                contributing_sources_json, confidence, rejection_reason,
                requested_at, filled_at, canceled_at, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(
                item["order_id"], task_id, user_id, item["strategy_id"], item["symbol"],
                item["direction"], item["status"], item["requested_volume"],
                item["filled_volume"], item["requested_price"], item.get("filled_price"),
                item["stop_loss"], item["take_profit"], item["signal_source"],
                json.dumps(item["contributing_sources"], ensure_ascii=False),
                item["confidence"], item.get("rejection_reason", ""),
                item["requested_at"], item.get("filled_at"), item.get("canceled_at"),
                now, now,
            ) for item in ledger["orders"]],
        )
        conn.executemany(
            """
            INSERT INTO backtest_positions(
                position_id, task_id, order_id, user_id, symbol, direction,
                status, volume, entry_price, stop_loss, take_profit, opened_at,
                closed_at, close_price, close_reason, net_profit, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(
                item["position_id"], task_id, item["order_id"], user_id, item["symbol"],
                item["direction"], item["status"], item["volume"], item["entry_price"],
                item["stop_loss"], item["take_profit"], item["opened_at"],
                item.get("closed_at"), item.get("close_price"),
                item.get("close_reason", ""), item.get("net_profit", 0), now, now,
            ) for item in ledger["positions"]],
        )
        conn.executemany(
            """
            INSERT INTO backtest_trades(
                trade_id, task_id, order_id, position_id, user_id, symbol,
                direction, volume, entry_price, exit_price, gross_profit,
                commission, net_profit, exit_reason, opened_at, closed_at, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(
                item["trade_id"], task_id, item["order_id"], item["position_id"],
                user_id, item["symbol"], item["direction"], item["volume"],
                item["entry_price"], item["exit_price"], item["gross_profit"],
                item["commission"], item["net_profit"], item["exit_reason"],
                item["opened_at"], item["closed_at"], now,
            ) for item in ledger["trades"]],
        )
        conn.executemany(
            """
            INSERT INTO backtest_equity_points(
                task_id, point_time, user_id, balance, equity, open_positions
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            [(
                task_id, item["time"], user_id, item["balance"], item["equity"],
                item["open_positions"],
            ) for item in ledger["equity_points"]],
        )
        conn.executemany(
            """
            INSERT INTO backtest_replay_bars(
                task_id, bar_time, end_time, user_id, open, high, low, close,
                tick_volume, bar_count
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(
                task_id, item["time"], item.get("end_time", item["time"]),
                user_id, item["open"], item["high"], item["low"],
                item["close"], item.get("tick_volume", 0),
                item.get("bar_count", 1),
            ) for item in ledger.get("replay_bars", [])],
        )

    def fail(self, task_id: str, message: str) -> None:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                """
                SELECT batch_id, cancel_requested
                FROM backtest_tasks WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                return
            if row["cancel_requested"]:
                conn.execute(
                    """
                    UPDATE backtest_tasks
                    SET status = ?, completed_at = ?, heartbeat_at = ?, worker_id = ''
                    WHERE task_id = ?
                    """,
                    (BacktestTaskStatus.CANCELED, now, now, task_id),
                )
                conn.execute(
                    """
                    UPDATE backtest_accounts SET status = 'canceled', updated_at = ?
                    WHERE task_id = ?
                    """,
                    (now, task_id),
                )
                self._refresh_batch(conn, row["batch_id"], now)
                conn.commit()
                return
            conn.execute(
                """
                UPDATE backtest_tasks
                SET status = ?, error_message = ?, completed_at = ?, heartbeat_at = ?
                WHERE task_id = ?
                """,
                (BacktestTaskStatus.FAILED, str(message)[:500], now, now, task_id),
            )
            self._refresh_batch(conn, row["batch_id"], now)
            conn.commit()

    @staticmethod
    def _decode_task(task: Dict) -> Dict:
        task["dataset_snapshot"] = json.loads(task.pop("dataset_snapshot_json"))
        task["strategy_snapshot"] = json.loads(task.pop("strategy_snapshot_json"))
        task["template_snapshot"] = json.loads(task.pop("template_snapshot_json"))
        return task

    @staticmethod
    def _refresh_batch(conn, batch_id: str, now: int) -> None:
        counts = {
            row["status"]: int(row["count"])
            for row in conn.execute(
                """
                SELECT status, COUNT(*) AS count FROM backtest_tasks
                WHERE batch_id = ? GROUP BY status
                """,
                (batch_id,),
            ).fetchall()
        }
        queued = counts.get(BacktestTaskStatus.QUEUED, 0)
        running = counts.get(BacktestTaskStatus.RUNNING, 0)
        completed = counts.get(BacktestTaskStatus.COMPLETED, 0)
        failed = counts.get(BacktestTaskStatus.FAILED, 0)
        canceled = counts.get(BacktestTaskStatus.CANCELED, 0)
        if queued or running:
            status = BacktestTaskStatus.RUNNING
            completed_at = None
        else:
            status = BacktestTaskStatus.FAILED if failed else (
                BacktestTaskStatus.CANCELED
                if canceled else BacktestTaskStatus.COMPLETED
            )
            completed_at = now
        conn.execute(
            """
            UPDATE backtest_batches
            SET status = ?, completed_tasks = ?, failed_tasks = ?,
                canceled_tasks = ?, completed_at = ?
            WHERE batch_id = ?
            """,
            (status, completed, failed, canceled, completed_at, batch_id),
        )


class BacktestLLMCache:
    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def get(self, cache_key: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            "SELECT result_json FROM backtest_llm_cache WHERE cache_key = ?",
            (cache_key,),
        )
        return json.loads(row["result_json"]) if row else None

    def save(self, metadata: Dict, result: Dict) -> None:
        self.storage.execute(
            """
            INSERT OR IGNORE INTO backtest_llm_cache(
                cache_key, user_id, dataset_hash, strategy_hash,
                analysis_time, model, prompt_hash, result_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["cache_key"],
                metadata["user_id"],
                metadata["dataset_hash"],
                metadata["strategy_hash"],
                metadata["analysis_time"],
                metadata["model"],
                metadata["prompt_hash"],
                json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                int(time.time()),
            ),
        )


class HistoricalBarReader:
    @staticmethod
    def read(path: str, data_format: str = "") -> List[Dict]:
        source = Path(path)
        if not source.exists():
            raise BacktestEngineError("历史行情文件不存在")
        normalized_format = (data_format or "").lower()
        if normalized_format == "parquet" or source.suffix == ".parquet":
            try:
                import pyarrow.parquet as pq
            except ImportError as exc:
                raise BacktestEngineError("读取 Parquet 行情需要 pyarrow") from exc
            rows = pq.read_table(source).to_pylist()
        else:
            opener = gzip.open if source.suffix == ".gz" else open
            with opener(source, "rt", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
        bars = []
        for row in rows:
            bars.append({
                "time": int(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "spread": int(row.get("spread") or 0),
                "tick_volume": int(row.get("tick_volume") or 0),
            })
        return sorted(bars, key=lambda item: item["time"])


def aggregate_period(m1_bars: List[Dict], period: str, limit: int) -> List[Dict]:
    """Build bars from data already seen; the final bucket may be partial."""
    seconds = PERIOD_SECONDS[period]
    aggregated: List[Dict] = []
    for bar in m1_bars:
        bucket = int(bar["time"]) // seconds * seconds
        if not aggregated or aggregated[-1]["bucket"] != bucket:
            aggregated.append({
                "bucket": bucket,
                "timestamp": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(),
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "tick_volume": int(bar.get("tick_volume", 0)),
                "spread": int(bar.get("spread", 0)),
            })
        else:
            current = aggregated[-1]
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
            current["tick_volume"] += int(bar.get("tick_volume", 0))
            current["spread"] = int(bar.get("spread", current["spread"]))
    result = aggregated[-limit:]
    return [{key: value for key, value in item.items() if key != "bucket"} for item in result]


class ReplayPivotProvider:
    """Incrementally exposes pivots only after right-side bars confirm them."""

    # Keep replay confirmation identical to the live PivotService.
    STRENGTH = {"M1": 6, "M5": 4, "M15": 3, "H1": 3, "H4": 3}

    def __init__(self):
        self._pivots: Dict[Tuple[str, str, str], Dict] = {}

    def update(self, seen: List[Dict], simulated_time: int) -> List[Dict]:
        for period, seconds in PERIOD_SECONDS.items():
            strength = self.STRENGTH[period]
            bars = aggregate_period(seen, period, 2 * strength + 12)
            closed = [
                bar for bar in bars
                if int(datetime.fromisoformat(bar["timestamp"]).timestamp()) + seconds
                <= simulated_time
            ]
            if len(closed) < 2 * strength + 1:
                continue
            index = len(closed) - strength - 1
            candidate = closed[index]
            left = closed[index - strength:index]
            right = closed[index + 1:index + strength + 1]
            checks = (
                ("high", candidate["high"], all(
                    item["high"] < candidate["high"] for item in left + right
                )),
                ("low", candidate["low"], all(
                    item["low"] > candidate["low"] for item in left + right
                )),
            )
            for direction, price, confirmed in checks:
                if confirmed:
                    key = (period, candidate["timestamp"], direction)
                    self._pivots[key] = {
                        "period": period, "timestamp": candidate["timestamp"],
                        "confirmed_at": simulated_time, "direction": direction,
                        "price": float(price), "strength": strength,
                    }
        by_period: Dict[str, List[Dict]] = {}
        for pivot in self._pivots.values():
            by_period.setdefault(pivot["period"], []).append(pivot)
        result = []
        for items in by_period.values():
            result.extend(sorted(items, key=lambda item: item["confirmed_at"])[-10:])
        return result


class CachedLLMProvider:
    """Calls the user's approved LLM config and caches immutable responses."""

    def __init__(self, storage: Optional[SQLiteStorage] = None, retries: int = 2):
        self.storage = storage or get_storage()
        self.cache = BacktestLLMCache(self.storage)
        self.retries = max(1, int(retries))
        self._services: Dict[int, LLMService] = {}

    def analyze(
        self,
        *,
        user_id: int,
        symbol: str,
        analysis_time: int,
        klines: Dict[str, List[Dict]],
        strategy: Dict,
        dataset_hash: str,
        strategy_hash: str,
        signal_source_ids: Optional[List[str]] = None,
    ) -> Tuple[Dict, bool]:
        service = self._services.get(user_id)
        if service is None:
            service = LLMService(LLMStore(user_id=user_id), None)
            self._services[user_id] = service
        config = service.llm_store.get_config()
        if not config.enabled:
            raise BacktestEngineError("当前用户未开通或未配置大模型分析")

        plan = {symbol: build_analysis_plan(strategy, signal_source_ids)}
        groups = service._group_analysis_plans(plan)
        combined_response: Dict = {}
        all_cache_hits = True
        for group in groups:
            group_plan = group["plan"]
            prompt = service.build_analysis_prompt(
                {symbol: klines},
                group_plan,
                analysis_prompt_template=group["analysis_prompt_template"],
                reference_context=service._shared_reference_context(
                    group["reference_runtime_ids"]
                ),
            )
            prompt_hash = service.prompt_hash(prompt, group["system_prompt"])
            cache_key = hashlib.sha256(
                "|".join((
                    str(user_id), dataset_hash, strategy_hash, str(analysis_time),
                    group["model"], prompt_hash,
                )).encode("utf-8")
            ).hexdigest()
            cached = self.cache.get(cache_key)
            if cached is not None:
                group_response = {symbol: cached}
            else:
                all_cache_hits = False
                group_response = None
                for _ in range(self.retries):
                    group_response = service.call_llm(
                        prompt,
                        model=group["model"],
                        system_prompt=group["system_prompt"],
                        object_type="backtest_replay",
                        object_id=cache_key,
                    )
                    if group_response:
                        break
                if not group_response:
                    raise BacktestEngineError("大模型分析连续失败，回测已暂停")
                group_response = service._normalize_analysis_response(
                    group_response, group_plan
                )
                group_analysis = group_response.get(symbol, group_response)
                self.cache.save(
                    {
                        "cache_key": cache_key,
                        "user_id": user_id,
                        "dataset_hash": dataset_hash,
                        "strategy_hash": strategy_hash,
                        "analysis_time": analysis_time,
                        "model": group["model"],
                        "prompt_hash": prompt_hash,
                    },
                    group_analysis,
                )
            service._merge_analysis_results(combined_response, group_response)

        analysis = combined_response.get(symbol, combined_response)
        if not isinstance(analysis, dict):
            raise BacktestEngineError("大模型返回的分析结果格式无效")
        return analysis, all_cache_hits


def build_analysis_plan(
    strategy: Dict, signal_source_ids: Optional[List[str]] = None,
) -> Dict:
    periods = {}
    profiles = []
    allowed = set(signal_source_ids) if signal_source_ids is not None else None
    model = TradingStrategy.from_dict(strategy)
    for source in model.get_signal_sources("ai_entry", enabled_only=True):
        if (source.get("params") or {}).get(
            "analysis_mode", "self_analysis"
        ) == "shared_reference":
            continue
        source_id = source["signal_source_id"]
        if allowed is not None and source_id not in allowed:
            continue
        period = source["period"]
        params = source.get("params") or {}
        current = periods.setdefault(period, {"weight": 0, "kline_count": 0})
        current["weight"] = max(current["weight"], int(source["weight"]))
        current["kline_count"] = max(
            current["kline_count"], int(params.get("kline_count", 100))
        )
        profiles.append({
            "strategy_id": strategy.get("strategy_id", ""),
            "strategy_name": strategy.get("strategy_name", ""),
            "signal_source_id": source_id,
            "periods": {period: int(source["weight"])},
            "min_confidence": int(params.get(
                "min_confidence", strategy.get("min_confidence", 50)
            )),
            "min_risk_reward": float(strategy.get("min_risk_reward", 1)),
            "analysis_interval_minutes": int(
                params.get("analysis_interval_minutes", 5)
            ),
            "kline_count": int(params.get("kline_count", 100)),
            "model": str(params.get("model") or ""),
            "system_prompt": str(params.get("system_prompt") or ""),
            "analysis_prompt_template": str(
                params.get("analysis_prompt_template") or ""
            ),
            "share_runtime_data": bool(params.get("share_runtime_data", False)),
            "reference_runtime_ids": list(
                params.get("reference_runtime_ids") or []
            ),
            "signal_params": dict(params),
            "symbol": strategy.get("symbol", ""),
            "strategy_lifecycle": strategy.get("lifecycle_status", "draft"),
        })
    return {
        "periods": periods,
        "strategies": profiles,
    }


class ReplaySignalEngine:
    """Generates all enabled signals using replay time instead of wall time."""

    SIGNAL_TTL = 300

    def __init__(self, strategy: Dict):
        self.strategy = strategy
        self.signal_sources = TradingStrategy.from_dict(strategy).signal_sources
        self._cooldowns: Dict[str, int] = {}
        self._consumed_ai_recommendations = set()
        self._pending_ma_crosses: Dict[str, Dict] = {}
        self._alpha_executor = AlphaRuntimeExecutor()
        self._alpha_library = None

    def _alpha_definition(self, params: Dict) -> Dict:
        if params.get("alpha_snapshot"):
            return params.get("alpha_snapshot") or {}
        if self._alpha_library is None:
            from alpha_research import AlphaLibraryRepository
            self._alpha_library = AlphaLibraryRepository()
        alpha = self._alpha_library.get_runtime_definition(
            params.get("alpha_id", ""), int(params.get("alpha_owner_user_id") or 0)
        )
        return (alpha or {}).get("definition") or {}

    def generate(
        self,
        seen_bars: List[Dict],
        current_price: float,
        simulated_time: int,
        llm_analysis: Optional[Dict],
    ) -> List[TradingSignal]:
        signals = []
        for config in self.signal_sources:
            if not config.get("enabled", True) or int(config.get("weight", 0)) <= 0:
                continue
            if config["source"] == "key_level":
                signal = self._key_level_signal(
                    config, current_price, simulated_time
                )
                if signal:
                    signals.append(signal)
            elif config["source"] == "ai_entry":
                signals.extend(self._ai_signals(
                    config, llm_analysis or {}, current_price, simulated_time
                ))
            elif config["source"] == "moving_average":
                signal = self._moving_average_signal(
                    config, seen_bars, current_price, simulated_time
                )
                if signal:
                    signals.append(signal)
            elif config["source"] == "alpha_factor":
                signal = self._alpha_factor_signal(
                    config, seen_bars, current_price, simulated_time
                )
                if signal:
                    signals.append(signal)
        return signals

    def _key_level_signal(
        self, config, current_price: float, simulated_time: int
    ) -> Optional[TradingSignal]:
        params = config.get("params") or {}
        mode = params.get("level_mode", "automatic")
        levels = None
        if mode == "levels":
            levels = [float(item) for item in params.get("levels", [])]
        elif mode == "expression":
            levels = evaluate_key_level_expression(
                params.get("expression", ""), current_price
            )
        signal = build_key_level_state_signal(
            self.strategy.get("symbol", ""),
            current_price,
            levels=levels,
            signal_time=replay_datetime(simulated_time),
            threshold=float(params.get("proximity_threshold", 0.0008)),
        )
        cooldown_key = (
            f"key_level:{config['signal_source_id']}:"
            f"{float(signal.key_level or 0):.8f}"
        )
        cooldown = max(0, int(params.get("cooldown_seconds", 180)))
        if signal.is_entry_trigger:
            if not self._can_emit(cooldown_key, simulated_time, cooldown):
                signal.is_entry_trigger = False
            else:
                self._mark_emitted(cooldown_key, simulated_time)
        signal.source_period = config["period"]
        signal.signal_source_id = config["signal_source_id"]
        return signal

    def _ai_signals(self, config, analysis, current_price, simulated_time) -> List[Dict]:
        trigger = None
        for suggestion in analysis.get("trade_suggestions", []) or []:
            period = str(suggestion.get("period", "")).upper()
            entry = float(suggestion.get("entry_price") or 0)
            if period != config["period"] or suggestion.get(
                "signal_source_id"
            ) not in {None, "", config["signal_source_id"]}:
                continue
            params = config.get("params") or {}
            if int(suggestion.get("confidence", 0)) < int(
                params.get("min_confidence", self.strategy.get("min_confidence", 50))
            ):
                continue
            direction = str(suggestion.get("direction", "")).lower()
            analysis_id = str(suggestion.get("_analysis_id") or "legacy")
            recommendation_key = (
                analysis_id,
                config["signal_source_id"],
                period,
                direction,
                round(entry, 8),
            )
            if recommendation_key in self._consumed_ai_recommendations:
                continue
            candidate = build_ai_entry_signal(
                self.strategy.get("symbol", ""),
                current_price,
                suggestion,
                replay_datetime(simulated_time),
                threshold=float(params.get("entry_threshold", 0.0008)),
            )
            if not candidate:
                continue
            self._consumed_ai_recommendations.add(recommendation_key)
            if trigger is None or candidate.confidence > trigger.confidence:
                trigger = candidate
        state = extract_ai_trend_state(analysis, config["period"])
        if trigger is not None and not state["ready"]:
            state = {
                "ready": True,
                "direction": "up" if trigger.action == "buy" else "down",
                "confidence": trigger.confidence,
                "reason": trigger.trigger_reason,
            }
        if trigger is None:
            trigger = TradingSignal(
                symbol=self.strategy.get("symbol", ""),
                action=direction_action(state["direction"]),
                market_direction=state["direction"],
                state_ready=state["ready"],
                is_entry_trigger=False,
                confidence=state["confidence"],
                source=SignalSource.AI_ENTRY,
                source_period=config["period"],
                trigger_price=current_price,
                trigger_time=replay_datetime(simulated_time),
                trigger_reason=state["reason"],
                suggested_entry=current_price,
                created_at=replay_datetime(simulated_time),
            )
        else:
            trigger_direction = trigger.action
            trigger.market_direction = state["direction"]
            trigger.action = direction_action(state["direction"])
            trigger.state_ready = state["ready"]
            trigger.confidence = state["confidence"]
            trigger.is_entry_trigger = (
                state["direction"] in {"up", "down"}
                and trigger_direction == direction_action(state["direction"])
            )
            trigger.trigger_reason = state["reason"] or trigger.trigger_reason
        trigger.signal_source_id = config["signal_source_id"]
        return [trigger]

    def _moving_average_signal(
        self, config, seen_bars, current_price: float, simulated_time: int,
    ) -> Optional[TradingSignal]:
        period = config["period"]
        if simulated_time % PERIOD_SECONDS[period] != 0:
            return None
        params = config.get("params") or {}
        fast_period = int(params.get("fast_period", 5))
        slow_period = int(params.get("slow_period", 20))
        ma_type = str(params.get("ma_type", "sma")).lower()
        min_confidence = max(0, min(100, int(params.get("min_confidence", 70))))
        period_bars = aggregate_period(seen_bars, period, slow_period + 2)
        state = evaluate_moving_average_state(
            [float(bar["close"]) for bar in period_bars],
            fast_period, slow_period, ma_type,
        )
        source_id = config["signal_source_id"]
        cooldown = max(0, int(params.get("cooldown_seconds", 180)))
        cooldown_key = f"moving_average:{source_id}"
        intent_key = f"moving_average:{source_id}"
        cross = state.get("cross")
        if cross in {"buy", "sell"}:
            self._pending_ma_crosses[intent_key] = {
                "direction": cross,
                "created_at": simulated_time,
            }
        pending = self._pending_ma_crosses.get(intent_key) or {}
        pending_direction = pending.get("direction")
        qualified = (
            pending_direction in {"buy", "sell"}
            and state.get("direction") == (
                "up" if pending_direction == "buy" else "down"
            )
            and int(state.get("confidence") or 0) >= min_confidence
        )
        trigger = qualified and self._can_emit(
            cooldown_key, simulated_time, cooldown
        )
        signal = build_moving_average_state_signal(
            symbol=self.strategy.get("symbol", ""),
            current_price=current_price,
            period=period,
            state=state,
            fast_period=fast_period,
            slow_period=slow_period,
            ma_type=ma_type,
            is_entry_trigger=trigger,
            signal_time=replay_datetime(simulated_time),
        )
        if signal:
            signal.signal_source_id = source_id
            if trigger:
                self._mark_emitted(cooldown_key, simulated_time)
                self._pending_ma_crosses.pop(intent_key, None)
        return signal

    def _can_emit(self, key: str, now: int, cooldown: int) -> bool:
        return now - self._cooldowns.get(key, -cooldown) >= cooldown

    def _mark_emitted(self, key: str, now: int) -> None:
        self._cooldowns[key] = now

    def _alpha_factor_signal(
        self, config, seen_bars, current_price: float, simulated_time: int,
    ) -> Optional[TradingSignal]:
        period = config["period"]
        if simulated_time % PERIOD_SECONDS[period] != 0:
            return None
        params = config.get("params") or {}
        definition = self._alpha_definition(params)
        bars = aggregate_period(seen_bars, period, 2000)
        state = self._alpha_executor.evaluate(bars, definition)
        state["is_entry_trigger"] = bool(
            state["is_entry_trigger"]
            and state["confidence"] >= int(params.get("min_confidence", 60))
        )
        cooldown_key = f"alpha_factor:{config['signal_source_id']}"
        cooldown = max(0, int(params.get("cooldown_seconds", 180)))
        if state["is_entry_trigger"]:
            if not self._can_emit(cooldown_key, simulated_time, cooldown):
                state["is_entry_trigger"] = False
            else:
                self._mark_emitted(cooldown_key, simulated_time)
        signal = self._alpha_executor.build_signal(
            self.strategy.get("symbol", ""), period, current_price, state,
            replay_datetime(simulated_time),
        )
        signal.signal_source_id = config["signal_source_id"]
        return signal


def replay_datetime(simulated_time: int) -> datetime:
    return datetime.fromtimestamp(int(simulated_time), timezone.utc).replace(
        tzinfo=None
    )


def detect_confirmed_pivots(
    bars: List[Dict], strength: int, merge_distance: float = 0.0004,
) -> List[Dict]:
    """A pivot appears only after `strength` right-hand bars have closed."""
    if len(bars) < strength * 2 + 1:
        return []
    pivots = []
    for index in range(strength, len(bars) - strength):
        current = bars[index]
        left = bars[index - strength:index]
        right = bars[index + 1:index + strength + 1]
        if all(item["high"] < current["high"] for item in left + right):
            pivots.append({
                "time": current["timestamp"],
                "price": float(current["high"]),
                "direction": "high",
            })
        if all(item["low"] > current["low"] for item in left + right):
            pivots.append({
                "time": current["timestamp"],
                "price": float(current["low"]),
                "direction": "low",
            })
    return merge_nearby_pivots(pivots, merge_distance)


def merge_nearby_pivots(
    pivots: List[Dict], merge_distance: float = 0.0004,
) -> List[Dict]:
    merged = []
    for direction in ("high", "low"):
        items = [item for item in pivots if item["direction"] == direction]
        index = 0
        while index < len(items):
            group = [items[index]]
            cursor = index + 1
            while cursor < len(items):
                if (
                    abs(items[cursor]["price"] - items[index]["price"])
                    / items[index]["price"] > merge_distance
                ):
                    break
                group.append(items[cursor])
                cursor += 1
            merged.append(
                max(group, key=lambda item: item["price"])
                if direction == "high"
                else min(group, key=lambda item: item["price"])
            )
            index = cursor
    return merged


def signal_source_enabled(strategy: Dict, source: str) -> bool:
    return bool(
        TradingStrategy.from_dict(strategy).get_signal_sources(
            source, enabled_only=True
        )
    )


def signal_period_enabled(strategy: Dict, source: str, period: str) -> bool:
    return any(
        item.get("period") == period
        for item in TradingStrategy.from_dict(strategy).get_signal_sources(
            source, enabled_only=True
        )
    )


def signal_weight(strategy: Dict, signal: Dict) -> int:
    return TradingStrategy.from_dict(strategy).get_signal_weight(
        signal["source"], signal.get("period"), signal.get("signal_source_id", "")
    )


def combine_signals(signals: List[Dict], strategy: Dict) -> Optional[Dict]:
    strategy_model = TradingStrategy.from_dict(strategy)
    normalized = [
        signal if isinstance(signal, TradingSignal) else TradingSignal(
            symbol=strategy_model.symbol,
            action=signal.get("direction", signal.get("action", "")),
            confidence=int(signal.get("confidence", 0)),
            market_direction=signal.get("market_direction", ""),
            state_ready=bool(signal.get("state_ready", True)),
            is_entry_trigger=bool(signal.get("is_entry_trigger", True)),
            source=signal.get("source", ""),
            source_period=signal.get("period", signal.get("source_period", "")),
            signal_source_id=signal.get("signal_source_id", ""),
            trigger_price=float(signal.get("trigger_price", 0)),
            suggested_entry=float(signal.get("trigger_price", 0)),
            suggested_sl=float(signal.get("stop_loss", signal.get("suggested_sl", 0))),
            suggested_tp=float(signal.get("take_profit", signal.get("suggested_tp", 0))),
        )
        for signal in signals
    ]
    service = StrategyService(
        strategy_store=object(), signal_service=object(), risk_manager=object()
    )
    analysis = service.analyze_signals(
        strategy_model.symbol, normalized, strategy_model
    )
    direction = analysis["action"]
    if direction == "none":
        return None
    enabled = [
        signal for signal in normalized
        if strategy_model.is_signal_enabled(
            signal.source,
            signal.source_period if signal.source != "key_level" else None,
            signal.signal_source_id,
        )
    ]
    best = service._select_best_signal(enabled, direction, strategy_model)
    if best is None:
        return None
    directional = [signal for signal in enabled if signal.action == direction]
    return {
        "direction": direction,
        "confidence": round(
            analysis["buy_confidence"]
            if direction == "buy" else analysis["sell_confidence"]
        ),
        "period": best.source_period,
        "source": best.source,
        "signal_source_id": best.signal_source_id,
        "contributing_sources": sorted({item.source for item in directional}),
        "stop_loss": best.suggested_sl,
        "take_profit": best.suggested_tp,
    }


@dataclass
class SimOrder:
    order_id: str
    strategy_id: str
    symbol: str
    direction: str
    requested_volume: float
    requested_price: float
    stop_loss: float
    take_profit: float
    requested_at: int
    confidence: int
    period: str
    signal_source: str
    contributing_sources: List[str]
    signal_source_id: str = ""
    exit_mode: str = "fixed_rr"
    trailing_activation_r: float = 1.0
    trailing_distance_r: float = 1.0
    status: str = "pending"
    filled_volume: float = 0.0
    filled_price: Optional[float] = None
    filled_at: Optional[int] = None
    canceled_at: Optional[int] = None
    rejection_reason: str = ""
    position_policy_snapshot: Dict = None

    def reject(self, reason: str) -> None:
        self.status = "rejected"
        self.rejection_reason = str(reason)

    def to_record(self) -> Dict:
        return {
            "order_id": self.order_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "status": self.status,
            "requested_volume": self.requested_volume,
            "filled_volume": self.filled_volume,
            "requested_price": self.requested_price,
            "filled_price": self.filled_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "signal_source": self.signal_source,
            "contributing_sources": self.contributing_sources,
            "signal_source_id": self.signal_source_id,
            "exit_mode": self.exit_mode,
            "confidence": self.confidence,
            "rejection_reason": self.rejection_reason,
            "requested_at": self.requested_at,
            "filled_at": self.filled_at,
            "canceled_at": self.canceled_at,
        }


@dataclass
class SimPosition:
    position_id: str
    order_id: str
    symbol: str
    direction: str
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    opened_at: int
    confidence: int
    period: str
    signal_source: str
    contributing_sources: List[str]
    open_commission: float
    signal_source_id: str = ""
    exit_mode: str = "fixed_rr"
    trailing_activation_r: float = 1.0
    trailing_distance_r: float = 1.0
    initial_risk: float = 0.0
    favorable_price: float = 0.0
    remaining_volume: float = 0.0
    partial_levels_done: List[str] = None
    status: str = "open"
    closed_at: Optional[int] = None
    close_price: Optional[float] = None
    close_reason: str = ""
    net_profit: float = 0.0
    position_policy_snapshot: Dict = None
    holding_bars: int = 0

    def to_record(self) -> Dict:
        return {
            "position_id": self.position_id,
            "order_id": self.order_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "status": self.status,
            "volume": self.volume,
            "remaining_volume": self.remaining_volume or self.volume,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "close_price": self.close_price,
            "close_reason": self.close_reason,
            "net_profit": self.net_profit,
            "signal_source_id": self.signal_source_id,
            "exit_mode": self.exit_mode,
            "partial_levels_done": self.partial_levels_done or [],
        }


class M1BacktestEngine:
    """Bar-close decisions, next-bar-open fills, conservative intrabar exits."""

    def __init__(
        self,
        llm_provider=None,
        progress_callback: Optional[Callable[[float], None]] = None,
        checkpoint_callback: Optional[Callable[[float, Dict], None]] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
        llm_usage_callback: Optional[Callable[[bool], None]] = None,
    ):
        self.llm_provider = llm_provider or CachedLLMProvider()
        self.progress_callback = progress_callback or (lambda progress: None)
        self.checkpoint_callback = checkpoint_callback or (
            lambda progress, ledger: None
        )
        self.cancel_callback = cancel_callback or (lambda: False)
        self.llm_usage_callback = llm_usage_callback or (lambda cache_hit: None)

    def run(self, task: Dict) -> Dict:
        dataset = task["dataset_snapshot"]
        strategy = task["strategy_snapshot"]
        template = task["template_snapshot"]
        bars = HistoricalBarReader.read(
            task["dataset_file_path"], dataset.get("data_format", "")
        )
        start = int(dataset["requested_start"])
        end = int(dataset["requested_end"])
        bars = [bar for bar in bars if int(bar["time"]) <= end]
        test_bars = [bar for bar in bars if int(bar["time"]) >= start]
        if len(test_bars) < 2:
            raise BacktestEngineError("测试区至少需要两根 M1 K线")

        initial = float(template.get("initial_capital", 100000))
        balance = initial
        pending_orders: List[SimOrder] = []
        positions: List[SimPosition] = []
        all_positions: List[SimPosition] = []
        orders: List[SimOrder] = []
        trades: List[Dict] = []
        equity_curve: List[Dict] = []
        equity_points: List[Dict] = []
        replay_bars: List[Dict] = []
        seen: List[Dict] = []
        llm_analyses = 0
        llm_cache_hits = 0
        point_size, contract_size = market_spec(dataset.get("symbol", ""), bars)
        strategy_model = TradingStrategy.from_dict(strategy)
        policy_snapshot = strategy.get("position_management_policy_snapshot")
        if not policy_snapshot:
            raise BacktestEngineError("回测任务缺少持仓管理方案快照")
        position_policy = PositionManagementPolicy.from_dict(policy_snapshot)
        position_manager = PositionManager()
        pivot_provider = ReplayPivotProvider()
        available_pivots: List[Dict] = []
        ai_sources = strategy_model.get_signal_sources(
            "ai_entry", enabled_only=True
        )
        ai_sources = [
            source for source in ai_sources
            if (source.get("params") or {}).get(
                "analysis_mode", "self_analysis"
            ) != "shared_reference"
        ]
        next_ai_analysis_at = {
            source["signal_source_id"]: start + max(
                1,
                int((source.get("params") or {}).get(
                    "analysis_interval_minutes", 5
                )),
            ) * 60
            for source in ai_sources
        }
        ai_enabled = bool(ai_sources)
        signal_engine = ReplaySignalEngine(strategy)
        decision_service = StrategyService(
            strategy_store=object(), signal_service=object(), risk_manager=object()
        )
        active_signals: List[TradingSignal] = []
        latest_llm_analysis: Optional[Dict] = None
        test_index = 0
        max_concurrent_positions = 0
        last_checkpoint_progress = -1.0
        last_checkpoint_at = 0.0

        self.checkpoint_callback(
            0.0,
            self._live_ledger(
                initial, balance, balance, orders, all_positions, trades,
                equity_points, replay_bars,
            ),
        )

        for index, bar in enumerate(bars):
            if self.cancel_callback():
                raise BacktestCanceled("回测任务已由用户停止")
            timestamp = int(bar["time"])
            if timestamp >= start:
                test_index += 1
                replay_bars.append(bar)
                for order in pending_orders:
                    reason = position_limit_reason(
                        order.direction, positions, strategy, template
                    )
                    if reason:
                        order.reject(reason)
                        continue
                    position, balance = self._fill_order(
                        order, bar, balance, template, point_size
                    )
                    if position:
                        positions.append(position)
                        all_positions.append(position)
                pending_orders = []
                max_concurrent_positions = max(
                    max_concurrent_positions, len(positions)
                )

                remaining_positions = []
                for position in positions:
                    closed = self._maybe_close(
                        position, bar, balance, template, point_size, contract_size
                    )
                    if closed is not None:
                        trade, balance = closed
                        trades.append(trade)
                    else:
                        remaining_positions.append(position)
                positions = remaining_positions

            seen.append(bar)
            if timestamp >= start:
                simulated_time = timestamp + 60
                available_pivots = pivot_provider.update(seen, simulated_time)
                due_ai_sources = [
                    source for source in ai_sources
                    if simulated_time >= next_ai_analysis_at[
                        source["signal_source_id"]
                    ]
                ]
                if ai_enabled and due_ai_sources:
                    if self.cancel_callback():
                        raise BacktestCanceled("回测任务已由用户停止")
                    ai_progress = test_index / len(test_bars) * 100
                    # LLM calls dominate AI replay time, so keep the task heartbeat
                    # fresh before and after every external request.
                    self.progress_callback(ai_progress)
                    due_source_ids = [
                        source["signal_source_id"] for source in due_ai_sources
                    ]
                    due_plan = build_analysis_plan(strategy, due_source_ids)
                    klines = {
                        period: aggregate_period(
                            seen,
                            period,
                            max(
                                PERIOD_LIMITS[period],
                                int(config.get("kline_count", PERIOD_LIMITS[period])),
                            ),
                        )
                        for period, config in due_plan["periods"].items()
                    }
                    analysis, cache_hit = self.llm_provider.analyze(
                        user_id=int(task["user_id"]),
                        symbol=dataset["symbol"],
                        analysis_time=timestamp + 60,
                        klines=klines,
                        strategy=strategy,
                        dataset_hash=dataset.get("data_hash", ""),
                        strategy_hash=task["strategy_snapshot_hash"],
                        signal_source_ids=due_source_ids,
                    )
                    self.progress_callback(ai_progress)
                    analysis = dict(analysis)
                    analysis_id = hashlib.sha256(
                        f"{task['task_id']}:{timestamp + 60}:{','.join(sorted(due_source_ids))}".encode(
                            "utf-8"
                        )
                    ).hexdigest()[:16]
                    analysis["trade_suggestions"] = [
                        {**suggestion, "_analysis_id": analysis_id}
                        for suggestion in analysis.get("trade_suggestions", [])
                        if isinstance(suggestion, dict)
                    ]
                    llm_analyses += 1
                    llm_cache_hits += int(cache_hit)
                    self.llm_usage_callback(bool(cache_hit))
                    retained = []
                    if latest_llm_analysis:
                        retained = [
                            item for item in latest_llm_analysis.get(
                                "trade_suggestions", []
                            )
                            if item.get("signal_source_id") not in due_source_ids
                        ]
                    latest_llm_analysis = dict(analysis)
                    latest_llm_analysis["trade_suggestions"] = (
                        retained + analysis.get("trade_suggestions", [])
                    )
                    for source in due_ai_sources:
                        interval = max(1, int(
                            (source.get("params") or {}).get(
                                "analysis_interval_minutes", 5
                            )
                        ))
                        next_ai_analysis_at[source["signal_source_id"]] = (
                            simulated_time + interval * 60
                        )

                generated_signals = signal_engine.generate(
                    seen,
                    float(bar["close"]),
                    timestamp + 60,
                    latest_llm_analysis,
                )
                reverse_directions = {
                    signal.action for signal in generated_signals
                    if signal.is_entry_trigger and signal.action in {"buy", "sell"}
                }
                remaining_positions = []
                for position in positions:
                    position.holding_bars += 1
                    position.favorable_price = (
                        max(position.favorable_price, float(bar["high"]))
                        if position.direction == "buy"
                        else min(position.favorable_price, float(bar["low"]))
                    )
                    action = position_manager.evaluate(
                        position.position_policy_snapshot["config"],
                        position.__dict__,
                        {"price": float(bar["close"]), "time": simulated_time},
                        pivots=available_pivots,
                        reverse_signal=(
                            bool(reverse_directions)
                            and position.direction not in reverse_directions
                        ),
                    )
                    if action.action == "close":
                        trade, balance = self._close_at_market(
                            position, bar, balance, template, point_size,
                            contract_size, action.reason,
                        )
                        trades.append(trade)
                    else:
                        if action.action == "partial_close" and action.close_volume:
                            trade, balance = self._close_at_price(
                                position, bar, balance, template, point_size,
                                contract_size, float(bar["close"]),
                                "partial_take_profit",
                                close_volume=action.close_volume,
                            )
                            trades.append(trade)
                            position.partial_levels_done = (
                                position.partial_levels_done or []
                            )
                            if action.level_id not in position.partial_levels_done:
                                position.partial_levels_done.append(action.level_id)
                            if action.stop_loss:
                                position.stop_loss = float(action.stop_loss)
                        if action.action == "modify_sl" and action.stop_loss:
                            position.stop_loss = float(action.stop_loss)
                        if position.status == "open":
                            remaining_positions.append(position)
                positions = remaining_positions
                decision_time = datetime.fromtimestamp(
                    timestamp + 60, timezone.utc
                ).replace(tzinfo=None)
                active_signals = [
                    signal for signal in active_signals
                    if signal.expires_at >= decision_time
                ]
                generated_keys = {
                    signal.signal_source_id or (
                        f"{signal.source}:{signal.source_period}"
                    )
                    for signal in generated_signals
                }
                active_signals = [
                    signal for signal in active_signals
                    if (
                        signal.signal_source_id
                        or f"{signal.source}:{signal.source_period}"
                    ) not in generated_keys
                ]
                active_signals.extend(generated_signals)
                decision = decision_service.make_decision(
                    symbol=dataset["symbol"],
                    current_price=float(bar["close"]),
                    force_signals=active_signals,
                    strategy=strategy_model,
                    execution_mode="backtest",
                    cooldown_scope=f"backtest:{task['task_id']}",
                    decision_time=decision_time,
                    volume_calculator=lambda _symbol, risk, _strategy: (
                        calculate_volume_from_risk(
                            balance, risk, strategy, template, contract_size
                        )
                    ),
                    position_checker=lambda _symbol, _strategy, action: (
                        backtest_position_check(
                            action, positions, strategy, template
                        )
                    ),
                    risk_checker=lambda *_args: {"allowed": True, "warnings": []},
                    position_policy=position_policy,
                    position_context={
                        "pivots": available_pivots, "time": simulated_time,
                    },
                )
                if decision:
                    order = self._create_order(
                        decision=decision,
                        symbol=dataset["symbol"],
                        requested_at=timestamp + 60,
                        strategy=strategy,
                    )
                    orders.append(order)
                    if order.status == "pending":
                        pending_orders.append(order)

                equity = balance + unrealized_positions(
                    positions, bar["close"], contract_size
                )
                equity_point = {
                    "time": timestamp,
                    "balance": round(balance, 2),
                    "equity": round(equity, 2),
                    "open_positions": len(positions),
                }
                equity_points.append(equity_point)
                equity_curve.append({
                    "time": timestamp,
                    "equity": equity_point["equity"],
                })

                progress = test_index / len(test_bars) * 100
                if test_index % 50 == 0 or index == len(bars) - 1:
                    self.progress_callback(progress)
                    now_monotonic = time.monotonic()
                    if (
                        progress - last_checkpoint_progress >= 1.0
                        or now_monotonic - last_checkpoint_at >= 2.0
                    ):
                        self.checkpoint_callback(
                            progress,
                            self._live_ledger(
                                initial, balance, equity, orders, all_positions,
                                trades, equity_points, replay_bars,
                            ),
                        )
                        last_checkpoint_progress = progress
                        last_checkpoint_at = now_monotonic

        if self.cancel_callback():
            raise BacktestCanceled("回测任务已由用户停止")
        ending_time = int(test_bars[-1]["time"]) + 60
        for order in pending_orders:
            order.status = "canceled"
            order.canceled_at = ending_time
            order.rejection_reason = "回测结束前没有下一根 K线可供成交"
        for position in positions:
            trade, balance = self._close_at_market(
                position, test_bars[-1], balance, template, point_size,
                contract_size, "end_of_test",
            )
            trades.append(trade)
        equity_curve.append({"time": ending_time, "equity": round(balance, 2)})
        equity_points.append({
            "time": ending_time,
            "balance": round(balance, 2),
            "equity": round(balance, 2),
            "open_positions": 0,
        })

        result = build_result(
            initial, balance, trades, equity_curve, llm_analyses,
            llm_cache_hits, point_size, contract_size, strategy,
        )
        order_status_counts = {}
        for order in orders:
            order_status_counts[order.status] = (
                order_status_counts.get(order.status, 0) + 1
            )
        result.update({
            "order_count": len(orders),
            "order_status_counts": order_status_counts,
            "max_concurrent_positions": max_concurrent_positions,
        })
        result["_ledger"] = {
            "account": {
                "initial_balance": round(initial, 2),
                "balance": round(balance, 2),
                "equity": round(balance, 2),
                "free_margin": round(balance, 2),
                "margin": 0,
                "status": "completed",
            },
            "orders": [order.to_record() for order in orders],
            "positions": [position.to_record() for position in all_positions],
            "trades": trades,
            "equity_points": downsample_records(equity_points, 5000),
            "replay_bars": aggregate_replay_bars(replay_bars),
        }
        return result

    @staticmethod
    def _live_ledger(
        initial: float,
        balance: float,
        equity: float,
        orders: List[SimOrder],
        positions: List[SimPosition],
        trades: List[Dict],
        equity_points: List[Dict],
        replay_bars: List[Dict],
    ) -> Dict:
        return {
            "account": {
                "initial_balance": round(initial, 2),
                "balance": round(balance, 2),
                "equity": round(equity, 2),
                "free_margin": round(equity, 2),
                "margin": 0,
                "status": "running",
            },
            "orders": [order.to_record() for order in orders],
            "positions": [position.to_record() for position in positions],
            "trades": list(trades),
            "equity_points": downsample_records(equity_points, 5000),
            "replay_bars": aggregate_replay_bars(replay_bars),
        }

    @staticmethod
    def _create_order(
        *, decision, symbol, requested_at, strategy,
    ) -> SimOrder:
        summary = decision.signal_summary or {}
        source = str(summary.get("selected_signal_source", "unknown"))
        period = str(summary.get("selected_signal_period", ""))
        source_id = str(summary.get("selected_signal_source_id", ""))
        management = summary.get("position_management") or {}
        policy_snapshot = management.get("policy_snapshot") or strategy.get(
            "position_management_policy_snapshot"
        )
        order = SimOrder(
            order_id=uuid.uuid4().hex[:16],
            strategy_id=str(strategy.get("strategy_id", "")),
            symbol=symbol,
            direction=decision.action,
            requested_volume=float(decision.volume),
            requested_price=float(decision.entry_price),
            stop_loss=float(decision.sl),
            take_profit=float(decision.tp),
            requested_at=requested_at,
            confidence=int(round(decision.confidence_score)),
            period=period,
            signal_source=source,
            contributing_sources=list(summary.get("contributing_sources", [source])),
            signal_source_id=source_id,
            exit_mode="position_manager",
            position_policy_snapshot=policy_snapshot,
        )
        if decision.status == "rejected":
            order.reject(decision.decision_reason or "共享策略风控未通过")
        return order

    @staticmethod
    def _fill_order(
        order: SimOrder, bar, balance, template, point_size
    ) -> Tuple[Optional[SimPosition], float]:
        direction = order.direction
        adverse_points = float(template.get("slippage_points", 0))
        spread_points = float(template.get("spread_points", 0)) or float(bar.get("spread", 0))
        adjustment = (spread_points / 2 + adverse_points) * point_size
        entry = float(bar["open"]) + adjustment * (1 if direction == "buy" else -1)
        valid_stop = (
            order.stop_loss < entry if direction == "buy"
            else order.stop_loss > entry
        )
        valid_take_profit = (
            order.take_profit == 0
            or (order.take_profit > entry if direction == "buy"
                else order.take_profit < entry)
        )
        if not valid_stop or not valid_take_profit:
            order.reject("开盘跳空后止盈止损方向无效")
            return None, balance
        volume = order.requested_volume
        commission = float(template.get("commission_per_lot", 0)) * volume
        order.status = "filled"
        order.filled_volume = volume
        order.filled_price = entry
        order.filled_at = int(bar["time"])
        return SimPosition(
            position_id=uuid.uuid4().hex[:16],
            order_id=order.order_id,
            symbol=order.symbol,
            direction=direction,
            volume=volume,
            entry_price=entry,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            opened_at=int(bar["time"]),
            confidence=order.confidence,
            period=order.period,
            signal_source=order.signal_source,
            contributing_sources=order.contributing_sources,
            open_commission=commission,
            signal_source_id=order.signal_source_id,
            exit_mode=order.exit_mode,
            trailing_activation_r=order.trailing_activation_r,
            trailing_distance_r=order.trailing_distance_r,
            initial_risk=abs(entry - order.stop_loss),
            favorable_price=entry,
            remaining_volume=volume,
            partial_levels_done=[],
            position_policy_snapshot=order.position_policy_snapshot,
        ), balance - commission

    def _maybe_close(
        self, position, bar, balance, template, point_size, contract_size
    ) -> Optional[Tuple[Dict, float]]:
        if position.direction == "buy":
            if float(bar["low"]) <= position.stop_loss:
                return self._close_at_price(
                    position, bar, balance, template, point_size,
                    contract_size, position.stop_loss, "stop_loss"
                )
            if position.take_profit > 0 and float(bar["high"]) >= position.take_profit:
                return self._close_at_price(
                    position, bar, balance, template, point_size,
                    contract_size, position.take_profit, "take_profit"
                )
        else:
            if float(bar["high"]) >= position.stop_loss:
                return self._close_at_price(
                    position, bar, balance, template, point_size,
                    contract_size, position.stop_loss, "stop_loss"
                )
            if position.take_profit > 0 and float(bar["low"]) <= position.take_profit:
                return self._close_at_price(
                    position, bar, balance, template, point_size,
                    contract_size, position.take_profit, "take_profit"
                )
        return None

    def _close_at_market(
        self, position, bar, balance, template, point_size, contract_size, reason
    ) -> Tuple[Dict, float]:
        return self._close_at_price(
            position, bar, balance, template, point_size, contract_size,
            float(bar["close"]), reason,
        )

    @staticmethod
    def _close_at_price(
        position, bar, balance, template, point_size, contract_size, price, reason,
        close_volume: Optional[float] = None,
    ) -> Tuple[Dict, float]:
        slippage = float(template.get("slippage_points", 0)) * point_size
        exit_price = float(price) - slippage * (1 if position.direction == "buy" else -1)
        multiplier = 1 if position.direction == "buy" else -1
        active_volume = position.remaining_volume or position.volume
        closed_volume = min(active_volume, close_volume or active_volume)
        gross = (exit_price - position.entry_price) * multiplier * closed_volume * contract_size
        close_commission = float(template.get("commission_per_lot", 0)) * closed_volume
        open_commission = position.open_commission * (closed_volume / position.volume)
        net = gross - open_commission - close_commission
        # Opening commission was already deducted, so only add gross minus closing commission.
        new_balance = balance + gross - close_commission
        position.remaining_volume = max(0.0, active_volume - closed_volume)
        if position.remaining_volume <= 1e-9:
            position.status = "closed"
            position.closed_at = int(bar["time"])
            position.close_price = exit_price
            position.close_reason = reason
        position.net_profit = round(position.net_profit + net, 2)
        trade = {
            "trade_id": uuid.uuid4().hex[:16],
            "order_id": position.order_id,
            "position_id": position.position_id,
            "symbol": position.symbol,
            "direction": position.direction,
            "volume": closed_volume,
            "entry_price": round(position.entry_price, 8),
            "exit_price": round(exit_price, 8),
            "stop_loss": round(position.stop_loss, 8),
            "take_profit": round(position.take_profit, 8),
            "opened_at": position.opened_at,
            "closed_at": int(bar["time"]),
            "period": position.period,
            "signal_source": position.signal_source,
            "contributing_sources": position.contributing_sources,
            "confidence": position.confidence,
            "exit_reason": reason,
            "gross_profit": round(gross, 2),
            "commission": round(open_commission + close_commission, 2),
            "net_profit": round(net, 2),
        }
        return trade, new_balance


def resolve_exits(entry: float, order: Dict, strategy: Dict, point_size: float) -> Tuple[float, float]:
    direction = order["direction"]
    sign = 1 if direction == "buy" else -1
    sl_mode = strategy.get("sl_mode", "signal")
    tp_mode = strategy.get("tp_mode", "signal")
    if sl_mode == "fixed_points":
        sl = entry - sign * float(strategy.get("sl_fixed_points", 20))
    else:
        sl = float(order.get("stop_loss", 0))
    risk = abs(entry - sl)
    if tp_mode == "fixed_points":
        tp = entry + sign * float(strategy.get("tp_fixed_points", 40))
    elif tp_mode == "risk_reward":
        tp = entry + sign * risk * float(strategy.get("tp_risk_reward", 2))
    else:
        tp = float(order.get("take_profit", 0))
    return sl, tp


def valid_exits(direction: str, entry: float, sl: float, tp: float) -> bool:
    return shared_valid_exits(direction, entry, sl, tp)


def calculate_volume(balance, entry, sl, strategy, template, contract_size) -> float:
    return calculate_volume_from_risk(
        balance, abs(entry - sl), strategy, template, contract_size
    )


def calculate_volume_from_risk(
    balance, risk_points, strategy, template, contract_size
) -> float:
    mode = template.get("position_sizing_mode", "strategy")
    if mode == "fixed":
        volume = float(template.get("fixed_volume", 0.01))
    elif mode == "risk_percent":
        risk_cash = balance * float(template.get("risk_percent", 1)) / 100
        loss_per_lot = abs(risk_points) * contract_size
        volume = risk_cash / loss_per_lot if loss_per_lot > 0 else 0
    elif strategy.get("volume_mode") == "risk_percent":
        risk_cash = balance * float(strategy.get("risk_percent", 1)) / 100
        loss_per_lot = abs(risk_points) * contract_size
        volume = risk_cash / loss_per_lot if loss_per_lot > 0 else 0
    else:
        volume = float(strategy.get("fixed_volume", 0.01))
    return max(0.01, round(volume, 2)) if volume > 0 else 0


def backtest_position_check(
    direction: str,
    positions: List[SimPosition],
    strategy: Dict,
    template: Dict,
) -> Dict:
    reason = position_limit_reason(direction, positions, strategy, template)
    return {
        "allowed": not reason,
        "warnings": [reason] if reason else [],
    }


def position_limit_reason(
    direction: str,
    positions: List[SimPosition],
    strategy: Dict,
    template: Dict,
) -> str:
    strategy_max = max(1, int(strategy.get("max_positions", 3)))
    max_positions = max(1, int(template.get("max_positions", strategy_max)))
    if len(positions) >= max_positions:
        return f"已达到最大持仓数 {max_positions}"

    same_direction = sum(
        1 for position in positions if position.direction == direction
    )
    strategy_same_direction = strategy.get("max_same_direction", max_positions)
    max_same_direction = max(1, int(
        template.get("max_same_direction", strategy_same_direction)
    ))
    if same_direction + 1 > max_same_direction:
        return f"同向持仓将超过限制 {max_same_direction}"

    opposite_direction = len(positions) - same_direction
    conflict = strategy.get("position_conflict", "allow_opposite")
    if opposite_direction and conflict in {"block", "allow_same"}:
        return (
            "有反向持仓，策略禁止新开仓"
            if conflict == "block"
            else "有反向持仓，策略只允许同向加仓"
        )
    return ""


def market_spec(symbol: str, bars: List[Dict]) -> Tuple[float, float]:
    upper = symbol.upper()
    if "GOLD" in upper or "XAU" in upper:
        return 0.01, 100.0
    if "JPY" in upper:
        return 0.001, 100000.0
    if len(upper.rstrip("#._")) == 6:
        return 0.00001, 100000.0
    sample = bars[0]["close"] if bars else 1
    decimals = max(0, min(8, len(str(sample).partition(".")[2])))
    return 10 ** -decimals, 1.0


def unrealized(position: Optional[SimPosition], price: float, contract_size: float) -> float:
    if position is None:
        return 0.0
    multiplier = 1 if position.direction == "buy" else -1
    return (float(price) - position.entry_price) * multiplier * position.volume * contract_size


def unrealized_positions(
    positions: List[SimPosition], price: float, contract_size: float
) -> float:
    return sum(
        unrealized(position, price, contract_size) for position in positions
    )


def downsample_records(records: List[Dict], max_points: int) -> List[Dict]:
    if len(records) <= max_points:
        return records
    step = max(1, math.ceil(len(records) / max_points))
    sampled = records[::step]
    if sampled[-1] != records[-1]:
        sampled.append(records[-1])
    return sampled


def aggregate_replay_bars(
    bars: List[Dict], max_points: int = 1200,
) -> List[Dict]:
    """Compress elapsed M1 bars into OHLC buckets without losing the trend."""
    if not bars:
        return []
    step = max(1, math.ceil(len(bars) / max_points))
    replay = []
    for offset in range(0, len(bars), step):
        bucket = bars[offset:offset + step]
        replay.append({
            "time": int(bucket[0]["time"]),
            "end_time": int(bucket[-1]["time"]),
            "open": float(bucket[0]["open"]),
            "high": max(float(item["high"]) for item in bucket),
            "low": min(float(item["low"]) for item in bucket),
            "close": float(bucket[-1]["close"]),
            "tick_volume": sum(int(item.get("tick_volume", 0)) for item in bucket),
            "bar_count": len(bucket),
        })
    return replay


def trade_group_stats(trades: List[Dict], key: str) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    for trade in trades:
        value = str(trade.get(key) or "unknown")
        groups.setdefault(value, []).append(trade)
    result = []
    for value, items in groups.items():
        profits = [float(item["net_profit"]) for item in items]
        wins = sum(1 for profit in profits if profit > 0)
        result.append({
            key: value,
            "trade_count": len(items),
            "win_count": wins,
            "win_rate_pct": round(wins / len(items) * 100, 2),
            "net_profit": round(sum(profits), 2),
        })
    return sorted(result, key=lambda item: (-item["trade_count"], item[key]))


def consecutive_trade_counts(profits: List[float]) -> Tuple[int, int]:
    max_wins = max_losses = current_wins = current_losses = 0
    for profit in profits:
        if profit > 0:
            current_wins += 1
            current_losses = 0
        elif profit < 0:
            current_losses += 1
            current_wins = 0
        else:
            current_wins = current_losses = 0
        max_wins = max(max_wins, current_wins)
        max_losses = max(max_losses, current_losses)
    return max_wins, max_losses


def daily_sharpe_ratio(initial: float, equity_curve: List[Dict]) -> Optional[float]:
    daily_closes: Dict[str, float] = {}
    for point in equity_curve:
        day = datetime.fromtimestamp(int(point["time"]), timezone.utc).date().isoformat()
        daily_closes[day] = float(point["equity"])
    closes = list(daily_closes.values())
    if len(closes) < 2:
        return None
    returns = []
    previous = initial
    for close in closes:
        if previous > 0:
            returns.append(close / previous - 1)
        previous = close
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    deviation = math.sqrt(variance)
    return round(mean / deviation * math.sqrt(252), 2) if deviation > 0 else None


def build_result(
    initial, final_balance, trades, equity_curve, llm_analyses,
    llm_cache_hits, point_size, contract_size, strategy,
) -> Dict:
    profits = [float(item["net_profit"]) for item in trades]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    peak = initial
    max_drawdown = 0.0
    max_drawdown_amount = 0.0
    drawdown_curve = []
    for point in equity_curve:
        equity = float(point["equity"])
        peak = max(peak, equity)
        if peak > 0:
            amount = peak - equity
            drawdown = amount / peak * 100
            max_drawdown = max(max_drawdown, drawdown)
            max_drawdown_amount = max(max_drawdown_amount, amount)
            drawdown_curve.append({
                "time": int(point["time"]),
                "drawdown_pct": round(drawdown, 4),
            })
    sampled_curve = downsample_records(equity_curve, 1000)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    max_consecutive_wins, max_consecutive_losses = consecutive_trade_counts(profits)
    holding_minutes = [
        max(0, int(item["closed_at"]) - int(item["opened_at"])) / 60
        for item in trades
        if item.get("closed_at") is not None and item.get("opened_at") is not None
    ]
    monthly_groups: Dict[str, List[float]] = {}
    for trade in trades:
        month = datetime.fromtimestamp(
            int(trade["closed_at"]), timezone.utc
        ).strftime("%Y-%m")
        monthly_groups.setdefault(month, []).append(float(trade["net_profit"]))
    monthly_stats = [{
        "month": month,
        "net_profit": round(sum(values), 2),
        "return_pct": round(sum(values) / initial * 100, 2),
        "trade_count": len(values),
    } for month, values in sorted(monthly_groups.items())]
    source_counts = {}
    for trade in trades:
        source = trade.get("signal_source", "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    enabled_sources = [
        source for source in (
            "key_level", "ai_entry", "moving_average", "alpha_factor"
        )
        if signal_source_enabled(strategy, source)
    ]
    return {
        "engine_version": ENGINE_VERSION,
        "supported_signal_sources": [
            "key_level", "ai_entry", "moving_average", "alpha_factor"
        ],
        "enabled_signal_sources": enabled_sources,
        "signal_source_trade_counts": source_counts,
        "warnings": [],
        "initial_capital": round(initial, 2),
        "final_balance": round(final_balance, 2),
        "net_profit": round(final_balance - initial, 2),
        "total_return_pct": round((final_balance / initial - 1) * 100, 2),
        "max_drawdown_pct": round(max_drawdown, 2),
        "max_drawdown_amount": round(max_drawdown_amount, 2),
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "average_trade": round(sum(profits) / len(profits), 2) if profits else 0,
        "average_win": round(gross_profit / len(wins), 2) if wins else 0,
        "average_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "payoff_ratio": round(
            (gross_profit / len(wins)) / (gross_loss / len(losses)), 2
        ) if wins and losses and gross_loss else None,
        "expectancy": round(sum(profits) / len(profits), 2) if profits else 0,
        "largest_win": round(max(wins), 2) if wins else 0,
        "largest_loss": round(abs(min(losses)), 2) if losses else 0,
        "max_consecutive_wins": max_consecutive_wins,
        "max_consecutive_losses": max_consecutive_losses,
        "average_holding_minutes": round(
            sum(holding_minutes) / len(holding_minutes), 2
        ) if holding_minutes else 0,
        "recovery_factor": round(
            (final_balance - initial) / max_drawdown_amount, 2
        ) if max_drawdown_amount > 0 else None,
        "sharpe_ratio": daily_sharpe_ratio(initial, equity_curve),
        "total_commission": round(
            sum(float(item.get("commission", 0)) for item in trades), 2
        ),
        "direction_stats": trade_group_stats(trades, "direction"),
        "signal_source_stats": trade_group_stats(trades, "signal_source"),
        "exit_reason_stats": trade_group_stats(trades, "exit_reason"),
        "monthly_stats": monthly_stats,
        "llm_analysis_count": llm_analyses,
        "llm_call_count": llm_analyses - llm_cache_hits,
        "llm_cache_hits": llm_cache_hits,
        "point_size": point_size,
        "contract_size": contract_size,
        "equity_curve": sampled_curve,
        "drawdown_curve": downsample_records(drawdown_curve, 1000),
        "trades": trades[:2000],
        "trades_truncated": len(trades) > 2000,
    }


class BacktestWorker:
    """Single-process worker; SQLite claiming keeps it safe to scale later."""

    def __init__(
        self,
        storage: Optional[SQLiteStorage] = None,
        engine_factory: Optional[Callable[..., M1BacktestEngine]] = None,
        poll_seconds: float = 1.0,
    ):
        self.storage = storage or get_storage()
        self.tasks = BacktestTaskRepository(self.storage)
        self.engine_factory = engine_factory or M1BacktestEngine
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.tasks.recover_stale()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="backtest-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> bool:
        task = self.tasks.claim_next(self.worker_id)
        if task is None:
            return False
        try:
            progress_callback = lambda value: self.tasks.heartbeat(
                task["task_id"], value
            )
            try:
                engine = self.engine_factory(
                    progress_callback=progress_callback,
                    checkpoint_callback=lambda value, ledger: self.tasks.checkpoint(
                        task["task_id"], value, ledger
                    ),
                    cancel_callback=lambda: self.tasks.is_cancel_requested(
                        task["task_id"]
                    ),
                    llm_usage_callback=lambda cache_hit: self.tasks.record_llm_analysis(
                        task["task_id"], cache_hit
                    ),
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                # Preserve compatibility with lightweight external/test engines.
                engine = self.engine_factory(progress_callback=progress_callback)
            self.tasks.complete(task["task_id"], engine.run(task))
        except BacktestCanceled:
            self.tasks.cancel(task["task_id"])
        except Exception as exc:
            traceback.print_exc()
            self.tasks.fail(task["task_id"], str(exc))
        return True

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self.poll_seconds)
