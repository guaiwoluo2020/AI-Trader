from datetime import datetime, timezone, timedelta

from account_auto_flatten_service import scheduled_window


def test_beijing_window_and_five_minutes():
    # 2026-09-11 07:02 Beijing is inside a 07:00 window
    now = datetime(2026, 9, 10, 23, 2, tzinfo=timezone.utc)
    result = scheduled_window("07:00", now)
    assert result is not None
    assert result[0].isoformat() == "2026-09-11T07:00:00+08:00"
    assert result[1].isoformat() == "2026-09-11T07:05:00+08:00"


def test_before_and_after_window_do_not_match():
    assert scheduled_window("07:00", datetime(2026, 9, 10, 22, 59, tzinfo=timezone.utc)) is None
    assert scheduled_window("07:00", datetime(2026, 9, 11, 0, 5, tzinfo=timezone.utc)) is None
