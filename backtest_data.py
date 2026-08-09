#!/usr/bin/env python3
"""历史回测行情数据集管理。"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from sqlite_storage import DATA_DIR, SQLiteStorage, get_storage


class DatasetStatus:
    PENDING = "pending"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    CANCELED = "canceled"
    ACTIVE = {PENDING, DOWNLOADING, VALIDATING}


class DatasetReferencedError(ValueError):
    """数据集仍被回测模板或任务引用。"""


class BacktestDatasetRepository:
    """数据集和分片元数据仓库。"""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()

    def create(
        self,
        user_id: int,
        account_id: int,
        dataset_name: str,
        symbol: str,
        requested_start: int,
        requested_end: int,
        warmup_start: int,
        visibility: str = "shared",
    ) -> Dict:
        dataset_id = str(uuid.uuid4())[:12]
        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO backtest_datasets(
                dataset_id, user_id, account_id, dataset_name, visibility, symbol,
                timeframe, requested_start, requested_end, warmup_start,
                cursor_time, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, 'M1', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                user_id,
                account_id,
                dataset_name,
                visibility,
                symbol,
                requested_start,
                requested_end,
                warmup_start,
                warmup_start,
                DatasetStatus.PENDING,
                now,
                now,
            ),
        )
        return self.get_for_user(user_id, dataset_id)

    def list_for_user(self, user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT d.*, a.account_name,
                   COALESCE(c.mt5_login, a.mt5_login) AS mt5_login,
                   COALESCE(c.mt5_server, a.mt5_server) AS mt5_server,
                   COALESCE(c.last_seen_at, a.last_seen_at) AS last_seen_at,
                   u.username AS creator_username,
                   (SELECT COUNT(*) FROM backtest_template_datasets td
                    WHERE td.dataset_id = d.dataset_id) AS template_reference_count,
                   (SELECT COUNT(*) FROM backtest_tasks bt
                    WHERE bt.dataset_id = d.dataset_id) AS task_reference_count
            FROM backtest_datasets d
            JOIN trading_accounts a ON a.id = d.account_id
            LEFT JOIN mt5_account_connections c ON c.account_id = a.id
            JOIN users u ON u.id = d.user_id
            WHERE d.user_id = ? OR d.visibility = 'shared'
            ORDER BY d.created_at DESC
            """,
            (user_id,),
        )
        return [self._row_to_dict(row, user_id) for row in rows]

    def get_for_user(self, user_id: int, dataset_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT d.*, a.account_name,
                   COALESCE(c.mt5_login, a.mt5_login) AS mt5_login,
                   COALESCE(c.mt5_server, a.mt5_server) AS mt5_server,
                   COALESCE(c.last_seen_at, a.last_seen_at) AS last_seen_at,
                   u.username AS creator_username,
                   (SELECT COUNT(*) FROM backtest_template_datasets td
                    WHERE td.dataset_id = d.dataset_id) AS template_reference_count,
                   (SELECT COUNT(*) FROM backtest_tasks bt
                    WHERE bt.dataset_id = d.dataset_id) AS task_reference_count
            FROM backtest_datasets d
            JOIN trading_accounts a ON a.id = d.account_id
            LEFT JOIN mt5_account_connections c ON c.account_id = a.id
            JOIN users u ON u.id = d.user_id
            WHERE d.user_id = ? AND d.dataset_id = ?
            """,
            (user_id, dataset_id),
        )
        return self._row_to_dict(row, user_id) if row else None

    def get_visible(self, user_id: int, dataset_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT d.*, a.account_name,
                   COALESCE(c.mt5_login, a.mt5_login) AS mt5_login,
                   COALESCE(c.mt5_server, a.mt5_server) AS mt5_server,
                   COALESCE(c.last_seen_at, a.last_seen_at) AS last_seen_at,
                   u.username AS creator_username,
                   (SELECT COUNT(*) FROM backtest_template_datasets td
                    WHERE td.dataset_id = d.dataset_id) AS template_reference_count,
                   (SELECT COUNT(*) FROM backtest_tasks bt
                    WHERE bt.dataset_id = d.dataset_id) AS task_reference_count
            FROM backtest_datasets d
            JOIN trading_accounts a ON a.id = d.account_id
            LEFT JOIN mt5_account_connections c ON c.account_id = a.id
            JOIN users u ON u.id = d.user_id
            WHERE d.dataset_id = ?
              AND (d.user_id = ? OR d.visibility = 'shared')
            """,
            (dataset_id, user_id),
        )
        return self._row_to_dict(row, user_id) if row else None

    def update_visibility(
        self, user_id: int, dataset_id: str, visibility: str
    ) -> Optional[Dict]:
        if visibility not in {"shared", "private"}:
            raise ValueError("数据集可见性必须是 shared 或 private")
        if self.get_for_user(user_id, dataset_id) is None:
            return None
        self.storage.execute(
            """
            UPDATE backtest_datasets
            SET visibility = ?, updated_at = ?
            WHERE user_id = ? AND dataset_id = ?
            """,
            (visibility, int(time.time()), user_id, dataset_id),
        )
        return self.get_for_user(user_id, dataset_id)

    def get_for_ea(self, account_id: int, dataset_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT * FROM backtest_datasets
            WHERE account_id = ? AND dataset_id = ?
            """,
            (account_id, dataset_id),
        )
        return self._row_to_dict(row) if row else None

    def claim_next(self, account_id: int, symbol: str) -> Optional[Dict]:
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            row = conn.execute(
                """
                SELECT * FROM backtest_datasets
                WHERE account_id = ?
                  AND upper(symbol) = upper(?)
                  AND status IN (?, ?)
                ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END, created_at
                LIMIT 1
                """,
                (
                    account_id,
                    symbol,
                    DatasetStatus.DOWNLOADING,
                    DatasetStatus.PENDING,
                    DatasetStatus.DOWNLOADING,
                ),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == DatasetStatus.PENDING:
                conn.execute(
                    """
                    UPDATE backtest_datasets
                    SET status = ?, claimed_at = ?, updated_at = ?
                    WHERE dataset_id = ? AND status = ?
                    """,
                    (
                        DatasetStatus.DOWNLOADING,
                        now,
                        now,
                        row["dataset_id"],
                        DatasetStatus.PENDING,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM backtest_datasets WHERE dataset_id = ?",
                    (row["dataset_id"],),
                ).fetchone()
            return self._row_to_dict(row)

    def get_chunk(self, dataset_id: str, chunk_index: int) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT * FROM backtest_dataset_chunks
            WHERE dataset_id = ? AND chunk_index = ?
            """,
            (dataset_id, chunk_index),
        )
        return dict(row) if row else None

    def record_chunk(
        self,
        account_id: int,
        dataset_id: str,
        chunk_index: int,
        range_start: int,
        range_end: int,
        bars: List[Dict],
        invalid_count: int,
        checksum: str,
        file_path: str,
        broker_server: str,
        ea_version: str,
    ) -> Tuple[str, Dict]:
        """原子记录分片并推进服务端游标。"""
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            row = conn.execute(
                """
                SELECT * FROM backtest_datasets
                WHERE account_id = ? AND dataset_id = ?
                """,
                (account_id, dataset_id),
            ).fetchone()
            if row is None:
                raise ValueError("历史数据任务不存在或不属于当前 MT5 账户")

            existing = conn.execute(
                """
                SELECT checksum FROM backtest_dataset_chunks
                WHERE dataset_id = ? AND chunk_index = ?
                """,
                (dataset_id, chunk_index),
            ).fetchone()
            if existing:
                if existing["checksum"] != checksum:
                    raise ValueError("同一分片重复上传但内容不一致")
                return "duplicate", self._row_to_dict(row)

            if row["status"] not in {
                DatasetStatus.DOWNLOADING,
                DatasetStatus.VALIDATING,
            }:
                raise ValueError(f"任务当前状态不允许上传: {row['status']}")

            if chunk_index != int(row["next_chunk_index"]):
                raise ValueError(
                    f"分片序号不连续，服务端期望 {row['next_chunk_index']}"
                )
            if range_start != int(row["cursor_time"]):
                raise ValueError("分片起始时间与服务端游标不一致")

            first_time = bars[0]["time"] if bars else None
            last_time = bars[-1]["time"] if bars else None
            conn.execute(
                """
                INSERT INTO backtest_dataset_chunks(
                    dataset_id, chunk_index, range_start, range_end,
                    first_bar_time, last_bar_time, bar_count, invalid_count,
                    checksum, file_path, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    chunk_index,
                    range_start,
                    range_end,
                    first_time,
                    last_time,
                    len(bars),
                    invalid_count,
                    checksum,
                    file_path,
                    now,
                ),
            )
            finished = range_end >= int(row["requested_end"])
            next_status = (
                DatasetStatus.VALIDATING
                if finished else DatasetStatus.DOWNLOADING
            )
            conn.execute(
                """
                UPDATE backtest_datasets
                SET cursor_time = ?, next_chunk_index = ?, status = ?,
                    received_bars = received_bars + ?,
                    invalid_count = invalid_count + ?,
                    broker_server = CASE WHEN ? = '' THEN broker_server ELSE ? END,
                    ea_version = CASE WHEN ? = '' THEN ea_version ELSE ? END,
                    updated_at = ?
                WHERE dataset_id = ?
                """,
                (
                    range_end + 1,
                    chunk_index + 1,
                    next_status,
                    len(bars),
                    invalid_count,
                    broker_server,
                    broker_server,
                    ea_version,
                    ea_version,
                    now,
                    dataset_id,
                ),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM backtest_datasets WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchone()
            return "accepted", self._row_to_dict(updated)

    def list_chunks(self, dataset_id: str) -> List[Dict]:
        return [
            dict(row)
            for row in self.storage.fetchall(
                """
                SELECT * FROM backtest_dataset_chunks
                WHERE dataset_id = ? ORDER BY chunk_index
                """,
                (dataset_id,),
            )
        ]

    def mark_ready(
        self,
        dataset_id: str,
        *,
        received_bars: int,
        duplicate_count: int,
        gap_count: int,
        invalid_count: int,
        quality_score: float,
        data_format: str,
        file_path: str,
        data_hash: str,
    ) -> None:
        now = int(time.time())
        self.storage.execute(
            """
            UPDATE backtest_datasets
            SET status = ?, received_bars = ?, duplicate_count = ?,
                gap_count = ?, invalid_count = ?, quality_score = ?,
                data_format = ?, file_path = ?, data_hash = ?,
                error_message = '', completed_at = ?, updated_at = ?
            WHERE dataset_id = ?
            """,
            (
                DatasetStatus.READY,
                received_bars,
                duplicate_count,
                gap_count,
                invalid_count,
                quality_score,
                data_format,
                file_path,
                data_hash,
                now,
                now,
                dataset_id,
            ),
        )

    def mark_failed(self, dataset_id: str, message: str) -> None:
        self.storage.execute(
            """
            UPDATE backtest_datasets
            SET status = ?, error_message = ?, updated_at = ?
            WHERE dataset_id = ?
            """,
            (DatasetStatus.FAILED, message[:500], int(time.time()), dataset_id),
        )

    def cancel(self, user_id: int, dataset_id: str) -> bool:
        dataset = self.get_for_user(user_id, dataset_id)
        if dataset is None or dataset["status"] == DatasetStatus.READY:
            return False
        self.storage.execute(
            """
            UPDATE backtest_datasets
            SET status = ?, updated_at = ?
            WHERE user_id = ? AND dataset_id = ?
            """,
            (DatasetStatus.CANCELED, int(time.time()), user_id, dataset_id),
        )
        return True

    def delete(self, user_id: int, dataset_id: str) -> bool:
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT d.dataset_id,
                       (SELECT COUNT(*) FROM backtest_template_datasets td
                        WHERE td.dataset_id = d.dataset_id) AS template_count,
                       (SELECT COUNT(*) FROM backtest_tasks bt
                        WHERE bt.dataset_id = d.dataset_id) AS task_count
                FROM backtest_datasets d
                WHERE d.user_id = ? AND d.dataset_id = ?
                """,
                (user_id, dataset_id),
            ).fetchone()
            if row is None:
                return False
            template_count = int(row["template_count"])
            task_count = int(row["task_count"])
            if template_count or task_count:
                raise DatasetReferencedError(
                    f"数据集已被 {template_count} 个模板和 {task_count} 个回测任务引用，不能删除"
                )
            conn.execute(
                "DELETE FROM backtest_datasets WHERE user_id = ? AND dataset_id = ?",
                (user_id, dataset_id),
            )
            conn.commit()
            return True

    @staticmethod
    def _row_to_dict(row, viewer_user_id: Optional[int] = None) -> Dict:
        data = dict(row)
        start = int(data["warmup_start"])
        end = int(data["requested_end"])
        cursor = min(int(data["cursor_time"]), end + 1)
        total = max(1, end - start + 1)
        data["progress"] = round(max(0, cursor - start) / total * 100, 1)
        data["template_reference_count"] = int(
            data.get("template_reference_count", 0)
        )
        data["task_reference_count"] = int(data.get("task_reference_count", 0))
        data["is_referenced"] = bool(
            data["template_reference_count"] or data["task_reference_count"]
        )
        if viewer_user_id is not None:
            data["is_owner"] = int(data["user_id"]) == int(viewer_user_id)
            data["can_manage"] = data["is_owner"]
            data["can_delete"] = data["is_owner"] and not data["is_referenced"]
            if not data["is_owner"]:
                for field in (
                    "user_id",
                    "account_id",
                    "account_name",
                    "mt5_login",
                    "mt5_server",
                    "last_seen_at",
                    "file_path",
                ):
                    data.pop(field, None)
        return data


class BacktestDatasetService:
    """数据集任务编排、分片存储和质量校验。"""

    CHUNK_SECONDS = 2 * 24 * 60 * 60
    MIN_RANGE_SECONDS = 2 * 60 * 60

    def __init__(
        self,
        repository: Optional[BacktestDatasetRepository] = None,
        data_root: Optional[Path] = None,
    ):
        self.repository = repository or BacktestDatasetRepository()
        self.data_root = Path(data_root or (DATA_DIR / "backtest"))

    def create_dataset(
        self,
        user_id: int,
        account_id: int,
        dataset_name: str,
        symbol: str,
        requested_start: int,
        requested_end: int,
        warmup_days: int = 30,
        visibility: str = "shared",
    ) -> Dict:
        name = str(dataset_name or "").strip()
        normalized_symbol = str(symbol or "").strip()
        if not name:
            raise ValueError("请输入数据集名称")
        if not normalized_symbol:
            raise ValueError("请选择交易品种")
        if requested_end <= requested_start:
            raise ValueError("结束时间必须晚于开始时间")
        if requested_end - requested_start < self.MIN_RANGE_SECONDS:
            raise ValueError("历史数据集时间范围不能少于 2 小时")
        if requested_end - requested_start > 2 * 366 * 24 * 60 * 60:
            raise ValueError("第一版单个数据集最多支持两年")
        if visibility not in {"shared", "private"}:
            raise ValueError("数据集可见性必须是 shared 或 private")

        account = self.repository.storage.fetchone(
            """
            SELECT id FROM trading_accounts
            WHERE id = ? AND user_id = ? AND enabled = 1
              AND account_type = 'mt5'
            """,
            (account_id, user_id),
        )
        if account is None:
            raise ValueError("MT5 账户不存在或未启用")

        warmup_days = max(0, min(int(warmup_days), 180))
        warmup_start = requested_start - warmup_days * 24 * 60 * 60
        return self.repository.create(
            user_id,
            account_id,
            name,
            normalized_symbol,
            requested_start,
            requested_end,
            warmup_start,
            visibility,
        )

    def get_next_task(self, account_id: int, symbol: str) -> Optional[Dict]:
        dataset = self.repository.claim_next(account_id, symbol)
        if dataset is None:
            return None
        range_start = int(dataset["cursor_time"])
        range_end = min(
            range_start + self.CHUNK_SECONDS - 1,
            int(dataset["requested_end"]),
        )
        return {
            "dataset_id": dataset["dataset_id"],
            "dataset_name": dataset["dataset_name"],
            "symbol": dataset["symbol"],
            "timeframe": dataset["timeframe"],
            "chunk_index": int(dataset["next_chunk_index"]),
            "range_start": range_start,
            "range_end": range_end,
            "requested_end": int(dataset["requested_end"]),
            "progress": dataset["progress"],
        }

    def accept_chunk(
        self,
        account_id: int,
        dataset_id: str,
        payload: Dict,
    ) -> Dict:
        dataset = self.repository.get_for_ea(account_id, dataset_id)
        if dataset is None:
            raise ValueError("历史数据任务不存在或不属于当前 MT5 账户")

        chunk_index = int(payload.get("chunk_index", -1))
        range_start = int(payload.get("range_start", 0))
        range_end = int(payload.get("range_end", 0))
        if chunk_index < 0 or range_end < range_start:
            raise ValueError("历史数据分片参数无效")
        if range_end > int(dataset["requested_end"]):
            raise ValueError("分片结束时间超过数据集范围")

        bars, invalid_count = self._normalize_bars(
            payload.get("bars", []), range_start, range_end
        )
        canonical = json.dumps(
            bars, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        checksum = hashlib.sha256(canonical).hexdigest()
        chunk_path = self._chunk_path(
            int(dataset["user_id"]), dataset_id, chunk_index
        )
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(chunk_path, "wt", encoding="utf-8") as handle:
            for bar in bars:
                handle.write(json.dumps(bar, ensure_ascii=False) + "\n")

        result, updated = self.repository.record_chunk(
            account_id,
            dataset_id,
            chunk_index,
            range_start,
            range_end,
            bars,
            invalid_count,
            checksum,
            str(chunk_path),
            str(payload.get("broker_server", "")).strip(),
            str(payload.get("ea_version", "")).strip(),
        )
        if updated["status"] == DatasetStatus.VALIDATING:
            self._finalize(updated)
            updated = self.repository.get_for_ea(account_id, dataset_id)
        return {
            "result": result,
            "dataset": updated,
        }

    def delete_dataset(self, user_id: int, dataset_id: str) -> bool:
        dataset = self.repository.get_for_user(user_id, dataset_id)
        if dataset is None:
            return False
        deleted = self.repository.delete(user_id, dataset_id)
        if deleted:
            dataset_dir = self.data_root / str(user_id) / dataset_id
            if dataset_dir.exists():
                shutil.rmtree(dataset_dir)
        return deleted

    def _finalize(self, dataset: Dict) -> None:
        dataset_id = dataset["dataset_id"]
        try:
            chunks = self.repository.list_chunks(dataset_id)
            all_bars: List[Dict] = []
            invalid_count = 0
            for chunk in chunks:
                invalid_count += int(chunk["invalid_count"])
                path = Path(chunk["file_path"])
                if not path.exists():
                    raise RuntimeError(
                        f"历史数据分片文件缺失: {chunk['chunk_index']}"
                    )
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    for line in handle:
                        if line.strip():
                            all_bars.append(json.loads(line))

            by_time: Dict[int, Dict] = {}
            duplicate_count = 0
            for bar in sorted(all_bars, key=lambda item: item["time"]):
                if bar["time"] in by_time:
                    duplicate_count += 1
                by_time[bar["time"]] = bar
            bars = list(by_time.values())
            if not bars:
                raise RuntimeError("MT5 未返回任何有效 M1 行情")

            gap_count = self._count_market_gaps(bars)
            quality_score = self._quality_score(
                len(bars), duplicate_count, invalid_count, gap_count
            )
            output_path, data_format = self._write_final_dataset(
                int(dataset["user_id"]), dataset_id, dataset["symbol"], bars
            )
            data_hash = self._sha256_file(output_path)
            self.repository.mark_ready(
                dataset_id,
                received_bars=len(bars),
                duplicate_count=duplicate_count,
                gap_count=gap_count,
                invalid_count=invalid_count,
                quality_score=quality_score,
                data_format=data_format,
                file_path=str(output_path),
                data_hash=data_hash,
            )
        except Exception as exc:
            self.repository.mark_failed(dataset_id, str(exc))

    def _write_final_dataset(
        self, user_id: int, dataset_id: str, symbol: str, bars: List[Dict]
    ) -> Tuple[Path, str]:
        dataset_dir = self.data_root / str(user_id) / dataset_id
        dataset_dir.mkdir(parents=True, exist_ok=True)
        safe_symbol = "".join(
            char if char.isalnum() or char in "-_" else "_" for char in symbol
        )
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            output = dataset_dir / f"{safe_symbol}_M1.parquet"
            pq.write_table(pa.Table.from_pylist(bars), output, compression="zstd")
            return output, "parquet"
        except ImportError:
            output = dataset_dir / f"{safe_symbol}_M1.csv.gz"
            with gzip.open(output, "wt", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "time", "open", "high", "low", "close",
                        "tick_volume", "real_volume", "spread",
                    ],
                )
                writer.writeheader()
                writer.writerows(bars)
            return output, "csv.gz"

    @staticmethod
    def _normalize_bars(
        raw_bars: Iterable[Dict], range_start: int, range_end: int
    ) -> Tuple[List[Dict], int]:
        normalized: Dict[int, Dict] = {}
        invalid = 0
        for raw in raw_bars if isinstance(raw_bars, list) else []:
            try:
                timestamp = int(raw.get("time", 0))
                open_price = float(raw.get("open", 0))
                high = float(raw.get("high", 0))
                low = float(raw.get("low", 0))
                close = float(raw.get("close", 0))
                if not range_start <= timestamp <= range_end:
                    raise ValueError
                if min(open_price, high, low, close) <= 0:
                    raise ValueError
                if low > min(open_price, close) or high < max(open_price, close):
                    raise ValueError
                if low > high:
                    raise ValueError
                normalized[timestamp] = {
                    "time": timestamp,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "tick_volume": max(0, int(raw.get("tick_volume", 0))),
                    "real_volume": max(0, int(raw.get("real_volume", 0))),
                    "spread": max(0, int(raw.get("spread", 0))),
                }
            except (TypeError, ValueError, AttributeError):
                invalid += 1
        return sorted(normalized.values(), key=lambda item: item["time"]), invalid

    @staticmethod
    def _count_market_gaps(bars: List[Dict]) -> int:
        gaps = 0
        for previous, current in zip(bars, bars[1:]):
            elapsed = current["time"] - previous["time"]
            if elapsed <= 90:
                continue
            previous_day = datetime.fromtimestamp(
                previous["time"], tz=timezone.utc
            ).weekday()
            current_day = datetime.fromtimestamp(
                current["time"], tz=timezone.utc
            ).weekday()
            if previous_day < 5 and current_day < 5:
                gaps += 1
        return gaps

    @staticmethod
    def _quality_score(
        total: int, duplicates: int, invalid: int, gaps: int
    ) -> float:
        if total <= 0:
            return 0.0
        row_penalty = (duplicates + invalid) / total * 100
        gap_penalty = min(20.0, gaps * 0.25)
        return round(max(0.0, 100.0 - row_penalty - gap_penalty), 2)

    def _chunk_path(
        self, user_id: int, dataset_id: str, chunk_index: int
    ) -> Path:
        return (
            self.data_root
            / str(user_id)
            / dataset_id
            / "chunks"
            / f"{chunk_index:06d}.jsonl.gz"
        )

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
