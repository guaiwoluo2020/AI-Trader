"""Strategy quote matching and signal-runtime orchestration helpers."""


class StrategyRuntimeCoordinator:
    def __init__(self, strategy_store, instrument_mappings, account_repository):
        self.strategy_store = strategy_store
        self.instrument_mappings = instrument_mappings
        self.account_repository = account_repository

    def strategies_for_quote(self, user_id, account_id, quote_symbol):
        account = (
            self.account_repository.get_by_id(user_id, account_id)
            if user_id is not None and account_id else None
        )
        target_server = str(getattr(account, "mt5_server", "") or "")
        matched = []
        for strategy in self.strategy_store.get_all_strategies():
            if str(strategy.symbol).upper() == str(quote_symbol).upper():
                matched.append(strategy)
                continue
            source_user_id = int(
                getattr(strategy, "source_owner_user_id", 0) or user_id or 0
            )
            source_server = self.instrument_mappings.source_server(
                source_user_id, strategy.symbol
            )
            if self.instrument_mappings.compatible(
                source_server, strategy.symbol, target_server, quote_symbol
            ):
                matched.append(strategy)
        return matched
