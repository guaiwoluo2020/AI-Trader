#!/usr/bin/env python3
"""实时模拟账户部署、订单撮合与资金核算。"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
import uuid
import copy
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from market.models import PositionManagementPolicy, StrategyLifecycle, TradingStrategy
from market.risk_clock import risk_day_start_timestamp
from market.services.position_manager import PositionManager
from market.services.position_attribution import (
    build_position_attribution, close_position_attribution,
)
from market.services.account_strategy_performance import build_paper_performance
from market.services.strategy.transient_decision_store import transient_decision_store
from market.store.structure_plan_store import StructureTradePlanRepository
from membership import MembershipService
from mysql_repositories import (
    PositionManagementPolicyRepository, MySQLStorage,
    PlatformInstrumentMappingRepository,
    RuntimeStateRepository, TradingAccountRepository, get_storage,
)
from repositories.strategy import StrategyConfigRepository
from repositories.trading import PositionManagementEventRepository
from strategy_admission import StrategyAdmissionService, strategy_fingerprint


def market_spec(symbol: str) -> Tuple[float, float]:
    upper = str(symbol or "").upper()
    if "GOLD" in upper or "XAU" in upper:
        return 0.01, 100.0
    # BTCUSD 形式与外汇六码品种相同，但不能套用外汇的 100,000 合约规模。
    if any(upper.startswith(asset) for asset in (
        "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "LTC",
        "AVAX", "TRX", "DOT", "LINK",
    )):
        return 0.01, 1.0
    if "JPY" in upper:
        return 0.001, 100000.0
    if len(upper.rstrip("#._")) == 6:
        return 0.00001, 100000.0
    return 0.01, 1.0


class PaperTradingService:
    """以 EA Tick 驱动的持久化模拟撮合器。"""

    # paper_orders.pending 只是等待下一次 Tick 撮合的短暂状态，不是长期限价单。
    # 报价链路中断时必须释放风险额度，避免旧订单永久阻塞后续信号。
    PENDING_ORDER_TIMEOUT_SECONDS = 60

    def __init__(self, storage: Optional[MySQLStorage] = None):
        self.storage = storage or get_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.instrument_mappings = PlatformInstrumentMappingRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)
        self.position_manager = PositionManager()
        self.position_events = PositionManagementEventRepository(self.storage)
        self.structure_plans = StructureTradePlanRepository(self.storage)
        self.memberships = MembershipService(self.storage)
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
        items = []
        strategy_repository = StrategyConfigRepository(self.storage)
        for row in strategies:
            # A shared strategy reference stores only its source pointer.  Use
            # the repository to materialize the publisher's current signals
            # before determining whether it may enter paper trading directly.
            # Most strategy rows contain the complete config.  Materialize
            # directly from it to avoid one extra SELECT per row; shared
            # references still go through the repository resolver.
            raw_config = json.loads(row["config_json"] or "{}")
            if raw_config.get("source_owner_user_id"):
                strategy = strategy_repository.get_strategy_by_id(
                    user_id, row["strategy_id"]
                )
            else:
                strategy = TradingStrategy.from_dict(raw_config)
            if strategy is None:
                continue
            config = strategy.to_dict()
            lifecycle = config.get(
                "lifecycle_status", StrategyLifecycle.PRODUCTION
            )
            direct_paper = self._direct_paper_eligible(config)
            paper_eligible = (
                lifecycle in {
                    StrategyLifecycle.BACKTEST_PASSED,
                    StrategyLifecycle.PAPER_TRADING,
                    StrategyLifecycle.PRODUCTION,
                }
                or direct_paper
            )
            items.append({
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "strategy_name": config.get("strategy_name", row["strategy_id"]),
                "enabled": True,
                "lifecycle_status": lifecycle,
                "paper_eligible": paper_eligible,
                "paper_direct_allowed": direct_paper,
                "paper_eligibility_reason": (
                    "包含 AI、转折点或整数点位信号源，可跳过回测直接进入模拟观察"
                    if direct_paper and lifecycle not in {
                        StrategyLifecycle.BACKTEST_PASSED,
                        StrategyLifecycle.PAPER_TRADING,
                        StrategyLifecycle.PRODUCTION,
                    }
                    else ""
                ),
                "live_eligible": lifecycle == StrategyLifecycle.PRODUCTION,
            })
        return {
            "strategies": items,
        }

    @staticmethod
    def _has_enabled_ai_signal(strategy_data: Dict) -> bool:
        return any(
            source.get("source") == "ai_entry"
            and source.get("enabled", True)
            for source in (strategy_data.get("signal_sources") or [])
        )

    @classmethod
    def _ai_direct_paper_eligible(cls, strategy_data: Dict) -> bool:
        """Compatibility alias for integrations that still use the old name."""
        return cls._direct_paper_eligible(strategy_data)

    @staticmethod
    def _direct_paper_eligible(strategy_data: Dict) -> bool:
        return (
            any(
                source.get("source") in {"ai_entry", "pivot", "key_level", "structure_plan"}
                and source.get("enabled", True)
                for source in (strategy_data.get("signal_sources") or [])
            )
            and strategy_data.get("lifecycle_status") != StrategyLifecycle.RETIRED
        )

    def _promote_for_paper_deployment(
        self, user_id: int, strategy: TradingStrategy, account_name: str,
        *, direct_observation: bool = False,
    ) -> TradingStrategy:
        if strategy.lifecycle_status == StrategyLifecycle.BACKTEST_PASSED:
            strategy.transition_lifecycle(
                StrategyLifecycle.PAPER_TRADING,
                f"部署到模拟账户 {account_name}",
            )
        elif direct_observation and strategy.lifecycle_status != StrategyLifecycle.PAPER_TRADING:
            now = datetime.now()
            previous = strategy.lifecycle_status
            strategy.lifecycle_status = StrategyLifecycle.PAPER_TRADING
            strategy.lifecycle_updated_at = now
            strategy.updated_at = now
            strategy.lifecycle_history.append({
                "from_status": previous,
                "to_status": StrategyLifecycle.PAPER_TRADING,
                "changed_at": now.isoformat(),
                "reason": (
                    f"包含 AI、转折点或整数点位信号源，跳过回测直接部署到模拟账户 "
                    f"{account_name} 观察"
                ),
            })
        # Shared references follow the publisher's strategy in real time.  Do
        # not persist the materialized publisher configuration into the
        # recipient's reference merely because it is deployed to paper.
        if not strategy.source_owner_user_id:
            StrategyConfigRepository(self.storage).save_strategy(user_id, strategy)
        return strategy

    def deploy(
        self, user_id: int, account_id: int, strategy_id: str,
        strategy_snapshot: Optional[Dict] = None,
        source_backtest_task_id: str = "",
        duration_days: Optional[int] = None,
    ) -> Dict:
        account = self._account(user_id, account_id)
        current_strategy = TradingStrategy.from_dict(
            self._strategy_config(user_id, strategy_id)
        )
        strategy = TradingStrategy.from_dict(
            strategy_snapshot or current_strategy.to_dict()
        ).to_dict()
        if strategy_fingerprint(strategy) != strategy_fingerprint(
            current_strategy.to_dict()
        ):
            raise ValueError("回测策略快照与当前策略版本不一致，请重新回测")
        policy_id = str(strategy.get("position_management_policy_id", ""))
        policy = self.position_policies.get_for_strategy(user_id, current_strategy)
        if policy is None or not policy.enabled:
            raise ValueError("策略必须绑定一个已启用的持仓管理方案")
        lifecycle = strategy.get("lifecycle_status", "production")
        if account.account_type == "paper":
            normal_eligible = lifecycle in {
                StrategyLifecycle.BACKTEST_PASSED,
                StrategyLifecycle.PAPER_TRADING,
                StrategyLifecycle.PRODUCTION,
            }
            direct_observation_bypass = (
                not normal_eligible
                and self._direct_paper_eligible(current_strategy.to_dict())
            )
            if not normal_eligible and not direct_observation_bypass:
                raise ValueError(
                    "策略通过回测后才能部署到模拟账户；"
                    "包含 AI、转折点或整数点位信号源的策略可直接模拟观察"
                )
            if (
                current_strategy.lifecycle_status == StrategyLifecycle.BACKTEST_PASSED
                or direct_observation_bypass
            ):
                current_strategy = self._promote_for_paper_deployment(
                    user_id, current_strategy, account.account_name,
                    direct_observation=direct_observation_bypass,
                )
                strategy = current_strategy.to_dict()
            execution_mode = "paper"
        elif account.account_type == "mt5":
            self.memberships.assert_live_trading(user_id, account.account_id)
            if lifecycle != "production":
                raise ValueError("只有已批准用于实盘的策略才能绑定 MT5 账户")
            execution_mode = "live"
        else:
            raise ValueError("当前账户类型不支持策略部署")
        now = int(time.time())
        strategy_version_hash = strategy_fingerprint(strategy)
        scheduled_end_at = None
        if duration_days is not None:
            duration_days = int(duration_days)
            if duration_days < 1 or duration_days > 365:
                raise ValueError("模拟运行期限必须在 1 至 365 天之间")
            scheduled_end_at = now + duration_days * 86400
        deployment_id = uuid.uuid4().hex[:12]
        with self.storage._lock, self.storage._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO strategy_deployments(
                    deployment_id, user_id, account_id, strategy_id, symbol,
                    strategy_snapshot_hash, source_backtest_task_id,
                    strategy_version_at, scheduled_end_at,
                    execution_mode, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(account_id, strategy_id) DO UPDATE SET
                    symbol = excluded.symbol, status = 'active',
                    execution_mode = excluded.execution_mode,
                    strategy_snapshot_hash = excluded.strategy_snapshot_hash,
                    strategy_version_at = excluded.strategy_version_at,
                    source_backtest_task_id = excluded.source_backtest_task_id,
                    scheduled_end_at = excluded.scheduled_end_at,
                    updated_at = excluded.updated_at
                """,
                (
                    deployment_id, user_id, account.account_id, strategy_id,
                    strategy["symbol"], strategy_version_hash,
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
            raise ValueError("回测策略快照与当前策略配置不一致，请重新回测后再部署模拟")
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
        return self.deploy(
            user_id, account_id, task["strategy_id"],
            source_backtest_task_id=task_id, duration_days=duration_days,
        )

    def set_deployment_status(
        self, user_id: int, account_id: int, deployment_id: str, active: bool
    ) -> Optional[Dict]:
        account = self._account(user_id, account_id)
        now = int(time.time())
        current = self.storage.fetchone(
            "SELECT scheduled_end_at FROM strategy_deployments WHERE deployment_id = ?",
            (deployment_id,),
        )
        if active and current and current["scheduled_end_at"]:
            if int(current["scheduled_end_at"]) <= now:
                raise ValueError("模拟运行期限已结束，请从回测报告重新部署")
        if active and account.account_type == "mt5":
            self.memberships.assert_live_trading(user_id, account_id)
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

    def end_deployment(
        self, user_id: int, account_id: int, deployment_id: str,
    ) -> Optional[Dict]:
        """Finish a deployment while retaining its orders and audit history."""
        self._account(user_id, account_id)
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_deployments WHERE deployment_id = ? "
                "AND user_id = ? AND account_id = ?",
                (deployment_id, user_id, account_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE strategy_deployments SET status = 'completed', updated_at = ? "
                "WHERE deployment_id = ?",
                (now, deployment_id),
            )
            conn.execute(
                "UPDATE paper_orders SET status = 'canceled', canceled_at = ?, "
                "updated_at = ?, rejection_reason = '策略部署已结束' "
                "WHERE deployment_id = ? AND status = 'pending'",
                (now, now, deployment_id),
            )
            conn.commit()
        result = self.storage.fetchone(
            "SELECT * FROM strategy_deployments WHERE deployment_id = ?",
            (deployment_id,),
        )
        return dict(result) if result else None

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
        account = self._account(user_id, account_id)
        self._expire_deployments(user_id, account_id)
        rows = self.storage.fetchall(
            """
            SELECT d.*, json_extract(s.config_json, '$.strategy_name') AS strategy_name,
                   s.config_json AS config_json,
                   s.created_at AS strategy_created_at,
                   json_extract(s.config_json, '$.lifecycle_status') AS lifecycle_status,
                   1 AS strategy_enabled
            FROM strategy_deployments d
            LEFT JOIN user_strategy_configs s
              ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            WHERE d.user_id = ? AND d.account_id = ?
            ORDER BY d.created_at DESC
            """,
            (user_id, account_id),
        )
        strategy_repository = StrategyConfigRepository(self.storage)
        deployments = []
        for row in rows:
            item = dict(row)
            raw_config = json.loads(item.get("config_json") or "{}")
            if raw_config.get("source_owner_user_id"):
                strategy = strategy_repository.get_strategy_by_id(
                    user_id, item["strategy_id"]
                )
            else:
                strategy = TradingStrategy.from_dict(raw_config) if raw_config else None
            configured_lifecycle = item.get("lifecycle_status") or "draft"
            if strategy is not None:
                item["strategy_name"] = strategy.strategy_name
                configured_lifecycle = strategy.lifecycle_status
                item["strategy_offline"] = False
            else:
                # A deployment whose source strategy was deleted is retained
                # in storage for audit, but must not appear in the runtime
                # consoles or be presented as an executable deployment.
                continue
            item["configured_lifecycle_status"] = configured_lifecycle

            # The account page describes a deployment, not merely its source
            # configuration. An active paper deployment is in paper validation
            # even when a shared AI strategy's publisher still keeps its source
            # strategy in draft for reuse by other users.
            runtime_lifecycle = configured_lifecycle
            if item["status"] in {"active", "paused"}:
                if account.account_type == "paper":
                    runtime_lifecycle = StrategyLifecycle.PAPER_TRADING
                elif account.account_type == "mt5":
                    runtime_lifecycle = StrategyLifecycle.PRODUCTION
            item["runtime_lifecycle_status"] = runtime_lifecycle
            item["lifecycle_status"] = runtime_lifecycle
            deployments.append(item)
        return deployments

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
        # Load eligible deployments once per request instead of querying the
        # same table for every strategy decision in the batch.
        eligible_rows = self.storage.fetchall(
            """
            SELECT d.* FROM strategy_deployments d
            JOIN trading_accounts a ON a.id = d.account_id
            WHERE d.user_id = ? AND d.status = 'active'
              AND d.execution_mode = 'paper'
              AND a.account_type = 'paper' AND a.status = 'active'
              AND a.enabled = 1 AND a.trading_enabled = 1
              AND a.auto_trading_enabled = 1
            """,
            (user_id,),
        )
        deployments_by_key = {}
        for deployment in eligible_rows:
            key = (str(deployment["strategy_id"]), str(deployment["symbol"]))
            deployments_by_key.setdefault(key, []).append(deployment)
        for decision in decisions or []:
            if (
                decision.get("action") not in {"buy", "sell"}
                or decision.get("status") == "rejected"
            ):
                continue
            deployments = deployments_by_key.get(
                (str(decision.get("strategy_id") or ""), str(decision.get("symbol") or "")),
                [],
            )
            for deployment in deployments:
                if self._create_order(user_id, deployment, decision, now):
                    created += 1
        return created

    def _paper_loss_streak_guard(
        self, user_id: int, account_id: int, deployment: Dict,
        symbol: str, strategy: TradingStrategy, action: str, signal,
        policy_config: Optional[Dict] = None,
    ) -> Dict:
        """Protect one paper deployment/setup/direction and consume AI plans once."""
        setup_type = str(
            getattr(signal, "setup_type", "") or "generic_entry"
        )
        setup_family = str(getattr(signal, "setup_family", "") or "generic")
        plan_id = str(
            getattr(signal, "trade_plan_id", "")
            or getattr(signal, "ai_plan_id", "") or ""
        )
        plan_valid_from = int(
            getattr(signal, "trade_plan_valid_from", 0)
            or getattr(signal, "ai_plan_valid_from", 0) or 0
        )
        plan_group_id = str(getattr(signal, "trade_plan_group_id", "") or "")
        plan_instance_id = (
            f"{plan_id}:{plan_valid_from}"
            if plan_id and plan_valid_from else plan_id
        )

        # One source analysis may be used once by each deployment. Persisted
        # order attribution makes this survive service restarts and also keeps
        # separate paper accounts independent from one another.
        if plan_instance_id:
            previous_orders = self.storage.fetchall(
                "SELECT position_attribution_json FROM paper_orders "
                "WHERE user_id = ? AND account_id = ? AND deployment_id = ? "
                "ORDER BY requested_at DESC LIMIT 200",
                (
                    int(user_id), int(account_id), deployment["deployment_id"],
                ),
            )
            for order in previous_orders:
                order = dict(order)
                try:
                    attribution = json.loads(
                        order.get("position_attribution_json") or "{}"
                    )
                except (TypeError, ValueError):
                    attribution = {}
                previous_plan = str(
                    attribution.get("trade_plan_instance_id")
                    or attribution.get("ai_plan_instance_id") or ""
                )
                previous_group = str(attribution.get("trade_plan_group_id") or "")
                if previous_plan == plan_instance_id or (
                    plan_group_id and previous_group == plan_group_id
                ):
                    return {
                        "allowed": False,
                        "scope": "paper_setup",
                        "setup_type": setup_type,
                        "setup_family": setup_family,
                        "plan_instance_id": plan_instance_id,
                        "reason": "本交易计划已经触发过，不在该模拟部署重复开仓",
                    }

        config = policy_config or {}
        if not bool(config.get("loss_streak_circuit_breaker_enabled", True)):
            return {"allowed": True, "loss_streak": 0, "scope": "deployment"}
        limit = max(1, int(config.get("loss_streak_limit", 3) or 3))
        pause_seconds = max(60, int(config.get("loss_streak_pause_minutes", 10) or 10) * 60)
        # A paper trade is written once when the complete position closes;
        # partial take-profits are separate rows and must not affect the streak.
        rows = self.storage.fetchall(
            "SELECT net_profit, closed_at FROM paper_trades WHERE user_id = ? "
            "AND account_id = ? AND deployment_id = ? "
            "AND exit_reason NOT IN ('partial_take_profit', 'signal_take_profit') "
            "ORDER BY closed_at DESC, created_at DESC LIMIT 100",
            (int(user_id), int(account_id), deployment["deployment_id"]),
        )
        streak = 0
        for row in rows:
            if float(row.get("net_profit") or 0) < 0:
                streak += 1
            else:
                break
        if streak < limit:
            return {"allowed": True, "loss_streak": streak, "scope": "deployment"}
        release_at = int(rows[0].get("closed_at") or 0) + pause_seconds
        if int(time.time()) < release_at:
            return {
                "allowed": False, "loss_streak": streak, "scope": "deployment",
                "release_at": release_at,
                "reason": f"连续亏损 {streak} 次，策略部署已风险暂停至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(release_at))}",
            }
        return {
            "allowed": True, "loss_streak": streak, "scope": "deployment",
            "cooldown_completed": True,
        }

    def process_strategy_signals(
        self, user_id: int, symbol: str, current_price: float, strategy_service,
        quote_account_id: Optional[int] = None,
        signal_snapshots: Optional[Dict[str, List]] = None,
    ) -> int:
        """Use one signal snapshot per strategy, then apply account-level checks."""
        self._expire_deployments(user_id)
        self._expire_stale_pending_orders(user_id, symbol, int(time.time()))
        deployments = self.storage.fetchall(
            """
            SELECT d.* FROM strategy_deployments d
            JOIN trading_accounts a ON a.id = d.account_id
            WHERE d.user_id = ? AND d.status = 'active'
              AND d.execution_mode = 'paper' AND a.account_type = 'paper'
              AND a.status = 'active' AND a.enabled = 1
              AND a.trading_enabled = 1 AND a.auto_trading_enabled = 1
            """,
            (user_id,),
        )
        if not deployments:
            return 0
        created = 0
        now = int(time.time())
        tick_signals = {
            str(strategy_id): copy.deepcopy(list(signals or []))
            for strategy_id, signals in (signal_snapshots or {}).items()
        }
        for deployment in deployments:
            try:
                runtime_strategy = self._deployment_strategy(user_id, deployment)
                strategy = TradingStrategy.from_dict(runtime_strategy)
            except ValueError:
                continue
            if not self._strategy_matches_quote(
                user_id, strategy, symbol, quote_account_id
            ):
                continue
            strategy_key = str(strategy.strategy_id)
            if strategy_key not in tick_signals:
                generator = getattr(
                    strategy_service.signal_service,
                    "generate_signals_for_strategy",
                    None,
                )
                generated = (
                    generator(symbol, current_price, strategy)
                    if generator is not None else None
                )
                if generated is None:
                    generated = [
                        signal for signal in
                        strategy_service.signal_service.get_active_signals(symbol)
                        if getattr(signal, "strategy_id", "") == strategy.strategy_id
                    ]
                # Multiple paper accounts deploying the same strategy must use
                # the same source result for this Tick as well.
                tick_signals[strategy_key] = copy.deepcopy(list(generated or []))
            signals = copy.deepcopy(tick_signals[strategy_key])
            account_id = int(deployment["account_id"])
            policy_snapshot = runtime_strategy["position_management_policy_snapshot"]
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
                position_checker=lambda s, st, action, aid=account_id, dep=deployment: (
                    self._paper_position_check(
                        aid, s, st, action, str(dep["deployment_id"])
                    )
                ),
                risk_checker=lambda s, volume, risk, st, aid=account_id, px=current_price: (
                    self._paper_risk_check(aid, s, volume, px)
                ),
                entry_guard=lambda s, st, action, signal, aid=account_id, dep=deployment: (
                    self._paper_loss_streak_guard(
                        user_id, aid, dep, s, st, action, signal,
                        policy_snapshot.get("config") or {},
                    )
                ),
                position_policy=PositionManagementPolicy.from_dict(policy_snapshot),
                audit_no_action=True,
            )
            if decision is not None:
                # Keep the simulated order on the EA's native broker symbol
                # after the strategy was matched through a platform mapping.
                decision.symbol = symbol
                decision_payload = decision.to_dict()
                if (
                    decision.action == "none"
                    and decision.decision_type == "no_action"
                ):
                    transient_decision_store.record(
                        user_id, account_id, decision,
                    )
                else:
                    transient_decision_store.clear_for_strategy(
                        user_id, account_id, decision.strategy_id, decision.symbol,
                    )
                    runtime = RuntimeStateRepository(
                        user_id, account_id, self.storage,
                    )
                    runtime.upsert_entity(
                        "strategy_decision", decision.decision_id, decision_payload,
                        symbol=decision.symbol, status=decision.status,
                    )
            if decision and decision_payload.get("action") != "none" and self._create_order(
                user_id, deployment, decision_payload, now
            ):
                created += 1
        return created

    def _strategy_matches_quote(
        self, user_id: int, strategy: TradingStrategy, quote_symbol: str,
        quote_account_id: Optional[int],
    ) -> bool:
        if str(strategy.symbol).upper() == str(quote_symbol).upper():
            return True
        if not quote_account_id:
            return False
        quote_account = self.accounts.get_by_id(user_id, int(quote_account_id))
        if quote_account is None:
            return False
        source_user_id = int(strategy.source_owner_user_id or user_id)
        source_server = self.instrument_mappings.source_server(
            source_user_id, strategy.symbol
        )
        return self.instrument_mappings.compatible(
            source_server, strategy.symbol,
            str(quote_account.mt5_server or ""), quote_symbol,
        )

    def process_tick(
        self,
        user_id: int,
        symbol: str,
        bid: float,
        ask: Optional[float] = None,
        timestamp: Optional[int] = None,
        pivots: Optional[List[Dict]] = None,
        structures: Optional[Dict[str, Dict]] = None,
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
            self._expire_stale_pending_orders(user_id, symbol, now)
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
                    user_id, int(row["id"]), symbol, bid, ask, now, pivots or [], structures or {}
                )
                for key in summary:
                    summary[key] += result[key]
            return summary

    def _expire_stale_pending_orders(
        self, user_id: int, symbol: str, now: int,
    ) -> int:
        cutoff = int(now) - self.PENDING_ORDER_TIMEOUT_SECONDS
        orders = self.storage.fetchall(
            """
            SELECT order_id, account_id, decision_id, deployment_id,
                   strategy_id, position_attribution_json
            FROM paper_orders
            WHERE user_id = ? AND symbol = ? AND status = 'pending'
              AND requested_at <= ?
            """,
            (user_id, symbol, cutoff),
        )
        if not orders:
            return 0
        reason = "等待下一次行情撮合超时，订单已自动取消"
        for order in orders:
            self.storage.execute(
                """
                UPDATE paper_orders
                SET status = 'canceled', rejection_reason = ?,
                    canceled_at = ?, updated_at = ?
                WHERE order_id = ? AND status = 'pending'
                """,
                (reason, now, now, order["order_id"]),
            )
            self._sync_paper_decision_status(
                user_id, int(order["account_id"]), str(order["decision_id"]),
                str(order["order_id"]), status="expired", auto_executed=False,
            )
            try:
                attribution = json.loads(
                    order["position_attribution_json"] or "{}"
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
            plan_id = str(attribution.get("trade_plan_id") or "")
            if plan_id:
                self.structure_plans.record_execution(
                    int(user_id), int(order["account_id"]),
                    str(order["deployment_id"]), str(order["strategy_id"]),
                    plan_id, str(attribution.get("trade_plan_group_id") or ""),
                    "expired", order_id=str(order["order_id"]), reason=reason,
                    payload=attribution,
                )
        return len(orders)

    def _equity_curve(self, account_id: int, page_size: int, offset: int,
                      equity_from: Optional[int] = None,
                      equity_to: Optional[int] = None) -> List[Dict]:
        sql = "SELECT point_time AS time, balance, equity, free_margin, margin, open_positions FROM paper_equity_points WHERE account_id = ?"
        params: List = [account_id]
        if equity_from is not None:
            sql += " AND point_time >= ?"; params.append(int(equity_from))
        if equity_to is not None:
            sql += " AND point_time <= ?"; params.append(int(equity_to))
        sql += " ORDER BY point_time DESC LIMIT ? OFFSET ?"
        params.extend([min(page_size * 10, 500), offset])
        return [dict(row) for row in self.storage.fetchall(sql, tuple(params))][::-1]

    def get_account_detail(self, user_id: int, account_id: int,
                           page: int = 1, page_size: int = 30,
                           equity_from: Optional[int] = None,
                           equity_to: Optional[int] = None) -> Dict:
        account = self._paper_account(user_id, account_id)
        settings = self._settings(account_id)
        # 决策快照保存在运行态仓储中；订单/成交通过 decision_id 读取开仓原因，
        # 不把会变化的策略配置反向当作历史原因。
        decision_reasons = {}
        try:
            runtime = RuntimeStateRepository(user_id, account_id, self.storage)
            # 运行态只用于补充最近订单的开仓原因；无界读取历史决策会让账户
            # 详情在长期运行账户上越来越慢。订单页本身只展示最近 30 条，
            # 因此读取最近 1000 条快照已足够覆盖关联，并避免首屏卡死。
            for payload in runtime.list_entities("strategy_decision", limit=1000):
                decision_id = str(payload.get("decision_id") or "")
                if decision_id:
                    decision_reasons[decision_id] = str(
                        payload.get("decision_reason")
                        or payload.get("signal_summary", {}).get("summary")
                        or "策略信号触发开仓"
                    )
        except Exception:
            # 历史运行态缺失不应影响模拟账户详情页面。
            decision_reasons = {}

        deployments = [dict(row) for row in self.storage.fetchall(
            """
            SELECT d.*, json_extract(s.config_json, '$.strategy_name') AS strategy_name
            FROM strategy_deployments d
            JOIN user_strategy_configs s
              ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            WHERE d.user_id = ? AND d.account_id = ?
            ORDER BY d.created_at DESC
            """,
            (user_id, account_id),
        )]
        positions = [dict(row) for row in self.storage.fetchall(
            "SELECT * FROM paper_positions WHERE account_id = ? AND status = 'open' ORDER BY opened_at DESC",
            (account_id,),
        )]
        for position in positions:
            position["position_attribution"] = json.loads(
                position.get("position_attribution_json") or "{}"
            )
            attribution = position["position_attribution"]
            position["setup_type"] = attribution.get("setup_type", "")
            position["setup_profile_name"] = attribution.get(
                "setup_profile_name", ""
            )
            position["open_reason"] = attribution.get("entry_reason", "")
            position["management_events"] = self.position_events.list_for_position(
                user_id, account_id, position["position_id"]
            )
        page = max(1, int(page)); page_size = max(1, min(int(page_size), 100))
        offset = (page - 1) * page_size
        orders = [dict(row) for row in self.storage.fetchall(
            """
            SELECT o.*, p.position_id AS linked_position_id,
                   COALESCE(p.entry_price, o.filled_price, o.requested_price) AS execution_entry_price
            FROM paper_orders o
            LEFT JOIN paper_positions p ON p.order_id = o.order_id
            WHERE o.account_id = ?
            ORDER BY o.requested_at DESC, o.order_id DESC LIMIT ? OFFSET ?
            """,
            (account_id, page_size + 1, offset),
        )]
        orders_has_more = len(orders) > page_size
        orders = orders[:page_size]
        for order in orders:
            order["position_attribution"] = json.loads(
                order.get("position_attribution_json") or "{}"
            )
            attribution = order["position_attribution"]
            order["position_id"] = order.get("linked_position_id") or ""
            order["open_reason"] = (
                attribution.get("entry_reason")
                or
                decision_reasons.get(str(order.get("decision_id") or ""))
                or ("模拟风控拒绝：" + str(order.get("rejection_reason")))
                if order.get("status") == "rejected" and order.get("rejection_reason")
                else (
                    attribution.get("entry_reason")
                    or decision_reasons.get(
                        str(order.get("decision_id") or ""), "策略信号触发开仓"
                    )
                )
            )
            order["setup_type"] = attribution.get("setup_type", "")
            order["setup_profile_name"] = attribution.get("setup_profile_name", "")
            order["initial_stop_loss"] = float(
                attribution.get("initial_stop_loss") or order.get("stop_loss") or 0
            )
            order["initial_take_profit"] = float(
                attribution.get("initial_take_profit") or order.get("take_profit") or 0
            )
        trades = [dict(row) for row in self.storage.fetchall(
            """
            SELECT t.*, o.stop_loss AS initial_stop_loss,
                   o.take_profit AS initial_take_profit,
                   o.decision_id AS open_decision_id
            FROM paper_trades t
            LEFT JOIN paper_orders o ON o.order_id = t.order_id
            WHERE t.account_id = ?
            ORDER BY t.closed_at DESC, t.trade_id DESC LIMIT ? OFFSET ?
            """,
            (account_id, page_size + 1, offset),
        )]
        trades_has_more = len(trades) > page_size
        trades = trades[:page_size]
        for trade in trades:
            trade["position_attribution"] = json.loads(
                trade.get("position_attribution_json") or "{}"
            )
            attribution = trade["position_attribution"]
            trade["position_id"] = trade.get("position_id") or ""
            trade["open_reason"] = (
                attribution.get("entry_reason")
                or decision_reasons.get(
                    str(trade.get("open_decision_id") or ""), "策略信号触发开仓"
                )
            )
            trade["setup_type"] = attribution.get("setup_type", "")
            trade["setup_profile_name"] = attribution.get("setup_profile_name", "")
            trade["initial_stop_loss"] = float(
                attribution.get("initial_stop_loss")
                or trade.get("initial_stop_loss") or 0
            )
            trade["initial_take_profit"] = float(
                attribution.get("initial_take_profit")
                or trade.get("initial_take_profit") or 0
            )
            trade["realized_r"] = float(attribution.get("realized_r") or 0)
            reason = str(trade.get("exit_reason") or "")
            trade["close_reason"] = reason
            trade["execution_reason"] = {
                "stop_loss": "触发持仓止损",
                "take_profit": "触发持仓止盈",
                "partial_take_profit": "达到分批止盈条件",
                "signal_take_profit": "达到 AI 止盈计划",
                "trailing_stop": "触发移动止损",
                "reverse_signal": "出现反向信号",
                "max_holding_bars": "达到最大持仓时间",
            }.get(reason, reason or "持仓平仓")
        return {
            "account": self._account_dict(account),
            "settings": settings,
            "deployments": deployments,
            "orders": orders,
            "positions": positions,
            "trades": trades,
            "strategy_performance": build_paper_performance(
                self.storage, user_id, account_id,
            ),
            "runtime_logs": [
                {
                    **dict(row),
                    "payload": json.loads(row["payload_json"] or "{}"),
                }
                for row in self.storage.fetchall(
                    """
                    SELECT * FROM paper_runtime_logs
                    WHERE account_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                    """,
                    (account_id, page_size + 1, offset),
                )[:page_size]
            ],
            "equity_curve": self._equity_curve(account_id, page_size, offset, equity_from, equity_to),
            "page": page,
            "page_size": page_size,
            "orders_has_more": orders_has_more,
            "trades_has_more": trades_has_more,
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
        where = "t.account_id = ?"
        if strategy_id:
            where += " AND t.strategy_id = ?"
            params.append(strategy_id)
        if started_at:
            where += " AND t.opened_at >= ?"
            params.append(int(started_at))
        trades = [dict(row) for row in self.storage.fetchall(
            f"""SELECT t.*, o.stop_loss AS opening_stop_loss,
                       o.take_profit AS opening_take_profit
                FROM paper_trades t
                LEFT JOIN paper_orders o ON o.order_id = t.order_id
                WHERE {where} ORDER BY t.closed_at""",
            params,
        )]
        position_outcomes = self._position_trade_outcomes(trades)
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
        position_profits = [
            float(item.get("net_profit") or 0) for item in position_outcomes
        ]
        position_wins = [value for value in position_profits if value > 0]
        position_losses = [value for value in position_profits if value < 0]
        position_r_values = [
            float(item.get("realized_r") or 0) for item in position_outcomes
        ]
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
        attributed_outcomes = [
            item for item in position_outcomes if item.get("setup_type")
        ]
        by_setup = self._group_position_outcomes(
            attributed_outcomes, "setup_type"
        )
        by_setup_family = self._group_position_outcomes(
            attributed_outcomes, "setup_family"
        )
        by_setup_profile = self._group_position_outcomes(
            attributed_outcomes, "setup_profile_name"
        )
        by_setup_direction = self._group_position_outcomes(
            attributed_outcomes, "setup_direction"
        )
        rejected = sum(item["status"] == "rejected" for item in orders)
        summary = {
            "trade_count": len(trades),
            "deal_count": len(trades),
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
            "closed_position_count": len(position_outcomes),
            "position_win_count": len(position_wins),
            "position_loss_count": len(position_losses),
            "position_win_rate": round(
                len(position_wins) / len(position_outcomes) * 100, 2
            ) if position_outcomes else 0,
            "position_profit_factor": (
                round(sum(position_wins) / abs(sum(position_losses)), 2)
                if position_losses else None
            ),
            "average_position_r": round(
                statistics.mean(position_r_values), 3
            ) if position_r_values else 0,
            "setup_attributed_position_count": len(attributed_outcomes),
            "setup_unattributed_position_count": (
                len(position_outcomes) - len(attributed_outcomes)
            ),
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
            "by_setup": by_setup,
            "by_setup_family": by_setup_family,
            "by_setup_profile": by_setup_profile,
            "by_setup_direction": by_setup_direction,
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

    @staticmethod
    def _position_trade_outcomes(trades: List[Dict]) -> List[Dict]:
        """Collapse partial exits into one completed-position outcome.

        Setup quality must be measured per position. Counting every partial
        take-profit as a separate winning trade inflates both the sample size
        and win rate, which is exactly the wrong signal for strategy tuning.
        """
        grouped: Dict[str, List[Dict]] = {}
        for trade in trades:
            key = str(trade.get("position_id") or trade.get("trade_id") or "")
            grouped.setdefault(key, []).append(trade)

        outcomes = []
        for position_id, items in grouped.items():
            ordered = sorted(
                items,
                key=lambda item: (
                    int(item.get("closed_at") or 0),
                    str(item.get("trade_id") or ""),
                ),
            )
            attribution = {}
            for item in reversed(ordered):
                raw = item.get("position_attribution")
                if not isinstance(raw, dict):
                    try:
                        raw = json.loads(
                            item.get("position_attribution_json") or "{}"
                        )
                    except (TypeError, ValueError):
                        raw = {}
                if raw:
                    attribution = raw
                    break
            total_volume = sum(float(item.get("volume") or 0) for item in ordered)
            net_profit = sum(float(item.get("net_profit") or 0) for item in ordered)
            gross_profit = sum(float(item.get("gross_profit") or 0) for item in ordered)
            commission = sum(float(item.get("commission") or 0) for item in ordered)
            initial_risk = float(attribution.get("initial_risk") or 0)
            if initial_risk <= 0:
                entry_price = float(ordered[0].get("entry_price") or 0)
                opening_stop = float(ordered[0].get("opening_stop_loss") or 0)
                if entry_price > 0 and opening_stop > 0:
                    initial_risk = abs(entry_price - opening_stop)
            symbol = str(ordered[-1].get("symbol") or "")
            _, contract_size = market_spec(symbol)
            risk_amount = initial_risk * total_volume * contract_size
            realized_r = (
                net_profit / risk_amount if risk_amount > 0
                else float(attribution.get("realized_r") or 0)
            )
            setup_type = str(attribution.get("setup_type") or "")
            direction = str(ordered[-1].get("direction") or "")
            outcomes.append({
                "position_id": position_id,
                "strategy_id": str(ordered[-1].get("strategy_id") or ""),
                "symbol": symbol,
                "direction": direction,
                "setup_type": setup_type,
                "setup_family": str(attribution.get("setup_family") or ""),
                "setup_profile_id": str(attribution.get("setup_profile_id") or ""),
                "setup_profile_name": str(attribution.get("setup_profile_name") or ""),
                "setup_direction": (
                    f"{setup_type}|{direction}" if setup_type else ""
                ),
                "entry_mode": str(attribution.get("entry_mode") or ""),
                "exit_reason": str(
                    attribution.get("exit_reason")
                    or ordered[-1].get("exit_reason") or ""
                ),
                "net_profit": net_profit,
                "gross_profit": gross_profit,
                "commission": commission,
                "realized_r": realized_r,
                "opened_at": min(int(item.get("opened_at") or 0) for item in ordered),
                "closed_at": max(int(item.get("closed_at") or 0) for item in ordered),
            })
        return sorted(outcomes, key=lambda item: item["closed_at"])

    @staticmethod
    def _group_position_outcomes(
        outcomes: List[Dict], key: str,
    ) -> List[Dict]:
        groups: Dict[str, List[Dict]] = {}
        for outcome in outcomes:
            name = str(outcome.get(key) or "unknown")
            groups.setdefault(name, []).append(outcome)

        results = []
        for name, values in groups.items():
            profits = [float(item.get("net_profit") or 0) for item in values]
            r_values = [float(item.get("realized_r") or 0) for item in values]
            wins = [value for value in profits if value > 0]
            losses = [value for value in profits if value < 0]
            win_r = [value for value in r_values if value > 0]
            loss_r = [value for value in r_values if value < 0]
            consecutive_losses = 0
            maximum_consecutive_losses = 0
            for value in profits:
                if value < 0:
                    consecutive_losses += 1
                    maximum_consecutive_losses = max(
                        maximum_consecutive_losses, consecutive_losses
                    )
                else:
                    consecutive_losses = 0
            count = len(values)
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            sample_status = (
                "insufficient" if count < 10
                else "preliminary" if count < 30
                else "reliable"
            )
            results.append({
                "name": name,
                "position_count": count,
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": round(len(wins) / count * 100, 2) if count else 0,
                "net_profit": round(sum(profits), 2),
                "average_profit": round(statistics.mean(profits), 2) if profits else 0,
                "profit_factor": (
                    round(gross_win / gross_loss, 2) if gross_loss else None
                ),
                "total_r": round(sum(r_values), 3),
                "average_r": round(statistics.mean(r_values), 3) if r_values else 0,
                "average_win_r": round(statistics.mean(win_r), 3) if win_r else 0,
                "average_loss_r": round(statistics.mean(loss_r), 3) if loss_r else 0,
                "payoff_ratio_r": (
                    round(statistics.mean(win_r) / abs(statistics.mean(loss_r)), 2)
                    if win_r and loss_r and statistics.mean(loss_r) != 0 else None
                ),
                "max_consecutive_losses": maximum_consecutive_losses,
                "commission": round(sum(float(item.get("commission") or 0) for item in values), 2),
                "sample_status": sample_status,
            })
        return sorted(
            results,
            key=lambda item: (-item["position_count"], item["name"]),
        )

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

    def _paper_position_check(
        self, account_id, symbol, strategy, action, deployment_id: str = "",
    ) -> Dict:
        scope_sql = " AND deployment_id = ?" if deployment_id else ""
        scope_params = (account_id, symbol, deployment_id) if deployment_id else (
            account_id, symbol,
        )
        rows = self.storage.fetchall(
            "SELECT direction FROM paper_positions WHERE account_id = ? "
            f"AND symbol = ? AND status = 'open'{scope_sql}",
            scope_params,
        )
        pending = self.storage.fetchall(
            "SELECT direction FROM paper_orders WHERE account_id = ? "
            f"AND symbol = ? AND status = 'pending'{scope_sql}",
            scope_params,
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
            today_start = risk_day_start_timestamp()
            daily = self.storage.fetchone(
                """
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(net_profit), 0) AS net_profit
                FROM paper_trades WHERE account_id = ? AND closed_at >= ?
                """,
                (account_id, today_start),
            )
            orders = self.storage.fetchone(
                """
                SELECT COUNT(*) AS count
                FROM paper_orders
                WHERE account_id = ? AND requested_at >= ?
                  AND status IN ('pending', 'filled')
                """,
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
        account_open_count = int(self.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_positions WHERE account_id = ? AND status = 'open'",
            (account_id,),
        )["count"])
        account_limits = self.storage.fetchone(
            "SELECT max_total_positions, max_single_volume FROM trading_accounts WHERE id = ?",
            (account_id,),
        )
        account_pending_count = int(self.storage.fetchone(
            "SELECT COUNT(*) AS count FROM paper_orders WHERE account_id = ? AND status = 'pending'",
            (account_id,),
        )["count"])
        reason = (
            str(decision.get("decision_reason") or "模拟盘独立风控未通过")
            if decision.get("status") == "rejected" else ""
        )
        if not reason and account_open_count + account_pending_count >= int(
            account_limits["max_total_positions"]
        ):
            reason = "已达到账户最大持仓数"
        deployment_id = str(deployment["deployment_id"])
        strategy_count = int(self.storage.fetchone(
            """
            SELECT (
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id = ? AND deployment_id = ? AND status = 'open'
            ) + (
                SELECT COUNT(*) FROM paper_orders
                WHERE account_id = ? AND deployment_id = ? AND status = 'pending'
            ) AS count
            """,
            (account_id, deployment_id, account_id, deployment_id),
        )["count"])
        if not reason and strategy_count >= max(
            1, int(strategy.get("max_positions", 3))
        ):
            reason = "已达到策略最大持仓数"
        same_direction = int(self.storage.fetchone(
            """
            SELECT (
                SELECT COUNT(*) FROM paper_positions
                WHERE account_id = ? AND deployment_id = ? AND symbol = ?
                  AND status = 'open' AND direction = ?
            ) + (
                SELECT COUNT(*) FROM paper_orders
                WHERE account_id = ? AND deployment_id = ? AND symbol = ?
                  AND status = 'pending' AND direction = ?
            ) AS count
            """,
            (
                account_id, deployment_id, decision["symbol"], decision["action"],
                account_id, deployment_id, decision["symbol"], decision["action"],
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
        attribution = build_position_attribution(
            summary,
            decision_id=str(decision.get("decision_id") or ""),
            strategy_id=str(decision.get("strategy_id") or ""),
            strategy_name=str(decision.get("strategy_name") or ""),
            direction=str(decision.get("action") or ""),
            entry_reason=str(decision.get("decision_reason") or ""),
            initial_stop_loss=sl,
            initial_take_profit=tp,
            initial_volume=max(0.01, float(decision.get("volume", 0.01))),
        )
        requested_volume = max(0.01, float(decision.get("volume", 0.01)))
        if requested_volume > float(account_limits["max_single_volume"]):
            reason = "超过账户单笔最大手数"
        if not reason and not self._valid_exits(decision["action"], entry, sl, tp):
            reason = "止盈止损价格无效"
        order_id = uuid.uuid4().hex[:12]
        status = "rejected" if reason else "pending"
        trade_plan_id = str(attribution.get("trade_plan_id") or "")
        trade_plan_group_id = str(
            attribution.get("trade_plan_group_id") or ""
        )
        claimed_structure_plan = False
        if trade_plan_id and status == "pending":
            claimed_structure_plan = self.structure_plans.claim_execution(
                int(user_id), account_id, deployment_id,
                str(deployment["strategy_id"]), trade_plan_id,
                trade_plan_group_id,
                reason=str(decision.get("decision_reason") or ""),
                payload=attribution,
            )
            if not claimed_structure_plan:
                # Another Tick/worker (or an earlier decision) has already
                # consumed this public plan for the same deployment.
                return False
        try:
            self.storage.execute(
                """
                INSERT INTO paper_orders(
                    order_id, user_id, account_id, deployment_id, strategy_id,
                    decision_id, symbol, direction, status, requested_volume,
                    requested_price, stop_loss, take_profit, confidence,
                    signal_source_id, exit_mode, trailing_activation_r,
                    trailing_distance_r, position_policy_snapshot_json,
                    position_attribution_json,
                    rejection_reason, requested_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, user_id, account_id, deployment["deployment_id"],
                    deployment["strategy_id"], decision["decision_id"],
                    decision["symbol"], decision["action"], status,
                    requested_volume, entry, sl, tp,
                    float(decision.get("confidence_score", 0)), source_id,
                    "position_manager", 1.0, 1.0,
                    json.dumps(policy_snapshot, ensure_ascii=False),
                    json.dumps(attribution, ensure_ascii=False),
                    reason, now, now, now,
                ),
            )
            if trade_plan_id and status == "pending":
                self.structure_plans.record_execution(
                    int(user_id), account_id, deployment["deployment_id"],
                    deployment["strategy_id"], trade_plan_id,
                    trade_plan_group_id,
                    "ordered", order_id=order_id,
                    reason=str(decision.get("decision_reason") or ""),
                    payload=attribution,
                )
            if status == "rejected":
                # The order can be rejected by the creation-time second guard
                # after the decision audit was persisted as pending.  Reflect
                # that terminal result immediately instead of leaving the
                # execution centre showing "等待模拟撮合" indefinitely.
                self._sync_paper_decision_status(
                    int(user_id), account_id, str(decision.get("decision_id") or ""),
                    order_id, status="rejected", auto_executed=False,
                )
            return True
        except Exception as exc:
            if claimed_structure_plan:
                self.structure_plans.release_claim(
                    int(user_id), account_id, deployment_id, trade_plan_id,
                    reason=f"模拟订单写入失败：{exc}",
                )
            if "UNIQUE constraint failed" in str(exc):
                return False
            raise

    def _process_account_tick(
        self, user_id: int, account_id: int, symbol: str,
        bid: float, ask: float, now: int, pivots: List[Dict], structures: Dict[str, Dict],
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
        decision_updates = []
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
                if open_count >= int(account["max_total_positions"]):
                    self._reject_order(conn, order["order_id"], "成交时已达到账户最大持仓数", now)
                    result["rejected"] += 1
                    continue
                deployment_open_count = int(conn.execute(
                    "SELECT COUNT(*) AS count FROM paper_positions "
                    "WHERE account_id = ? AND deployment_id = ? AND status = 'open'",
                    (account_id, order["deployment_id"]),
                ).fetchone()["count"])
                if deployment_open_count >= max(
                    1, int(strategy.get("max_positions", 3))
                ):
                    self._reject_order(conn, order["order_id"], "成交时已达到策略最大持仓数", now)
                    result["rejected"] += 1
                    continue
                deployment_direction_count = int(conn.execute(
                    "SELECT COUNT(*) AS count FROM paper_positions "
                    "WHERE account_id = ? AND deployment_id = ? AND symbol = ? "
                    "AND status = 'open' AND direction = ?",
                    (account_id, order["deployment_id"], symbol, order["direction"]),
                ).fetchone()["count"])
                if deployment_direction_count >= max(
                    1, int(strategy.get("max_same_direction", 2))
                ):
                    self._reject_order(
                        conn, order["order_id"], "成交时已达到同方向最大持仓数", now
                    )
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
                        close_reason,
                        entry_price, stop_loss, take_profit, open_commission,
                        current_price, remaining_volume,
                        partial_levels_done_json, signal_source_id, exit_mode,
                        trailing_activation_r, trailing_distance_r, initial_risk,
                        favorable_price, position_policy_snapshot_json,
                        position_attribution_json,
                        opened_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position_id, user_id, account_id, order["order_id"],
                        order["deployment_id"], order["strategy_id"], symbol,
                        order["direction"], volume, fill_price, order["stop_loss"],
                        order["take_profit"], commission,
                        bid if order["direction"] == "buy" else ask,
                        volume, "[]",
                        order["signal_source_id"], order["exit_mode"],
                        order["trailing_activation_r"], order["trailing_distance_r"],
                        abs(fill_price - float(order["stop_loss"])), fill_price,
                        order["position_policy_snapshot_json"],
                        order["position_attribution_json"],
                        now, now, now,
                    ),
                )
                decision_updates.append((
                    str(order["decision_id"]), str(order["order_id"]),
                    "confirmed", True,
                ))
                policy_snapshot = json.loads(order["position_policy_snapshot_json"] or "{}")
                conn.execute(
                    """
                    INSERT INTO position_management_events(
                        event_id, user_id, account_id, position_key, position_id,
                        ticket, symbol, event_time, event_type, rule_type, status,
                        message, price, stop_loss, take_profit, volume,
                        payload_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex, user_id, account_id, position_id,
                        position_id, symbol, now, "initial_plan",
                        "initial_plan", "triggered",
                        "模拟成交后建立初始止损止盈保护",
                        fill_price, float(order["stop_loss"]),
                        float(order["take_profit"]), volume,
                        json.dumps({
                            "policy_id": policy_snapshot.get("policy_id", ""),
                            "policy_name": policy_snapshot.get("name", ""),
                            "initial_risk": abs(fill_price - float(order["stop_loss"])),
                            "exit_levels": policy_snapshot.get("exit_levels", []),
                            "disaster_stop_loss": policy_snapshot.get(
                                "disaster_stop_loss", float(order["stop_loss"])
                            ),
                            "stop_rule": (
                                policy_snapshot.get("config", {})
                                .get("initial_stop_rules", [{}])[0]
                            ),
                            "take_profit_rule": (
                                policy_snapshot.get("config", {})
                                .get("initial_take_profit_rules", [{}])[0]
                            ),
                        }, ensure_ascii=False),
                        now,
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
                policy_snapshot = json.loads(
                    position["position_policy_snapshot_json"] or "{}"
                )
                signal_tp_partial = float(
                    policy_snapshot.get("config", {}).get(
                        "signal_take_profit_close_percent", 0
                    ) or 0
                )
                partial_done = set(json.loads(
                    position["partial_levels_done_json"] or "[]"
                ))
                if position["direction"] == "buy":
                    if mark <= float(position["stop_loss"]):
                        reason = "stop_loss"
                    elif (
                        float(position["take_profit"]) > 0
                        and mark >= float(position["take_profit"])
                    ):
                        reason = (
                            "" if signal_tp_partial > 0
                            and "signal_take_profit" not in partial_done
                            else "take_profit"
                        )
                else:
                    if mark >= float(position["stop_loss"]):
                        reason = "stop_loss"
                    elif (
                        float(position["take_profit"]) > 0
                        and mark <= float(position["take_profit"])
                    ):
                        reason = (
                            "" if signal_tp_partial > 0
                            and "signal_take_profit" not in partial_done
                            else "take_profit"
                        )
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
                    favorable = float(position["favorable_price"] or position["entry_price"])
                    favorable = (
                        max(favorable, mark) if position["direction"] == "buy"
                        else min(favorable, mark)
                    )
                    # Position-manager trailing exits depend on the historical
                    # high/low watermark. Persist it even when this Tick does
                    # not emit an action, otherwise the next Tick would reload
                    # the stale value and a later pullback could not trigger.
                    conn.execute(
                        "UPDATE paper_positions SET favorable_price = ? "
                        "WHERE position_id = ?",
                        (favorable, position["position_id"]),
                    )
                    position_state = dict(position)
                    position_state["favorable_price"] = favorable
                    position_state["remaining_volume"] = float(
                        position["remaining_volume"] or position["volume"]
                    )
                    position_state["initial_volume"] = float(position["volume"])
                    position_state["partial_levels_done"] = json.loads(
                        position["partial_levels_done_json"] or "[]"
                    )
                    # 保本止损不单独占用表字段：根据当前保护价是否已到
                    # 入场价判断，兼容已有模拟持仓并避免每个 TICK 重复触发。
                    entry_price = float(position["entry_price"])
                    current_sl = float(position["stop_loss"] or 0)
                    position_state["break_even_done"] = bool(
                        (position["direction"] == "buy" and current_sl >= entry_price)
                        or (position["direction"] == "sell" and current_sl <= entry_price)
                    )
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
                    attribution = json.loads(position["position_attribution_json"] or "{}")
                    position_state["exit_levels"] = list(
                        attribution.get("exit_levels") or []
                    )
                    structure_period = str(attribution.get("signal_source_period") or "M5").upper()
                    structure = structures.get(structure_period) or {}
                    action = self.position_manager.evaluate(
                        policy_snapshot.get("config", {}), position_state,
                        {"price": mark, "time": now,
                         "atr": float(structure.get("atr") or 0),
                         "structure_hierarchy": structure.get("structure_hierarchy") or {}}, pivots=pivots,
                    )
                    for event in action.events:
                        if event.get("status") == "triggered":
                            # Persist the effective value so the timeline shows
                            # the new protection level rather than the old one.
                            event_stop_loss = event.get(
                                "new_stop_loss", position["stop_loss"]
                            )
                            self.position_events.record(
                                user_id, account_id, position["position_id"],
                                event.get("rule_type", "position_management"),
                                event.get("message", ""),
                                symbol=symbol,
                                position_id=position["position_id"],
                                rule_type=event.get("rule_type", ""),
                                status=event.get("status", ""),
                                price=event.get("price", mark),
                                stop_loss=event_stop_loss,
                                take_profit=position["take_profit"],
                                volume=position_state["remaining_volume"],
                                payload=event,
                                event_time=now,
                            )
                    if action.action == "close":
                        reason = action.reason
                    elif action.action == "partial_close" and action.close_volume:
                        remaining = float(
                            position["remaining_volume"] or position["volume"]
                        )
                        close_volume = min(remaining, float(action.close_volume))
                        if close_volume > 0:
                            multiplier = 1 if position["direction"] == "buy" else -1
                            gross = (
                                mark - float(position["entry_price"])
                            ) * multiplier * close_volume * contract_size
                            commission = close_volume * settings["commission_per_lot"]
                            net = gross - commission
                            risk_amount = float(position["initial_risk"] or 0) * close_volume * contract_size
                            trade_attribution = close_position_attribution(
                                json.loads(position["position_attribution_json"] or "{}"),
                                action.reason or "partial_take_profit",
                                net / risk_amount if risk_amount > 0 else 0,
                            )
                            balance += gross - commission
                            trade_id = uuid.uuid4().hex[:12]
                            done = set(position_state["partial_levels_done"])
                            done.update(action.level_ids or [action.level_id])
                            conn.execute(
                                """
                                INSERT INTO paper_trades(
                                    trade_id, user_id, account_id, order_id, position_id,
                                    deployment_id, strategy_id, symbol, direction, volume,
                                    entry_price, exit_price, gross_profit, commission,
                                    net_profit, exit_reason, opened_at, closed_at, created_at
                                    , position_attribution_json
                                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    trade_id, user_id, account_id, position["order_id"],
                                    position["position_id"], position["deployment_id"],
                                    position["strategy_id"], symbol, position["direction"],
                                    close_volume, position["entry_price"], mark,
                                    gross, commission, net,
                                    action.reason or "partial_take_profit",
                                    position["opened_at"], now, now,
                                    json.dumps(trade_attribution, ensure_ascii=False),
                                ),
                            )
                            conn.execute(
                                """
                                UPDATE paper_positions SET remaining_volume = ?,
                                    partial_levels_done_json = ?, stop_loss = ?,
                                    take_profit = ?, favorable_price = ?,
                                    updated_at = ? WHERE position_id = ?
                                """,
                                (
                                    max(0.0, remaining - close_volume),
                                    json.dumps(sorted(done), ensure_ascii=False),
                                    action.stop_loss or position["stop_loss"],
                                    0 if action.level_id == "signal_take_profit"
                                    else position["take_profit"],
                                    favorable, now, position["position_id"],
                                ),
                            )
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
                    close_volume = float(
                        position["remaining_volume"] or position["volume"]
                    )
                    gross = (
                        exit_price - float(position["entry_price"])
                    ) * multiplier * close_volume * contract_size
                    close_commission = close_volume * settings["commission_per_lot"]
                    total_commission = float(position["open_commission"]) + close_commission
                    net = gross - total_commission
                    risk_amount = float(position["initial_risk"] or 0) * close_volume * contract_size
                    trade_attribution = close_position_attribution(
                        json.loads(position["position_attribution_json"] or "{}"),
                        reason, net / risk_amount if risk_amount > 0 else 0,
                    )
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
                            , position_attribution_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trade_id, user_id, account_id, position["order_id"],
                            position["position_id"], position["deployment_id"],
                            position["strategy_id"], symbol, position["direction"],
                            close_volume, position["entry_price"], exit_price,
                            gross, total_commission, net, reason,
                            position["opened_at"], now, now,
                            json.dumps(trade_attribution, ensure_ascii=False),
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
        for decision_id, order_id, status, auto_executed in decision_updates:
            self._sync_paper_decision_status(
                user_id, account_id, decision_id, order_id,
                status=status, auto_executed=auto_executed,
            )
        for order in pending:
            try:
                attribution = json.loads(
                    order["position_attribution_json"] or "{}"
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
            plan_id = str(attribution.get("trade_plan_id") or "")
            if not plan_id:
                continue
            outcome = self.storage.fetchone(
                "SELECT status,rejection_reason FROM paper_orders WHERE order_id=?",
                (str(order["order_id"]),),
            )
            if not outcome or outcome["status"] not in {"filled", "rejected"}:
                continue
            self.structure_plans.record_execution(
                int(user_id), int(account_id), str(order["deployment_id"]),
                str(order["strategy_id"]), plan_id,
                str(attribution.get("trade_plan_group_id") or ""),
                str(outcome["status"]), order_id=str(order["order_id"]),
                reason=str(outcome["rejection_reason"] or ""),
                payload=attribution,
            )
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

    def reconcile_decision_statuses(self, user_id: int, account_id: int) -> None:
        """Backfill execution status for paper decisions created before a fill."""
        orders = self.storage.fetchall(
            """
            SELECT decision_id, order_id, status
            FROM paper_orders
            WHERE user_id = ? AND account_id = ?
              AND status IN ('filled', 'rejected', 'canceled')
            ORDER BY updated_at DESC
            LIMIT 1000
            """,
            (user_id, account_id),
        )
        for order in orders:
            self._sync_paper_decision_status(
                user_id, account_id, str(order["decision_id"]),
                str(order["order_id"]),
                status=(
                    "confirmed" if order["status"] == "filled"
                    else "expired" if order["status"] == "canceled"
                    else "rejected"
                ),
                auto_executed=order["status"] == "filled",
            )

    def _sync_paper_decision_status(
        self, user_id: int, account_id: int, decision_id: str, order_id: str,
        status: str, auto_executed: bool,
    ) -> None:
        if not decision_id:
            return
        runtime = RuntimeStateRepository(user_id, account_id, self.storage)
        payload = runtime.get_entity("strategy_decision", decision_id)
        if payload is None:
            return
        payload["status"] = status
        payload["auto_executed"] = bool(auto_executed)
        payload["order_id"] = order_id
        runtime.upsert_entity(
            "strategy_decision", decision_id, payload,
            symbol=str(payload.get("symbol") or ""), status=status,
        )

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
            active_volume = float(position["remaining_volume"] or position["volume"])
            unrealized = (
                mark - float(position["entry_price"])
            ) * multiplier * active_volume * contract_size
            margin += (
                float(position["entry_price"]) * active_volume
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
        strategy = StrategyConfigRepository(self.storage).get_strategy_by_id(
            user_id, strategy_id
        )
        if strategy is None:
            raise ValueError("策略不存在")
        return strategy.to_dict()

    def _deployment_strategy(self, user_id: int, deployment) -> Dict:
        # Deployments resolve the current source configuration. A shared AI
        # strategy may intentionally remain draft at its publisher, but an
        # active paper deployment must still be runnable for its recipient.
        strategy = self._strategy_config(user_id, deployment["strategy_id"])
        if (
            deployment["execution_mode"] == "paper"
            and deployment["status"] in {"active", "paused"}
            and strategy.get("lifecycle_status") == StrategyLifecycle.DRAFT
        ):
            strategy["lifecycle_status"] = StrategyLifecycle.PAPER_TRADING
        policy_id = str(strategy.get("position_management_policy_id", ""))
        policy = self.position_policies.get_for_strategy(user_id, strategy)
        if policy is not None:
            strategy["position_management_policy_snapshot"] = policy.to_dict()
        # A deployment is the runtime enablement switch for paper trading.
        strategy["enabled"] = True
        return strategy

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
