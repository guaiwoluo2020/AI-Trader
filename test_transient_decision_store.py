from datetime import datetime, timedelta

from market.models import TradingDecision
from market.services.strategy.transient_decision_store import TransientDecisionStore


def _waiting_decision(created_at, reason="等待价格进入入场区"):
    return TradingDecision(
        symbol="BTCUSD",
        strategy_id="btc-policy",
        strategy_name="BTC Policy",
        execution_mode="paper",
        action="none",
        decision_type="no_action",
        decision_reason=reason,
        status="skipped",
        created_at=created_at,
        signal_summary={"direction": "buy", "action": "buy"},
    )


def test_waiting_decisions_are_aggregated_per_deployment_in_memory():
    store = TransientDecisionStore()
    first_at = datetime.now()
    first = store.record(7, 11, _waiting_decision(first_at))
    merged = store.record(7, 11, _waiting_decision(first_at + timedelta(seconds=5)))

    assert first.decision_id == merged.decision_id
    assert merged.observation_count == 2
    assert merged.first_observed_at == first_at
    assert merged.last_observed_at == first_at + timedelta(seconds=5)
    assert store.list(7, 11) == [merged]


def test_changed_waiting_reason_updates_the_same_aggregate():
    store = TransientDecisionStore()
    now = datetime.now()
    store.record(7, 11, _waiting_decision(now))
    store.record(7, 11, _waiting_decision(now, "置信度不足"))

    entries = store.list(7, 11)
    assert len(entries) == 1
    assert entries[0].observation_count == 2
    assert entries[0].decision_reason == "置信度不足"


def test_actionable_event_clears_prior_waiting_aggregate():
    store = TransientDecisionStore()
    now = datetime.now()
    store.record(7, 11, _waiting_decision(now))
    store.clear_for_strategy(7, 11, "btc-policy", "BTCUSD")

    assert store.list(7, 11) == []
