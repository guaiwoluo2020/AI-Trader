"""Per-deployment performance attribution for trading account views."""

from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional


def _as_dict(value) -> Dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _deployment_rows(storage, user_id: int, account_id: int, mode: str) -> List[Dict]:
    rows = storage.fetchall(
        """
        SELECT d.*, s.config_json
        FROM strategy_deployments d
        LEFT JOIN user_strategy_configs s
          ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
        WHERE d.user_id = ? AND d.account_id = ? AND d.execution_mode = ?
        ORDER BY CASE d.status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END,
                 d.created_at DESC
        """,
        (int(user_id), int(account_id), str(mode)),
    )
    deployments = []
    for row in rows:
        item = dict(row)
        config = _as_dict(item.pop("config_json", "{}"))
        item["strategy_name"] = str(
            config.get("strategy_name") or item.get("strategy_id") or "策略"
        )
        deployments.append(item)
    return deployments


def _summarize(
    deployment: Dict,
    outcomes: Iterable[Dict],
    *,
    filled_order_count: int = 0,
    open_position_count: int = 0,
    unrealized_profit: float = 0,
) -> Dict:
    values = sorted(
        list(outcomes),
        key=lambda item: (
            int(item.get("closed_at") or 0), str(item.get("position_id") or "")
        ),
    )
    profits = [float(item.get("net_profit") or 0) for item in values]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    breakeven = len(profits) - len(wins) - len(losses)
    commission = sum(float(item.get("commission") or 0) for item in values)

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    loss_streak = 0
    max_loss_streak = 0
    for value in profits:
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        if value < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "deployment_id": str(deployment.get("deployment_id") or ""),
        "strategy_id": str(deployment.get("strategy_id") or ""),
        "strategy_name": str(deployment.get("strategy_name") or "策略"),
        "symbol": str(deployment.get("symbol") or ""),
        "execution_mode": str(deployment.get("execution_mode") or ""),
        "status": str(deployment.get("status") or ""),
        "deployed_at": int(deployment.get("created_at") or 0),
        "updated_at": int(deployment.get("updated_at") or 0),
        "filled_order_count": int(filled_order_count),
        "closed_position_count": len(values),
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": breakeven,
        "win_rate": round(len(wins) / len(values) * 100, 2) if values else 0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_profit": round(sum(profits), 2),
        "commission": round(commission, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "average_win": round(gross_profit / len(wins), 2) if wins else 0,
        "average_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "max_drawdown": round(max_drawdown, 2),
        "max_consecutive_losses": max_loss_streak,
        "open_position_count": int(open_position_count),
        "unrealized_profit": round(float(unrealized_profit or 0), 2),
    }


def build_paper_performance(storage, user_id: int, account_id: int) -> List[Dict]:
    deployments = _deployment_rows(storage, user_id, account_id, "paper")
    if not deployments:
        return []
    positions = [dict(row) for row in storage.fetchall(
        "SELECT position_id, deployment_id, status, unrealized_profit "
        "FROM paper_positions WHERE user_id = ? AND account_id = ?",
        (int(user_id), int(account_id)),
    )]
    closed_ids = {
        str(item["position_id"]) for item in positions if item.get("status") == "closed"
    }
    trades = [dict(row) for row in storage.fetchall(
        "SELECT position_id, deployment_id, net_profit, commission, closed_at "
        "FROM paper_trades WHERE user_id = ? AND account_id = ? ORDER BY closed_at",
        (int(user_id), int(account_id)),
    )]
    grouped: Dict[tuple, List[Dict]] = {}
    for trade in trades:
        position_id = str(trade.get("position_id") or "")
        if position_id not in closed_ids:
            continue
        grouped.setdefault(
            (str(trade.get("deployment_id") or ""), position_id), []
        ).append(trade)

    outcomes: Dict[str, List[Dict]] = {}
    for (deployment_id, position_id), items in grouped.items():
        outcomes.setdefault(deployment_id, []).append({
            "position_id": position_id,
            "net_profit": sum(float(item.get("net_profit") or 0) for item in items),
            "commission": sum(float(item.get("commission") or 0) for item in items),
            "closed_at": max(int(item.get("closed_at") or 0) for item in items),
        })

    order_counts = {
        str(row["deployment_id"]): int(row["count"])
        for row in storage.fetchall(
            "SELECT deployment_id, COUNT(*) AS count FROM paper_orders "
            "WHERE user_id = ? AND account_id = ? AND status = 'filled' "
            "GROUP BY deployment_id",
            (int(user_id), int(account_id)),
        )
    }
    open_stats: Dict[str, Dict] = {}
    for position in positions:
        if position.get("status") != "open":
            continue
        stats = open_stats.setdefault(
            str(position.get("deployment_id") or ""), {"count": 0, "profit": 0.0}
        )
        stats["count"] += 1
        stats["profit"] += float(position.get("unrealized_profit") or 0)

    return [
        _summarize(
            deployment,
            outcomes.get(str(deployment.get("deployment_id") or ""), []),
            filled_order_count=order_counts.get(
                str(deployment.get("deployment_id") or ""), 0
            ),
            open_position_count=open_stats.get(
                str(deployment.get("deployment_id") or ""), {}
            ).get("count", 0),
            unrealized_profit=open_stats.get(
                str(deployment.get("deployment_id") or ""), {}
            ).get("profit", 0),
        )
        for deployment in deployments
    ]


def build_live_performance(
    storage, user_id: int, account_id: int, positions: Optional[List[Dict]] = None,
) -> List[Dict]:
    deployments = _deployment_rows(storage, user_id, account_id, "live")
    if not deployments:
        return []
    rows = [dict(row) for row in storage.fetchall(
        "SELECT ticket, mt5_position_id, entry_type, profit, swap, commission, "
        "deal_timestamp, position_attribution_json FROM live_trade_deals "
        "WHERE user_id = ? AND account_id = ? ORDER BY deal_timestamp, id",
        (int(user_id), int(account_id)),
    )]
    by_position: Dict[str, List[Dict]] = {}
    for row in rows:
        attribution = _as_dict(row.get("position_attribution_json"))
        strategy_id = str(attribution.get("strategy_id") or "")
        if not strategy_id:
            continue
        row["strategy_id"] = strategy_id
        key = str(row.get("mt5_position_id") or row.get("ticket") or "")
        by_position.setdefault(key, []).append(row)

    current_positions = {
        str(item.get("ticket") or item.get("position_id") or ""): item
        for item in (positions or [])
    }
    outcomes: Dict[str, List[Dict]] = {}
    for position_id, items in by_position.items():
        has_exit = any(int(item.get("entry_type") or 0) != 0 for item in items)
        if not has_exit or position_id in current_positions:
            continue
        strategy_id = str(items[-1].get("strategy_id") or "")
        outcomes.setdefault(strategy_id, []).append({
            "position_id": position_id,
            "net_profit": sum(
                float(item.get("profit") or 0)
                + float(item.get("swap") or 0)
                + float(item.get("commission") or 0)
                for item in items
            ),
            "commission": abs(sum(
                min(0.0, float(item.get("commission") or 0)) for item in items
            )),
            "closed_at": max(int(item.get("deal_timestamp") or 0) for item in items),
        })

    reports = [dict(row) for row in storage.fetchall(
        "SELECT success, mt5_position_id, position_attribution_json "
        "FROM trade_execution_reports WHERE user_id = ? AND account_id = ?",
        (int(user_id), int(account_id)),
    )]
    order_counts: Dict[str, int] = {}
    position_strategies: Dict[str, str] = {}
    for report in reports:
        attribution = _as_dict(report.get("position_attribution_json"))
        strategy_id = str(attribution.get("strategy_id") or "")
        if not strategy_id:
            continue
        if bool(report.get("success")):
            order_counts[strategy_id] = order_counts.get(strategy_id, 0) + 1
        position_id = str(report.get("mt5_position_id") or "")
        if position_id:
            position_strategies[position_id] = strategy_id

    open_stats: Dict[str, Dict] = {}
    for position_id, position in current_positions.items():
        strategy_id = position_strategies.get(position_id, "")
        if not strategy_id:
            continue
        stats = open_stats.setdefault(strategy_id, {"count": 0, "profit": 0.0})
        stats["count"] += 1
        stats["profit"] += float(position.get("profit") or 0)

    return [
        _summarize(
            deployment,
            outcomes.get(str(deployment.get("strategy_id") or ""), []),
            filled_order_count=order_counts.get(
                str(deployment.get("strategy_id") or ""), 0
            ),
            open_position_count=open_stats.get(
                str(deployment.get("strategy_id") or ""), {}
            ).get("count", 0),
            unrealized_profit=open_stats.get(
                str(deployment.get("strategy_id") or ""), {}
            ).get("profit", 0),
        )
        for deployment in deployments
    ]
