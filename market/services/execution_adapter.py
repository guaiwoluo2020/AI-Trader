"""Execution adapters for Paper and MT5 transports.

Both transports receive the exact same TradingInstruction.  This keeps risk,
attribution and command construction identical while allowing transport-specific
acknowledgement and error handling later.
"""

from __future__ import annotations

from typing import Any

from .execution_result import ExecutionResult


class ExecutionAdapter:
    transport = "unknown"

    def submit(self, order: Any, instruction_service) -> ExecutionResult:
        raise NotImplementedError


class InstructionExecutionAdapter(ExecutionAdapter):
    """Submit a confirmed pending order through the shared instruction service."""

    def submit(self, order: Any, instruction_service) -> ExecutionResult:
        if instruction_service is None:
            return ExecutionResult(False, transport=self.transport, status="rejected", reason="交易指令服务未设置")
        try:
            instruction_id = instruction_service.create_from_pending_order(order)
            return ExecutionResult(True, str(instruction_id), self.transport, status="accepted")
        except Exception as exc:
            return ExecutionResult(False, transport=self.transport, status="failed", reason=str(exc))


class PaperExecutionAdapter(InstructionExecutionAdapter):
    transport = "paper"


class MT5ExecutionAdapter(InstructionExecutionAdapter):
    transport = "mt5"


class IBKRExecutionAdapter(InstructionExecutionAdapter):
    """Create the shared instruction and hand it to the matching Gateway connector."""
    transport = "ibkr"

    def submit(self, order: Any, instruction_service) -> ExecutionResult:
        result = super().submit(order, instruction_service)
        if not result.accepted:
            return result
        try:
            from routes_ibkr_connector import dispatch_order_to_ibkr
            dispatched = dispatch_order_to_ibkr(
                int(getattr(order, "account_id", 0) or 0), order, result.instruction_id,
            )
            if not dispatched:
                return ExecutionResult(False, result.instruction_id, self.transport,
                                       status="rejected", reason="IBKR Gateway Connector 未连接")
        except Exception as exc:
            return ExecutionResult(False, result.instruction_id, self.transport,
                                   status="failed", reason=str(exc))
        return result


def adapter_for_mode(execution_mode: str, account_type: str = "", account_id: int = 0) -> ExecutionAdapter:
    if str(execution_mode).lower() != "live":
        return PaperExecutionAdapter()
    if str(account_type).lower() == "ibkr":
        return IBKRExecutionAdapter()
    return MT5ExecutionAdapter()
