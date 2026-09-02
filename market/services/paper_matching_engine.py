"""Stable application boundary for Paper Tick matching."""

import json
import uuid
from typing import Dict, List


class PaperMatchingEngine:
    """Delegate matching to the current service while the algorithm migrates."""

    def __init__(self, paper_service):
        self.paper_service = paper_service

    def process_account_tick(
        self, user_id: int, account_id: int, symbol: str,
        bid: float, ask: float, now: int, pivots: List[Dict], structures: Dict[str, Dict],
    ) -> Dict:
        from paper_trading import market_spec
        point_size, contract_size = market_spec(symbol)
        settings = self.paper_service._settings(account_id)
        slippage = settings["slippage_points"] * point_size
        configured_spread = settings["spread_points"] * point_size
        if ask - bid < configured_spread:
            midpoint = (ask + bid) / 2
            bid = midpoint - configured_spread / 2
            ask = midpoint + configured_spread / 2
        result = {"filled": 0, "closed": 0, "rejected": 0}
        decision_updates = []
        with self.paper_service.storage._lock, self.paper_service.storage._connect() as conn:
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
                    self.paper_service._reject_order(conn, order["order_id"], "策略运行已暂停", now)
                    result["rejected"] += 1
                    continue
                try:
                    strategy = self.paper_service._strategy_config(user_id, order["strategy_id"])
                except ValueError:
                    self.paper_service._reject_order(conn, order["order_id"], "来源策略已删除", now)
                    result["rejected"] += 1
                    continue
                if open_count >= int(account["max_total_positions"]):
                    self.paper_service._reject_order(conn, order["order_id"], "成交时已达到账户最大持仓数", now)
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
                    self.paper_service._reject_order(conn, order["order_id"], "成交时已达到策略最大持仓数", now)
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
                    self.paper_service._reject_order(
                        conn, order["order_id"], "成交时已达到同方向最大持仓数", now
                    )
                    result["rejected"] += 1
                    continue
                fill_price = ask + slippage if order["direction"] == "buy" else bid - slippage
                if not self.paper_service._valid_exits(
                    order["direction"], fill_price,
                    float(order["stop_loss"]), float(order["take_profit"]),
                ):
                    self.paper_service._reject_order(conn, order["order_id"], "滑点后止盈止损无效", now)
                    result["rejected"] += 1
                    continue
                volume = float(order["requested_volume"])
                required_margin = fill_price * volume * contract_size / settings["leverage"]
                if required_margin > available_margin:
                    self.paper_service._reject_order(conn, order["order_id"], "可用保证金不足", now)
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

            balance = self.paper_service.position_service.manage(
                conn, user_id, account_id, symbol, bid, ask, now, settings,
                pivots, structures, result, balance, contract_size, slippage,
            )
            equity, margin, open_positions = self.paper_service.accounting_service.mark_positions(
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
            self.paper_service._sync_paper_decision_status(
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
            outcome = self.paper_service.storage.fetchone(
                "SELECT status,rejection_reason FROM paper_orders WHERE order_id=?",
                (str(order["order_id"]),),
            )
            if not outcome or outcome["status"] not in {"filled", "rejected", "canceled"}:
                continue
            self.paper_service._record_execution_receipt(
                int(user_id), int(account_id), dict(order),
                "filled" if outcome["status"] == "filled" else (
                    "canceled" if outcome["status"] == "canceled" else "rejected"
                ),
                str(outcome["rejection_reason"] or ""),
                executed_price=float(order.get("filled_price") or 0),
                executed_volume=float(order.get("filled_volume") or 0),
            )
            self.paper_service.structure_plans.record_execution(
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
            self.paper_service._log_runtime(
                user_id,
                account_id,
                "execution",
                "，".join(part for part in parts if part),
                {"symbol": symbol, **result},
                now,
            )
        return result


    def expire_stale_pending_orders(self, user_id, symbol, now):
        service = self.paper_service
        cutoff = int(now) - service.PENDING_ORDER_TIMEOUT_SECONDS
        orders = service.storage.fetchall(
            "SELECT order_id,account_id,decision_id,deployment_id,strategy_id,"
            "position_attribution_json FROM paper_orders "
            "WHERE user_id=? AND symbol=? AND status='pending' AND requested_at<=?",
            (user_id, symbol, cutoff),
        )
        reason = "等待下一次行情撮合超时，订单已自动取消"
        for order in orders:
            service.storage.execute(
                "UPDATE paper_orders SET status='canceled',rejection_reason=?,"
                "canceled_at=?,updated_at=? WHERE order_id=? AND status='pending'",
                (reason, now, now, order["order_id"]),
            )
            service._sync_paper_decision_status(
                user_id, int(order["account_id"]), str(order["decision_id"]),
                str(order["order_id"]), status="expired", auto_executed=False,
            )
            service._record_execution_receipt(
                int(user_id), int(order["account_id"]),
                {**dict(order), "symbol": symbol}, "timeout", reason,
            )
            try:
                attribution = json.loads(order["position_attribution_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                attribution = {}
            plan_id = str(attribution.get("trade_plan_id") or "")
            if plan_id:
                service.structure_plans.record_execution(
                    int(user_id), int(order["account_id"]),
                    str(order["deployment_id"]), str(order["strategy_id"]), plan_id,
                    str(attribution.get("trade_plan_group_id") or ""), "expired",
                    order_id=str(order["order_id"]), reason=reason, payload=attribution,
                )
        return len(orders)
