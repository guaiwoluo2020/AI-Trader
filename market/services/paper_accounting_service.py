"""Paper account valuation and ledger updates."""



class PaperAccountingService:
    def __init__(self, paper_service):
        self.paper_service = paper_service

    def mark_positions(self, conn, user_id, account_id, balance, leverage, now):
        from paper_trading import market_spec
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE account_id = ? AND status = 'open'",
            (account_id,),
        ).fetchall()
        unrealized_total = margin = 0.0
        for position in rows:
            quote = self.paper_service._quotes.get((user_id, position["symbol"]))
            mark = (quote[0] if position["direction"] == "buy" else quote[1]) if quote else float(position["current_price"])
            _, contract_size = market_spec(position["symbol"])
            multiplier = 1 if position["direction"] == "buy" else -1
            active_volume = float(position["remaining_volume"] or position["volume"])
            unrealized = (mark - float(position["entry_price"])) * multiplier * active_volume * contract_size
            margin += float(position["entry_price"]) * active_volume * contract_size / leverage
            unrealized_total += unrealized
            conn.execute(
                "UPDATE paper_positions SET current_price=?, unrealized_profit=?, updated_at=? WHERE position_id=?",
                (mark, unrealized, now, position["position_id"]),
            )
        return balance + unrealized_total, margin, len(rows)
