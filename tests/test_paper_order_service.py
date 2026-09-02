import json

from market.services.paper_order_service import PaperOrderService


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

    created = PaperOrderService(paper).create(
        1, {"account_id": 11, "deployment_id": "deployment-1", "strategy_id": "strategy-1"},
        decision, 1001,
    )

    assert created is True
    insert = next(params for sql, params in paper.storage.executed if "INSERT INTO paper_orders" in sql)
    attribution = json.loads(insert[19])
    assert attribution["trade_plan_id"] == "plan-1"
    assert attribution["setup_type"] == "range_breakout"
