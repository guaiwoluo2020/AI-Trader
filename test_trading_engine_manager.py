#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多账户交易引擎管理测试。"""

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta

from auth import AuthManager, AuthUser, reset_auth_manager
from fastapi import HTTPException
from background_scheduler import SharedTaskScheduler
from market.models import (
    PendingOrder,
    TradingDecision,
    TradingInstruction as StoredTradingInstruction,
    TradingStrategy,
)
from sqlite_storage import (
    RuntimeStateRepository,
    TradingAccountRepository,
    UserRepository,
    reset_storage,
)
from trading_engine_manager import TradingEngineManager
from web_account_context import resolve_web_engine


class _FakeEngine:
    def __init__(self, user_id, account_id):
        self.user_id = user_id
        self.account_id = account_id
        self.values = []
        self.event_loop = None
        self.closed = False

    def set_event_loop(self, loop):
        self.event_loop = loop

    def get_status(self):
        return {"values": list(self.values)}

    def close(self):
        self.closed = True


class _KlineServiceStub:
    def __init__(self):
        self.symbols = []

    def get_symbols(self):
        return list(self.symbols)


class _ScheduledEngine(_FakeEngine):
    def __init__(self, user_id, account_id):
        super().__init__(user_id, account_id)
        self.kline_service = _KlineServiceStub()
        self.llm_runs = 0
        self.llm_analyzer = type("Analyzer", (), {"ANALYZE_INTERVAL": 300})()

    def run_scheduled_llm_analysis(self):
        self.llm_runs += 1


class TradingEngineManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["AI_TRADER_AUTH_FILE"] = os.path.join(
            self.temp_dir.name,
            "auth-users.json",
        )
        os.environ["AI_TRADER_DB_FILE"] = os.path.join(
            self.temp_dir.name,
            "ai-trader.db",
        )
        reset_storage()
        reset_auth_manager()
        self.auth_manager = AuthManager()
        self.admin = UserRepository().get_by_username("admin")

    def tearDown(self):
        reset_auth_manager()
        reset_storage()
        os.environ.pop("AI_TRADER_AUTH_FILE", None)
        os.environ.pop("AI_TRADER_DB_FILE", None)
        self.temp_dir.cleanup()

    @staticmethod
    def _stub_strategy_decision(engine, decision):
        strategy = TradingStrategy(
            symbol=decision.symbol,
            strategy_id=decision.strategy_id,
            strategy_name=decision.strategy_name,
        )
        engine._active_strategy_ids = lambda mode="live": [strategy.strategy_id]
        engine.strategy_service.get_strategies = lambda symbol: [strategy]
        engine._signal_service.generate_signals_for_strategy = (
            lambda symbol, price, current_strategy: [object()]
        )
        engine.strategy_service.make_decision = (
            lambda symbol, price, force_signals=None, strategy=None: decision
        )

    def test_instruction_without_tp_delegates_default_to_ea(self):
        instruction = StoredTradingInstruction(
            symbol="GOLD_",
            action="b",
            price=4095.0,
            mount=0.01,
        )

        payload = instruction.to_dict()
        self.assertEqual(payload["tp"], 0.0)
        self.assertEqual(payload["instruction_id"], instruction.instruction_id)
        self.assertIn("order_id", payload)

    def test_web_account_context_rejects_another_users_account(self):
        salt, password_hash = self.auth_manager._build_password_credentials("user2")
        user2 = UserRepository().create_user("user2", password_hash, salt)
        account_repo = TradingAccountRepository()
        own_account, _ = account_repo.create_or_rotate_default(self.admin.user_id)
        other_account, _ = account_repo.create_or_rotate_default(user2.user_id)
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(user_id, account_id)
        )
        auth_user = AuthUser(
            user_id=self.admin.user_id,
            username=self.admin.username,
            role=self.admin.role,
        )

        resolved, engine = resolve_web_engine(
            manager, auth_user, own_account.account_id
        )
        self.assertEqual(resolved.account_id, own_account.account_id)
        self.assertEqual(engine.account_id, own_account.account_id)
        with self.assertRaises(HTTPException) as raised:
            resolve_web_engine(manager, auth_user, other_account.account_id)
        self.assertEqual(raised.exception.status_code, 404)
        manager.close_all()

    def test_accounts_receive_distinct_engine_instances(self):
        salt, password_hash = self.auth_manager._build_password_credentials("user2")
        user2 = UserRepository().create_user("user2", password_hash, salt)
        account_repo = TradingAccountRepository()
        account1, _ = account_repo.create_or_rotate_default(self.admin.user_id)
        account2, _ = account_repo.create_or_rotate_default(user2.user_id)
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(
                user_id,
                account_id,
            )
        )

        engine1 = manager.get_engine(self.admin.user_id, account1.account_id)
        engine2 = manager.get_engine(user2.user_id, account2.account_id)
        engine1.values.append("admin")

        self.assertIsNot(engine1, engine2)
        self.assertEqual(engine1.values, ["admin"])
        self.assertEqual(engine2.values, [])

    def test_same_account_reuses_engine(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(
                user_id,
                account_id,
            )
        )

        first = manager.get_engine(self.admin.user_id, account.account_id)
        second = manager.get_engine(self.admin.user_id, account.account_id)

        self.assertIs(first, second)

    def test_llm_analysis_waits_for_initial_kline_data(self):
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _ScheduledEngine(
                user_id,
                account_id,
            )
        )
        engine = manager.get_engine(self.admin.user_id, 0)
        runtime = next(iter(manager._engines.values()))
        initial_due_at = runtime.next_llm_analysis_at

        manager.run_maintenance_once(initial_due_at)

        self.assertEqual(engine.llm_runs, 0)
        self.assertEqual(runtime.next_llm_analysis_at, initial_due_at + 10)

        engine.kline_service.symbols.append("GOLD_")
        manager.run_maintenance_once(runtime.next_llm_analysis_at)
        deadline = time.time() + 1
        while engine.llm_runs == 0 and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(engine.llm_runs, 1)
        manager.close_all()

    def test_binding_migrates_temporary_user_engine(self):
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(
                user_id,
                account_id,
            )
        )
        temporary = manager.get_engine_for_user(self.admin.user_id)
        temporary.values.append("before-binding")
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )

        bound = manager.bind_account(self.admin.user_id, account.account_id)

        self.assertIs(bound, temporary)
        self.assertEqual(bound.account_id, account.account_id)
        self.assertEqual(
            manager.get_engine_for_user(self.admin.user_id).values,
            ["before-binding"],
        )

    def test_real_engines_isolate_runtime_data(self):
        salt, password_hash = self.auth_manager._build_password_credentials("user2")
        user2 = UserRepository().create_user("user2", password_hash, salt)
        account_repo = TradingAccountRepository()
        account1, _ = account_repo.create_or_rotate_default(self.admin.user_id)
        account2, _ = account_repo.create_or_rotate_default(user2.user_id)
        manager = TradingEngineManager()
        engine1 = manager.get_engine(self.admin.user_id, account1.account_id)
        engine2 = manager.get_engine(user2.user_id, account2.account_id)

        engine1.save_statistics({
            "symbol": "GOLD#",
            "timestamp": "2026-07-30 12:00:00",
            "tickCount": 1,
            "bidPrice": 3300.0,
            "askPrice": 3300.2,
            "balance": 10000.0,
            "equity": 10001.0,
            "marginLevel": 1000.0,
            "positions": [],
            "trades": [],
        })
        engine1.position_service.update_positions(
            "GOLD#",
            [{
                "ticket": 1001,
                "symbol": "GOLD#",
                "volume": 0.01,
                "priceOpen": 3300.0,
                "type": "BUY",
                "profit": 1.0,
            }],
        )
        engine1.trading_instruction_service.create_instruction(
            symbol="GOLD#",
            action="b",
            mount=0.01,
            price=3300.0,
            sl=3290.0,
            tp=3320.0,
            source="test",
        )
        engine1.system_log.add_log(
            "ea_statistics",
            message="account one",
        )

        self.assertEqual(len(engine1.get_latest_statistics(10)), 1)
        self.assertEqual(
            engine1.get_latest_statistics(10, "GOLD#")[0]["symbol"],
            "GOLD#",
        )
        self.assertEqual(engine1.get_latest_statistics(10, "OILCASH#"), [])
        self.assertEqual(engine2.get_latest_statistics(10), [])
        self.assertEqual(len(engine1.position_service.get_positions()), 1)
        self.assertEqual(engine2.position_service.get_positions(), [])
        self.assertEqual(len(engine1.get_all_pending_trades()["GOLD#"]), 1)
        self.assertEqual(engine2.get_all_pending_trades(), {})
        self.assertEqual(len(engine1.system_log.get_logs()), 1)
        self.assertEqual(engine2.system_log.get_logs(), [])
        self.assertEqual(
            engine1.system_log.get_logs()[0]["account_id"],
            account1.account_id,
        )

    def test_dashboard_overview_is_scoped_to_selected_account(self):
        salt, password_hash = self.auth_manager._build_password_credentials("user2")
        user2 = UserRepository().create_user("user2", password_hash, salt)
        account_repo = TradingAccountRepository()
        account1, _ = account_repo.create_or_rotate_default(self.admin.user_id)
        account2, _ = account_repo.create_or_rotate_default(user2.user_id)
        account_repo.update_financial_snapshot(
            account1.account_id,
            balance=10000,
            equity=10008,
            free_margin=9900,
            margin=108,
        )
        manager = TradingEngineManager()
        engine1 = manager.get_engine(self.admin.user_id, account1.account_id)
        engine2 = manager.get_engine(user2.user_id, account2.account_id)
        engine1.position_service.update_positions("GOLD#", [{
            "ticket": 3001,
            "symbol": "GOLD#",
            "volume": 0.01,
            "priceOpen": 3300.0,
            "type": "BUY",
            "profit": 8.0,
        }])
        engine2.position_service.update_positions("BTCUSD", [{
            "ticket": 3002,
            "symbol": "BTCUSD",
            "volume": 0.01,
            "priceOpen": 90000.0,
            "type": "SELL",
            "profit": -5.0,
        }])

        overview = engine1.get_dashboard_overview(
            account_repo.get_by_id(self.admin.user_id, account1.account_id)
        )

        self.assertEqual(overview["account"]["account_id"], account1.account_id)
        self.assertEqual(overview["financial"]["equity"], 10008)
        self.assertEqual(overview["positions"]["count"], 1)
        self.assertEqual(overview["positions"]["items"][0]["symbol"], "GOLD#")
        self.assertNotIn("BTCUSD", str(overview))
        manager.close_all()

    def test_close_all_stops_and_removes_engines(self):
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(
                user_id,
                account_id,
            )
        )
        engine = manager.get_engine(self.admin.user_id, 0)

        manager.close_all()

        self.assertTrue(engine.closed)
        self.assertEqual(manager.get_status()["engine_count"], 0)

    def test_idle_engine_is_evicted_and_recreated_on_demand(self):
        manager = TradingEngineManager(
            engine_factory=lambda user_id, account_id: _FakeEngine(
                user_id,
                account_id,
            ),
            idle_timeout_seconds=1,
        )
        first = manager.get_engine(self.admin.user_id, 0)

        manager.run_maintenance_once(time.monotonic() + 2)

        self.assertTrue(first.closed)
        self.assertEqual(manager.get_status()["engine_count"], 0)
        second = manager.get_engine(self.admin.user_id, 0)
        self.assertIsNot(first, second)
        manager.close_all()

    def test_runtime_data_survives_engine_restart_without_redelivery(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)

        pending = PendingOrder(
            symbol="GOLD#",
            action="b",
            price=3300.0,
            mount=0.01,
            sl=3290.0,
            tp=3320.0,
        )
        engine.pending_order_store.add_order(pending)
        engine.position_service.update_positions(
            "GOLD#",
            [{
                "ticket": 1001,
                "symbol": "GOLD#",
                "volume": 0.01,
                "priceOpen": 3300.0,
                "type": "BUY",
                "profit": 2.0,
            }],
        )
        engine.save_statistics({
            "symbol": "GOLD#",
            "timestamp": datetime.now().isoformat(),
            "bidPrice": 3300.0,
            "askPrice": 3300.2,
            "spread": 0.2,
            "spreadPoints": 20,
            "balance": 10000.0,
            "equity": 10002.0,
            "marginLevel": 1000.0,
        })
        engine.trade_history_service.process_deals([{
            "ticket": 2001,
            "order": 2000,
            "symbol": "GOLD#",
            "type": 0,
            "entry": 0,
            "volume": 0.01,
            "price": 3300.0,
            "profit": 0.0,
            "swap": 0.0,
            "commission": 0.0,
            "time": datetime.now().isoformat(),
            "comment": "mt5TerminalEA",
        }])
        engine.trading_instruction_service.create_instruction(
            symbol="GOLD#",
            action="b",
            mount=0.01,
            price=3300.0,
            sl=3290.0,
            tp=3320.0,
            source="test",
        )
        sent = engine.trading_instruction_service.fetch_instructions_for_ea(
            "GOLD#",
            3300.0,
        )
        engine.add_close_position_instruction("GOLD#", 1001)
        manager.close_all()

        restarted = TradingEngineManager().get_engine(
            self.admin.user_id,
            account.account_id,
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["symbol"], "GOLD#")
        self.assertEqual(
            restarted.trading_instruction_service.fetch_instructions_for_ea(
                "GOLD#",
                3300.0,
            ),
            [],
        )
        self.assertEqual(
            restarted.pending_order_service.get_pending_count("GOLD#"),
            1,
        )
        self.assertEqual(len(restarted.position_service.get_positions("GOLD#")), 1)
        self.assertEqual(restarted.get_latest_statistics(10), [])
        self.assertEqual(
            restarted.statistics_service.get_account_info()["equity"],
            10002.0,
        )
        self.assertEqual(len(restarted.trade_history_service.get_deals("GOLD#")), 1)
        self.assertEqual(restarted.get_close_position_instructions("GOLD#"), [1001])

        rows = RuntimeStateRepository(
            self.admin.user_id,
            account.account_id,
        ).list_entities("trading_instruction")
        self.assertEqual(rows[0]["status"], "sent")
        restarted.close()

    def test_strategy_decisions_persist_and_support_audit_filters(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        now = datetime.now()
        confirmed = TradingDecision(
            decision_id="decision-confirmed",
            order_id="order-confirmed",
            symbol="GOLD#",
            strategy_id="strategy-gold",
            strategy_name="Gold strategy",
            action="buy",
            status="pending",
            created_at=now - timedelta(minutes=2),
        )
        rejected = TradingDecision(
            decision_id="decision-rejected",
            symbol="BTCUSD",
            strategy_id="strategy-btc",
            strategy_name="BTC strategy",
            action="sell",
            status="rejected",
            created_at=now - timedelta(minutes=1),
        )
        engine._record_decision(confirmed)
        engine._record_decision(rejected)
        self.assertTrue(
            engine.update_decision_status("order-confirmed", "confirmed")
        )

        manager.close_all()
        restarted_manager = TradingEngineManager()
        restarted = restarted_manager.get_engine(
            self.admin.user_id, account.account_id
        )

        self.assertEqual(
            [item["decision_id"] for item in restarted.get_decision_history()],
            ["decision-rejected", "decision-confirmed"],
        )
        self.assertEqual(
            restarted.get_decision_history(status="confirmed")[0]["status"],
            "confirmed",
        )
        self.assertEqual(
            restarted.get_decision_history(strategy_id="strategy-btc")[0]["symbol"],
            "BTCUSD",
        )
        self.assertEqual(
            restarted.get_decision_history(
                date_from=(now - timedelta(seconds=90)).isoformat()
            )[0]["decision_id"],
            "decision-rejected",
        )
        restarted_manager.close_all()

    def test_strategy_decision_broadcast_contains_real_pending_order_id(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        decision = TradingDecision(
            symbol="GOLD#",
            strategy_id="strategy-gold",
            strategy_name="Gold Strategy",
            action="buy",
            entry_price=3300.0,
            sl=3290.0,
            tp=3320.0,
            volume=0.01,
            decision_reason="test signal",
        )
        broadcasts = []
        self._stub_strategy_decision(engine, decision)
        engine._broadcast_decision = lambda current: broadcasts.append(
            current.to_dict()
        )

        result = engine.process_price("GOLD#", 3300.0)

        self.assertIsNotNone(result["pending_order"])
        self.assertEqual(
            broadcasts[0]["order_id"],
            result["pending_order"]["order_id"],
        )
        self.assertNotEqual(broadcasts[0]["order_id"], decision.decision_id)
        manager.close_all()

    def test_executable_strategy_creates_instruction_without_confirmation(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        decision = TradingDecision(
            symbol="GOLD#",
            strategy_id="auto-strategy",
            strategy_name="Auto Strategy",
            action="buy",
            entry_price=3300.0,
            sl=3290.0,
            tp=3320.0,
            volume=0.01,
            decision_reason="auto test signal",
        )
        self._stub_strategy_decision(engine, decision)

        result = engine.process_price("GOLD#", 3300.0)
        queued_instructions = (
            engine.trading_instruction_service.get_all_instructions()
        )
        instructions = engine.trading_instruction_service.fetch_instructions_for_ea(
            "GOLD#",
            3300.0,
        )

        self.assertTrue(result["pending_order"]["confirmed"])
        self.assertTrue(result["decision"]["auto_executed"])
        self.assertEqual(engine.pending_order_service.get_pending_count("GOLD#"), 0)
        self.assertEqual(queued_instructions[0].order_id, decision.order_id)
        self.assertEqual(len(instructions), 1)
        self.assertEqual(instructions[0]["action"], "b")
        manager.close_all()

    def test_account_controls_block_strategy_execution(self):
        repository = TradingAccountRepository()
        account, _ = repository.create_or_rotate_default(self.admin.user_id)
        repository.update_controls(
            self.admin.user_id,
            account.account_id,
            auto_trading_enabled=False,
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        decision = TradingDecision(
            symbol="GOLD#",
            strategy_id="auto-strategy",
            strategy_name="Auto Strategy",
            action="buy",
            entry_price=3300.0,
            sl=3290.0,
            tp=3320.0,
            volume=0.01,
            decision_reason="account control",
        )
        self._stub_strategy_decision(engine, decision)

        result = engine.process_price("GOLD#", 3300.0)

        self.assertIsNone(result["pending_order"])
        self.assertEqual(engine.pending_order_service.get_pending_count("GOLD#"), 0)
        self.assertEqual(
            engine.trading_instruction_service.get_all_instructions(), []
        )

        repository.update_controls(
            self.admin.user_id,
            account.account_id,
            trading_enabled=False,
        )
        paused = engine.process_price("GOLD#", 3300.0)
        self.assertEqual(paused["decisions"], [])
        manager.close_all()

    def test_binding_migrates_persisted_temporary_runtime_data(self):
        manager = TradingEngineManager()
        temporary = manager.get_engine_for_user(self.admin.user_id)
        temporary.add_close_position_instruction("GOLD#", 3001)
        temporary.position_service.update_positions(
            "GOLD#",
            [{
                "ticket": 3001,
                "symbol": "GOLD#",
                "volume": 0.01,
                "priceOpen": 3300.0,
                "type": "BUY",
                "profit": 0.0,
            }],
        )
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )

        manager.bind_account(self.admin.user_id, account.account_id)
        manager.close_all()
        restarted = TradingEngineManager().get_engine(
            self.admin.user_id,
            account.account_id,
        )

        self.assertEqual(len(restarted.position_service.get_positions()), 1)
        self.assertEqual(restarted.get_close_position_instructions("GOLD#"), [3001])
        restarted.close()

    def test_risk_state_and_real_margin_survive_restart(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        engine.save_statistics({
            "symbol": "GOLD#",
            "timestamp": datetime.now().isoformat(),
            "bidPrice": 3300.0,
            "askPrice": 3300.2,
            "balance": 10000.0,
            "equity": 9800.0,
            "marginLevel": 500.0,
            "freeMargin": 7500.0,
            "margin": 2300.0,
        })
        order = PendingOrder(
            symbol="GOLD#",
            action="b",
            price=3300.0,
            mount=0.1,
            sl=3290.0,
            tp=3320.0,
        )
        engine.pending_order_store.add_order(order)
        engine.pending_order_service.confirm_order(order.order_id)
        engine.trade_history_service.process_deals([{
            "ticket": 9001,
            "order": 9000,
            "symbol": "GOLD#",
            "type": 1,
            "entry": 1,
            "volume": 0.1,
            "price": 3290.0,
            "profit": -600.0,
            "swap": 0.0,
            "commission": 0.0,
            "time": datetime.now().isoformat(),
            "comment": "mt5TerminalEA",
        }])
        before = engine.strategy_service.risk_manager.get_status()
        manager.close_all()

        restarted_manager = TradingEngineManager()
        restarted = restarted_manager.get_engine(
            self.admin.user_id,
            account.account_id,
        )
        after = restarted.strategy_service.risk_manager.get_status()
        account_info = restarted.statistics_service.get_account_info()

        self.assertEqual(before["daily_order_count"], 1)
        self.assertGreater(before["daily_risk_used"], 0)
        self.assertTrue(before["circuit_breaker"])
        self.assertEqual(after["daily_order_count"], 1)
        self.assertEqual(after["daily_risk_used"], before["daily_risk_used"])
        self.assertTrue(after["circuit_breaker"])
        self.assertEqual(account_info["free_margin"], 7500.0)
        self.assertEqual(account_info["margin"], 2300.0)
        restarted_manager.close_all()

    def test_engine_services_do_not_start_per_account_maintenance_threads(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)

        self.assertFalse(hasattr(engine.llm_analyzer, "_thread"))
        self.assertFalse(hasattr(engine.pending_order_service, "_thread"))
        self.assertFalse(hasattr(engine._signal_service, "_thread"))
        manager.close_all()

    def test_ai_analysis_survives_restart_and_explains_non_signal_state(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        strategy = TradingStrategy(
            symbol="GOLD#",
            strategy_id="ai-market-strategy",
            strategy_name="AI 行情测试",
            enabled=True,
            lifecycle_status="production",
            signal_sources=[{
                "signal_source_id": "ai-market-m5",
                "source": "ai_entry",
                "period": "M5",
                "enabled": True,
                "weight": 30,
                "params": {
                    "analysis_interval_minutes": 5,
                    "kline_count": 100,
                    "min_confidence": 70,
                    "entry_threshold": 0.001,
                },
            }],
        )
        engine._strategy_store.set_strategy(strategy)
        engine.llm_store.save_analysis_dict("GOLD#", {
            "trend_analysis": {
                "M5": {
                    "trend": "震荡上升",
                    "confidence": 55,
                    "reason": "上涨结构尚未确认",
                },
            },
            "overall_trend": {
                "direction": "上涨",
                "summary": "偏多观察",
            },
            "trade_suggestions": [],
        })

        card = engine.get_ai_market_cards()[0]
        self.assertEqual(card["direction"], "up")
        self.assertEqual(card["status"], "observing")
        self.assertIn("低于策略要求", card["status_reason"])
        analyzed_at = card["analyzed_at"]
        manager.close_all()

        restarted_manager = TradingEngineManager()
        restarted = restarted_manager.get_engine(
            self.admin.user_id, account.account_id
        )
        restored = restarted.get_ai_market_cards()[0]
        self.assertEqual(restored["analyzed_at"], analyzed_at)
        self.assertEqual(restored["status"], "observing")
        restarted_manager.close_all()

    def test_shared_scheduler_deduplicates_same_account_task(self):
        started = threading.Event()
        release = threading.Event()
        scheduler = SharedTaskScheduler(
            lambda now, current: None,
            interval_seconds=60,
            max_workers=1,
        )
        scheduler.start()

        def blocking_task():
            started.set()
            release.wait(2)

        self.assertTrue(scheduler.submit(("account-1", "llm"), blocking_task))
        self.assertTrue(started.wait(1))
        self.assertFalse(scheduler.submit(("account-1", "llm"), blocking_task))
        self.assertTrue(scheduler.is_busy("account-1"))
        release.set()
        deadline = time.time() + 1
        while scheduler.is_busy("account-1") and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(scheduler.is_busy("account-1"))
        scheduler.shutdown()


if __name__ == "__main__":
    unittest.main()
