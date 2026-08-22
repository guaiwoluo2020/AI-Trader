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

from market.models import PositionManagementPolicy, StrategyLifecycle, TradingStrategy
from market.services.position_manager import PositionManager
from market.services.strategy.transient_decision_store import transient_decision_store
from membership import MembershipService
from sqlite_storage import (
    PositionManagementEventRepository,
    PositionManagementPolicyRepository, SQLiteStorage,
    PlatformInstrumentMappingRepository, StrategyConfigRepository,
    RuntimeStateRepository, TradingAccountRepository, get_storage,
)
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

    def __init__(self, storage: Optional[SQLiteStorage] = None):
        self.storage = storage or get_storage()
        self.accounts = TradingAccountRepository(self.storage)
        self.instrument_mappings = PlatformInstrumentMappingRepository(self.storage)
        self.position_policies = PositionManagementPolicyRepository(self.storage)
        self.position_manager = PositionManager()
        self.position_events = PositionManagementEventRepository(self.storage)
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
            strategy = strategy_repository.get_strategy_by_id(
                user_id, row["strategy_id"]
            )
            if strategy is None:
                continue
            config = strategy.to_dict()
            lifecycle = config.get(
                "lifecycle_status", StrategyLifecycle.PRODUCTION
            )
            ai_direct = self._ai_direct_paper_eligible(config)
            paper_eligible = (
                lifecycle in {
                    StrategyLifecycle.BACKTEST_PASSED,
                    StrategyLifecycle.PAPER_TRADING,
                    StrategyLifecycle.PRODUCTION,
                }
                or ai_direct
            )
            items.append({
                "strategy_id": row["strategy_id"],
                "symbol": row["symbol"],
                "strategy_name": config.get("strategy_name", row["strategy_id"]),
                "enabled": True,
                "lifecycle_status": lifecycle,
                "paper_eligible": paper_eligible,
                "paper_direct_allowed": ai_direct,
                "paper_eligibility_reason": (
                    "包含 AI 信号源，可跳过回测直接进入模拟观察"
                    if ai_direct and lifecycle not in {
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
        return (
            cls._has_enabled_ai_signal(strategy_data)
            and strategy_data.get("lifecycle_status") != StrategyLifecycle.RETIRED
        )

    def _promote_for_paper_deployment(
        self, user_id: int, strategy: TradingStrategy, account_name: str,
        *, direct_ai: bool = False,
    ) -> TradingStrategy:
        if strategy.lifecycle_status == StrategyLifecycle.BACKTEST_PASSED:
            strategy.transition_lifecycle(
                StrategyLifecycle.PAPER_TRADING,
                f"部署到模拟账户 {account_name}",
            )
        elif direct_ai and strategy.lifecycle_status != StrategyLifecycle.PAPER_TRADING:
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
                    f"包含 AI 信号源，跳过回测直接部署到模拟账户 "
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
            direct_ai_bypass = (
                not normal_eligible
                and self._ai_direct_paper_eligible(current_strategy.to_dict())
            )
            if not normal_eligible and not direct_ai_bypass:
                raise ValueError("策略通过回测后才能部署到模拟账户；包含 AI 信号源的策略可直接模拟观察")
            if (
                current_strategy.lifecycle_status == StrategyLifecycle.BACKTEST_PASSED
                or direct_ai_bypass
            ):
                current_strategy = self._promote_for_paper_deployment(
                    user_id, current_strategy, account.account_name,
                    direct_ai=direct_ai_bypass,
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
            strategy = strategy_repository.get_strategy_by_id(
                user_id, item["strategy_id"]
            )
            configured_lifecycle = item.get("lifecycle_status") or "draft"
            if strategy is not None:
                item["strategy_name"] = strategy.strategy_name
                configured_lifecycle = strategy.lifecycle_status
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
        self, user_id: int, symbol: str, current_price: float, strategy_service,
        quote_account_id: Optional[int] = None,
    ) -> int:
        """按每个模拟部署独立生成决策，不复用实盘风控或实盘冷却。"""
        self._expire_deployments(user_id)
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
                position_checker=lambda s, st, action, aid=account_id: (
                    self._paper_position_check(aid, s, st, action)
                ),
                risk_checker=lambda s, volume, risk, st, aid=account_id, px=current_price: (
                    self._paper_risk_check(aid, s, volume, px)
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
            SELECT d.*, json_extract(s.config_json, '$.strategy_name') AS strategy_name
            FROM strategy_deployments d
            LEFT JOIN user_strategy_configs s
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
            position["management_events"] = self.position_events.list_for_position(
                user_id, account_id, position["position_id"]
            )
        return {
            "account": self._account_dict(account),
            "settings": settings,
            "deployments": deployments,
            "orders": [dict(row) for row in self.storage.fetchall(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY requested_at DESC LIMIT 200",
                (account_id,),
            )],
            "positions": positions,
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
                        close_reason,
                        entry_price, stop_loss, take_profit, open_commission,
                        current_price, remaining_volume,
                        partial_levels_done_json, signal_source_id, exit_mode,
                        trailing_activation_r, trailing_distance_r, initial_risk,
                        favorable_price, position_policy_snapshot_json,
                        opened_at, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    position_state = dict(position)
                    position_state["favorable_price"] = favorable
                    position_state["remaining_volume"] = float(
                        position["remaining_volume"] or position["volume"]
                    )
                    position_state["partial_levels_done"] = json.loads(
                        position["partial_levels_done_json"] or "[]"
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
                    action = self.position_manager.evaluate(
                        policy_snapshot.get("config", {}), position_state,
                        {"price": mark, "time": now}, pivots=pivots,
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
                            balance += gross - commission
                            trade_id = uuid.uuid4().hex[:12]
                            done = set(position_state["partial_levels_done"])
                            done.add(action.level_id)
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
                                    close_volume, position["entry_price"], mark,
                                    gross, commission, net, "partial_take_profit",
                                    position["opened_at"], now, now,
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
                            close_volume, position["entry_price"], exit_price,
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
        for decision_id, order_id, status, auto_executed in decision_updates:
            self._sync_paper_decision_status(
                user_id, account_id, decision_id, order_id,
                status=status, auto_executed=auto_executed,
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
              AND status IN ('filled', 'rejected')
            ORDER BY updated_at DESC
            LIMIT 200
            """,
            (user_id, account_id),
        )
        for order in orders:
            self._sync_paper_decision_status(
                user_id, account_id, str(order["decision_id"]),
                str(order["order_id"]),
                status="confirmed" if order["status"] == "filled" else "rejected",
                auto_executed=order["status"] == "filled",
            )

    def _sync_paper_decision_status(
        self, user_id: int, account_id: int, decision_id: str, order_id: str,
        status: str, auto_executed: bool,
    ) -> None:
        if not decision_id:
            return
        runtime = RuntimeStateRepository(user_id, account_id, self.storage)
        for payload in runtime.list_entities("strategy_decision"):
            if str(payload.get("decision_id") or "") != decision_id:
                continue
            payload["status"] = status
            payload["auto_executed"] = bool(auto_executed)
            payload["order_id"] = order_id
            runtime.upsert_entity(
                "strategy_decision", decision_id, payload,
                symbol=str(payload.get("symbol") or ""), status=status,
            )
            return

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
