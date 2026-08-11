#!/usr/bin/env python3
"""Asynchronous, user-scoped AI analysis for finished backtest tasks."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Callable, Dict, List, Optional

from market.services.llm_service import LLMRequestError, LLMService
from market.models import TradingStrategy
from market.store.llm_store import LLMStore
from sqlite_storage import SQLiteStorage, get_storage
from llm_governance import BACKTEST_REPORT_ANALYSIS


class BacktestAIAnalysisService:
    ALLOWED_TASK_STATUSES = {"completed", "canceled"}
    ACTIVE_STATUSES = {"queued", "running"}

    def __init__(
        self,
        storage: Optional[SQLiteStorage] = None,
        llm_factory: Optional[Callable[[int], LLMService]] = None,
    ):
        self.storage = storage or get_storage()
        self._llm_factory = llm_factory or self._create_llm_service
        self._lock = threading.RLock()
        self._running_task_ids = set()

    @staticmethod
    def _create_llm_service(user_id: int) -> LLMService:
        return LLMService(LLMStore(user_id=user_id), None)

    def get_analysis(self, user_id: int, task_id: str) -> Optional[Dict]:
        task = self._get_owned_task(user_id, task_id)
        if task is None:
            return None
        row = self.storage.fetchone(
            """
            SELECT task_id, status, model, prompt_hash, result_json,
                   error_message, created_at, updated_at, completed_at
            FROM backtest_ai_analyses
            WHERE user_id = ? AND task_id = ?
            """,
            (user_id, task_id),
        )
        return self._row_to_dict(row) if row else self.empty_analysis(task_id)

    def start_analysis(
        self, user_id: int, user_role: str, task_id: str, regenerate: bool = False
    ) -> Dict:
        task = self._get_owned_task(user_id, task_id)
        if task is None:
            raise LookupError("回测任务不存在")
        if task["status"] not in self.ALLOWED_TASK_STATUSES:
            raise ValueError("只有已完成或已取消的回测任务可以进行 AI 分析")
        llm_service = self._llm_factory(user_id)
        governance = getattr(llm_service, "_llm_governance", None)
        if governance is not None:
            scene = governance.scene_options(
                user_id, BACKTEST_REPORT_ANALYSIS
            )
            if user_role != "admin" and scene["quota"]["remaining"] <= 0:
                from llm_governance import LLMQuotaExceeded
                raise LLMQuotaExceeded(
                    f"今日免费大模型调用额度（{scene['quota']['limit']}次）已用完，明日可继续使用"
                )
            model = scene["default_model_id"]
        else:
            model = llm_service.llm_store.get_config().model

        current = self.get_analysis(user_id, task_id)
        if current["status"] in self.ACTIVE_STATUSES:
            return current
        if current["status"] == "completed" and not regenerate:
            return current

        now = int(time.time())
        self.storage.execute(
            """
            INSERT INTO backtest_ai_analyses(
                task_id, user_id, status, model, prompt_hash, result_json,
                error_message, created_at, updated_at, completed_at
            ) VALUES(?, ?, 'queued', ?, '', '{}', '', ?, ?, NULL)
            ON CONFLICT(task_id) DO UPDATE SET
                status = 'queued', model = excluded.model, prompt_hash = '',
                result_json = '{}', error_message = '', updated_at = excluded.updated_at,
                completed_at = NULL
            """,
            (task_id, user_id, model, now, now),
        )
        with self._lock:
            if task_id not in self._running_task_ids:
                self._running_task_ids.add(task_id)
                threading.Thread(
                    target=self._run,
                    args=(user_id, task_id, llm_service),
                    name=f"backtest-ai-{task_id}",
                    daemon=True,
                ).start()
        return self.get_analysis(user_id, task_id)

    def _run(self, user_id: int, task_id: str, llm_service: LLMService) -> None:
        try:
            self._set_status(user_id, task_id, "running")
            snapshot = self._build_snapshot(user_id, task_id)
            scene = {}
            governance = getattr(llm_service, "_llm_governance", None)
            if governance is not None:
                scene = governance.scene_options(user_id, BACKTEST_REPORT_ANALYSIS)
            prompt = self.build_prompt(
                snapshot, scene.get("user_prompt_template") or ""
            )
            system_prompt = scene.get("system_prompt") or None
            prompt_hash = hashlib.sha256(
                f"{system_prompt or ''}\n{prompt}".encode("utf-8")
            ).hexdigest()
            self.storage.execute(
                """
                UPDATE backtest_ai_analyses SET prompt_hash = ?, updated_at = ?
                WHERE user_id = ? AND task_id = ?
                """,
                (prompt_hash, int(time.time()), user_id, task_id),
            )
            result = llm_service.call_llm_stream(
                prompt,
                system_prompt=system_prompt,
                scene_code=BACKTEST_REPORT_ANALYSIS,
                object_type="backtest_task",
                object_id=task_id,
            )
            normalized = self._normalize_result(result)
            now = int(time.time())
            self.storage.execute(
                """
                UPDATE backtest_ai_analyses
                SET status = 'completed', result_json = ?, error_message = '',
                    updated_at = ?, completed_at = ?
                WHERE user_id = ? AND task_id = ?
                """,
                (
                    json.dumps(normalized, ensure_ascii=False),
                    now,
                    now,
                    user_id,
                    task_id,
                ),
            )
        except Exception as exc:
            message = str(exc) or "大模型分析失败"
            if not isinstance(exc, (LLMRequestError, ValueError)):
                message = f"大模型分析失败: {message}"
            self.storage.execute(
                """
                UPDATE backtest_ai_analyses
                SET status = 'failed', error_message = ?, updated_at = ?, completed_at = ?
                WHERE user_id = ? AND task_id = ?
                """,
                (message[:500], int(time.time()), int(time.time()), user_id, task_id),
            )
        finally:
            with self._lock:
                self._running_task_ids.discard(task_id)

    def _build_snapshot(self, user_id: int, task_id: str) -> Dict:
        task = self.storage.fetchone(
            """
            SELECT t.task_id, t.status, t.progress, t.result_json,
                   t.dataset_snapshot_json, t.started_at, t.completed_at,
                   b.strategy_name, b.strategy_snapshot_json,
                   b.template_snapshot_json
            FROM backtest_tasks t
            JOIN backtest_batches b ON b.batch_id = t.batch_id
            WHERE t.user_id = ? AND t.task_id = ?
            """,
            (user_id, task_id),
        )
        if task is None:
            raise ValueError("回测任务不存在")
        trades = [dict(row) for row in self.storage.fetchall(
            """
            SELECT direction, volume, entry_price, exit_price, net_profit,
                   exit_reason, opened_at, closed_at
            FROM backtest_trades WHERE user_id = ? AND task_id = ?
            ORDER BY closed_at, trade_id
            """,
            (user_id, task_id),
        )]
        equity = [dict(row) for row in self.storage.fetchall(
            """
            SELECT point_time AS time, balance, equity, open_positions
            FROM backtest_equity_points WHERE task_id = ? ORDER BY point_time
            """,
            (task_id,),
        )]
        return {
            "task": {
                "task_id": task["task_id"],
                "status": task["status"],
                "progress": task["progress"],
                "started_at": task["started_at"],
                "completed_at": task["completed_at"],
            },
            "strategy_name": task["strategy_name"],
            "strategy_snapshot": self._strategy_for_analysis(
                json.loads(task["strategy_snapshot_json"])
            ),
            "template_snapshot": json.loads(task["template_snapshot_json"]),
            "dataset": json.loads(task["dataset_snapshot_json"]),
            "performance": self._compact_performance(
                json.loads(task["result_json"] or "{}")
            ),
            "trade_sample": self._sample_trades(trades),
            "equity_sample": self._downsample(equity, 120),
            "trade_sample_total": len(trades),
        }

    @classmethod
    def build_prompt(cls, snapshot: Dict, template: str = "") -> str:
        from llm_governance import BACKTEST_REPORT_PROMPT_TEMPLATE
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        return (template or BACKTEST_REPORT_PROMPT_TEMPLATE).replace(
            "{{backtest_snapshot}}", payload
        )

    @staticmethod
    def _strategy_for_analysis(snapshot: Dict) -> Dict:
        """Remove legacy and inactive signal configs before prompting the LLM."""
        model = TradingStrategy.from_dict(snapshot)
        cleaned = dict(snapshot)
        for legacy_key in (
            "signal_config", "signal_weights", "period_weights",
        ):
            cleaned.pop(legacy_key, None)
        cleaned["signal_sources"] = [
            {
                "signal_source_id": source["signal_source_id"],
                "source": source["source"],
                "period": source["period"],
                "weight": int(source["weight"]),
                "params": dict(source.get("params") or {}),
            }
            for source in model.get_signal_sources(enabled_only=True)
        ]
        return cleaned

    @staticmethod
    def _sample_trades(trades: List[Dict]) -> List[Dict]:
        if len(trades) <= 100:
            return trades
        worst = sorted(trades, key=lambda item: item.get("net_profit", 0))[:25]
        best = sorted(trades, key=lambda item: item.get("net_profit", 0), reverse=True)[:25]
        recent = trades[-50:]
        unique = {}
        for trade in worst + best + recent:
            key = (trade.get("opened_at"), trade.get("closed_at"), trade.get("entry_price"))
            unique[key] = trade
        return list(unique.values())[:100]

    @staticmethod
    def _compact_performance(result: Dict) -> Dict:
        compact = dict(result)
        for key in ("equity_curve", "drawdown_curve", "orders", "trades", "replay_bars"):
            compact.pop(key, None)
        return compact

    @staticmethod
    def _downsample(items: List[Dict], limit: int) -> List[Dict]:
        if len(items) <= limit:
            return items
        step = (len(items) - 1) / (limit - 1)
        return [items[round(index * step)] for index in range(limit)]

    @staticmethod
    def _normalize_result(result: Optional[Dict]) -> Dict:
        if not isinstance(result, dict):
            raise ValueError("大模型没有返回有效的分析结果")
        return {
            "executive_summary": str(result.get("executive_summary", "")).strip(),
            "data_quality": result.get("data_quality") if isinstance(result.get("data_quality"), dict) else {},
            "diagnosis": result.get("diagnosis") if isinstance(result.get("diagnosis"), list) else [],
            "optimization_suggestions": result.get("optimization_suggestions") if isinstance(result.get("optimization_suggestions"), list) else [],
            "risk_warnings": result.get("risk_warnings") if isinstance(result.get("risk_warnings"), list) else [],
            "next_backtest_plan": result.get("next_backtest_plan") if isinstance(result.get("next_backtest_plan"), dict) else {},
        }

    def _get_owned_task(self, user_id: int, task_id: str):
        return self.storage.fetchone(
            "SELECT task_id, status FROM backtest_tasks WHERE user_id = ? AND task_id = ?",
            (user_id, task_id),
        )

    def _set_status(self, user_id: int, task_id: str, status: str) -> None:
        self.storage.execute(
            """
            UPDATE backtest_ai_analyses SET status = ?, updated_at = ?
            WHERE user_id = ? AND task_id = ?
            """,
            (status, int(time.time()), user_id, task_id),
        )

    @staticmethod
    def empty_analysis(task_id: str) -> Dict:
        return {
            "task_id": task_id, "status": "idle", "model": "",
            "prompt_hash": "", "result": {}, "error_message": "",
            "created_at": None, "updated_at": None, "completed_at": None,
        }

    @staticmethod
    def _row_to_dict(row) -> Dict:
        data = dict(row)
        data["result"] = json.loads(data.pop("result_json") or "{}")
        return data
