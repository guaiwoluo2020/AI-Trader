from datetime import datetime, timezone

from market.mt5_time import (
    beijing_text,
    broker_wall_epoch_to_utc,
    parse_ea_instant,
)
from market.models.position import PositionData
from market.models.statistics import StatisticsData
from market.models.trade_history import TradeDeal


UTC_EPOCH = 1767225600  # 2026-01-01 00:00:00Z


def test_broker_wall_epoch_converts_to_utc():
    assert broker_wall_epoch_to_utc(UTC_EPOCH + 3 * 3600, 3 * 3600) == UTC_EPOCH
    assert parse_ea_instant(
        {
            "broker_timestamp": UTC_EPOCH + 2 * 3600,
            "broker_utc_offset_seconds": 2 * 3600,
        },
        utc_field="timestamp_utc",
        broker_epoch_field="broker_timestamp",
    ) == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_trade_deal_uses_utc_and_exposes_beijing_time():
    deal = TradeDeal.from_ea_data({
        "ticket": 1,
        "deal_timestamp": UTC_EPOCH,
        "time": "2026.01.01 03:00:00",
        "broker_utc_offset_seconds": 3 * 3600,
    })
    payload = deal.to_dict()
    assert deal.time == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert payload["deal_timestamp"] == UTC_EPOCH
    assert payload["time_utc"] == "2026-01-01T00:00:00Z"
    assert payload["time_beijing"] == "2026-01-01 08:00:00"


def test_position_and_statistics_prefer_explicit_utc_timestamp():
    position = PositionData.from_ea_data({
        "ticket": 2,
        "symbol": "BTCUSD",
        "openTime": UTC_EPOCH + 3 * 3600,
        "open_timestamp": UTC_EPOCH,
        "broker_utc_offset_seconds": 3 * 3600,
    })
    statistics = StatisticsData.from_ea_data({
        "symbol": "BTCUSD",
        "reported_timestamp": UTC_EPOCH,
    })
    assert position.opened_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert position.to_dict()["opened_at"] == "2026-01-01T00:00:00Z"
    assert statistics.timestamp == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert statistics.to_dict()["reported_timestamp"] == UTC_EPOCH
    assert beijing_text(statistics.timestamp) == "2026-01-01 08:00:00"
