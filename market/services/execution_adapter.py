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


def adapter_for_mode(execution_mode: str) -> ExecutionAdapter:
    return MT5ExecutionAdapter() if str(execution_mode).lower() == "live" else PaperExecutionAdapter()
