"""Daily realized PnL summaries persisted for fast account queries."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

def _bucket(value, fallback):
    value = str(value or fallback).strip()
    return value or fallback

def _metrics(values):
    values = [float(v or 0) for v in values]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    return (len(values), round(sum(wins), 8), round(abs(sum(losses)), 8),
            round(max(wins), 8) if wins else None, round(min(wins), 8) if wins else None,
            round(max(losses), 8) if losses else None, round(min(losses), 8) if losses else None)

def _date_bounds(day):
    start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())

def build_daily_pnl_statistics(storage, user_id, account_id, day):
    """Rebuild one account/day idempotently from closed positions/deals."""
    start, end = _date_bounds(day)
    account = storage.fetchone("SELECT account_type FROM trading_accounts WHERE id=? AND user_id=?", (int(account_id), int(user_id))) or {}
    mode = "paper" if str(account.get("account_type") or "").lower() == "paper" else "live"
    rows = []
    if mode == "paper":
        rows = storage.fetchall("""SELECT p.symbol, SUM(t.net_profit) AS profit,
            MAX(t.closed_at) AS closed_at, p.position_attribution_json AS attribution
            FROM paper_trades t JOIN paper_positions p ON p.position_id=t.position_id
            WHERE t.user_id=? AND t.account_id=? AND t.closed_at>=? AND t.closed_at<?
            GROUP BY p.position_id, p.symbol, p.position_attribution_json""", (int(user_id), int(account_id), start, end))
    else:
        rows = storage.fetchall("""SELECT symbol, mt5_position_id, SUM(profit+swap+commission) AS profit,
            MAX(deal_timestamp) AS closed_at, MAX(position_attribution_json) AS attribution
            FROM live_trade_deals WHERE user_id=? AND account_id=? AND deal_timestamp>=? AND deal_timestamp<?
            GROUP BY mt5_position_id, symbol""", (int(user_id), int(account_id), start, end))
    buckets = {}
    for row in rows or []:
        try: attr = json.loads(row.get("attribution") or "{}")
        except (TypeError, ValueError): attr = {}
        period = (
            attr.get("period") or attr.get("source_period")
            or attr.get("signal_source_period") or attr.get("selected_signal_period")
            or attr.get("plan_period")
        )
        key = (str(row.get("symbol") or ""), _bucket(period, "未知"), _bucket(attr.get("setup_type") or attr.get("selected_setup_type"), "未分类"))
        buckets.setdefault(key, []).append(float(row.get("profit") or 0))
    storage.execute("DELETE FROM daily_pnl_statistics WHERE user_id=? AND account_id=? AND business_date=?", (int(user_id), int(account_id), day.isoformat()))
    for (symbol, period, setup), values in buckets.items():
        count, gross_profit, gross_loss, max_win, min_win, max_loss, min_loss = _metrics(values)
        storage.execute("""INSERT INTO daily_pnl_statistics
            (user_id,account_id,execution_mode,business_date,symbol,period,setup_type,
             trade_count,gross_profit,gross_loss,max_profit,min_profit,max_loss,min_loss,net_profit,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (int(user_id), int(account_id), mode, day.isoformat(), symbol, period, setup, count,
             gross_profit, gross_loss, max_win, min_win, max_loss, min_loss, round(sum(values), 8), int(datetime.now(TZ).timestamp())))
    return len(buckets)

def query_daily_pnl_statistics(storage, user_id, account_id, day):
    rows = storage.fetchall("SELECT * FROM daily_pnl_statistics WHERE user_id=? AND account_id=? AND business_date=? ORDER BY symbol,period,setup_type", (int(user_id), int(account_id), day.isoformat()))
    return [dict(row) for row in rows or []]

def rebuild_yesterday_for_all_accounts(storage):
    day = datetime.now(TZ).date() - timedelta(days=1)
    rows = storage.fetchall("SELECT id, user_id FROM trading_accounts WHERE status <> 'closed'") or []
    total = 0
    for row in rows:
        total += build_daily_pnl_statistics(storage, 0 if not row.get("user_id") else row["user_id"], row["id"], day)
    return total
