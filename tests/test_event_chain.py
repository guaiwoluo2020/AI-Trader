from market.services.event_bus import EventBus
from market.services.events import BAR_CLOSED, PIVOT_UPDATED, STRUCTURE_UPDATED
from market.services.kline_ingestion_coordinator import KlineIngestionCoordinator


class _Klines:
    def process_kline_data(self, symbol, period, klines, is_full):
        return {"status": "ok"}

    def get_all_kline_objects(self, symbol, period):
        return [{"timestamp": 1}]


class _Pivots:
    def update_pivots(self, symbol, period, objects):
        return None

    def get_pivots(self, symbol, period):
        return [{"price": 1}]


def test_kline_event_chain_is_ordered_and_scoped():
    bus = EventBus()
    names = []
    for name in (BAR_CLOSED, PIVOT_UPDATED, STRUCTURE_UPDATED):
        bus.subscribe(name, lambda event, name=name: names.append((name, event.symbol, event.period if hasattr(event, "period") else event.payload.get("period"))))
    coordinator = KlineIngestionCoordinator(
        _Klines(), _Pivots(), lambda *_: 1, lambda *_: None,
        event_bus=bus, user_id=7, account_id=9,
    )
    coordinator.process_batch("BTCUSD", {"M5": [{"timestamp": 10}]})
    assert [item[0] for item in names] == [BAR_CLOSED, PIVOT_UPDATED, STRUCTURE_UPDATED]
    assert all(item[1] == "BTCUSD" for item in names)
