"""Bounded, setup-scoped adaptive tuning for AI signal sources.

This module is deliberately deterministic: it proposes small configuration
changes from closed, attributed trades and never changes a source by itself.
The caller decides whether to persist the patch and hot-reload it.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Callable, Dict, Iterable, List, Optional

from sqlite_storage import AISignalSourceRepository, get_storage


DEFAULT_SAMPLE_SIZE = 7


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def analyze_setup_trades(
    trades: Iterable[Dict], source_id: str, setup_type: str,
    config: Dict, sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> Dict:
    """Return stats and a bounded patch for one source/setup combination."""
    sample_size = max(5, min(50, int(sample_size or DEFAULT_SAMPLE_SIZE)))
    source_id = str(source_id or "")
    setup_type = str(setup_type or "generic_entry").lower()
    matched = []
    for trade in trades or []:
        attribution = trade.get("position_attribution") or {}
        if str(attribution.get("signal_source_id") or trade.get("signal_source_id") or "") != source_id:
            continue
        if str(attribution.get("setup_type") or trade.get("setup_type") or "generic_entry").lower() != setup_type:
            continue
        matched.append(trade)
    matched.sort(key=lambda item: int(item.get("closed_at") or item.get("created_at") or 0), reverse=True)
    matched = matched[:sample_size]
    profits = [float(item.get("net_profit") or 0) for item in matched]
    wins = sum(value > 0 for value in profits)
    losses = sum(value < 0 for value in profits)
    stats = {
        "sample_size": sample_size,
        "matched_count": len(matched),
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / len(profits) * 100, 2) if profits else 0,
        "net_profit": round(sum(profits), 2),
        "average_profit": round(sum(profits) / len(profits), 2) if profits else 0,
    }
    current = {
        "range_min_touches": int(config.get("range_min_touches", 3) or 3),
        "range_min_inside_ratio": float(config.get("range_min_inside_ratio", 0.80) or 0.80),
        "range_tolerance_atr_multiplier": float(config.get("range_tolerance_atr_multiplier", 0.60) or 0.60),
        "range_min_width_atr": float(config.get("range_min_width_atr", 2.0) or 2.0),
        "range_max_width_atr": float(config.get("range_max_width_atr", 6.0) or 6.0),
    }
    patch: Dict = {}
    reason = "样本不足，仅展示统计，不建议调整"
    if len(matched) >= sample_size:
        if stats["win_rate"] < 40 and stats["net_profit"] < 0:
            patch = {
                "range_min_touches": min(5, current["range_min_touches"] + 1),
                "range_min_inside_ratio": round(_clamp(current["range_min_inside_ratio"] + 0.05, 0.5, 1.0), 2),
            }
            reason = "该 SETUP 最近样本胜率和净收益偏低，收紧箱体成立条件"
        elif stats["win_rate"] >= 70 and stats["net_profit"] > 0:
            patch = {}
            reason = "该 SETUP 最近样本表现稳定，保持当前参数，避免过拟合"
        else:
            reason = "样本表现中性，暂不调整参数"
    return {
        "source_id": source_id,
        "setup_type": setup_type,
        "stats": stats,
        "current": current,
        "patch": patch,
        "reason": reason,
        "safe_to_apply": bool(patch),
    }


class AdaptiveSignalTuner:
    """Evaluate and apply one bounded change per new setup sample."""

    def __init__(
        self, storage=None, source_repository=None,
        refresh_callback: Optional[Callable[[int], None]] = None,
    ):
        self.storage = storage or get_storage()
        self.sources = source_repository or AISignalSourceRepository(self.storage)
        self.refresh_callback = refresh_callback

    @staticmethod
    def _attribution(row: Dict) -> Dict:
        value = row.get("position_attribution")
        if isinstance(value, dict):
            return value
        try:
            return json.loads(row.get("position_attribution_json") or "{}")
        except (TypeError, ValueError):
            return {}

    def _closed_trades(self, user_id: int, source_id: str) -> List[Dict]:
        rows = [dict(row) for row in self.storage.fetchall(
            "SELECT trade_id, net_profit, closed_at, position_attribution_json "
            "FROM paper_trades WHERE user_id = ? AND closed_at > 0 "
            "AND exit_reason NOT IN ('partial_take_profit', 'signal_take_profit') "
            "ORDER BY closed_at DESC LIMIT 1000",
            (int(user_id),),
        )]
        for row in rows:
            row["trade_id"] = f"paper:{row.get('trade_id')}"
        try:
            live_deals = [dict(row) for row in self.storage.fetchall(
                "SELECT account_id, ticket, mt5_position_id, entry_type, volume, "
                "profit, swap, commission, deal_timestamp, position_attribution_json "
                "FROM live_trade_deals WHERE user_id = ? AND mt5_position_id > 0 "
                "ORDER BY deal_timestamp DESC LIMIT 5000",
                (int(user_id),),
            )]
            positions = {}
            for deal in live_deals:
                key = (
                    int(deal.get("account_id") or 0),
                    int(deal.get("mt5_position_id") or 0),
                )
                item = positions.setdefault(key, {
                    "entry_volume": 0.0, "exit_volume": 0.0,
                    "net_profit": 0.0, "closed_at": 0,
                    "position_attribution_json": "{}",
                })
                entry_type = int(deal.get("entry_type") or 0)
                if entry_type == 0:
                    item["entry_volume"] += float(deal.get("volume") or 0)
                elif entry_type in (1, 2, 3):
                    item["exit_volume"] += float(deal.get("volume") or 0)
                    item["net_profit"] += sum(
                        float(deal.get(name) or 0)
                        for name in ("profit", "swap", "commission")
                    )
                    item["closed_at"] = max(
                        item["closed_at"], int(deal.get("deal_timestamp") or 0)
                    )
                    if deal.get("position_attribution_json"):
                        item["position_attribution_json"] = deal[
                            "position_attribution_json"
                        ]
            for (account_id, position_id), item in positions.items():
                if (
                    item["entry_volume"] <= 0
                    or item["exit_volume"] + 1e-9 < item["entry_volume"]
                    or item["closed_at"] <= 0
                ):
                    continue
                rows.append({
                    "trade_id": f"live:{account_id}:{position_id}",
                    "net_profit": item["net_profit"],
                    "closed_at": item["closed_at"],
                    "position_attribution_json": item["position_attribution_json"],
                })
        except Exception:
            # A temporary data-source failure must not stop other users' tuning.
            pass
        for row in rows:
            row["position_attribution"] = self._attribution(row)
        rows = [
            row for row in rows
            if str((row.get("position_attribution") or {}).get("signal_source_id") or "") == source_id
        ]
        rows.sort(key=lambda item: int(item.get("closed_at") or 0), reverse=True)
        return rows

    @staticmethod
    def _sample_signature(trades: List[Dict], setup_type: str) -> str:
        ids = [str(item.get("trade_id") or "") for item in trades]
        return hashlib.sha256(
            (str(setup_type) + "|" + "|".join(ids)).encode("utf-8")
        ).hexdigest()[:20]

    def run_once(self) -> Dict:
        rows = self.storage.fetchall(
            "SELECT DISTINCT user_id FROM ai_signal_sources WHERE enabled = 1"
        )
        applied, evaluated = [], []
        for row in rows:
            user_id = int(row["user_id"])
            for source in self.sources.list(user_id, enabled_only=True):
                config = dict(source.get("config") or {})
                if str(config.get("signal_source_version") or "1.0") != "2.0":
                    continue
                if config.get("adaptive_enabled", True) is False:
                    continue
                source_id = str(source["signal_source_id"])
                sample_size = max(5, min(50, int(config.get("adaptive_sample_size", 7) or 7)))
                trades = self._closed_trades(user_id, source_id)
                setup_types = sorted({
                    str((item.get("position_attribution") or {}).get("setup_type") or "generic_entry").lower()
                    for item in trades
                })
                for setup_type in setup_types:
                    result = analyze_setup_trades(
                        trades, source_id, setup_type, config, sample_size,
                    )
                    evaluated.append(result)
                    setup_trades = [
                        item for item in trades
                        if str((item.get("position_attribution") or {}).get("setup_type") or "generic_entry").lower() == setup_type
                    ][:sample_size]
                    signature = self._sample_signature(setup_trades, setup_type)
                    signatures = dict(config.get("adaptive_sample_signatures") or {})
                    if not result["safe_to_apply"] or signatures.get(setup_type) == signature:
                        continue
                    before = {key: config.get(key) for key in result["patch"]}
                    config.update(result["patch"])
                    signatures[setup_type] = signature
                    config["adaptive_enabled"] = True
                    config["adaptive_sample_size"] = sample_size
                    config["adaptive_sample_signatures"] = signatures
                    config["adaptive_last_applied_at"] = int(time.time())
                    history = list(config.get("adaptive_history") or [])[-9:]
                    history.append({
                        "applied_at": int(time.time()), "setup_type": setup_type,
                        "sample_signature": signature, "stats": result["stats"],
                        "before": before, "after": result["patch"],
                        "reason": result["reason"],
                    })
                    config["adaptive_history"] = history
                    self.sources.update_adaptive_config(user_id, source_id, config)
                    applied.append({"user_id": user_id, **result, "before": before})
                if any(item.get("user_id") == user_id for item in applied) and self.refresh_callback:
                    self.refresh_callback(user_id)
        return {"evaluated": len(evaluated), "applied": applied}
