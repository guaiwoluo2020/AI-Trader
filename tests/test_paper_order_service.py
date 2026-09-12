import json

from market.services.paper_order_service import PaperOrderService
from market.services.plan_execution_service import PlanExecutionService
from market.services.structure_plan_execution_coordinator import StructurePlanExecutionCoordinator


class _Storage:
    def __init__(self):
        self.executed = []

    def fetchone(self, sql, params):
        if "decision_id" in sql:
            return None
        if "max_total_positions" in sql:
            return {"max_total_positions": 10, "max_single_volume": 10}
        if "COUNT(*) AS count" in sql or ") AS count" in sql:
            return {"count": 0}
        raise AssertionError(sql)

    def execute(self, sql, params):
        self.executed.append((sql, params))


class _Plans:
    def claim_execution(self, *args, **kwargs):
        return True

    def record_execution(self, *args, **kwargs):
        pass

    def release_claim(self, *args, **kwargs):
        pass


class _PaperService:
    def __init__(self):
        self.storage = _Storage()
        self.structure_plans = _Plans()
        self.structure_plan_execution_coordinator = StructurePlanExecutionCoordinator(
            self.structure_plans, PlanExecutionService(self.structure_plans)
        )

    @staticmethod
    def _deployment_strategy(user_id, deployment):
        return {"max_positions": 3, "max_same_direction": 2}

    @staticmethod
    def _valid_exits(direction, entry, stop_loss, take_profit):
        return stop_loss < entry < take_profit

    @staticmethod
    def _record_execution_receipt(*args, **kwargs):
        pass


def test_create_builds_attribution_and_persists_structure_order():
    paper = _PaperService()
    decision = {
        "decision_id": "decision-1", "strategy_id": "strategy-1",
        "strategy_name": "Structure", "symbol": "US100Cash",
        "action": "buy", "status": "pending", "entry_price": 100,
        "sl": 99, "tp": 102, "volume": 0.1, "confidence_score": 80,
        "decision_reason": "range breakout retest",
        "signal_summary": {
            "selected_signal_source_id": "source-1",
            "selected_signal_source": "structure_plan",
            "selected_trade_plan_id": "plan-1",
            "selected_trade_plan_group_id": "group-1",
            "selected_trade_plan_valid_from": 1000,
            "selected_setup_type": "range_breakout",
        },
    }

    result = PaperOrderService(paper).create(
        1, {"account_id": 11, "deployment_id": "deployment-1", "strategy_id": "strategy-1"},
        decision, 1001,
    )

    assert result.created is True
    assert result.reason_code == "eligible"
    insert = next(params for sql, params in paper.storage.executed if "INSERT INTO paper_orders" in sql)
    attribution = json.loads(insert[19])
    assert attribution["trade_plan_id"] == "plan-1"
    assert attribution["setup_type"] == "range_breakout"


def test_create_returns_claim_conflict_without_inserting_an_order():
    paper = _PaperService()
    paper.structure_plans.claim_execution = lambda *args, **kwargs: False
    decision = {
        "decision_id": "decision-2", "strategy_id": "strategy-1",
        "strategy_name": "Structure", "symbol": "US100Cash",
        "action": "sell", "status": "pending", "entry_price": 100,
        "sl": 101, "tp": 98, "volume": 0.1, "confidence_score": 80,
        "decision_reason": "range breakout retest",
        "signal_summary": {
            "selected_signal_source": "structure_plan",
            "selected_trade_plan_id": "plan-2",
            "selected_trade_opportunity_stage": "breakout",
        },
    }

    result = PaperOrderService(paper).create(
        1, {"account_id": 11, "deployment_id": "deployment-1", "strategy_id": "strategy-1"},
        decision, 1001,
    )

    assert result.created is False
    assert result.reason_code == "claim_conflict"
    assert not any("INSERT INTO paper_orders" in sql for sql, _ in paper.storage.executed)
