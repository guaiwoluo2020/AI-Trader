"""Application boundary for Paper position marking and lifecycle."""

import json
import uuid

from market.services.position_attribution import close_position_attribution


class PaperPositionService:
    def __init__(self, paper_service):
        self.paper_service = paper_service

    def manage(self, conn, user_id, account_id, symbol, bid, ask, now,
                   settings, pivots, structures, result, balance, contract_size, slippage):
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
                action = self.paper_service.position_manager.evaluate(
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
                        self.paper_service.position_events.record(
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

        return balance
