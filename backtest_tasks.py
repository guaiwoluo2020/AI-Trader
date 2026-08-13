#!/usr/bin/env python3
"""回测模板、批次和任务管理。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Dict, List, Optional

from backtest_data import BacktestDatasetRepository, DatasetStatus
from sqlite_storage import (
    PositionManagementPolicyRepository, SQLiteStorage,
    StrategyConfigRepository, get_storage,
)


class BacktestTaskStatus:
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class BacktestTemplateService:
    """管理可复用模板，并将一次运行展开为批次和原子任务。"""

    POSITION_MODES = {"strategy", "fixed", "risk_percent"}

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.strategies = StrategyConfigRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)
        self.datasets = BacktestDatasetRepository(self.storage)

    def get_context(self, user_id: int) -> Dict:
        strategies = [
            {
                "strategy_id": item.strategy_id,
                "strategy_name": item.strategy_name,
                "symbol": item.symbol,
                "lifecycle_status": item.lifecycle_status,
                "fixed_volume": item.fixed_volume,
                "risk_percent": item.risk_percent,
                "max_positions": item.max_positions,
                "max_same_direction": item.max_same_direction,
            }
            for item in self.strategies.get_all_strategies(user_id)
        ]
        datasets = [
            item
            for item in self.datasets.list_for_user(user_id)
            if item["status"] == DatasetStatus.READY
        ]
        return {"strategies": strategies, "datasets": datasets}

    def list_templates(self, user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT t.*, u.username AS creator_username
            FROM backtest_templates t
            JOIN users u ON u.id = t.user_id
            WHERE t.user_id = ?
            ORDER BY t.updated_at DESC
            """,
            (user_id,),
        )
        return [self._template_to_dict(row, user_id) for row in rows]

    def get_template(self, user_id: int, template_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT t.*, u.username AS creator_username
            FROM backtest_templates t
            JOIN users u ON u.id = t.user_id
            WHERE t.user_id = ? AND t.template_id = ?
            """,
            (user_id, template_id),
        )
        return self._template_to_dict(row, user_id) if row else None

    def create_template(self, user_id: int, payload: Dict) -> Dict:
        values = self._validate_template(user_id, payload)
        template_id = str(uuid.uuid4())[:12]
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO backtest_templates(
                    template_id, user_id, template_name, visibility, strategy_id,
                    description, initial_capital, position_sizing_mode,
                    fixed_volume, risk_percent, spread_points,
                    slippage_points, commission_per_lot, max_positions,
                    max_same_direction, use_strategy_exits, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    user_id,
                    values["template_name"],
                    values["visibility"],
                    values["strategy_id"],
                    values["description"],
                    values["initial_capital"],
                    values["position_sizing_mode"],
                    values["fixed_volume"],
                    values["risk_percent"],
                    values["spread_points"],
                    values["slippage_points"],
                    values["commission_per_lot"],
                    values["max_positions"],
                    values["max_same_direction"],
                    int(values["use_strategy_exits"]),
                    now,
                    now,
                ),
            )
            self._replace_template_datasets(
                conn, template_id, values["dataset_ids"], now
            )
            conn.commit()
        return self.get_template(user_id, template_id)

    def update_template(
        self, user_id: int, template_id: str, payload: Dict
    ) -> Optional[Dict]:
        if self.get_template(user_id, template_id) is None:
            return None
        values = self._validate_template(user_id, payload)
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                UPDATE backtest_templates SET
                    template_name = ?, visibility = ?, strategy_id = ?, description = ?,
                    initial_capital = ?, position_sizing_mode = ?,
                    fixed_volume = ?, risk_percent = ?, spread_points = ?,
                    slippage_points = ?, commission_per_lot = ?,
                    max_positions = ?, max_same_direction = ?,
                    use_strategy_exits = ?, updated_at = ?
                WHERE user_id = ? AND template_id = ?
                """,
                (
                    values["template_name"],
                    values["visibility"],
                    values["strategy_id"],
                    values["description"],
                    values["initial_capital"],
                    values["position_sizing_mode"],
                    values["fixed_volume"],
                    values["risk_percent"],
                    values["spread_points"],
                    values["slippage_points"],
                    values["commission_per_lot"],
                    values["max_positions"],
                    values["max_same_direction"],
                    int(values["use_strategy_exits"]),
                    now,
                    user_id,
                    template_id,
                ),
            )
            self._replace_template_datasets(
                conn, template_id, values["dataset_ids"], now
            )
            conn.commit()
        return self.get_template(user_id, template_id)

    def delete_template(self, user_id: int, template_id: str) -> bool:
        if self.get_template(user_id, template_id) is None:
            return False
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                UPDATE backtest_batches SET template_id = NULL
                WHERE user_id = ? AND template_id = ?
                """,
                (user_id, template_id),
            )
            conn.execute(
                "DELETE FROM backtest_templates WHERE user_id = ? AND template_id = ?",
                (user_id, template_id),
            )
            conn.commit()
        return True

    def run_template(self, user_id: int, template_id: str) -> Dict:
        template_row = self.storage.fetchone(
            """
            SELECT t.*, u.username AS creator_username
            FROM backtest_templates t
            JOIN users u ON u.id = t.user_id
            WHERE t.template_id = ? AND t.user_id = ?
            """,
            (template_id, user_id),
        )
        if template_row is None:
            raise ValueError("回测模板不存在")
        template_owner_id = int(template_row["user_id"])
        template = self._template_to_dict(template_row, user_id)
        strategy = self.strategies.get_strategy_by_id(
            template_owner_id, template["strategy_id"]
        )
        if strategy is None:
            raise ValueError("模板关联的策略已不存在")

        datasets = []
        for dataset_id in template["dataset_ids"]:
            dataset = self._get_runnable_dataset(user_id, dataset_id)
            if dataset is None:
                raise ValueError("模板包含已不可见、已删除或尚未就绪的数据集")
            if dataset["symbol"] != strategy.symbol:
                raise ValueError(
                    f"数据集 {dataset['dataset_name']} 的品种与策略不一致"
                )
            datasets.append(dataset)
        if not datasets:
            raise ValueError("模板至少需要一个可用数据集")

        strategy_snapshot = strategy.to_dict()
        if not strategy.position_management_policy_id:
            raise ValueError("策略尚未绑定持仓管理方案")
        position_policy = self.position_policies.get(
            int(template_owner_id), strategy.position_management_policy_id
        )
        if position_policy is None or not position_policy.enabled:
            raise ValueError("策略绑定的持仓管理方案不存在或已停用")
        strategy_snapshot["position_management_policy_snapshot"] = (
            position_policy.to_dict()
        )
        strategy_json = self._canonical_json(strategy_snapshot)
        strategy_hash = hashlib.sha256(strategy_json.encode("utf-8")).hexdigest()
        template_snapshot = {
            key: value
            for key, value in template.items()
            if key not in {
                "datasets", "created_at", "updated_at", "is_owner",
                "can_manage", "creator_username", "strategy_name",
                "strategy_symbol", "user_id",
            }
        }
        template_json = self._canonical_json(template_snapshot)
        now = int(time.time())
        batch_id = str(uuid.uuid4())[:12]
        batch_name = f"{template['template_name']} #{time.strftime('%Y%m%d-%H%M%S')}"

        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO backtest_batches(
                    batch_id, template_id, user_id, batch_name, status,
                    task_count, strategy_id, strategy_name,
                    strategy_snapshot_json, strategy_snapshot_hash,
                    template_snapshot_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    template_id,
                    user_id,
                    batch_name,
                    BacktestTaskStatus.QUEUED,
                    len(datasets),
                    strategy.strategy_id,
                    strategy.strategy_name,
                    strategy_json,
                    strategy_hash,
                    template_json,
                    now,
                ),
            )
            for dataset in datasets:
                public_snapshot = {
                    key: dataset.get(key)
                    for key in (
                        "dataset_id",
                        "dataset_name",
                        "symbol",
                        "timeframe",
                        "requested_start",
                        "requested_end",
                        "warmup_start",
                        "received_bars",
                        "quality_score",
                        "data_format",
                        "data_hash",
                        "creator_username",
                    )
                }
                conn.execute(
                    """
                    INSERT INTO backtest_tasks(
                        task_id, batch_id, user_id, dataset_id, status,
                        dataset_file_path, dataset_snapshot_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4())[:12],
                        batch_id,
                        user_id,
                        dataset["dataset_id"],
                        BacktestTaskStatus.QUEUED,
                        dataset["file_path"],
                        self._canonical_json(public_snapshot),
                        now,
                    ),
                )
            conn.commit()
        return self.get_batch(user_id, batch_id)

    def list_batches(self, user_id: int) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT * FROM backtest_batches
            WHERE user_id = ? ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [self._batch_to_dict(row, include_tasks=False) for row in rows]

    def get_batch(self, user_id: int, batch_id: str) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT * FROM backtest_batches
            WHERE user_id = ? AND batch_id = ?
            """,
            (user_id, batch_id),
        )
        return self._batch_to_dict(row, include_tasks=True) if row else None

    def cancel_task(self, user_id: int, task_id: str) -> Optional[Dict]:
        from backtest_engine import BacktestTaskRepository

        return BacktestTaskRepository(self.storage).request_cancel_task(
            user_id, task_id
        )

    def cancel_batch(self, user_id: int, batch_id: str) -> Optional[Dict]:
        from backtest_engine import BacktestTaskRepository

        result = BacktestTaskRepository(self.storage).request_cancel_batch(
            user_id, batch_id
        )
        return self.get_batch(user_id, batch_id) if result else None

    def get_task_ledger(self, user_id: int, task_id: str) -> Optional[Dict]:
        task = self.storage.fetchone(
            """
            SELECT task_id, status FROM backtest_tasks
            WHERE user_id = ? AND task_id = ?
            """,
            (user_id, task_id),
        )
        if task is None:
            return None

        account_row = self.storage.fetchone(
            "SELECT * FROM backtest_accounts WHERE task_id = ?",
            (task_id,),
        )
        order_rows = self.storage.fetchall(
            """
            SELECT * FROM backtest_orders
            WHERE task_id = ? ORDER BY requested_at, order_id
            """,
            (task_id,),
        )
        position_rows = self.storage.fetchall(
            """
            SELECT * FROM backtest_positions
            WHERE task_id = ? ORDER BY opened_at, position_id
            """,
            (task_id,),
        )
        trade_rows = self.storage.fetchall(
            """
            SELECT * FROM backtest_trades
            WHERE task_id = ? ORDER BY closed_at, trade_id
            """,
            (task_id,),
        )
        equity_rows = self.storage.fetchall(
            """
            SELECT point_time AS time, balance, equity, open_positions
            FROM backtest_equity_points
            WHERE task_id = ? ORDER BY point_time
            """,
            (task_id,),
        )
        replay_rows = self.storage.fetchall(
            """
            SELECT bar_time AS time, end_time, open, high, low, close,
                   tick_volume, bar_count
            FROM backtest_replay_bars
            WHERE task_id = ? ORDER BY bar_time
            """,
            (task_id,),
        )
        orders = []
        for row in order_rows:
            item = dict(row)
            item["contributing_sources"] = json.loads(
                item.pop("contributing_sources_json") or "[]"
            )
            orders.append(item)
        return {
            "task_id": task_id,
            "status": task["status"],
            "account": dict(account_row) if account_row else None,
            "orders": orders,
            "positions": [dict(row) for row in position_rows],
            "trades": [dict(row) for row in trade_rows],
            "equity_curve": [dict(row) for row in equity_rows],
            "replay_bars": [dict(row) for row in replay_rows],
        }

    def _validate_template(self, user_id: int, payload: Dict) -> Dict:
        name = str(payload.get("template_name", "")).strip()
        strategy_id = str(payload.get("strategy_id", "")).strip()
        raw_dataset_ids = payload.get("dataset_ids") or []
        if not isinstance(raw_dataset_ids, list):
            raise ValueError("历史数据集参数格式无效")
        dataset_ids = list(dict.fromkeys(
            str(item).strip() for item in raw_dataset_ids if str(item).strip()
        ))
        if not name:
            raise ValueError("请输入模板名称")
        if len(name) > 100:
            raise ValueError("模板名称不能超过 100 个字符")
        strategy = self.strategies.get_strategy_by_id(user_id, strategy_id)
        if strategy is None:
            raise ValueError("请选择当前用户的有效策略")
        if not dataset_ids:
            raise ValueError("请至少选择一个可用数据集")
        for dataset_id in dataset_ids:
            dataset = self._get_runnable_dataset(user_id, str(dataset_id))
            if dataset is None:
                raise ValueError("只能选择自己可见且已经就绪的数据集")
            if dataset["symbol"] != strategy.symbol:
                raise ValueError(
                    f"数据集 {dataset['dataset_name']} 与策略品种 {strategy.symbol} 不一致"
                )

        mode = str(payload.get("position_sizing_mode", "strategy"))
        if mode not in self.POSITION_MODES:
            raise ValueError("仓位模式无效")
        values = {
            "template_name": name,
            "visibility": "private",
            "strategy_id": strategy_id,
            "dataset_ids": [str(item) for item in dataset_ids],
            "description": str(payload.get("description", "")).strip()[:500],
            "initial_capital": float(payload.get("initial_capital", 100000)),
            "position_sizing_mode": mode,
            "fixed_volume": float(payload.get("fixed_volume", strategy.fixed_volume)),
            "risk_percent": float(payload.get("risk_percent", strategy.risk_percent)),
            "spread_points": float(payload.get("spread_points", 0)),
            "slippage_points": float(payload.get("slippage_points", 0)),
            "commission_per_lot": float(payload.get("commission_per_lot", 0)),
            "max_positions": int(payload.get("max_positions", strategy.max_positions)),
            "max_same_direction": int(
                payload.get("max_same_direction", strategy.max_same_direction)
            ),
            "use_strategy_exits": bool(payload.get("use_strategy_exits", True)),
        }
        if values["initial_capital"] <= 0:
            raise ValueError("初始资金必须大于 0")
        if values["fixed_volume"] <= 0:
            raise ValueError("固定手数必须大于 0")
        if not 0 < values["risk_percent"] <= 100:
            raise ValueError("风险比例必须在 0 到 100 之间")
        if any(values[key] < 0 for key in (
            "spread_points", "slippage_points", "commission_per_lot"
        )):
            raise ValueError("点差、滑点和手续费不能为负数")
        if not 1 <= values["max_positions"] <= 100:
            raise ValueError("最大持仓数必须在 1 到 100 之间")
        if not 1 <= values["max_same_direction"] <= values["max_positions"]:
            raise ValueError("同向最大持仓必须在 1 到最大持仓数之间")
        return values

    def _get_runnable_dataset(
        self, user_id: int, dataset_id: str
    ) -> Optional[Dict]:
        row = self.storage.fetchone(
            """
            SELECT d.*, u.username AS creator_username
            FROM backtest_datasets d
            JOIN users u ON u.id = d.user_id
            WHERE d.dataset_id = ? AND d.status = ?
              AND (d.user_id = ? OR d.visibility = 'shared')
            """,
            (dataset_id, DatasetStatus.READY, user_id),
        )
        return dict(row) if row else None

    def _template_to_dict(self, row, user_id: int) -> Dict:
        data = dict(row)
        data["use_strategy_exits"] = bool(data["use_strategy_exits"])
        links = self.storage.fetchall(
            """
            SELECT dataset_id FROM backtest_template_datasets
            WHERE template_id = ? ORDER BY created_at, dataset_id
            """,
            (data["template_id"],),
        )
        data["dataset_ids"] = [item["dataset_id"] for item in links]
        data["datasets"] = []
        for dataset_id in data["dataset_ids"]:
            visible = self.datasets.get_visible(user_id, dataset_id)
            data["datasets"].append(
                visible or {"dataset_id": dataset_id, "available": False}
            )
        strategy = self.strategies.get_strategy_by_id(
            int(data["user_id"]), data["strategy_id"]
        )
        data["strategy_name"] = strategy.strategy_name if strategy else "策略已删除"
        data["strategy_symbol"] = strategy.symbol if strategy else ""
        data["is_owner"] = int(data["user_id"]) == int(user_id)
        data["can_manage"] = data["is_owner"]
        if not data["is_owner"]:
            data.pop("user_id", None)
        return data

    def _batch_to_dict(self, row, include_tasks: bool) -> Dict:
        data = dict(row)
        llm_usage = self.storage.fetchone(
            """
            SELECT COALESCE(SUM(llm_analysis_count), 0) AS analysis_count,
                   COALESCE(SUM(llm_call_count), 0) AS call_count,
                   COALESCE(SUM(llm_cache_hits), 0) AS cache_hits
            FROM backtest_tasks WHERE batch_id = ?
            """,
            (data["batch_id"],),
        )
        data["llm_analysis_count"] = int(llm_usage["analysis_count"])
        data["llm_call_count"] = int(llm_usage["call_count"])
        data["llm_cache_hits"] = int(llm_usage["cache_hits"])
        canceling = self.storage.fetchone(
            """
            SELECT COUNT(*) AS count FROM backtest_tasks
            WHERE batch_id = ? AND status = 'running' AND cancel_requested = 1
            """,
            (data["batch_id"],),
        )
        data["cancel_requested"] = bool(canceling and canceling["count"])
        data["strategy_snapshot"] = json.loads(data.pop("strategy_snapshot_json"))
        data["template_snapshot"] = json.loads(data.pop("template_snapshot_json"))
        data["strategy_snapshot_hash"] = data["strategy_snapshot_hash"][:12]
        if include_tasks:
            task_rows = self.storage.fetchall(
                """
                SELECT task_id, batch_id, dataset_id, status, progress,
                       llm_analysis_count, llm_call_count, llm_cache_hits,
                       dataset_snapshot_json, result_json, error_message,
                       engine_version, worker_id, heartbeat_at, cancel_requested,
                       created_at, started_at, completed_at
                FROM backtest_tasks WHERE batch_id = ? ORDER BY created_at, task_id
                """,
                (data["batch_id"],),
            )
            data["tasks"] = []
            for task_row in task_rows:
                task = dict(task_row)
                task["cancel_requested"] = bool(task["cancel_requested"])
                task["dataset"] = json.loads(task.pop("dataset_snapshot_json"))
                task["result"] = json.loads(task.pop("result_json"))
                analysis_row = self.storage.fetchone(
                    """
                    SELECT task_id, status, model, prompt_hash, result_json,
                           error_message, created_at, updated_at, completed_at
                    FROM backtest_ai_analyses WHERE task_id = ?
                    """,
                    (task["task_id"],),
                )
                if analysis_row:
                    task["ai_analysis"] = dict(analysis_row)
                    task["ai_analysis"]["result"] = json.loads(
                        task["ai_analysis"].pop("result_json") or "{}"
                    )
                else:
                    task["ai_analysis"] = {"task_id": task["task_id"], "status": "idle", "result": {}}
                data["tasks"].append(task)
        return data

    @staticmethod
    def _replace_template_datasets(conn, template_id, dataset_ids, now) -> None:
        conn.execute(
            "DELETE FROM backtest_template_datasets WHERE template_id = ?",
            (template_id,),
        )
        conn.executemany(
            """
            INSERT INTO backtest_template_datasets(template_id, dataset_id, created_at)
            VALUES(?, ?, ?)
            """,
            [(template_id, dataset_id, now) for dataset_id in dataset_ids],
        )

    @staticmethod
    def _canonical_json(payload: Dict) -> str:
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
