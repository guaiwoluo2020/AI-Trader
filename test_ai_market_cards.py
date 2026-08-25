import unittest

from server import TradingServer


class _SignalService:
    @staticmethod
    def get_active_signals():
        return []


class _StrategyStore:
    def __init__(self, strategies=None):
        self._strategies = strategies or []

    def get_all_strategies(self):
        return self._strategies


class _SourceRepository:
    def __init__(self, sources):
        self._sources = sources

    def list(self, user_id):
        return self._sources

    def get(self, user_id, source_id):
        return next(
            (source for source in self._sources
             if source["signal_source_id"] == source_id),
            None,
        )


class _KlineService:
    @staticmethod
    def get_klines(symbol, period, count):
        return [{"close": 100.0}]


class _Strategy:
    symbol = "BTCUSD"
    strategy_id = "strategy-1"
    strategy_name = "BTC strategy"
    lifecycle_status = "draft"
    min_confidence = 65

    def __init__(self, source):
        self._source = source

    def get_signal_sources(self, source_type):
        return [self._source] if source_type == "ai_entry" else []


def _managed_source(source_id="btc-m5"):
    return {
        "signal_source_id": source_id,
        "name": "BTC M5 独立分析",
        "symbol": "BTCUSD",
        "period": "M5",
        "enabled": True,
        "share_runtime_data": False,
        "config": {
            "analysis_mode": "self_analysis",
            "model": "deepseek-v4-flash",
            "min_confidence": 70,
            "analysis_interval_minutes": 5,
            "kline_count": 288,
        },
    }


def _analysis(source_id="btc-m5"):
    return {
        "trend_analysis": {
            "M5": {"trend": "单边上涨", "confidence": 82},
        },
        "trade_suggestions": [{
            "strategy_id": "__independent__",
            "signal_source_id": source_id,
            "period": "M5",
            "direction": "buy",
            "confidence": 82,
            "entry_price": 100,
        }],
        "analyzed_at": "2026-08-16T01:00:00",
        "market_status": "open",
    }


class AIMarketCardsTestCase(unittest.TestCase):
    def _engine(self, strategies=None):
        engine = TradingServer.__new__(TradingServer)
        engine.user_id = 7
        engine._strategy_store = _StrategyStore(strategies)
        engine._signal_service = _SignalService()
        engine._ai_signal_source_repository = _SourceRepository([
            _managed_source(),
        ])
        engine.kline_service = _KlineService()
        engine._decision_history = []
        engine.get_llm_analysis = lambda: {"BTCUSD": _analysis()}
        return engine

    def test_ai_source_is_rendered_once_without_strategy_binding(self):
        cards = self._engine().get_ai_market_cards()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["card_id"], "source:btc-m5")
        self.assertEqual(cards[0]["period"], "M5")
        self.assertEqual(cards[0]["confidence"], 82)
        self.assertEqual(cards[0]["linked_strategies"], [])
        self.assertEqual(cards[0]["status"], "analysis_ready")

    def test_bound_source_is_rendered_once_without_strategy_threshold(self):
        strategy_source = {
            "signal_source_id": "btc-m5",
            "source": "ai_entry",
            "period": "M5",
            "enabled": True,
            "weight": 100,
            "params": {
                "ai_signal_source_id": "btc-m5",
                "min_confidence": 70,
            },
        }
        cards = self._engine([
            _Strategy(strategy_source),
        ]).get_ai_market_cards()

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["card_id"], "source:btc-m5")
        self.assertEqual(cards[0]["model"], "deepseek-v4-flash")
        self.assertEqual(cards[0]["kline_count"], 288)
        self.assertEqual(cards[0]["confidence"], 82)
        self.assertEqual(cards[0]["linked_strategies"][0]["strategy_id"], "strategy-1")

    def test_each_source_card_uses_its_own_snapshot_and_timestamp(self):
        m1 = _managed_source("btc-m1")
        m1["period"] = "M1"
        m5 = _managed_source("btc-m5")
        engine = self._engine()
        engine._ai_signal_source_repository = _SourceRepository([m1, m5])
        aggregate = _analysis("btc-m1")
        aggregate["analyzed_at"] = "2026-08-26T02:00:00"
        aggregate["source_results"] = {
            "btc-m1": {
                "trend_analysis": {"M1": {"trend": "单边上涨", "confidence": 81}},
                "trade_suggestions": [],
                "analyzed_at": "2026-08-26T02:00:00",
                "market_status": "active",
            },
            "btc-m5": {
                "trend_analysis": {"M5": {"trend": "区间震荡", "confidence": 63}},
                "trade_suggestions": [],
                "analyzed_at": "2026-08-25T20:00:00",
                "market_status": "active",
            },
        }
        engine.get_llm_analysis = lambda: {"BTCUSD": aggregate}

        cards = {card["signal_source_id"]: card for card in engine.get_ai_market_cards()}

        self.assertEqual(cards["btc-m1"]["analyzed_at"], "2026-08-26T02:00:00")
        self.assertEqual(cards["btc-m1"]["confidence"], 81)
        self.assertEqual(cards["btc-m5"]["analyzed_at"], "2026-08-25T20:00:00")
        self.assertEqual(cards["btc-m5"]["confidence"], 63)


if __name__ == "__main__":
    unittest.main()
