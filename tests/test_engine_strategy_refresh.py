from dataclasses import dataclass

from trading_engine_manager import EngineKey, EngineRuntime, TradingEngineManager


class _Store:
    def __init__(self):
        self.reload_count = 0

    def reload_from_storage(self):
        self.reload_count += 1


class _Engine:
    def __init__(self):
        self.strategy_service = type(
            "_StrategyService", (), {"strategy_store": _Store()}
        )()


def _runtime(engine):
    return EngineRuntime(
        engine=engine,
        last_active_at=0,
        next_pending_cleanup_at=0,
        next_signal_cleanup_at=0,
        next_llm_analysis_at=0,
    )


def test_refresh_user_strategies_can_target_one_account():
    manager = TradingEngineManager.__new__(TradingEngineManager)
    manager._lock = __import__("threading").RLock()
    first = _Engine()
    second = _Engine()
    manager._engines = {
        EngineKey(7, 21): _runtime(first),
        EngineKey(7, 22): _runtime(second),
        EngineKey(8, 21): _runtime(_Engine()),
    }

    manager.refresh_user_strategies(7, account_id=21)

    assert first.strategy_service.strategy_store.reload_count == 1
    assert second.strategy_service.strategy_store.reload_count == 0
