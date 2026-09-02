from market.services.paper_execution_reporter import PaperExecutionReporter


class _Repo:
    def __init__(self):
        self.calls = []

    def record(self, user_id, account_id, payload):
        self.calls.append((user_id, account_id, payload))


def test_paper_reporter_uses_canonical_transport_and_status():
    repo = _Repo()
    PaperExecutionReporter(repo).record(
        1, 2,
        {"order_id": "o1", "symbol": "BTCUSD", "direction": "buy",
         "requested_price": 10, "requested_volume": 0.1,
         "position_attribution_json": "{}"},
        "filled", executed_price=11, executed_volume=0.1,
    )
    payload = repo.calls[0][2]
    assert payload["transport"] == "paper"
    assert payload["status"] == "filled"
    assert payload["instruction_id"] == "paper:o1"
