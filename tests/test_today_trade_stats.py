from datetime import datetime, timezone

from market.services.today_trade_stats import beijing_day_window, today_trade_stats


class FakeStorage:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def fetchone(self, sql, params):
        self.calls.append((sql, params))
        return self.row


def test_beijing_day_window_crosses_utc_date_boundary():
    # 2026-09-08 16:00 UTC is 2026-09-09 00:00 Beijing time.
    start, end, date = beijing_day_window(
        datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)
    )
    assert date == "2026-09-09"
    assert end - start == 86400
    assert start == int(datetime(2026, 9, 8, 16, tzinfo=timezone.utc).timestamp())


def test_paper_today_stats_counts_wins_losses_and_breakeven():
    storage = FakeStorage({
        "filled_count": 4,
        "win_count": 2,
        "win_amount": 15.5,
        "loss_count": 1,
        "loss_amount": -4.25,
        "breakeven_count": 1,
        "net_profit": 11.25,
    })
    result = today_trade_stats(
        storage, 7, 22, "paper",
        datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert result == {
        "date": "2026-09-09",
        "timezone": "Asia/Shanghai",
        "filled_count": 4,
        "win_count": 2,
        "win_amount": 15.5,
        "loss_count": 1,
        "loss_amount": -4.25,
        "breakeven_count": 1,
        "net_profit": 11.25,
    }
    assert storage.calls[0][1][2:] == (1788883200, 1788969600)


def test_live_stats_uses_realized_exit_deals_only():
    storage = FakeStorage({
        "filled_count": 1,
        "win_count": 0,
        "win_amount": 0,
        "loss_count": 1,
        "loss_amount": -3.0,
        "breakeven_count": 0,
        "net_profit": -3.0,
    })
    result = today_trade_stats(
        storage, 7, 22, "mt5",
        datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert result["filled_count"] == 1
    assert "entry_type <> 0" in storage.calls[0][0]
