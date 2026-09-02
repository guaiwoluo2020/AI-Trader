"""Read-only runtime status projection for API and monitoring pages."""


class RuntimeStatusQueryService:
    def build(self, server) -> dict:
        return {
            "ws_clients": server.get_ws_client_count(),
            "statistics": server.statistics_service.get_status(),
            "positions": server.position_service.get_status(),
            "trade_history": server.trade_history_service.get_status(),
            "pending_orders": server.pending_order_service.get_status(),
            "trading_instructions": server.trading_instruction_service.get_status(),
            "strategy_service": server.strategy_service.get_status(),
        }
