"""Account-level trading statistics for the current Beijing calendar day."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def beijing_day_window(now: Optional[datetime] = None) -> tuple[int, int, str]:
    """Return ``[start, end)`` Unix seconds for today's Beijing date.

    ``now`` may be naive (treated as UTC for deterministic callers) or aware.
    The database stores event timestamps as Unix seconds, so the conversion is
    done here rather than relying on the MySQL session time zone.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = current.astimezone(BEIJING_TZ).date()
    start_local = datetime.combine(local_date, time.min, tzinfo=BEIJING_TZ)
    end_local = datetime.combine(local_date, time.max, tzinfo=BEIJING_TZ)
    # Use the next midnight so the SQL predicate is half-open and has no
    # fractional-second ambiguity.
    end_local = end_local.replace(microsecond=0) + timedelta(seconds=1)
    return int(start_local.timestamp()), int(end_local.timestamp()), local_date.isoformat()


def _stats(date: str, *, filled: int, wins: int, win_amount: float,
           losses: int, loss_amount: float, breakeven: int,
           net_profit: float) -> Dict:
    return {
        "date": date,
        "timezone": "Asia/Shanghai",
        "filled_count": int(filled or 0),
        "win_count": int(wins or 0),
        "win_amount": round(float(win_amount or 0), 2),
        "loss_count": int(losses or 0),
        "loss_amount": round(float(loss_amount or 0), 2),
        "breakeven_count": int(breakeven or 0),
        "net_profit": round(float(net_profit or 0), 2),
    }


def today_trade_stats(storage, user_id: int, account_id: int,
                      account_type: str, now: Optional[datetime] = None) -> Dict:
    """Aggregate realized trades for an account since Beijing midnight.

    Paper rows are one completed/partial close in ``paper_trades``. Live rows
    are exit deals only (``entry_type <> 0``), because opening deals have no
    realized P/L and must not be classified as wins or losses.
    """
    start, end, date = beijing_day_window(now)
    if account_type == "paper":
        row = storage.fetchone(
            """
            SELECT COUNT(*) AS filled_count,
                   SUM(CASE WHEN net_profit > 0 THEN 1 ELSE 0 END) AS win_count,
                   COALESCE(SUM(CASE WHEN net_profit > 0 THEN net_profit ELSE 0 END), 0) AS win_amount,
                   SUM(CASE WHEN net_profit < 0 THEN 1 ELSE 0 END) AS loss_count,
                   COALESCE(SUM(CASE WHEN net_profit < 0 THEN net_profit ELSE 0 END), 0) AS loss_amount,
                   SUM(CASE WHEN net_profit = 0 THEN 1 ELSE 0 END) AS breakeven_count,
                   COALESCE(SUM(net_profit), 0) AS net_profit
            FROM paper_trades
            WHERE user_id = ? AND account_id = ? AND closed_at >= ? AND closed_at < ?
            """,
            (int(user_id), int(account_id), start, end),
        )
    else:
        row = storage.fetchone(
            """
            SELECT COUNT(*) AS filled_count,
                   SUM(CASE WHEN (profit + swap + commission) > 0 THEN 1 ELSE 0 END) AS win_count,
                   COALESCE(SUM(CASE WHEN (profit + swap + commission) > 0
                                     THEN (profit + swap + commission) ELSE 0 END), 0) AS win_amount,
                   SUM(CASE WHEN (profit + swap + commission) < 0 THEN 1 ELSE 0 END) AS loss_count,
                   COALESCE(SUM(CASE WHEN (profit + swap + commission) < 0
                                     THEN (profit + swap + commission) ELSE 0 END), 0) AS loss_amount,
                   SUM(CASE WHEN (profit + swap + commission) = 0 THEN 1 ELSE 0 END) AS breakeven_count,
                   COALESCE(SUM(profit + swap + commission), 0) AS net_profit
            FROM live_trade_deals
            WHERE user_id = ? AND account_id = ? AND entry_type <> 0
              AND deal_timestamp >= ? AND deal_timestamp < ?
            """,
            (int(user_id), int(account_id), start, end),
        )
    row = dict(row or {})
    return _stats(
        date,
        filled=row.get("filled_count"),
        wins=row.get("win_count"),
        win_amount=row.get("win_amount"),
        losses=row.get("loss_count"),
        loss_amount=row.get("loss_amount"),
        breakeven=row.get("breakeven_count"),
        net_profit=row.get("net_profit"),
    )
