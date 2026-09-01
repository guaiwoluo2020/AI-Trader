#!/usr/bin/env python3
"""统一交易账户与 MT5 连接迁移测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from market.models import PositionManagementPolicy
from mysql_repositories import (
    PositionManagementPolicyRepository,
    EAActivationRepository,
    MySQLStorage,
    StrategyDeploymentRepository,
    TradeExecutionRepository,
    TradingAccountRepository,
    UserRepository,
)
from paper_trading import PaperTradingService
from routes_accounts import _account_payload


class TradingAccountRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = MySQLStorage(str(Path(self.temp_dir.name) / "accounts.db"))
        self.storage.initialize()
        self.user = UserRepository(self.storage).create_user(
            "account-user", "hash", "salt",
            membership_level="diamond", live_trading_enabled=True,
        )
        self.repository = TradingAccountRepository(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_mt5_account_uses_separate_connection_and_financial_snapshot(self):
        account, token = self.repository.create_or_rotate_default(
            self.user.user_id
        )

        updated = self.repository.update_financial_snapshot(
            account.account_id,
            balance=12000,
            equity=12150,
            free_margin=11800,
            margin=350,
        )
        refreshed = self.repository.authenticate(self.user.user_id, token)
        connection = self.storage.fetchone(
            "SELECT * FROM mt5_account_connections WHERE account_id = ?",
            (account.account_id,),
        )

        self.assertTrue(updated)
        self.assertIsNotNone(connection)
        self.assertEqual(refreshed.account_type, "mt5")
        self.assertEqual(refreshed.balance, 12000)
        self.assertEqual(refreshed.equity, 12150)
        self.assertEqual(refreshed.initial_balance, 12000)
        self.assertIsNotNone(refreshed.financial_updated_at)

    def test_paper_account_is_isolated_and_has_no_mt5_connection(self):
        activations = EAActivationRepository(self.storage)
        code, _ = activations.create(self.user.user_id)
        mt5, _ = activations.consume(
            code, mt5_login="90001", mt5_server="Broker-Demo"
        )
        paper = self.repository.create_paper_account(
            self.user.user_id, "Gold Paper", 50000, "usd"
        )

        accounts = self.repository.list_for_user(self.user.user_id)
        connection = self.storage.fetchone(
            "SELECT 1 FROM mt5_account_connections WHERE account_id = ?",
            (paper.account_id,),
        )

        self.assertEqual([item.account_id for item in accounts], [mt5.account_id, paper.account_id])
        self.assertEqual(paper.account_type, "paper")
        self.assertEqual(paper.environment, "simulated")
        self.assertEqual(paper.currency, "USD")
        self.assertEqual(paper.balance, 50000)
        self.assertEqual(paper.free_margin, 50000)
        self.assertEqual(paper.daily_order_limit, 100)
        self.assertIsNone(connection)
        self.assertIsNone(self.repository.authenticate(self.user.user_id, "invalid"))

    def test_paper_account_is_active_only_with_a_runnable_deployment(self):
        paper = self.repository.create_paper_account(
            self.user.user_id, "Runtime Paper", 10000
        )

        ready = _account_payload(paper, [])
        running = _account_payload(paper, [{
            "status": "active", "execution_mode": "paper",
        }])
        paused = _account_payload(paper, [{
            "status": "paused", "execution_mode": "paper",
        }])

        self.assertFalse(ready["active"])
        self.assertEqual(ready["active_deployment_count"], 0)
        self.assertEqual(ready["engine_status"], "ready")
        self.assertTrue(running["active"])
        self.assertEqual(running["active_deployment_count"], 1)
        self.assertEqual(running["engine_status"], "running")
        self.assertFalse(paused["active"])

    def test_accounts_are_scoped_to_user(self):
        self.repository.create_paper_account(
            self.user.user_id, "Private Paper", 10000
        )
        other = UserRepository(self.storage).create_user(
            "other-account-user", "hash", "salt"
        )

        self.assertEqual(self.repository.list_for_user(other.user_id), [])
        paper = self.repository.list_for_user(self.user.user_id)[0]
        self.assertIsNone(self.repository.get_by_id(other.user_id, paper.account_id))

    def test_account_controls_are_isolated_and_pause_keeps_ea_connected(self):
        account, token = self.repository.create_or_rotate_default(self.user.user_id)
        self.assertIsNotNone(self.repository.authenticate(self.user.user_id, token))

        updated = self.repository.update_controls(
            self.user.user_id,
            account.account_id,
            account_name="黄金主账户",
            trading_enabled=False,
            auto_trading_enabled=False,
            max_total_positions=2,
            max_single_volume=0.5,
            daily_loss_limit=3,
            daily_order_limit=8,
        )

        self.assertEqual(updated.account_name, "黄金主账户")
        self.assertFalse(updated.trading_enabled)
        self.assertFalse(updated.auto_trading_enabled)
        self.assertEqual(updated.max_total_positions, 2)
        self.assertEqual(updated.max_single_volume, 0.5)
        self.assertEqual(updated.daily_loss_limit, 3)
        self.assertEqual(updated.daily_order_limit, 8)
        self.assertIsNotNone(self.repository.authenticate(self.user.user_id, token))

    def test_online_mt5_cannot_be_archived_and_offline_account_can_restore(self):
        account, token = self.repository.create_or_rotate_default(self.user.user_id)
        self.repository.authenticate(self.user.user_id, token)
        with self.assertRaisesRegex(ValueError, "终端在线"):
            self.repository.set_archived(
                self.user.user_id, account.account_id, True
            )

        self.storage.execute(
            "UPDATE mt5_account_connections SET last_seen_at = 1 WHERE account_id = ?",
            (account.account_id,),
        )
        self.storage.execute(
            "UPDATE trading_accounts SET last_seen_at = 1 WHERE id = ?",
            (account.account_id,),
        )
        archived = self.repository.set_archived(
            self.user.user_id, account.account_id, True
        )
        self.assertEqual(archived.status, "archived")
        self.assertFalse(archived.trading_enabled)
        self.assertIsNone(self.repository.authenticate(self.user.user_id, token))

        restored = self.repository.set_archived(
            self.user.user_id, account.account_id, False
        )
        self.assertEqual(restored.status, "active")
        self.assertTrue(restored.trading_enabled)
        self.assertIsNotNone(self.repository.authenticate(self.user.user_id, token))

    def test_mt5_accounts_are_discovered_from_ea_identity(self):
        activations = EAActivationRepository(self.storage)
        self.assertEqual(self.repository.list_for_user(self.user.user_id), [])

        first_code, _ = activations.create(self.user.user_id)
        first, token = activations.consume(
            first_code, mt5_login="90002", mt5_server="Broker-Demo"
        )
        second_code, _ = activations.create(self.user.user_id)
        same_account, _ = activations.consume(
            second_code, mt5_login="90002", mt5_server="broker-demo"
        )
        third_code, _ = activations.create(self.user.user_id)
        second, _ = activations.consume(
            third_code, mt5_login="90003", mt5_server="Broker-Demo"
        )

        self.assertNotEqual(first.account_id, second.account_id)
        self.assertEqual(first.account_id, same_account.account_id)
        self.assertEqual(first.mt5_login, "90002")
        self.assertTrue(token)
        self.assertEqual(len(self.repository.list_for_user(self.user.user_id)), 2)

    def test_accounts_bind_different_strategies_independently(self):
        activations = EAActivationRepository(self.storage)
        first_code, _ = activations.create(self.user.user_id)
        first, _ = activations.consume(
            first_code, mt5_login="10001", mt5_server="Broker-Demo"
        )
        second_code, _ = activations.create(self.user.user_id)
        second, _ = activations.consume(
            second_code, mt5_login="10002", mt5_server="Broker-Demo"
        )
        now = 100
        PositionManagementPolicyRepository(self.storage).save(
            PositionManagementPolicy(
                policy_id="policy-1", user_id=self.user.user_id,
                name="Live exits", config={
                    "initial_stop_rules": [{"type": "signal"}],
                    "initial_take_profit_rules": [{"type": "signal"}],
                    "management_rules": [],
                },
            )
        )
        for strategy_id, name in (("trend-1", "趋势"), ("breakout-1", "突破")):
            config = {
                "strategy_id": strategy_id, "strategy_name": name,
                "symbol": "GOLD_", "enabled": True,
                "lifecycle_status": "production",
                "position_management_policy_id": "policy-1",
            }
            self.storage.execute(
                """
                INSERT INTO user_strategy_configs(
                    user_id, strategy_id, symbol, config_json, created_at, updated_at
                ) VALUES(?, ?, 'GOLD_', ?, ?, ?)
                """,
                (self.user.user_id, strategy_id, json.dumps(config), now, now),
            )
        deployments = PaperTradingService(self.storage)
        first_deployment = deployments.deploy(
            self.user.user_id, first.account_id, "trend-1"
        )
        deployments.deploy(self.user.user_id, second.account_id, "breakout-1")
        repository = StrategyDeploymentRepository(self.storage)

        self.assertEqual(
            repository.list_active_strategy_ids(
                self.user.user_id, first.account_id, "live"
            ),
            ["trend-1"],
        )
        self.assertEqual(
            repository.list_active_strategy_ids(
                self.user.user_id, second.account_id, "live"
            ),
            ["breakout-1"],
        )
        deployments.set_deployment_status(
            self.user.user_id, first.account_id,
            first_deployment["deployment_id"], False,
        )
        self.assertEqual(
            repository.list_active_strategy_ids(
                self.user.user_id, first.account_id, "live"
            ),
            [],
        )
        self.assertEqual(
            repository.list_active_strategy_ids(
                self.user.user_id, second.account_id, "live"
            ),
            ["breakout-1"],
        )

    def test_trade_execution_reports_are_account_scoped_and_directional(self):
        activations = EAActivationRepository(self.storage)
        first_code, _ = activations.create(self.user.user_id)
        first, _ = activations.consume(
            first_code, mt5_login="20001", mt5_server="Broker-Demo"
        )
        second_code, _ = activations.create(self.user.user_id)
        second, _ = activations.consume(
            second_code, mt5_login="20002", mt5_server="Broker-Demo"
        )
        reports = TradeExecutionRepository(self.storage)

        buy = reports.record(self.user.user_id, first.account_id, {
            "instruction_id": "buy-1", "symbol": "GOLD_", "action": "b",
            "success": True, "requested_price": 3300, "executed_price": 3300.5,
            "mt5_position_id": 900001,
        })
        sell = reports.record(self.user.user_id, first.account_id, {
            "instruction_id": "sell-1", "symbol": "GOLD_", "action": "s",
            "success": False, "requested_price": 3300, "executed_price": 3299,
            "error_message": "market closed",
        })
        reports.record(self.user.user_id, first.account_id, {
            "instruction_id": "buy-1", "symbol": "GOLD_", "action": "b",
            "success": True, "requested_price": 3300, "executed_price": 3300.25,
        })

        first_reports = reports.list_for_account(
            self.user.user_id, first.account_id
        )
        self.assertEqual(buy["slippage"], 0.5)
        self.assertEqual(buy["mt5_position_id"], 900001)
        self.assertEqual(sell["slippage"], 1.0)
        self.assertEqual(len(first_reports), 2)
        self.assertEqual(
            next(item for item in first_reports if item["instruction_id"] == "buy-1")["slippage"],
            0.25,
        )
        self.assertEqual(
            reports.list_for_account(self.user.user_id, second.account_id), []
        )


if __name__ == "__main__":
    unittest.main()
