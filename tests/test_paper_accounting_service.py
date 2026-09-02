from market.services.paper_accounting_service import PaperAccountingService


class _Conn:
    def execute(self, sql, params=()):
        if sql.startswith("SELECT * FROM paper_positions"):
            return _Rows([{
                "symbol": "BTCUSD", "direction": "buy", "current_price": 100,
                "entry_price": 100, "remaining_volume": 1, "volume": 1,
                "position_id": "p1",
            }])
        return _Rows([])


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Storage:
    def execute(self, *args):
        return None


class _Paper:
    _quotes = {(1, "BTCUSD"): (110, 111)}


def test_accounting_marks_open_positions():
    result = PaperAccountingService(_Paper()).mark_positions(
        _Conn(), 1, 2, 1000, 100, 10,
    )
    assert result == (1010.0, 1.0, 1)
