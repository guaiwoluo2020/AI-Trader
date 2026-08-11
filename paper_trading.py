#!/usr/bin/env python3
"""实时模拟账户部署、订单撮合与资金核算。"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from market.models import PositionManagementPolicy, TradingStrategy
from market.services.position_manager import PositionManager
from sqlite_storage import (
    PositionManagementPolicyRepository, SQLiteStorage,
    StrategyConfigRepository, TradingAccountRepository, get_storage,
)
from strategy_admission import StrategyAdmissionService, strategy_fingerprint


def market_spec(symbol: str) -> Tuple[float, float]:
    upper = str(symbol or "").upper()
    if "GOLD" in upper or "XAU" in upper:
        return 0.01, 100.0
    if "JPY" in upper:
        return 0.001, 100000.0
    if len(upper.rstrip("#._")) == 6:
        return 0.00001, 100000.0
    return 0.01, 1.0


class PaperTradingService:
    """以 EA Tick 驱动的持久化模拟撮合器。"""

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)
        self.position_manager = PositionManager()
        self._lock = threading.RLock()
        self._quotes: Dict[Tuple[int, str], Tuple[float, float]] = {}

    def list_context(self, user_id: int) -> Dict:
        strategies = self.storage.fetchall(
            """
            SELECT strategy_id, symbol, config_json
            FROM user_strategy_configs
            WHERE user_id = ? ORDER BY updated_at DESC
            """,
            (user_id,),
        )
        return {
            "strategies": [{
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "strategy_name": json.loads(row["config_json"]).get(
                    "strategy_name", row["strategy_id"]
                ),
                "enabled": bool(json.loads(row["config_json"]).get("enabled", True)),
                "auto_execute": bool(
                    json.loads(row["config_json"]).get("auto_execute", False)
                ),
                "lifecycle_status": json.loads(row["config_json"]).get(
                    "lifecycle_status", "production"
                ),
                "paper_eligible": json.loads(row["config_json"]).get(
                    "lifecycle_status", "production"
                ) in {"backtest_passed", "paper_trading", "production"},
                "live_eligible": json.loads(row["config_json"]).get(
                    "lifecycle_status", "production"
                ) == "production",
            } for row in strategies],
        }

    def deploy(
        self, user_id: int, account_id: int, strategy_id: str,
        strategy_snapshot: Optional[Dict] = None,
        source_backtest_task_id: str = "",
        duration_days: Optional[int] = None,
    ) -> Dict:
        account = self._account(user_id, account_id)
        current_strategy = TradingStrategy.from_dict(
            self._strategy_config(user_id, strategy_id)
        ).to_dict()
        strategy = TradingStrategy.from_dict(
            strategy_snapshot or current_strategy
        ).to_dict()
        if strategy_fingerprint(strategy) != strategy_fingerprint(current_strategy):
            raise ValueError("回测策略快照与当前策略版本不一致，请重新回测")
        policy_id = str(strategy.get("position_management_policy_id", ""))
        policy = self.position_policies.get(user_id, policy_id)
        if policy is None or not policy.enabled:
            raise ValueError("策略必须绑定一个已启用的持仓管理方案")
        strategy["position_management_policy_snapshot"] = policy.to_dict()
        lifecycle = strategy.get("lifecycle_status", "production")
        if account.account_type == "paper":
            if lifecycle not in {"backtest_passed", "paper_trading", "production"}:
                raise ValueError("策略通过回测后才能部署到模拟账户")
            execution_mode = "paper"
        elif account.account_type == "mt5":
            if lifecycle != "production":
                raise ValueError("只有已批准用于实盘的策略才能绑定 MT5 账户")
            execution_mode = "live"
        else:
            raise ValueError("当前账户类型不支持策略部署")
        now = int(time.time())
        scheduled_end_at = None
        if duration_days is not None:
            duration_days = int(duration_days)
            if duration_days < 1 or duration_days > 365:
                raise ValueError("模拟运行期限必须在 1 至 365 天之间")
            scheduled_end_at = now + duration_days * 86400
        deployment_id = uuid.uuid4().hex[:12]
        snapshot_json = json.dumps(
            strategy, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO strategy_deployments(
                    deployment_id, user_id, account_id, strategy_id, symbol,
                    strategy_snapshot_hash, strategy_snapshot_json,
                    source_backtest_task_id, strategy_version_at, scheduled_end_at,
                    execution_mode, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(account_id, strategy_id) DO UPDATE SET
                    symbol = excluded.symbol, status = 'active',
                    execution_mode = excluded.execution_mode,
                    strategy_version_at = CASE
                        WHEN strategy_deployments.strategy_snapshot_hash
                             != excluded.strategy_snapshot_hash
                        THEN excluded.strategy_version_at
                        ELSE strategy_deployments.strategy_version_at
                    END,
                    strategy_snapshot_hash = excluded.strategy_snapshot_hash,
                    strategy_snapshot_json = excluded.strategy_snapshot_json,
                    source_backtest_task_id = excluded.source_backtest_task_id,
                    scheduled_end_at = excluded.scheduled_end_at,
                    updated_at = excluded.updated_at
                """,
                (
                    deployment_id, user_id, account.account_id, strategy_id,
                    strategy["symbol"], strategy_fingerprint(strategy), snapshot_json,
                    source_backtest_task_id, now, scheduled_end_at,
                    execution_mode, now, now,
                ),
            )
            conn.commit()
        row = self.storage.fetchone(
            """
            SELECT * FROM strategy_deployments
            WHERE account_id = ? AND strategy_id = ?
            """,
            (account_id, strategy_id),
        )
        return dict(row)

    def deploy_backtest(
        self, user_id: int, account_id: int, task_id: str, duration_days: int = 30
    ) -> Dict:
        task = self.storage.fetchone(
            """
            SELECT t.task_id, t.status, t.result_json, b.strategy_id,
                   b.strategy_snapshot_json
            FROM backtest_tasks t
            JOIN backtest_batches b ON b.batch_id = t.batch_id
            WHERE t.task_id = ? AND t.user_id = ?
            """,
            (task_id, user_id),
        )
        if task is None:
            raise ValueError("回测任务不存在")
        if task["status"] != "completed":
            raise ValueError("只有已完成的回测任务才能部署到模拟账户")
        result = json.loads(task["result_json"] or "{}")
        if not result:
            raise ValueError("回测任务没有有效报告")
        snapshot = TradingStrategy.from_dict(
            json.loads(task["strategy_snapshot_json"] or "{}")
        ).to_dict()
        current = TradingStrategy.from_dict(
            self._strategy_config(user_id, task["strategy_id"])
        ).to_dict()
        if strategy_fingerprint(snapshot) != strategy_fingerprint(current):
            raise ValueError("回测策略快照与当前策略版本不一致，请重新回测")
        lifecycle = current.get("lifecycle_status", "draft")
        if lifecycle == "backtesting":
            strategy_model = TradingStrategy.from_dict(current)
            admission = StrategyAdmissionService(self, self.storage)
            checks = admission._checks(
                result.get("trade_count", 0), result.get("net_profit", 0),
                result.get("profit_factor"), result.get("max_drawdown_pct", 0),
                skip_trade_count=admission._is_ai_only_backtest(result),
            )
            if not all(item["passed"] for item in checks):
                raise ValueError("本次回测报告尚未达到策略准入门槛")
            strategy_model.transition_lifecycle(
                "backtest_passed", f"回测任务 {task_id} 达到准入门槛"
            )
            StrategyConfigRepository(self.storage).save_strategy(
                user_id, strategy_model
            )
            current = strategy_model.to_dict()
            lifecycle = "backtest_passed"
        if lifecycle not in {"backtest_passed", "paper_trading", "production"}:
            raise ValueError("策略需要先进入回测中，并通过回测准入门槛")
        snapshot["lifecycle_status"] = lifecycle
        return self.deploy(
            user_id, account_id, task["strategy_id"], snapshot,
            source_backtest_task_id=task_id, duration_days=duration_days,
        )

    def set_deployment_status(
        self, user_id: int, account_id: int, deployment_id: str, active: bool
    ) -> Optional[Dict]:
        self._account(user_id, account_id)
        now = int(time.time())
        current = self.storage.fetchone(
            "SELECT scheduled_end_at FROM strategy_deployments WHERE deployment_id = ?",
            (deployment_id,),
        )
        if active and current and current["scheduled_end_at"]:
            if int(current["scheduled_end_at"]) <= now:
                raise ValueError("模拟运行期限已结束，请从回测报告重新部署")
        status = "active" if active else "paused"
        with self.storage._lock, self.storage._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE strategy_deployments SET status = ?, updated_at = ?
                WHERE deployment_id = ? AND user_id = ? AND account_id = ?
                """,
                (status, now, deployment_id, user_id, account_id),
            )
            if cursor.rowcount:
                conn.execute(
                    """
                    UPDATE paper_orders
                    SET status = 'canceled', canceled_at = ?, updated_at = ?,
                        rejection_reason = '策略运行已暂停'
                    WHERE deployment_id = ? AND status = 'pending'
                    """,
                    (now, now, deployment_id),
                )
            conn.commit()
        row = self.storage.fetchone(
            "SELECT * FROM strategy_deployments WHERE deployment_id = ?",
            (deployment_id,),
        )
        return dict(row) if row else None

    def remove_deployment(
        self, user_id: int, account_id: int, deployment_id: str
    ) -> bool:
        self._account(user_id, account_id)
        with self.storage._lock, self.storage._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM strategy_deployments
                WHERE deployment_id = ? AND user_id = ? AND account_id = ?
                """,
                (deployment_id, user_id, account_id),
            )
            conn.commit()
        return cursor.rowcount == 1

    def list_deployments(self, user_id: int, account_id: int) -> List[Dict]:
        self._account(user_id, account_id)
        self._expire_deployments(user_id, account_id)
        return [dict(row) for row in self.storage.fetchall(
            """
            SELECT d.*, json_extract(s.config_json, '$.strategy_name') AS strategy_name,
                   json_extract(s.config_json, '$.lifecycle_status') AS lifecycle_status,
                   json_extract(s.config_json, '$.enabled') AS strategy_enabled
            FROM strategy_deployments d
            LEFT JOIN user_strategy_configs s
              ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            WHERE d.user_id = ? AND d.account_id = ?
            ORDER BY d.created_at DESC
            """,
            (user_id, account_id),
        )]

    def _expire_deployments(self, user_id: int, account_id: Optional[int] = None) -> None:
        now = int(time.time())
        where = "user_id = ? AND status = 'active' AND scheduled_end_at IS NOT NULL AND scheduled_end_at <= ?"
        params: List = [user_id, now]
        if account_id is not None:
            where += " AND account_id = ?"
            params.append(account_id)
        with self.storage._lock, self.storage._connect() as conn:
            rows = conn.execute(
                f"SELECT deployment_id FROM strategy_deployments WHERE {where}", params
            ).fetchall()
            if not rows:
                return
            ids = [row["deployment_id"] for row in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE strategy_deployments SET status = 'completed', updated_at = ? WHERE deployment_id IN ({placeholders})",
                [now, *ids],
            )
            conn.execute(
                f"""
                UPDATE paper_orders SET status = 'canceled', canceled_at = ?,
                    updated_at = ?, rejection_reason = '模拟运行期限已结束'
                WHERE status = 'pending' AND deployment_id IN ({placeholders})
                """,
                [now, now, *ids],
            )
            conn.commit()

    def enqueue_decisions(self, user_id: int, decisions: List[Dict]) -> int:
        created = 0
        now = int(time.time())
        for decision in decisions or []:
            if (
                decision.get("action") not in {"buy", "sell"}
                or decision.get("status") == "rejected"
            ):
                continue
            deployments = self.storage.fetchall(
                """
                SELECT d.* FROM strategy_deployments d
                JOIN trading_accounts a ON a.id = d.account_id
                WHERE d.user_id = ? AND d.strategy_id = ? AND d.symbol = ?
                  AND d.status = 'active' AND d.execution_mode = 'paper'
                  AND a.account_type = 'paper' AND a.status = 'active'
                  AND a.enabled = 1 AND a.trading_enabled = 1
                  AND a.auto_trading_enabled = 1
                """,
                (user_id, decision.get("strategy_id"), decision.get("symbol")),
            )
            for deployment in deployments:
                if self._create_order(user_id, deployment, decision, now):
                    created += 1
        return created

    def process_strategy_signals(
        self, user_id: int, symbol: str, current_price: float, strategy_service
    ) -> int:
        """按每个模拟部署独立生成决策，不复用实盘风控或实盘冷却。"""
        self._expire_deployments(user_id)
        deployments = self.storage.fetchall(
            """
            SELECT d.* FROM strategy_deployments d
            JOIN trading_accounts a ON a.id = d.account_id
            WHERE d.user_id = ? AND d.symbol = ? AND d.status = 'active'
              AND d.execution_mode = 'paper' AND a.account_type = 'paper'
              AND a.status = 'active' AND a.enabled = 1
              AND a.trading_enabled = 1 AND a.auto_trading_enabled = 1
            """,
            (user_id, symbol),
        )
        if not deployments:
            return 0
        created = 0
        now = int(time.time())
        for deployment in deployments:
            try:
                strategy = TradingStrategy.from_dict(
                    self._deployment_strategy(user_id, deployment)
                )
            except ValueError:
                continue
            signals = [
                signal for signal in
                strategy_service.signal_service.get_active_signals(symbol)
                if getattr(signal, "strategy_id", "") == strategy.strategy_id
            ]
            expected_source_ids = {
                item["signal_source_id"]
                for item in strategy.get_signal_sources(enabled_only=True)
            }
            reported_source_ids = {
                signal.signal_source_id for signal in signals
                if signal.signal_source_id
            }
            generator = getattr(
                strategy_service.signal_service,
                "generate_signals_for_strategy",
                None,
            )
            if (
                generator is not None
                and not expected_source_ids.issubset(reported_source_ids)
            ):
                signals = generator(symbol, current_price, strategy)
            account_id = int(deployment["account_id"])
            policy_snapshot = self._deployment_strategy(user_id, deployment)[
                "position_management_policy_snapshot"
            ]
            reverse_enabled = any(
                rule.get("type") == "reverse_signal"
                for rule in policy_snapshot["config"].get("management_rules", [])
            )
            for signal in signals:
                if (
                    getattr(signal, "strategy_id", "") == strategy.strategy_id
                    and getattr(signal, "is_entry_trigger", True)
                    and getattr(signal, "action", "") in {"buy", "sell"}
                    and reverse_enabled
                ):
                    self.storage.execute(
                        """
                        UPDATE paper_positions SET close_reason = 'reverse_signal'
                        WHERE account_id = ? AND deployment_id = ?
                          AND status = 'open' AND direction != ?
                        """,
                        (
                            account_id, deployment["deployment_id"],
                            getattr(signal, "action", ""),
                        ),
                    )
            decision = strategy_service.make_decision(
                symbol,
                current_price,
                force_signals=signals,
                strategy=strategy,
                execution_mode="paper",
                cooldown_scope=f"paper:{account_id}:{deployment['deployment_id']}",
                volume_calculator=lambda s, risk, st, aid=account_id: (
                    self._paper_volume(aid, s, risk, st)
                ),
                position_checker=lambda s, st, action, aid=account_id: (
                    self._paper_position_check(aid, s, st, action)
                ),
                risk_checker=lambda s, volume, risk, st, aid=account_id, px=current_price: (
                    self._paper_risk_check(aid, s, volume, px)
                ),
                position_policy=PositionManagementPolicy.from_dict(
                    self._deployment_strategy(user_id, deployment)[
                        "position_management_policy_snapshot"
                    ]
                ),
            )
            if decision and self._create_order(
                user_id, deployment, decision.to_dict(), now
            ):
                created += 1
        return created

    def process_tick(
        self,
        user_id: int,
        symbol: str,
        bid: float,
        ask: Optional[float] = None,
        timestamp: Optional[int] = None,
        pivots: Optional[List[Dict]] = None,
    ) -> Dict:
        bid = float(bid)
        ask = float(ask if ask is not None else bid)
        if not all(math.isfinite(value) and value > 0 for value in (bid, ask)):
            raise ValueError("模拟撮合价格无效")
        if ask < bid:
            bid, ask = ask, bid
        now = int(timestamp or time.time())
        symbol = str(symbol)
        with self._lock:
            self._expire_deployments(user_id)
            self._quotes[(user_id, symbol)] = (bid, ask)
            account_rows = self.storage.fetchall(
                """
                SELECT DISTINCT a.id
                FROM trading_accounts a
                LEFT JOIN strategy_deployments d ON d.account_id = a.id
                LEFT JOIN paper_orders o ON o.account_id = a.id
                    AND o.symbol = ? AND o.status = 'pending'
                LEFT JOIN paper_positions p ON p.account_id = a.id
                    AND p.symbol = ? AND p.status = 'open'
                WHERE a.user_id = ? AND a.account_type = 'paper'
                  AND a.status = 'active' AND a.enabled = 1
                  AND (d.symbol = ? OR o.order_id IS NOT NULL OR p.position_id IS NOT NULL)
                """,
                (symbol, symbol, user_id, symbol),
            )
            summary = {"filled": 0, "closed": 0, "rejected": 0}
            for row in account_rows:
                result = self._process_account_tick(
                    user_id, int(row["id"]), symbol, bid, ask, now, pivots or []
                )
                for key in summary:
                    summary[key] += result[key]
            return summary

    def get_account_detail(self, user_id: int, account_id: int) -> Dict:
        account = self._paper_account(user_id, account_id)
        self._expire_deployments(user_id, account_id)
        settings = self._settings(account_id)
        deployments = [dict(row) for row in self.storage.fetchall(
            """
            SELECT d.*, json_extract(s.config_json, '$.strategy_name') AS strategy_name,
                   json_extract(s.config_json, '$.auto_execute') AS auto_execute
            FROM strategy_deployments d
            LEFT JOIN user_strategy_configs s
              ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            WHERE d.user_id = ? AND d.account_id = ?
            ORDER BY d.created_at DESC
            """,
            (user_id, account_id),
        )]
        return {
            "account": self._account_dict(account),
            "settings": settings,
            "deployments": deployments,
            "orders": [dict(row) for row in self.storage.fetchall(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY requested_at DESC LIMIT 200",
                (account_id,),
            )],
            "positions": [dict(row) for row in self.storage.fetchall(
                "SELECT * FROM paper_positions WHERE account_id = ? AND status = 'open' ORDER BY opened_at DESC",
                (account_id,),
            )],
            "trades": [dict(row) for row in self.storage.fetchall(
                "SELECT * FROM paper_trades WHERE account_id = ? ORDER BY closed_at DESC LIMIT 200",
                (account_id,),
            )],
            "runtime_logs": [
                {
                    **dict(row),
                    "payload": json.loads(row["payload_json"] or "{}"),
                }
                for row in self.storage.fetchall(
                    """
                    SELECT * FROM paper_runtime_logs
                    WHERE account_id = ? ORDER BY created_at DESC, id DESC LIMIT 100
                    """,
                    (account_id,),
                )
            ],
            "equity_curve": [dict(row) for row in self.storage.fetchall(
                """
                SELECT point_time AS time, balance, equity, free_margin, margin,
                       open_positions
                FROM paper_equity_points WHERE account_id = ?
                ORDER BY point_time DESC LIMIT 1440
                """,
                (account_id,),
            )][::-1],
        }

    def build_report(
        self, user_id: int, account_id: int, strategy_id: str = "",
        started_at: int = 0,
    ) -> Dict:
        account = self._paper_account(user_id, account_id)
        if strategy_id and not started_at:
            deployment = self.storage.fetchone(
                """
                SELECT strategy_version_at FROM strategy_deployments
                WHERE account_id = ? AND strategy_id = ?
                  AND source_backtest_task_id != ''
                """,
                (account_id, strategy_id),
            )
            if deployment:
                started_at = int(deployment["strategy_version_at"] or 0)
        params: List = [account_id]
        where = "account_id = ?"
        if strategy_id:
            where += " AND strategy_id = ?"
            params.append(strategy_id)
        if started_at:
            where += " AND opened_at >= ?"
            params.append(int(started_at))
        trades = [dict(row) for row in self.storage.fetchall(
            f"SELECT * FROM paper_trades WHERE {where} ORDER BY closed_at", params
        )]
        order_where = "account_id = ?"
        order_params: List = [account_id]
        if strategy_id:
            order_where += " AND strategy_id = ?"
            order_params.append(strategy_id)
        if started_at:
            order_where += " AND requested_at >= ?"
            order_params.append(int(started_at))
        orders = [dict(row) for row in self.storage.fetchall(
            f"SELECT * FROM paper_orders WHERE {order_where} ORDER BY requested_at",
            order_params,
        )]
        profits = [float(item["net_profit"]) for item in trades]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        gross_profit, gross_loss = sum(wins), abs(sum(losses))
        initial = float(account.initial_balance)
        net_profit = sum(profits)
        equity_rows = [dict(row) for row in self.storage.fetchall(
            "SELECT point_time AS time, equity, balance FROM paper_equity_points "
            "WHERE account_id = ? AND point_time >= ? ORDER BY point_time",
            (account_id, int(started_at or 0)),
        )]
        peak, max_drawdown = initial, 0.0
        for point in equity_rows:
            equity = float(point["equity"])
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        holding = [
            max(0, int(item["closed_at"]) - int(item["opened_at"])) / 60
            for item in trades
        ]
        grouped = {}
        for trade in trades:
            month = datetime.fromtimestamp(
                int(trade["closed_at"]), timezone.utc
            ).strftime("%Y-%m")
            grouped.setdefault(month, []).append(float(trade["net_profit"]))
        by_strategy = self._group_trade_stats(trades, "strategy_id")
        by_symbol = self._group_trade_stats(trades, "symbol")
        by_exit = self._group_trade_stats(trades, "exit_reason")
        rejected = sum(item["status"] == "rejected" for item in orders)
        summary = {
            "trade_count": len(trades),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "net_profit": round(net_profit, 2),
            "return_pct": round(net_profit / initial * 100, 2) if initial else 0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
            "max_drawdown_pct": round(max_drawdown, 2),
            "average_profit": round(statistics.mean(profits), 2) if profits else 0,
            "average_holding_minutes": round(statistics.mean(holding), 1) if holding else 0,
            "commission": round(sum(float(item["commission"]) for item in trades), 2),
            "order_count": len(orders),
            "rejected_order_count": rejected,
        }
        benchmark = self._backtest_benchmark(account_id, strategy_id)
        comparison = self._compare_to_backtest(summary, benchmark)
        return {
            "scope": {"account_id": account_id, "strategy_id": strategy_id or None},
            "summary": summary,
            "backtest_benchmark": benchmark,
            "comparison": comparison,
            "monthly": [{
                "month": month, "trade_count": len(values),
                "net_profit": round(sum(values), 2),
            } for month, values in sorted(grouped.items())],
            "by_strategy": by_strategy,
            "by_symbol": by_symbol,
            "by_exit_reason": by_exit,
            "equity_curve": equity_rows,
        }

    def _backtest_benchmark(self, account_id: int, strategy_id: str) -> Optional[Dict]:
        if not strategy_id:
            return None
        params: List = [account_id]
        where = "d.account_id = ? AND d.source_backtest_task_id != ''"
        where += " AND d.strategy_id = ?"
        params.append(strategy_id)
        row = self.storage.fetchone(
            f"""
            SELECT d.source_backtest_task_id, t.result_json
            FROM strategy_deployments d
            JOIN backtest_tasks t ON t.task_id = d.source_backtest_task_id
            WHERE {where} ORDER BY d.updated_at DESC LIMIT 1
            """,
            params,
        )
        if row is None:
            return None
        result = json.loads(row["result_json"] or "{}")
        return {
            "task_id": row["source_backtest_task_id"],
            "trade_count": int(result.get("trade_count", 0)),
            "win_rate": float(result.get("win_rate_pct", result.get("win_rate", 0))),
            "return_pct": float(result.get("total_return_pct", 0)),
            "profit_factor": result.get("profit_factor"),
            "max_drawdown_pct": float(result.get("max_drawdown_pct", 0)),
        }

    @staticmethod
    def _compare_to_backtest(summary: Dict, benchmark: Optional[Dict]) -> Optional[Dict]:
        if benchmark is None:
            return None
        keys = ("trade_count", "win_rate", "return_pct", "max_drawdown_pct")
        comparison = {
            key: round(float(summary.get(key, 0)) - float(benchmark.get(key, 0)), 2)
            for key in keys
        }
        live_factor = summary.get("profit_factor")
        test_factor = benchmark.get("profit_factor")
        comparison["profit_factor"] = (
            round(float(live_factor) - float(test_factor), 2)
            if live_factor is not None and test_factor is not None else None
        )
        return comparison

    @staticmethod
    def _group_trade_stats(trades: List[Dict], key: str) -> List[Dict]:
        groups: Dict[str, List[float]] = {}
        for trade in trades:
            groups.setdefault(str(trade.get(key) or "unknown"), []).append(
                float(trade["net_profit"])
            )
        return [{
            "name": name,
            "trade_count": len(values),
            "win_rate": round(sum(value > 0 for value in values) / len(values) * 100, 2),
            "net_profit": round(sum(values), 2),
        } for name, values in groups.items()]

    def _paper_volume(self, account_id, symbol, risk_points, strategy) -> float:
        if strategy.volume_mode == "fixed":
            return max(0.01, round(float(strategy.fixed_volume), 2))
        account = self.storage.fetchone(
            "SELECT balance FROM trading_accounts WHERE id = ?", (account_id,)
        )
        _, contract_size = market_spec(symbol)
        risk_amount = float(account["balance"]) * float(strategy.risk_percent) / 100
        raw = risk_amount / max(risk_points * contract_size, 0.000001)
        return max(0.01, math.floor(raw * 100) / 100)

    def _paper_position_check(self, account_id, symbol, strategy, action) -> Dict:
        rows = self.storage.fetchall(
            "SELECT direction FROM paper_positions WHERE account_id = ? "
            "AND symbol = ? AND status = 'open'",
            (account_id, symbol),
        )
        pending = self.storage.fetchall(
            "SELECT direction FROM paper_orders WHERE account_id = ? "
            "AND symbol = ? AND status = 'pending'",
            (account_id, symbol),
        )
        directions = [row["direction"] for row in rows + pending]
        warnings = []
        account = self.storage.fetchone(
            "SELECT max_total_positions FROM trading_accounts WHERE id = ?",
            (account_id,),
        )
        max_positions = min(
            max(1, int(strategy.max_positions)),
            int(account["max_total_positions"]),
        )
        if len(directions) >= max_positions:
            warnings.append("模拟账户已达到策略最大持仓数")
        if sum(value == action for value in directions) >= max(
            1, int(strategy.max_same_direction)
        ):
            warnings.append("模拟账户已达到同方向最大持仓数")
        if strategy.position_conflict == "block" and directions:
            warnings.append("策略配置为存在持仓时阻止开仓")
        return {"allowed": not warnings, "warnings": warnings}

    def _paper_risk_check(self, account_id, symbol, volume, current_price) -> Dict:
        account = self.storage.fetchone(
            """SELECT balance, free_margin, status, enabled, trading_enabled,
                      max_single_volume, daily_loss_limit, daily_order_limit
               FROM trading_accounts WHERE id = ?""",
            (account_id,),
        )
        warnings = []
        if (
            not account or account["status"] != "active"
            or not account["enabled"] or not account["trading_enabled"]
        ):
            warnings.append("模拟账户不可用")
        else:
            if float(volume) > float(account["max_single_volume"]):
                warnings.append("超过账户单笔最大手数")
            today_start = int(time.time()) - int(time.time()) % 86400
            daily = self.storage.fetchone(
                """
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(net_profit), 0) AS net_profit
                FROM paper_trades WHERE account_id = ? AND closed_at >= ?
                """,
                (account_id, today_start),
            )
            orders = self.storage.fetchone(
                "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id = ? AND requested_at >= ?",
                (account_id, today_start),
            )
            if int(orders["count"]) >= int(account["daily_order_limit"]):
                warnings.append("已达到账户每日订单上限")
            balance = max(float(account["balance"]), 0.0)
            if balance > 0 and float(daily["net_profit"]) < 0:
                loss_pct = abs(float(daily["net_profit"])) / balance * 100
                if loss_pct >= float(account["daily_loss_limit"]):
                    warnings.append("已达到账户每日亏损限制")
            _, contract_size = market_spec(symbol)
            leverage = self._settings(account_id)["leverage"]
            if current_price * volume * contract_size / leverage > float(account["free_margin"]):
                warnings.append("模拟账户可用保证金不足")
        return {"allowed": not warnings, "warnings": warnings}

    def _create_order(self, user_id: int, deployment, decision: Dict, now: int) -> bool:
        account_id = int(deployment["account_id"])
        if self.storage.fetchone(
            "SELECT 1 FROM paper_orders WHERE account_id = ? AND decision_id = ?",
            (account_id, decision["decision_id"]),
        ):
            return False
        strategy = self._deployment_strategy(user_id, deployment)
        open_count = int(self.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_positions WHERE account_id = ? AND status = 'open'",
            (account_id,),
        )["count"])
        account_limits = self.storage.fetchone(
            "SELECT max_total_positions, max_single_volume FROM trading_accounts WHERE id = ?",
            (account_id,),
        )
        pending_count = int(self.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id = ? AND status = 'pending'",
            (account_id,),
        )["count"])
        reason = (
            str(decision.get("decision_reason") or "模拟盘独立风控未通过")
            if decision.get("status") == "rejected" else ""
        )
        max_positions = min(
            max(1, int(strategy.get("max_positions", 3))),
            int(account_limits["max_total_positions"]),
        )
        if not reason and open_count + pending_count >= max_positions:
            reason = "已达到策略最大持仓数"
        same_direction = int(self.storage.fetchone(
            """
            SELECT (
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id = ? AND status = 'open' AND direction = ?
            ) + (
                SELECT COUNT(*) FROM paper_orders
                WHERE account_id = ? AND status = 'pending' AND direction = ?
            ) AS count
            """,
            (
                account_id, decision["action"],
                account_id, decision["action"],
            ),
        )["count"])
        if not reason and same_direction >= max(
            1, int(strategy.get("max_same_direction", 2))
        ):
            reason = "已达到同方向最大持仓数"
        entry = float(decision.get("entry_price", 0))
        sl = float(decision.get("sl", 0))
        tp = float(decision.get("tp", 0))
        summary = decision.get("signal_summary") or {}
        source_id = str(summary.get("selected_signal_source_id", ""))
        source = str(summary.get("selected_signal_source", ""))
        management = summary.get("position_management") or {}
        policy_snapshot = management.get("policy_snapshot") or strategy.get(
            "position_management_policy_snapshot", {}
        )
        requested_volume = max(0.01, float(decision.get("volume", 0.01)))
        if requested_volume > float(account_limits["max_single_volume"]):
            reason = "超过账户单笔最大手数"
        if not reason and not self._valid_exits(decision["action"], entry, sl, tp):
            reason = "止盈止损价格无效"
        order_id = uuid.uuid4().hex[:12]
        status = "rejected" if reason else "pending"
        try:
            self.storage.execute(
                """
                INSERT INTO paper_orders(
                    order_id, user_id, account_id, deployment_id, strategy_id,
                    decision_id, symbol, direction, status, requested_volume,
                    requested_price, stop_loss, take_profit, confidence,
                    signal_source_id, exit_mode, trailing_activation_r,
                    trailing_distance_r, position_policy_snapshot_json,
                    rejection_reason, requested_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, user_id, account_id, deployment["deployment_id"],
                    deployment["strategy_id"], decision["decision_id"],
                    decision["symbol"], decision["action"], status,
                    requested_volume, entry, sl, tp,
                    float(decision.get("confidence_score", 0)), source_id,
                    "position_manager", 1.0, 1.0,
                    json.dumps(policy_snapshot, ensure_ascii=False),
                    reason, now, now, now,
                ),
            )
            return True
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return False
            raise

    def _process_account_tick(
        self, user_id: int, account_id: int, symbol: str,
        bid: float, ask: float, now: int, pivots: List[Dict],
    ) -> Dict:
        point_size, contract_size = market_spec(symbol)
        settings = self._settings(account_id)
        slippage = settings["slippage_points"] * point_size
        configured_spread = settings["spread_points"] * point_size
        if ask - bid < configured_spread:
            midpoint = (ask + bid) / 2
            bid = midpoint - configured_spread / 2
            ask = midpoint + configured_spread / 2
        result = {"filled": 0, "closed": 0, "rejected": 0}
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            account = conn.execute(
                "SELECT * FROM trading_accounts WHERE id = ? AND user_id = ?",
                (account_id, user_id),
            ).fetchone()
            balance = float(account["balance"])
            available_margin = float(account["free_margin"])
            pending = conn.execute(
                """
                SELECT o.*, d.status AS deployment_status
                FROM paper_orders o
                JOIN strategy_deployments d ON d.deployment_id = o.deployment_id
                WHERE o.account_id = ? AND o.symbol = ? AND o.status = 'pending'
                ORDER BY o.requested_at, o.order_id
                """,
                (account_id, symbol),
            ).fetchall()
            open_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM paper_positions WHERE account_id = ? AND status = 'open'",
                (account_id,),
            ).fetchone()["count"])
            for order in pending:
                if order["deployment_status"] != "active":
                    self._reject_order(conn, order["order_id"], "策略运行已暂停", now)
                    result["rejected"] += 1
                    continue
                try:
                    strategy = self._strategy_config(user_id, order["strategy_id"])
                except ValueError:
                    self._reject_order(conn, order["order_id"], "来源策略已删除", now)
                    result["rejected"] += 1
                    continue
                if open_count >= max(1, int(strategy.get("max_positions", 3))):
                    self._reject_order(conn, order["order_id"], "成交时已达到最大持仓数", now)
                    result["rejected"] += 1
                    continue
                fill_price = ask + slippage if order["direction"] == "buy" else bid - slippage
                if not self._valid_exits(
                    order["direction"], fill_price,
                    float(order["stop_loss"]), float(order["take_profit"]),
                ):
                    self._reject_order(conn, order["order_id"], "滑点后止盈止损无效", now)
                    result["rejected"] += 1
                    continue
                volume = float(order["requested_volume"])
                required_margin = fill_price * volume * contract_size / settings["leverage"]
                if required_margin > available_margin:
                    self._reject_order(conn, order["order_id"], "可用保证金不足", now)
                    result["rejected"] += 1
                    continue
                commission = volume * settings["commission_per_lot"]
                balance -= commission
                available_margin -= required_margin + commission
                position_id = uuid.uuid4().hex[:12]
                conn.execute(
                    """
                    UPDATE paper_orders SET status = 'filled', filled_volume = ?,
                        filled_price = ?, filled_at = ?, updated_at = ?
                    WHERE order_id = ?
                    """,
                    (volume, fill_price, now, now, order["order_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO paper_positions(
                        position_id, user_id, account_id, order_id, deployment_id,
                        strategy_id, symbol, direction, status, volume,
                        entry_price, stop_loss, take_profit, open_commission,
                        current_price, signal_source_id, exit_mode,
                        trailing_activation_r, trailing_distance_r, initial_risk,
                        favorable_price, position_policy_snapshot_json,
                        opened_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position_id, user_id, account_id, order["order_id"],
                        order["deployment_id"], order["strategy_id"], symbol,
                        order["direction"], volume, fill_price, order["stop_loss"],
                        order["take_profit"], commission,
                        bid if order["direction"] == "buy" else ask,
                        order["signal_source_id"], order["exit_mode"],
                        order["trailing_activation_r"], order["trailing_distance_r"],
                        abs(fill_price - float(order["stop_loss"])), fill_price,
                        order["position_policy_snapshot_json"],
                        now, now, now,
                    ),
                )
                open_count += 1
                result["filled"] += 1

            positions = conn.execute(
                """
                SELECT * FROM paper_positions
                WHERE account_id = ? AND symbol = ? AND status = 'open'
                ORDER BY opened_at, position_id
                """,
                (account_id, symbol),
            ).fetchall()
            for position in positions:
                mark = bid if position["direction"] == "buy" else ask
                reason = str(position["close_reason"] or "")
                if position["direction"] == "buy":
                    if mark <= float(position["stop_loss"]):
                        reason = "stop_loss"
                    elif (
                        float(position["take_profit"]) > 0
                        and mark >= float(position["take_profit"])
                    ):
                        reason = "take_profit"
                else:
                    if mark >= float(position["stop_loss"]):
                        reason = "stop_loss"
                    elif (
                        float(position["take_profit"]) > 0
                        and mark <= float(position["take_profit"])
                    ):
                        reason = "take_profit"
                if not reason and position["exit_mode"] == "trailing_reverse":
                    initial_risk = float(position["initial_risk"])
                    favorable = float(position["favorable_price"])
                    if position["direction"] == "buy":
                        favorable = max(favorable, mark)
                        activated = favorable - float(position["entry_price"]) >= (
                            initial_risk * float(position["trailing_activation_r"])
                        )
                        trailing_price = favorable - initial_risk * float(
                            position["trailing_distance_r"]
                        )
                        if activated and mark <= trailing_price:
                            reason = "trailing_stop"
                    else:
                        favorable = min(favorable, mark)
                        activated = float(position["entry_price"]) - favorable >= (
                            initial_risk * float(position["trailing_activation_r"])
                        )
                        trailing_price = favorable + initial_risk * float(
                            position["trailing_distance_r"]
                        )
                        if activated and mark >= trailing_price:
                            reason = "trailing_stop"
                    conn.execute(
                        "UPDATE paper_positions SET favorable_price = ? WHERE position_id = ?",
                        (favorable, position["position_id"]),
                    )
                if not reason and position["exit_mode"] == "position_manager":
                    policy_snapshot = json.loads(
                        position["position_policy_snapshot_json"] or "{}"
                    )
                    favorable = float(position["favorable_price"] or position["entry_price"])
                    favorable = (
                        max(favorable, mark) if position["direction"] == "buy"
                        else min(favorable, mark)
                    )
                    position_state = dict(position)
                    position_state["favorable_price"] = favorable
                    max_bars = 0
                    period_seconds = {
                        "M1": 60, "M5": 300, "M15": 900,
                        "H1": 3600, "H4": 14400,
                    }
                    for rule in policy_snapshot.get("config", {}).get("management_rules", []):
                        if rule.get("type") == "max_holding_bars":
                            seconds = period_seconds.get(rule.get("period", "M1"), 60)
                            max_bars = max(max_bars, (now - int(position["opened_at"])) // seconds)
                    position_state["holding_bars"] = max_bars
                    action = self.position_manager.evaluate(
                        policy_snapshot.get("config", {}), position_state,
                        {"price": mark, "time": now}, pivots=pivots,
                    )
                    if action.action == "close":
                        reason = action.reason
                    elif action.action == "modify_sl" and action.stop_loss:
                        conn.execute(
                            """
                            UPDATE paper_positions SET stop_loss = ?, favorable_price = ?,
                                holding_bars = ?, updated_at = ? WHERE position_id = ?
                            """,
                            (action.stop_loss, favorable, max_bars, now,
                             position["position_id"]),
                        )
                if reason:
                    exit_price = bid - slippage if position["direction"] == "buy" else ask + slippage
                    multiplier = 1 if position["direction"] == "buy" else -1
                    gross = (
                        exit_price - float(position["entry_price"])
                    ) * multiplier * float(position["volume"]) * contract_size
                    close_commission = float(position["volume"]) * settings["commission_per_lot"]
                    total_commission = float(position["open_commission"]) + close_commission
                    net = gross - total_commission
                    balance += gross - close_commission
                    trade_id = uuid.uuid4().hex[:12]
                    conn.execute(
                        """
                        UPDATE paper_positions SET status = 'closed', current_price = ?,
                            unrealized_profit = 0, net_profit = ?, closed_at = ?,
                            close_price = ?, close_reason = ?, updated_at = ?
                        WHERE position_id = ?
                        """,
                        (exit_price, net, now, exit_price, reason, now, position["position_id"]),
                    )
                    conn.execute(
                        """
                        INSERT INTO paper_trades(
                            trade_id, user_id, account_id, order_id, position_id,
                            deployment_id, strategy_id, symbol, direction, volume,
                            entry_price, exit_price, gross_profit, commission,
                            net_profit, exit_reason, opened_at, closed_at, created_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trade_id, user_id, account_id, position["order_id"],
                            position["position_id"], position["deployment_id"],
                            position["strategy_id"], symbol, position["direction"],
                            position["volume"], position["entry_price"], exit_price,
                            gross, total_commission, net, reason,
                            position["opened_at"], now, now,
                        ),
                    )
                    result["closed"] += 1

            equity, margin, open_positions = self._mark_all_positions(
                conn, user_id, account_id, balance, settings["leverage"], now
            )
            free_margin = equity - margin
            conn.execute(
                """
                UPDATE trading_accounts SET balance = ?, equity = ?,
                    free_margin = ?, margin = ?, financial_updated_at = ?,
                    updated_at = ? WHERE id = ?
                """,
                (balance, equity, free_margin, margin, now, now, account_id),
            )
            point_time = now - now % 60
            conn.execute(
                """
                INSERT INTO paper_equity_points(
                    account_id, point_time, user_id, balance, equity,
                    free_margin, margin, open_positions
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, point_time) DO UPDATE SET
                    balance = excluded.balance, equity = excluded.equity,
                    free_margin = excluded.free_margin, margin = excluded.margin,
                    open_positions = excluded.open_positions
                """,
                (
                    account_id, point_time, user_id, balance, equity,
                    free_margin, margin, open_positions,
                ),
            )
            conn.commit()
        if any(result.values()):
            parts = [
                f"成交 {result['filled']} 笔" if result["filled"] else "",
                f"平仓 {result['closed']} 笔" if result["closed"] else "",
                f"拒单 {result['rejected']} 笔" if result["rejected"] else "",
            ]
            self._log_runtime(
                user_id,
                account_id,
                "execution",
                "，".join(part for part in parts if part),
                {"symbol": symbol, **result},
                now,
            )
        return result

    def run_maintenance(self) -> Dict:
        """使用最近一次有效报价维护 Paper 持仓，页面关闭后仍持续运行。"""
        with self._lock:
            quotes = list(self._quotes.items())
        summary = {"quotes": len(quotes), "filled": 0, "closed": 0, "rejected": 0}
        for (user_id, symbol), (bid, ask) in quotes:
            result = self.process_tick(user_id, symbol, bid, ask)
            for key in ("filled", "closed", "rejected"):
                summary[key] += result[key]

        now = int(time.time())
        minute = now - now % 60
        accounts = self.storage.fetchall(
            """
            SELECT id, user_id FROM trading_accounts
            WHERE account_type = 'paper' AND status = 'active' AND enabled = 1
            """
        )
        for account in accounts:
            exists = self.storage.fetchone(
                """
                SELECT 1 FROM paper_runtime_logs
                WHERE account_id = ? AND event_type = 'heartbeat' AND created_at >= ?
                """,
                (int(account["id"]), minute),
            )
            if exists is None:
                self._log_runtime(
                    int(account["user_id"]), int(account["id"]),
                    "heartbeat", "模拟账户后台运行正常",
                    {"quote_count": len(quotes)}, now,
                )
        return summary

    def _log_runtime(
        self, user_id: int, account_id: int, event_type: str,
        message: str, payload: Optional[Dict] = None,
        created_at: Optional[int] = None,
    ) -> None:
        self.storage.execute(
            """
            INSERT INTO paper_runtime_logs(
                user_id, account_id, event_type, message, payload_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, account_id, event_type, message,
                json.dumps(payload or {}, ensure_ascii=False),
                int(created_at or time.time()),
            ),
        )

    def _mark_all_positions(
        self, conn, user_id: int, account_id: int, balance: float,
        leverage: float, now: int,
    ) -> Tuple[float, float, int]:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? AND status = 'open'",
            (account_id,),
        ).fetchall()
        unrealized_total = margin = 0.0
        for position in rows:
            quote = self._quotes.get((user_id, position["symbol"]))
            mark = (
                quote[0] if position["direction"] == "buy" else quote[1]
            ) if quote else float(position["current_price"])
            _, contract_size = market_spec(position["symbol"])
            multiplier = 1 if position["direction"] == "buy" else -1
            unrealized = (
                mark - float(position["entry_price"])
            ) * multiplier * float(position["volume"]) * contract_size
            margin += (
                float(position["entry_price"]) * float(position["volume"])
                * contract_size / leverage
            )
            unrealized_total += unrealized
            conn.execute(
                """
                UPDATE paper_positions SET current_price = ?,
                    unrealized_profit = ?, updated_at = ? WHERE position_id = ?
                """,
                (mark, unrealized, now, position["position_id"]),
            )
        return balance + unrealized_total, margin, len(rows)

    def _settings(self, account_id: int) -> Dict:
        row = self.storage.fetchone(
            "SELECT * FROM paper_account_settings WHERE account_id = ?",
            (account_id,),
        )
        if row is None:
            return {
                "leverage": 100.0, "spread_points": 0.0,
                "slippage_points": 0.0, "commission_per_lot": 0.0,
            }
        return {
            key: float(row[key]) for key in (
                "leverage", "spread_points", "slippage_points",
                "commission_per_lot",
            )
        }

    def _strategy_config(self, user_id: int, strategy_id: str) -> Dict:
        row = self.storage.fetchone(
            """
            SELECT symbol, config_json FROM user_strategy_configs
            WHERE user_id = ? AND strategy_id = ?
            """,
            (user_id, strategy_id),
        )
        if row is None:
            raise ValueError("策略不存在")
        config = json.loads(row["config_json"])
        config["symbol"] = row["symbol"]
        return config

    def _deployment_strategy(self, user_id: int, deployment) -> Dict:
        raw_snapshot = deployment["strategy_snapshot_json"] \
            if "strategy_snapshot_json" in deployment.keys() else ""
        if raw_snapshot:
            snapshot = json.loads(raw_snapshot)
            if snapshot:
                return snapshot
        return self._strategy_config(user_id, deployment["strategy_id"])

    def _paper_account(self, user_id: int, account_id: int):
        account = self.accounts.get_by_id(user_id, account_id)
        if account is None or account.account_type != "paper":
            raise ValueError("模拟账户不存在")
        return account

    def _account(self, user_id: int, account_id: int):
        account = self.accounts.get_by_id(user_id, account_id)
        if account is None:
            raise ValueError("交易账户不存在")
        return account

    @staticmethod
    def _valid_exits(direction: str, entry: float, sl: float, tp: float) -> bool:
        if min(entry, sl) <= 0 or tp < 0:
            return False
        if tp == 0:
            return sl < entry if direction == "buy" else entry < sl
        return sl < entry < tp if direction == "buy" else tp < entry < sl

    @staticmethod
    def _reject_order(conn, order_id: str, reason: str, now: int) -> None:
        conn.execute(
            """
            UPDATE paper_orders SET status = 'rejected', rejection_reason = ?,
                canceled_at = ?, updated_at = ? WHERE order_id = ?
            """,
            (reason, now, now, order_id),
        )

    @staticmethod
    def _account_dict(account) -> Dict:
        return {
            "account_id": account.account_id,
            "account_name": account.account_name,
            "currency": account.currency,
            "initial_balance": account.initial_balance,
            "balance": account.balance,
            "equity": account.equity,
            "free_margin": account.free_margin,
            "margin": account.margin,
            "status": account.status,
            "financial_updated_at": account.financial_updated_at,
        }
