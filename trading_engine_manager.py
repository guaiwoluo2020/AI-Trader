#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按用户和 MT5 账户隔离交易引擎。"""

import os
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from background_scheduler import SharedTaskScheduler
from data_retention import DataRetentionService
from market.services.adaptive_signal_tuner import AdaptiveSignalTuner
from market.services.daily_review_service import DailyReviewCoordinator, CHINA_TZ
from ea_auth import EAIdentity
from paper_trading import PaperTradingService
from server import TradingServer
from mysql_repositories import TradingAccountRepository


@dataclass(frozen=True)
class EngineKey:
    user_id: int
    account_id: int


@dataclass
class EngineRuntime:
    engine: TradingServer
    last_active_at: float
    next_pending_cleanup_at: float
    next_signal_cleanup_at: float
    next_llm_analysis_at: float


class TradingEngineManager:
    """线程安全地创建和复用账户级 TradingServer。"""

    def __init__(
        self,
        engine_factory: Optional[Callable[[int, int], TradingServer]] = None,
        idle_timeout_seconds: Optional[float] = None,
        scheduler_interval_seconds: float = 1.0,
    ):
        self._engine_factory = engine_factory or self._create_engine
        self._engines: Dict[EngineKey, EngineRuntime] = {}
        self._lock = threading.RLock()
        self._event_loop = None
        self._account_repo = TradingAccountRepository()
        self.paper_trading = PaperTradingService()
        self.data_retention = DataRetentionService()
        self.adaptive_signal_tuner = AdaptiveSignalTuner(
            refresh_callback=self.refresh_user_strategies,
        )
        self.daily_reviews = DailyReviewCoordinator(
            engine_provider=self.get_market_engine,
        )
        self._idle_timeout_seconds = float(
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else os.getenv("AI_TRADER_ENGINE_IDLE_SECONDS", "1800")
        )
        self._scheduler = SharedTaskScheduler(
            self._scheduler_tick,
            interval_seconds=scheduler_interval_seconds,
            max_workers=int(os.getenv("AI_TRADER_TASK_WORKERS", "4")),
        )
        self._scheduler_started = False
        self._paper_maintenance_enabled = os.getenv(
            "AI_TRADER_ENABLE_PAPER_MAINTENANCE", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._data_retention_enabled = os.getenv(
            "AI_TRADER_ENABLE_DATA_RETENTION", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self._next_paper_maintenance_at = time.monotonic() + 10
        self._next_data_retention_at = time.monotonic() + 60
        self._next_adaptive_tuning_at = time.monotonic() + 120
        self._last_data_retention_date = ""
        self._last_daily_review_date = ""

    def _create_engine(self, user_id: int, account_id: int) -> TradingServer:
        market_source = (
            None if not account_id else self.get_engine(user_id, 0)
        )
        return TradingServer(
            user_id=user_id, account_id=account_id or None,
            market_data_source=market_source,
        )

    def get_engine(self, user_id: int, account_id: int) -> TradingServer:
        key = EngineKey(user_id=int(user_id), account_id=int(account_id))
        now = time.monotonic()
        with self._lock:
            runtime = self._engines.get(key)
            if runtime is None:
                engine = self._engine_factory(key.user_id, key.account_id)
                analyzer = getattr(engine, "llm_analyzer", None)
                if analyzer is not None:
                    analyzer.set_task_scheduler(self._scheduler)
                if self._event_loop is not None:
                    engine.set_event_loop(self._event_loop)
                runtime = EngineRuntime(
                    engine=engine,
                    last_active_at=now,
                    next_pending_cleanup_at=now + 10,
                    next_signal_cleanup_at=now + 30,
                    next_llm_analysis_at=now + 5,
                )
                self._engines[key] = runtime
                self._ensure_scheduler_started()
            else:
                runtime.last_active_at = now
            return runtime.engine

    def list_background_tasks(self, limit: int = 50):
        """统一暴露后台任务状态，供运维页轮询，不触发任务执行。"""
        return self._scheduler.list_tasks(limit)

    def get_background_task(self, task_id: str):
        return self._scheduler.get_task(task_id)

    def get_engine_for_ea(self, identity: EAIdentity) -> TradingServer:
        return self.get_engine(identity.user_id, identity.account_id)

    def get_market_engine(self, user_id: int) -> TradingServer:
        """Return the user's account-independent market-data engine."""
        return self.get_engine(user_id, 0)

    def process_user_market_tick(
        self, user_id: int, account_ids, symbol: str, price: float,
    ) -> Dict[int, Dict]:
        """Drive every eligible live account once from one authoritative Tick."""
        results = {}
        for account_id in dict.fromkeys(int(value) for value in account_ids):
            engine = self.get_engine(int(user_id), account_id)
            results[account_id] = engine.process_price(symbol, float(price))
        return results

    def get_engine_for_user(self, user_id: int) -> TradingServer:
        account = self._account_repo.get_primary_mt5(user_id)
        if account is None:
            default = self._account_repo.get_default(user_id)
            account = default if default and default.status == "active" else None
        account_id = account.account_id if account else 0
        return self.get_engine(user_id, account_id)

    def bind_account(self, user_id: int, account_id: int) -> TradingServer:
        """Bind an execution account without consuming the shared market engine."""
        account_key = EngineKey(user_id=int(user_id), account_id=int(account_id))
        with self._lock:
            account_runtime = self._engines.get(account_key)
            if account_runtime is not None:
                account_runtime.last_active_at = time.monotonic()
                return account_runtime.engine
        # account_id=0 is a durable user-level market runtime. The account
        # engine created below references its K-lines, Pivot, structure and AI
        # stores while retaining isolated positions, risk and order state.
        return self.get_engine(account_key.user_id, account_key.account_id)

    def set_event_loop(self, loop) -> None:
        with self._lock:
            self._event_loop = loop
            runtimes = list(self._engines.values())
        for runtime in runtimes:
            runtime.engine.set_event_loop(loop)
        self._ensure_scheduler_started()

    def get_status(self) -> Dict:
        with self._lock:
            items: Tuple[Tuple[EngineKey, EngineRuntime], ...] = tuple(
                self._engines.items()
            )
        now = time.monotonic()
        return {
            "engine_count": len(items),
            "idle_timeout_seconds": self._idle_timeout_seconds,
            "engines": [
                {
                    "user_id": key.user_id,
                    "account_id": key.account_id,
                    "idle_seconds": round(now - runtime.last_active_at, 1),
                    "status": runtime.engine.get_status(),
                }
                for key, runtime in items
            ],
        }

    def refresh_user_strategies(self, user_id: int) -> None:
        """策略变更后同步刷新该用户所有已运行账户引擎。"""
        with self._lock:
            engines = [
                runtime.engine for key, runtime in self._engines.items()
                if key.user_id == int(user_id)
            ]
        for engine in engines:
            store = getattr(getattr(engine, "strategy_service", None), "strategy_store", None)
            reload_strategy = getattr(store, "reload_from_storage", None)
            if reload_strategy:
                reload_strategy()

    def suspend_user_live_orders(self, user_id: int) -> None:
        """撤销实盘授权后清理内存中尚未发送的开仓订单。"""
        with self._lock:
            engines = [
                runtime.engine for key, runtime in self._engines.items()
                if key.user_id == int(user_id)
            ]
        for engine in engines:
            engine.pending_order_service.clear_all()
            engine.trading_instruction_service.clear_all()

    def close_all(self) -> None:
        with self._lock:
            runtimes = list(self._engines.values())
            self._engines.clear()
        for runtime in runtimes:
            close = getattr(runtime.engine, "close", None)
            if close is not None:
                close()
        self._scheduler.shutdown()

    def run_maintenance_once(self, now: Optional[float] = None) -> None:
        """测试和运维场景下立即执行一次调度检查。"""
        self._scheduler_tick(now if now is not None else time.monotonic(), self._scheduler)

    def _ensure_scheduler_started(self) -> None:
        if not self._scheduler_started:
            self._scheduler.start()
            self._scheduler_started = True

    def _scheduler_tick(
        self,
        now: float,
        scheduler: SharedTaskScheduler,
    ) -> None:
        if self._paper_maintenance_enabled and now >= self._next_paper_maintenance_at:
            self._next_paper_maintenance_at = now + 10
            scheduler.submit(
                ("paper", "maintenance"), self.paper_trading.run_maintenance
            )
        current_wall = datetime.now(CHINA_TZ)
        retention_due = (
            self._data_retention_enabled
            and current_wall.hour == 2
            and current_wall.minute == 0
            and self._last_data_retention_date != current_wall.date().isoformat()
        )
        if retention_due:
            self._last_data_retention_date = current_wall.date().isoformat()
            scheduler.submit(
                ("system", "data_retention"),
                self.data_retention.run_maintenance,
            )
        review_date = current_wall.date().isoformat()
        review_due = (
            current_wall.hour == 6
            and self._last_daily_review_date != review_date
        )
        if review_due:
            self._last_daily_review_date = review_date
            scheduler.submit(
                ("system", "daily_reviews"),
                lambda: self.daily_reviews.run_once(review_date),
            )
        if now >= self._next_adaptive_tuning_at:
            # A completed sample is fingerprinted, so running every five
            # minutes cannot repeatedly tune from the same closed trades.
            self._next_adaptive_tuning_at = now + 300
            scheduler.submit(
                ("system", "adaptive_signal_tuning"),
                self.adaptive_signal_tuner.run_once,
            )

        with self._lock:
            items = list(self._engines.items())

        for key, runtime in items:
            engine = runtime.engine
            if self._evict_if_idle(key, runtime, now, scheduler):
                continue

            if now >= runtime.next_pending_cleanup_at:
                runtime.next_pending_cleanup_at = now + 10
                callback = getattr(engine, "cleanup_pending_orders", None)
                if callback:
                    scheduler.submit((key, "pending_cleanup"), callback)

            if now >= runtime.next_signal_cleanup_at:
                runtime.next_signal_cleanup_at = now + 30
                callback = getattr(engine, "cleanup_signals", None)
                if callback:
                    scheduler.submit((key, "signal_cleanup"), callback)

            # AI market analysis is user-scoped and runs only once on the
            # account-independent market engine. Execution engines consume
            # the same stored result and must never schedule duplicate calls.
            if key.account_id == 0 and now >= runtime.next_llm_analysis_at:
                kline_service = getattr(engine, "kline_service", None)
                get_symbols = getattr(kline_service, "get_symbols", None)
                if get_symbols is not None and not get_symbols():
                    # EA may need several seconds to upload its initial Kline batch.
                    # Retry readiness soon without recording a failed LLM analysis.
                    runtime.next_llm_analysis_at = now + 10
                    continue

                # 缩短检查粒度，减少 2/3/5 分钟信号源的调度抖动；
                # 具体调用频率仍由信号源自身的间隔配置控制。
                runtime.next_llm_analysis_at = now + 15
                callback = getattr(engine, "run_scheduled_llm_analysis", None)
                if callback:
                    scheduler.submit((key, "llm_analysis"), callback)

    def _evict_if_idle(
        self,
        key: EngineKey,
        runtime: EngineRuntime,
        now: float,
        scheduler: SharedTaskScheduler,
    ) -> bool:
        # The user-level market engine owns shared K-lines, Pivot, structure
        # plans and AI analysis scheduling. Account engines retain references
        # to these objects, so evicting account_id=0 would silently stop AI
        # refreshes while deployed strategies are still running.
        if key.account_id == 0:
            return False
        if self._idle_timeout_seconds <= 0:
            return False
        if now - runtime.last_active_at < self._idle_timeout_seconds:
            return False
        if scheduler.is_busy(key):
            return False
        can_evict = getattr(runtime.engine, "can_evict", None)
        if can_evict is not None and not can_evict():
            return False

        with self._lock:
            if self._engines.get(key) is not runtime:
                return False
            del self._engines[key]
        close = getattr(runtime.engine, "close", None)
        if close:
            close()
        print(
            f"[TradingEngineManager] 已回收空闲引擎 "
            f"user_id={key.user_id}, account_id={key.account_id}"
        )
        return True
