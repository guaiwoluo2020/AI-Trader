"""Centralized daily reviews for structure signals and strategy executions."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
import time
from typing import Callable, Dict, List, Optional

from llm_governance import AI_SIGNAL_ANALYSIS
from mysql_repositories import get_storage
from repositories.strategy_config import StrategyConfigRepository
from repositories.runtime import RuntimeStateRepository
from market.services.signal.structure_plan_signal import (
    resolve_structure_plan_config,
)
from market.store.structure_plan_store import StructureTradePlanRepository
from market.services.daily_review_file_store import load as load_review_file, save as save_review_file


CHINA_TZ = timezone(timedelta(hours=8))
PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400}
# LLM reviews are retired by default. Set explicitly to true only for a
# controlled, temporary re-enable; the daily scheduler still records a
# skipped review so the audit trail remains complete.
LLM_REVIEW_ENABLED = str(os.getenv("AI_TRADER_DAILY_LLM_REVIEW_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}


def _json(value, default=None):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {} if default is None else default


def _epoch(value) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


class DailyReviewCoordinator:
    """Run all review families once in one Beijing-time daily batch."""

    STRUCTURE_ENTITY = "daily_structure_signal_review"
    STRATEGY_ENTITY = "daily_strategy_execution_review"
    BATCH_ENTITY = "daily_review_batch"

    def __init__(
        self, engine_provider: Callable[[int], object], storage=None,
        now_provider: Callable[[], int] = lambda: int(time.time()),
    ):
        self.storage = storage or get_storage()
        self.engine_provider = engine_provider
        self.now_provider = now_provider
        self.plan_repo = StructureTradePlanRepository(self.storage)

    def run_once(self, review_date: Optional[str] = None) -> Dict:
        now = int(self.now_provider())
        local_date = review_date or datetime.fromtimestamp(now, CHINA_TZ).date().isoformat()
        batch_id = local_date
        existing = self.storage.fetchone(
            "SELECT payload_json FROM runtime_entities WHERE user_id=0 AND account_id=0 "
            "AND entity_type=? AND entity_id=?",
            (self.BATCH_ENTITY, batch_id),
        )
        if existing:
            prior = _json(existing["payload_json"])
            if prior.get("status") == "completed":
                return {**prior, "already_completed": True}
        result = {
            "batch_id": batch_id, "review_date": local_date,
            "scheduled_hour": 6, "started_at": now, "status": "running",
            "structure": {"completed": 0, "skipped": 0, "failed": 0},
            "strategy": {"completed": 0, "skipped": 0, "failed": 0},
        }
        RuntimeStateRepository(0, 0, self.storage).upsert_entity(
            self.BATCH_ENTITY, batch_id, result, status="running",
        )
        self._run_family(
            result, "structure", lambda: self._structure_scopes(now),
            lambda scope: self._review_structure_scope(scope, local_date, now),
        )
        self._run_family(
            result, "strategy", self._strategy_scopes,
            lambda scope: self._review_strategy_scope(scope, local_date, now),
        )
        result.update(status="completed", completed_at=int(self.now_provider()))
        RuntimeStateRepository(0, 0, self.storage).upsert_entity(
            self.BATCH_ENTITY, batch_id, result, status="completed",
        )
        return result

    @staticmethod
    def _run_family(result: Dict, family: str, scope_provider, reviewer) -> None:
        """Keep one broken scope or review family from aborting the daily batch."""
        try:
            scopes = scope_provider()
        except Exception as exc:
            result[family]["failed"] += 1
            result[family]["batch_error"] = str(exc)[:1000]
            return
        for scope in scopes:
            try:
                status = reviewer(scope)
            except Exception as exc:
                status = "failed"
                result[family].setdefault("scope_errors", []).append(str(exc)[:1000])
            if status not in {"completed", "skipped", "failed"}:
                status = "failed"
            result[family][status] += 1

    def _structure_scopes(self, now: int) -> List[Dict]:
        # Seven-day MySQL retention is the source of truth. Include stale
        # scopes so the review can explicitly record why it was skipped.
        rows = self.storage.fetchall(
            """
            SELECT user_id,symbol,period,
                   MAX(CASE WHEN timestamp_utc>0 THEN timestamp_utc ELSE timestamp END) AS latest_market_at,
                   COUNT(*) AS bar_count
            FROM historical_klines
            WHERE account_id=0 AND timestamp>=?
            GROUP BY user_id,symbol,period
            ORDER BY user_id,symbol,period
            """,
            (now - 7 * 86400,),
        )
        return [dict(row) for row in rows]

    def _strategy_scopes(self) -> List[Dict]:
        rows = self.storage.fetchall(
            """
            SELECT d.user_id,d.account_id,d.deployment_id,d.strategy_id,
                   d.symbol,d.execution_mode,d.status,a.account_name
            FROM strategy_deployments d
            JOIN trading_accounts a ON a.id=d.account_id
            WHERE d.status='active' AND a.status='active' AND a.enabled=1
              AND a.trading_enabled=1 AND a.auto_trading_enabled=1
            ORDER BY d.user_id,d.strategy_id,d.deployment_id
            """
        )
        return [dict(row) for row in rows]

    def _save_structure(self, user_id: int, entity_id: str, payload: Dict) -> None:
        review_path = save_review_file(user_id, entity_id, payload)
        # Keep a compact index in MySQL; the full evidence/review lives on disk.
        index_payload = {
            "review_id": entity_id,
            "review_date": payload.get("review_date"),
            "user_id": int(user_id),
            "symbol": payload.get("symbol", ""),
            "period": payload.get("period", ""),
            "status": payload.get("status", ""),
            "generated_at": payload.get("generated_at", 0),
            "latest_market_at": payload.get("latest_market_at", 0),
            "review_path": str(review_path),
        }
        RuntimeStateRepository(user_id, 0, self.storage).upsert_entity(
            self.STRUCTURE_ENTITY, entity_id, index_payload,
            symbol=str(payload.get("symbol") or ""),
            status=str(payload.get("status") or ""),
        )

    def _save_strategy(self, user_id: int, account_id: int, entity_id: str,
                       payload: Dict) -> None:
        """Persist a compact index and keep the potentially large review on disk."""
        review_path = save_review_file(user_id, entity_id, payload)
        index_payload = {
            "review_id": entity_id,
            "review_date": payload.get("review_date"),
            "user_id": int(user_id),
            "account_id": int(account_id),
            "deployment_id": payload.get("deployment_id", ""),
            "strategy_id": payload.get("strategy_id", ""),
            "symbol": payload.get("symbol", ""),
            "status": payload.get("status", ""),
            "generated_at": payload.get("generated_at", 0),
            "review_path": str(review_path),
        }
        RuntimeStateRepository(user_id, account_id, self.storage).upsert_entity(
            self.STRATEGY_ENTITY, entity_id, index_payload,
            symbol=str(payload.get("symbol") or ""),
            status=str(payload.get("status") or ""),
        )

    def _review_structure_scope(self, scope: Dict, review_date: str, now: int) -> str:
        user_id = int(scope["user_id"])
        symbol = str(scope["symbol"])
        period = str(scope["period"]).upper()
        entity_id = f"{review_date}:{symbol}:{period}"
        latest_market_at = int(scope.get("latest_market_at") or 0)
        base = {
            "review_id": entity_id, "review_date": review_date,
            "user_id": user_id, "symbol": symbol, "period": period,
            "generated_at": now, "latest_market_at": latest_market_at,
            "review_window_hours": 24,
        }
        if latest_market_at <= 0 or latest_market_at < now - 12 * 3600:
            last_text = (
                datetime.fromtimestamp(latest_market_at, CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
                if latest_market_at else "无"
            )
            payload = {
                **base, "status": "skipped",
                "skip_reason": (
                    f"过去12小时没有收到行情，已自动跳过复盘；"
                    f"最后行情时间（北京时间）：{last_text}"
                ),
            }
            self._save_structure(user_id, entity_id, payload)
            return "skipped"
        if not LLM_REVIEW_ENABLED:
            self._save_structure(user_id, entity_id, {
                **base, "status": "skipped",
                "skip_reason": "大模型结构复盘已停用；保留行情证据和历史文件，不调用大模型",
            })
            return "skipped"
        try:
            evidence = self._structure_evidence(user_id, symbol, period, now)
            prompt = self._structure_prompt(evidence)
            review = self._call_llm(
                user_id, prompt, "daily_structure_signal_review", entity_id,
            )
            payload = {**base, "status": "completed", "evidence": evidence, "review": review}
            self._save_structure(user_id, entity_id, payload)
            return "completed"
        except Exception as exc:
            self._save_structure(user_id, entity_id, {
                **base, "status": "failed", "error": str(exc)[:1000],
            })
            return "failed"

    def _structure_evidence(
        self, user_id: int, symbol: str, period: str, now: int,
    ) -> Dict:
        start_at = now - 24 * 3600
        plan_rows = self.storage.fetchall(
            """
            SELECT plan_id,plan_group_id,setup_type,direction,entry_mode,status,
                   structure_bar_time,valid_from,expires_at,payload_json,created_at,updated_at
            FROM structure_trade_plans
            WHERE user_id=? AND account_id=0 AND symbol=? AND period=?
              AND (structure_bar_time>=? OR created_at>=?)
            ORDER BY structure_bar_time,created_at
            LIMIT 500
            """,
            (user_id, symbol, period, start_at, start_at),
        )
        plans = []
        for row in plan_rows:
            payload = _json(row["payload_json"])
            payload.update({
                "plan_id": str(row["plan_id"]),
                "plan_group_id": str(row["plan_group_id"] or ""),
                "setup_type": str(row["setup_type"] or ""),
                "direction": str(row["direction"] or ""),
                "entry_mode": str(row["entry_mode"] or ""),
                "plan_status": str(row["status"] or ""),
                "valid_from": int(row["valid_from"] or 0),
                "expires_at": int(row["expires_at"] or 0),
            })
            plans.append(payload)
        bar_rows = self.storage.fetchall(
            """
            SELECT timestamp,timestamp_utc,open_price,high_price,low_price,close_price
            FROM historical_klines
            WHERE user_id=? AND account_id=0 AND symbol=? AND period=? AND timestamp>=?
            ORDER BY timestamp
            """,
            (user_id, symbol, period, start_at - PERIOD_SECONDS.get(period, 300) * 10),
        )
        bars = [{
            "time": int(row["timestamp_utc"] or row["timestamp"] or 0),
            "open": float(row["open_price"]), "high": float(row["high_price"]),
            "low": float(row["low_price"]), "close": float(row["close_price"]),
        } for row in bar_rows]
        outcomes = [self._plan_outcome(plan, bars, now) for plan in plans]
        executions = self.plan_repo.list_executions(
            user_id, [plan["plan_id"] for plan in plans]
        )
        setup_stats = defaultdict(lambda: Counter())
        for item in outcomes:
            stats = setup_stats[item["setup_type"]]
            stats["plans"] += 1
            stats[item["outcome"]] += 1
        execution_stats = Counter(str(item.get("status") or "unknown") for item in executions)
        tradable = [item for item in outcomes if item["direction"] in {"buy", "sell"}]
        return {
            "scope": {"symbol": symbol, "period": period, "hours": 24},
            "current_config": resolve_structure_plan_config(symbol, period),
            "metrics": {
                "plan_count": len(plans),
                "tradable_plan_count": len(tradable),
                "observation_count": len(plans) - len(tradable),
                "triggered_count": sum(item["triggered"] for item in tradable),
                "target_hit_count": sum(item["outcome"] == "target_hit" for item in tradable),
                "stop_hit_count": sum(item["outcome"] == "stop_hit" for item in tradable),
                "expired_untriggered_count": sum(item["outcome"] == "not_triggered" for item in tradable),
                "execution_status_counts": dict(execution_stats),
            },
            "by_setup": [{"setup_type": key, **dict(value)} for key, value in setup_stats.items()],
            "plans": outcomes[-120:],
        }

    @staticmethod
    def _plan_outcome(plan: Dict, bars: List[Dict], now: int) -> Dict:
        direction = str(plan.get("direction") or "none")
        result = {
            "plan_id": plan.get("plan_id"), "plan_group_id": plan.get("plan_group_id"),
            "setup_type": plan.get("setup_type"), "direction": direction,
            "entry_mode": plan.get("entry_mode"), "status": plan.get("plan_status"),
            "entry_price": float(plan.get("entry_price") or 0),
            "stop_loss": float(plan.get("stop_loss") or 0),
            "take_profit": float(plan.get("take_profit") or 0),
            "risk_reward_ratio": float(plan.get("risk_reward_ratio") or 0),
            "minimum_risk_reward": float(plan.get("minimum_risk_reward") or 0),
            "confidence": int(plan.get("confidence") or 0),
            "valid_from": int(plan.get("valid_from") or 0),
            "expires_at": int(plan.get("expires_at") or 0),
            "reason": str(plan.get("reason") or "")[:500],
            "structure_snapshot": plan.get("structure_snapshot") or {},
            "triggered": False, "triggered_at": 0,
            "outcome": "observation" if direction not in {"buy", "sell"} else "not_triggered",
        }
        if direction not in {"buy", "sell"}:
            return result
        zone = plan.get("entry_zone") or {}
        lower = float(zone.get("lower") or result["entry_price"])
        upper = float(zone.get("upper") or result["entry_price"])
        valid_to = int(result["expires_at"] or now)
        scoped = [bar for bar in bars if result["valid_from"] <= bar["time"] <= valid_to]
        trigger_index = next((i for i, bar in enumerate(scoped) if bar["low"] <= upper and bar["high"] >= lower), None)
        if trigger_index is None:
            return result
        result["triggered"] = True
        result["triggered_at"] = scoped[trigger_index]["time"]
        sl, tp = result["stop_loss"], result["take_profit"]
        for bar in scoped[trigger_index:]:
            stop_hit = bar["low"] <= sl if direction == "buy" else bar["high"] >= sl
            target_hit = bar["high"] >= tp if direction == "buy" else bar["low"] <= tp
            if stop_hit and target_hit:
                result["outcome"] = "ambiguous_same_bar"
                result["resolved_at"] = bar["time"]
                break
            if stop_hit or target_hit:
                result["outcome"] = "stop_hit" if stop_hit else "target_hit"
                result["resolved_at"] = bar["time"]
                break
        else:
            result["outcome"] = "open_after_trigger"
        return result

    @staticmethod
    def _structure_prompt(evidence: Dict) -> str:
        return (
            "请对过去24小时的结构识别与结构交易计划做复盘。重点检查主结构/局部结构、"
            "箱体与三角形、HL/LH、BOS/CHoCH、流动性扫单、入场区、止损止盈、有效期和"
            "真实盈亏比是否合理。只能依据输入证据，不得臆测。输出严格JSON："
            '{"summary":"","problems":[{"category":"structure_recognition|plan_generation|trigger|risk_reward|lifecycle",'
            '"severity":"high|medium|low","evidence":"","analysis":""}],'
            '"improvement_plan":[{"parameter":"","current_value":null,"suggested_value":null,'
            '"reason":"","risk":"","validation":""}],"risk_notes":[""],"confidence":0}。'
            "参数建议只能使用 current_config 中存在的字段；样本不足时必须说明，不得强行调参。\n\n"
            + json.dumps(evidence, ensure_ascii=False, default=str)
        )

    def _review_strategy_scope(self, scope: Dict, review_date: str, now: int) -> str:
        user_id, account_id = int(scope["user_id"]), int(scope["account_id"])
        deployment_id, strategy_id = str(scope["deployment_id"]), str(scope["strategy_id"])
        entity_id = f"{review_date}:{deployment_id}"
        base = {
            "review_id": entity_id, "review_date": review_date,
            "user_id": user_id, "account_id": account_id,
            "deployment_id": deployment_id, "strategy_id": strategy_id,
            "strategy_name": "", "symbol": str(scope["symbol"]),
            "execution_mode": str(scope["execution_mode"]),
            "account_name": str(scope.get("account_name") or ""),
            "generated_at": now, "review_window_hours": 24,
        }
        try:
            strategy = StrategyConfigRepository(self.storage).get_strategy_by_id(user_id, strategy_id)
            if strategy:
                base["strategy_name"] = strategy.strategy_name
            evidence = self._strategy_evidence(scope, now, strategy)
            if not any(evidence["metrics"].get(key) for key in (
                "decision_count", "order_count", "trade_count", "structure_plan_count",
            )):
                payload = {**base, "status": "skipped", "skip_reason": "过去24小时没有策略决策、订单或成交，已自动跳过复盘", "evidence": evidence}
                self._save_strategy(user_id, account_id, entity_id, payload)
                return "skipped"
            if not LLM_REVIEW_ENABLED:
                self._save_strategy(user_id, account_id, entity_id, {
                    **base, "status": "skipped", "evidence": evidence,
                    "skip_reason": "大模型策略复盘已停用；保留执行证据和历史文件，不调用大模型",
                })
                return "skipped"
            prompt = (
                "请复盘这个策略部署过去24小时的决策、订单和成交。结合结构计划的领取与消费状态，"
                "区分信号问题、策略筛选问题、风控问题、执行问题和持仓管理问题。只返回严格JSON："
                '{"summary":"","root_causes":[{"category":"signal_source|strategy|position_management|execution",'
                '"severity":"high|medium|low","evidence":"","explanation":""}],'
                '"suggestions":[{"target":"signal_source|strategy|position_management","field":"",'
                '"change":"","patch":{},"reason":"","risk":"","validation":""}],'
                '"risk_notes":[""],"confidence":0}。不得自动修改配置。\n\n'
                + json.dumps(evidence, ensure_ascii=False, default=str)
            )
            review = self._call_llm(user_id, prompt, "daily_strategy_execution_review", entity_id)
            payload = {**base, "status": "completed", "evidence": evidence, "review": review}
            self._save_strategy(user_id, account_id, entity_id, payload)
            return "completed"
        except Exception as exc:
            self._save_strategy(
                user_id, account_id, entity_id,
                {**base, "status": "failed", "error": str(exc)[:1000]},
            )
            return "failed"

    def _strategy_evidence(self, scope: Dict, now: int, strategy) -> Dict:
        user_id, account_id = int(scope["user_id"]), int(scope["account_id"])
        deployment_id, strategy_id = str(scope["deployment_id"]), str(scope["strategy_id"])
        start_at = now - 24 * 3600
        mode = str(scope["execution_mode"])
        if mode == "paper":
            orders = [dict(row) for row in self.storage.fetchall(
                "SELECT order_id,decision_id,direction,status,requested_price,filled_price,"
                "stop_loss,take_profit,rejection_reason,requested_at,filled_at,position_attribution_json "
                "FROM paper_orders WHERE user_id=? AND account_id=? AND deployment_id=? "
                "AND requested_at>=? ORDER BY requested_at DESC LIMIT 100",
                (user_id, account_id, deployment_id, start_at),
            )]
            trades = [dict(row) for row in self.storage.fetchall(
                "SELECT trade_id,order_id,position_id,direction,entry_price,exit_price,net_profit,"
                "exit_reason,opened_at,closed_at,position_attribution_json FROM paper_trades "
                "WHERE user_id=? AND account_id=? AND deployment_id=? AND closed_at>=? "
                "ORDER BY closed_at DESC LIMIT 100",
                (user_id, account_id, deployment_id, start_at),
            )]
        else:
            orders = [dict(row) for row in self.storage.fetchall(
                "SELECT instruction_id AS order_id,success AS status,requested_price,executed_price AS filled_price,"
                "error_message AS rejection_reason,reported_at AS requested_at,position_attribution_json "
                "FROM trade_execution_reports WHERE user_id=? AND account_id=? AND reported_at>=? "
                "ORDER BY reported_at DESC LIMIT 100",
                (user_id, account_id, start_at),
            )]
            orders = [item for item in orders if str(_json(item.get("position_attribution_json")).get("strategy_id") or "") == strategy_id]
            trades = [dict(row) for row in self.storage.fetchall(
                "SELECT ticket AS trade_id,mt5_order AS order_id,mt5_position_id AS position_id,"
                "profit,swap,commission,deal_timestamp AS closed_at,position_attribution_json "
                "FROM live_trade_deals WHERE user_id=? AND account_id=? AND deal_timestamp>=? "
                "ORDER BY deal_timestamp DESC LIMIT 200",
                (user_id, account_id, start_at),
            )]
            trades = [item for item in trades if str(_json(item.get("position_attribution_json")).get("strategy_id") or "") == strategy_id]
        decisions = []
        for payload in RuntimeStateRepository(user_id, account_id, self.storage).list_entities("strategy_decision", limit=1000):
            if str(payload.get("strategy_id") or "") != strategy_id:
                continue
            if _epoch(payload.get("created_at") or payload.get("timestamp")) < start_at:
                continue
            decision = {key: payload.get(key) for key in (
                "decision_id", "created_at", "action", "status", "decision_reason",
                "confidence_score", "order_id",
            )}
            summary = payload.get("signal_summary") or {}
            decision.update({
                "selected_trade_plan_id": summary.get("selected_trade_plan_id"),
                "selected_trade_plan_group_id": summary.get("selected_trade_plan_group_id"),
                "selected_setup_type": summary.get("selected_setup_type"),
                "selected_entry_mode": summary.get("selected_entry_mode"),
                "selected_signal_source": summary.get("selected_signal_source"),
            })
            decisions.append(decision)
        for item in orders + trades:
            attribution = _json(item.get("position_attribution_json"))
            item["position_attribution"] = attribution
            item.pop("position_attribution_json", None)
        structure_context = self._deployment_structure_context(
            user_id, account_id, deployment_id, str(scope["symbol"]),
            strategy, start_at,
        )
        return {
            "scope": {key: scope.get(key) for key in (
                "deployment_id", "strategy_id", "symbol", "execution_mode", "account_name"
            )},
            "metrics": {
                "decision_count": len(decisions), "order_count": len(orders),
                "trade_count": len(trades),
                "net_profit": round(sum(float(item.get("net_profit") or item.get("profit") or 0) for item in trades), 2),
                "rejected_order_count": sum(str(item.get("status")) in {"rejected", "0", "False"} for item in orders),
                **structure_context["metrics"],
            },
            "strategy_config": strategy.to_dict() if strategy else {},
            "structure_plan_subscription": structure_context,
            "decisions": decisions[-60:], "orders": orders[-60:], "trades": trades[-60:],
        }

    def _deployment_structure_context(
        self, user_id: int, account_id: int, deployment_id: str,
        symbol: str, strategy, start_at: int,
    ) -> Dict:
        """Collect all public plans subscribed by a deployment, even if no order exists."""
        sources = (
            strategy.get_signal_sources("structure_plan", enabled_only=True)
            if strategy else []
        )
        periods = sorted({
            str(source.get("period") or "M5").upper() for source in sources
        })
        if not periods:
            return {
                "enabled": False, "periods": [], "plans": [], "executions": [],
                "metrics": {
                    "structure_plan_count": 0,
                    "structure_plan_consumed_count": 0,
                    "structure_plan_unconsumed_count": 0,
                    "structure_plan_execution_status_counts": {},
                },
            }
        placeholders = ",".join("?" for _ in periods)
        plan_rows = self.storage.fetchall(
            "SELECT plan_id,plan_group_id,period,setup_type,direction,entry_mode,status,"
            "structure_bar_time,valid_from,expires_at,payload_json,created_at,updated_at "
            "FROM structure_trade_plans WHERE user_id=? AND account_id=0 "
            "AND strategy_id='' AND UPPER(symbol)=? "
            f"AND period IN ({placeholders}) "
            "AND (structure_bar_time>=? OR created_at>=? OR updated_at>=?) "
            "ORDER BY structure_bar_time DESC,updated_at DESC LIMIT 500",
            (int(user_id), str(symbol).upper(), *periods, start_at, start_at, start_at),
        )
        execution_rows = self.storage.fetchall(
            "SELECT execution_id,plan_id,plan_group_id,status,order_id,reason,"
            "created_at,updated_at FROM structure_plan_executions "
            "WHERE user_id=? AND account_id=? AND deployment_id=? "
            "AND (created_at>=? OR updated_at>=?) ORDER BY updated_at DESC LIMIT 500",
            (int(user_id), int(account_id), str(deployment_id), start_at, start_at),
        )
        executions = [dict(row) for row in execution_rows]
        execution_by_plan = {str(item["plan_id"]): item for item in executions}
        plans = []
        status_counts = Counter()
        consumed_statuses = {
            "claimed", "triggered", "ordered", "filled", "rejected",
            "expired", "canceled",
        }
        for row in plan_rows:
            payload = _json(row["payload_json"])
            execution = execution_by_plan.get(str(row["plan_id"]))
            execution_status = str(execution.get("status") or "") if execution else "unconsumed"
            status_counts[execution_status] += 1
            plans.append({
                "plan_id": str(row["plan_id"]),
                "plan_group_id": str(row["plan_group_id"] or ""),
                "period": str(row["period"] or ""),
                "setup_type": str(row["setup_type"] or ""),
                "direction": str(row["direction"] or ""),
                "entry_mode": str(row["entry_mode"] or ""),
                "plan_status": str(row["status"] or ""),
                "entry_price": float(payload.get("entry_price") or 0),
                "stop_loss": float(payload.get("stop_loss") or 0),
                "take_profit": float(payload.get("take_profit") or 0),
                "risk_reward_ratio": float(payload.get("risk_reward_ratio") or 0),
                "minimum_risk_reward": float(payload.get("minimum_risk_reward") or 0),
                "reason": str(payload.get("reason") or "")[:500],
                "generated_at": int(payload.get("generated_at") or row["created_at"] or 0),
                "valid_from": int(row["valid_from"] or 0),
                "expires_at": int(row["expires_at"] or 0),
                "execution_status": execution_status,
                "execution_reason": str(execution.get("reason") or "")[:500] if execution else "",
                "order_id": str(execution.get("order_id") or "") if execution else "",
            })
        consumed = sum(
            count for status, count in status_counts.items()
            if status in consumed_statuses
        )
        return {
            "enabled": True,
            "periods": periods,
            # Keep the latest detailed samples while metrics still cover every
            # row fetched for the 24-hour window. This prevents M1 strategies
            # from producing an oversized LLM prompt.
            "plans": plans[:120],
            "executions": executions[:120],
            "metrics": {
                "structure_plan_count": len(plans),
                "structure_plan_consumed_count": consumed,
                "structure_plan_unconsumed_count": max(0, len(plans) - consumed),
                "structure_plan_execution_status_counts": dict(status_counts),
            },
        }

    def _call_llm(self, user_id: int, prompt: str, object_type: str, object_id: str) -> Dict:
        engine = self.engine_provider(int(user_id))
        system_prompt = (
            "你是量化交易复盘分析器。只能依据输入证据分析；样本不足必须明确说明。"
            "不得臆测，不得直接修改配置，只返回请求约定的严格JSON。"
        )
        result = engine.llm_service.call_llm(
            prompt, system_prompt=system_prompt,
            scene_code=AI_SIGNAL_ANALYSIS,
            object_type=object_type, object_id=object_id, max_tokens=5000,
        )
        return result or {}

    def list_structure_reviews(
        self, user_id: int, symbol: str, period: str, limit: int = 30,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT payload_json FROM runtime_entities WHERE user_id=? AND account_id=0 "
            "AND entity_type=? AND UPPER(symbol)=? ORDER BY created_at DESC,entity_id DESC LIMIT ?",
            (
                int(user_id), self.STRUCTURE_ENTITY, str(symbol).upper(),
                max(5, min(int(limit) * 5, 450)),
            ),
        )
        items = []
        for row in rows:
            index = _json(row["payload_json"])
            item = load_review_file(index.get("review_path", "")) if index.get("review_path") else None
            items.append(item or index)
        return [
            item for item in items
            if str(item.get("period") or "").upper() == str(period).upper()
        ][:max(1, min(int(limit), 90))]

    def list_strategy_reviews(
        self, user_id: int, account_id: int, strategy_id: str,
        deployment_id: str = "", limit: int = 30,
    ) -> List[Dict]:
        rows = self.storage.fetchall(
            "SELECT payload_json FROM runtime_entities WHERE user_id=? AND account_id=? "
            "AND entity_type=? ORDER BY created_at DESC,entity_id DESC LIMIT ?",
            (int(user_id), int(account_id), self.STRATEGY_ENTITY, max(1, min(int(limit) * 4, 360))),
        )
        items = []
        for row in rows:
            index = _json(row["payload_json"])
            item = load_review_file(index.get("review_path", "")) if index.get("review_path") else None
            items.append(item or index)
        return [item for item in items if str(item.get("strategy_id") or "") == str(strategy_id) and (not deployment_id or str(item.get("deployment_id") or "") == str(deployment_id))][:max(1, min(int(limit), 90))]
