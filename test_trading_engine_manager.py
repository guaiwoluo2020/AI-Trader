#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多账户交易引擎管理测试。"""

import os
import tempfile
import threading
import time
import unittest
from datetime import datetime

from auth import AuthManager, reset_auth_manager
from background_scheduler import SharedTaskScheduler
from market.models import (
    PendingOrder,
    TradingDecision,
    TradingInstruction as StoredTradingInstruction,
)
from models import TradeInstruction
from sqlite_storage import (
    RuntimeStateRepository,
    TradingAccountRepository,
    UserRepository,
    reset_storage,
)
from trading_engine_manager import TradingEngineManager


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

    def test_instruction_without_tp_delegates_default_to_ea(self):
        instruction = StoredTradingInstruction(
            symbol="GOLD_",
            action="b",
            price=4095.0,
            mount=0.01,
        )

        self.assertEqual(instruction.to_dict()["tp"], 0.0)

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
        engine1.add_trade_instruction([
            TradeInstruction(
                symbol="GOLD#",
                action="b",
                mount=0.01,
                price=3300.0,
                sl=3290.0,
                tp=3320.0,
            )
        ])
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
        engine.add_trade_instruction([
            TradeInstruction(
                symbol="GOLD#",
                action="b",
                mount=0.01,
                price=3300.0,
                sl=3290.0,
                tp=3320.0,
            )
        ])
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

    def test_strategy_decision_broadcast_contains_real_pending_order_id(self):
        account, _ = TradingAccountRepository().create_or_rotate_default(
            self.admin.user_id
        )
        manager = TradingEngineManager()
        engine = manager.get_engine(self.admin.user_id, account.account_id)
        decision = TradingDecision(
            symbol="GOLD#",
            strategy_id="strategy-gold",
            action="buy",
            entry_price=3300.0,
            sl=3290.0,
            tp=3320.0,
            volume=0.01,
            decision_reason="test signal",
        )
        broadcasts = []
        engine._signal_service.generate_signals = lambda symbol, price: [object()]
        engine.strategy_service.make_decision = lambda symbol, price: decision
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
