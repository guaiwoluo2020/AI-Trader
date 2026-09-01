"""Coordinates confirmed strategy orders across Paper and MT5 transports."""
from __future__ import annotations

from typing import Callable, Optional

from market.services.execution_adapter import adapter_for_mode


class StrategyExecutionCoordinator:
    """Keep order-confirmation side effects out of TradingServer."""

    def __init__(
        self, *, risk_manager, account_repository, instruction_service,
        live_entries_allowed: Callable[[], bool],
        broadcast_pending_order: Callable[[object], None],
        user_id: Optional[int] = None, account_id: Optional[int] = None,
    ):
        self.risk_manager = risk_manager
        self.account_repository = account_repository
        self.instruction_service = instruction_service
        self.live_entries_allowed = live_entries_allowed
        self.broadcast_pending_order = broadcast_pending_order
        self.user_id = user_id
        self.account_id = account_id

    def on_order_confirmed(self, order) -> Optional[object]:
        print(f"[StrategyExecutionCoordinator] 订单确认: {order.order_id}")
        if not self.live_entries_allowed():
            print(f"[StrategyExecutionCoordinator] 实盘权限已关闭，忽略开仓订单: {order.order_id}")
            return None
        self.broadcast_pending_order(order)
        try:
            self.risk_manager.record_confirmed_order(
                order.order_id, order.symbol, order.mount,
                abs(order.price - order.sl),
            )
            account = (
                self.account_repository.get_by_id(self.user_id, self.account_id)
                if self.user_id and self.account_id else None
            )
            execution_mode = "live" if account and account.account_type == "mt5" else "paper"
            result = adapter_for_mode(execution_mode).submit(order, self.instruction_service)
            if not result.accepted:
                raise RuntimeError(result.reason or "执行适配器拒绝订单")
            print(f"[StrategyExecutionCoordinator] {result.transport} 交易指令已创建: {result.instruction_id}")
            return result
        except Exception as exc:
            print(f"[StrategyExecutionCoordinator] 创建交易指令失败: {exc}")
            import traceback
            traceback.print_exc()
            return None
