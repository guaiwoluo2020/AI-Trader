#!/usr/bin/env python3
"""基于回测和模拟盘事实数据的策略准入检查。"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

from sqlite_storage import SQLiteStorage, get_storage


def strategy_fingerprint(strategy: Dict) -> str:
    """只对影响交易行为的策略字段计算版本指纹。"""
    ignored = {
        "strategy_name", "lifecycle_status", "lifecycle_label",
        "lifecycle_updated_at", "lifecycle_history", "created_at", "updated_at",
        "enabled",
    }
    payload = {key: value for key, value in strategy.items() if key not in ignored}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StrategyAdmissionService:
    MIN_TRADES = 20
    MIN_PROFIT_FACTOR = 1.1
    MAX_DRAWDOWN_PCT = 20.0

    def __init__(self, paper_trading, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.paper_trading = paper_trading

    def evaluate(self, user_id: int, strategy) -> Dict:
        strategy_data = strategy.to_dict()
        backtest = self._backtest_evidence(user_id, strategy_data)
        paper = self._paper_evidence(user_id, strategy.strategy_id, strategy_data)
        direct_paper_strategy = self._has_enabled_direct_paper_signal(
            strategy_data
        )
        return {
            "strategy_id": strategy.strategy_id,
            "strategy_name": strategy.strategy_name,
            "lifecycle_status": strategy.lifecycle_status,
            "thresholds": {
                "min_trades": self.MIN_TRADES,
                "min_profit_factor": self.MIN_PROFIT_FACTOR,
                "max_drawdown_pct": self.MAX_DRAWDOWN_PCT,
                "net_profit_must_be_positive": True,
                "ai_only_backtest_skips_trade_count": True,
            },
            "backtest": backtest,
            "paper": paper,
            "ai_strategy": self._has_enabled_ai_signal(strategy_data),
            "direct_paper_strategy": direct_paper_strategy,
            "eligible_for_paper": backtest["passed"] or direct_paper_strategy,
            "eligible_for_production": (
                paper["passed"] if direct_paper_strategy
                else backtest["passed"] and paper["passed"]
            ),
        }

    def validate_transition(self, user_id: int, strategy, target_status: str) -> Dict:
        admission = self.evaluate(user_id, strategy)
        if target_status == "backtest_passed" and not admission["backtest"]["passed"]:
            raise ValueError("当前策略版本尚未通过回测准入，请先完成满足门槛的回测")
        if target_status == "paper_trading" and not admission["eligible_for_paper"]:
            raise ValueError(
                "当前策略版本缺少有效回测证据，不能进入模拟盘验证；"
                "包含 AI、转折点或整数点位信号源的策略可直接模拟观察"
            )
        if target_status == "production" and not admission["eligible_for_production"]:
            raise ValueError("策略尚未通过模拟盘准入，不能批准用于实盘")
        return admission

    def _backtest_evidence(self, user_id: int, strategy_data: Dict) -> Dict:
        rows = self.storage.fetchall(
            """
            SELECT t.task_id, t.completed_at, t.result_json,
                   b.batch_id, b.strategy_snapshot_json
            FROM backtest_tasks t
            JOIN backtest_batches b ON b.batch_id = t.batch_id
            WHERE t.user_id = ? AND b.strategy_id = ? AND t.status = 'completed'
            ORDER BY t.completed_at DESC
            """,
            (user_id, strategy_data.get("strategy_id")),
        )
        current_fingerprint = strategy_fingerprint(strategy_data)
        matched = None
        for row in rows:
            snapshot = json.loads(row["strategy_snapshot_json"] or "{}")
            if strategy_fingerprint(snapshot) == current_fingerprint:
                matched = row
                break
        if matched is None:
            return self._empty_evidence("当前策略版本没有已完成的回测任务")
        result = json.loads(matched["result_json"] or "{}")
        checks = self._checks(
            result.get("trade_count", 0), result.get("net_profit", 0),
            result.get("profit_factor"), result.get("max_drawdown_pct", 0),
            skip_trade_count=self._is_ai_only_backtest(result),
        )
        return {
            "passed": all(item["passed"] for item in checks),
            "message": "回测指标满足准入门槛" if all(item["passed"] for item in checks)
            else "回测指标尚未全部达到准入门槛",
            "task_id": matched["task_id"],
            "batch_id": matched["batch_id"],
            "completed_at": matched["completed_at"],
            "metrics": self._metrics(result),
            "checks": checks,
        }

    def _paper_evidence(
        self, user_id: int, strategy_id: str, strategy_data: Dict
    ) -> Dict:
        account = self.storage.fetchone(
            """
            SELECT a.id, d.strategy_version_at FROM trading_accounts a
            JOIN strategy_deployments d ON d.account_id = a.id
            WHERE d.user_id = ? AND d.strategy_id = ?
              AND a.account_type = 'paper'
            ORDER BY d.updated_at DESC LIMIT 1
            """,
            (user_id, strategy_id),
        )
        if account is None:
            return self._empty_evidence("策略尚未部署到模拟账户")
        report = self.paper_trading.build_report(
            user_id, int(account["id"]), strategy_id,
            started_at=int(account["strategy_version_at"] or 0),
        )
        metrics = report["summary"]
        checks = self._checks(
            metrics["trade_count"], metrics["net_profit"],
            metrics["profit_factor"], metrics["max_drawdown_pct"],
        )
        return {
            "passed": all(item["passed"] for item in checks),
            "message": "模拟盘指标满足实盘准入门槛" if all(item["passed"] for item in checks)
            else "模拟盘指标尚未全部达到实盘准入门槛",
            "account_id": int(account["id"]),
            "metrics": metrics,
            "checks": checks,
        }

    @staticmethod
    def _has_enabled_ai_signal(strategy_data: Dict) -> bool:
        return any(
            source.get("source") == "ai_entry"
            and source.get("enabled", True)
            for source in (strategy_data.get("signal_sources") or [])
        )

    @staticmethod
    def _has_enabled_direct_paper_signal(strategy_data: Dict) -> bool:
        return any(
            source.get("source") in {"ai_entry", "pivot", "key_level", "structure_plan"}
            and source.get("enabled", True)
            for source in (strategy_data.get("signal_sources") or [])
        ) and strategy_data.get("lifecycle_status") != "retired"

    @staticmethod
    def _is_ai_only_backtest(result: Dict) -> bool:
        sources = result.get("enabled_signal_sources") or []
        if not sources and result.get("signal_source_trade_counts"):
            sources = list(result["signal_source_trade_counts"])
        return bool(sources) and set(sources) == {"ai_entry"}

    def _checks(
        self, trade_count, net_profit, profit_factor, drawdown,
        skip_trade_count: bool = False,
    ) -> list:
        effective_factor = float("inf") if profit_factor is None and net_profit > 0 else float(profit_factor or 0)
        return [
            {
                "key": "trade_count",
                "label": (
                    "交易次数：AI 专属回测不强制要求"
                    if skip_trade_count else f"交易次数 >= {self.MIN_TRADES}"
                ),
                "passed": True if skip_trade_count else int(trade_count) >= self.MIN_TRADES,
                "skipped": bool(skip_trade_count),
            },
            {"key": "net_profit", "label": "净利润 > 0", "passed": float(net_profit) > 0},
            {"key": "profit_factor", "label": f"收益因子 >= {self.MIN_PROFIT_FACTOR}", "passed": effective_factor >= self.MIN_PROFIT_FACTOR},
            {"key": "max_drawdown_pct", "label": f"最大回撤 <= {self.MAX_DRAWDOWN_PCT}%", "passed": float(drawdown or 0) <= self.MAX_DRAWDOWN_PCT},
        ]

    @staticmethod
    def _metrics(result: Dict) -> Dict:
        return {
            "trade_count": int(result.get("trade_count", 0)),
            "net_profit": float(result.get("net_profit", 0)),
            "profit_factor": result.get("profit_factor"),
            "max_drawdown_pct": float(result.get("max_drawdown_pct", 0)),
            "win_rate": float(result.get("win_rate", result.get("win_rate_pct", 0))),
        }

    @staticmethod
    def _empty_evidence(message: str) -> Dict:
        return {"passed": False, "message": message, "metrics": {}, "checks": []}
