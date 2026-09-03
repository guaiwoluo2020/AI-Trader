"""Administrator analytics for Structure Plan setup performance."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query

from auth import AuthUser, require_admin
from mysql_repositories import get_storage


def _attribution(row: Dict) -> Dict:
    value = row.get("position_attribution_json")
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _bucket_start(timestamp: int) -> int:
    """Four-hour bucket aligned to Beijing time (UTC+8)."""
    offset = 8 * 3600
    return ((int(timestamp) + offset) // (4 * 3600)) * (4 * 3600) - offset


def create_structure_plan_analysis_routes() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/admin/structure-plan/performance",
        dependencies=[Depends(require_admin)],
    )
    async def structure_plan_performance(
        symbol: str = Query("", max_length=64),
        setup_type: str = Query("", max_length=80),
        days: int = Query(7, ge=1, le=30),
        user: AuthUser = Depends(require_admin),
    ) -> Dict:
        now = int(time.time())
        start = now - int(days) * 86400
        wanted_symbol = symbol.strip().upper()
        wanted_setup = setup_type.strip().lower()
        storage = get_storage()

        # Only the bounded time window is read. Attribution is decoded in the
        # service so this endpoint remains compatible with JSON columns and
        # older rows whose payload shape differs slightly.
        paper_rows = storage.fetchall(
            "SELECT symbol, direction, net_profit, closed_at, "
            "position_attribution_json FROM paper_trades "
            "WHERE closed_at >= ? AND closed_at <= ? ORDER BY closed_at",
            (start, now),
        )
        live_rows = storage.fetchall(
            "SELECT symbol, deal_type, profit, deal_timestamp, "
            "position_attribution_json FROM live_trade_deals "
            "WHERE deal_timestamp >= ? AND deal_timestamp <= ? ORDER BY deal_timestamp",
            (start, now),
        )

        buckets = defaultdict(lambda: {
            "pnl": 0.0, "orders": 0, "wins": 0, "losses": 0,
            "paper_orders": 0, "live_orders": 0,
        })
        setup_summary = defaultdict(lambda: {
            "setup_type": "", "pnl": 0.0, "orders": 0,
            "wins": 0, "losses": 0, "paper_orders": 0, "live_orders": 0,
        })
        symbols = set()
        setups = set()

        def consume(row: Dict, profit_key: str, time_key: str, source: str) -> None:
            attribution = _attribution(row)
            if attribution.get("signal_source") != "structure_plan":
                return
            row_symbol = str(row.get("symbol") or "").strip()
            row_setup = str(
                attribution.get("setup_type")
                or attribution.get("selected_setup_type")
                or "unknown"
            ).strip().lower()
            if wanted_symbol and row_symbol.upper() != wanted_symbol:
                return
            if wanted_setup and row_setup != wanted_setup:
                return
            timestamp = int(row.get(time_key) or 0)
            if not timestamp:
                return
            profit = float(row.get(profit_key) or 0)
            symbols.add(row_symbol)
            setups.add(row_setup)
            bucket = buckets[_bucket_start(timestamp)]
            bucket["pnl"] += profit
            bucket["orders"] += 1
            bucket["wins"] += int(profit > 0)
            bucket["losses"] += int(profit < 0)
            bucket[f"{source}_orders"] += 1
            summary = setup_summary[row_setup]
            summary["setup_type"] = row_setup
            summary["pnl"] += profit
            summary["orders"] += 1
            summary["wins"] += int(profit > 0)
            summary["losses"] += int(profit < 0)
            summary[f"{source}_orders"] += 1

        for row in paper_rows:
            consume(row, "net_profit", "closed_at", "paper")
        for row in live_rows:
            consume(row, "profit", "deal_timestamp", "live")

        points = []
        cumulative = 0.0
        for timestamp in sorted(buckets):
            item = buckets[timestamp]
            cumulative += item["pnl"]
            points.append({
                "time": timestamp,
                "pnl": round(item["pnl"], 2),
                "cumulative_pnl": round(cumulative, 2),
                "orders": item["orders"],
                "wins": item["wins"],
                "losses": item["losses"],
                "paper_orders": item["paper_orders"],
                "live_orders": item["live_orders"],
            })
        summaries = []
        for item in sorted(setup_summary.values(), key=lambda x: x["pnl"]):
            item = dict(item)
            item["pnl"] = round(item["pnl"], 2)
            item["win_rate"] = round(item["wins"] / item["orders"] * 100, 2) if item["orders"] else 0
            summaries.append(item)
        total_orders = sum(item["orders"] for item in setup_summary.values())
        total_pnl = sum(item["pnl"] for item in setup_summary.values())
        return {
            "status": "ok",
            "filters": {"symbol": symbol.strip(), "setup_type": setup_type.strip(), "days": days},
            "window": {"from": start, "to": now, "bucket_seconds": 14400, "timezone": "Asia/Shanghai"},
            "options": {"symbols": sorted(symbols), "setups": sorted(setups)},
            "summary": {
                "orders": total_orders,
                "pnl": round(total_pnl, 2),
                "wins": sum(item["wins"] for item in setup_summary.values()),
                "losses": sum(item["losses"] for item in setup_summary.values()),
                "setup_count": len(summaries),
            },
            "by_setup": summaries,
            "points": points,
        }

    return router
