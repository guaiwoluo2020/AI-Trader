"""统一交易执行回执模型。

Paper 与 MT5 传输层都使用这套状态和值域。数据库仍保留原有字段，
额外状态写入 payload_json，避免不同执行通道在上层产生分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


EXECUTION_STATUSES = {
    "accepted", "rejected", "pending", "filled", "partially_filled",
    "failed", "timeout", "canceled",
}


def normalize_execution_status(value: Any, *, success: Optional[bool] = None) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    aliases = {"cancelled": "canceled", "partial": "partially_filled", "ok": "filled"}
    status = aliases.get(status, status)
    if status in EXECUTION_STATUSES:
        return status
    if success is True:
        return "filled"
    if success is False:
        return "failed"
    return "pending"


@dataclass(frozen=True)
class ExecutionResult:
    """跨 Paper/MT5 的标准执行结果。"""

    accepted: bool
    instruction_id: str = ""
    transport: str = ""
    reason: str = ""
    status: str = ""
    order_id: str = ""
    strategy_id: str = ""
    account_id: Optional[int] = None
    requested_price: float = 0.0
    executed_price: float = 0.0
    requested_volume: float = 0.0
    executed_volume: float = 0.0
    retcode: int = 0
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", normalize_execution_status(
            self.status, success=self.accepted,
        ))

    @classmethod
    def from_payload(cls, payload: Dict[str, Any], *, transport: str = "mt5") -> "ExecutionResult":
        success = payload.get("success")
        status = normalize_execution_status(payload.get("status"), success=success)
        return cls(
            accepted=bool(success) if success is not None else status not in {"rejected", "failed", "timeout", "canceled"},
            instruction_id=str(payload.get("instruction_id") or ""),
            transport=str(payload.get("transport") or transport),
            reason=str(payload.get("reason") or payload.get("error_message") or ""),
            status=status,
            order_id=str(payload.get("order_id") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            account_id=payload.get("account_id"),
            requested_price=float(payload.get("requested_price") or 0),
            executed_price=float(payload.get("executed_price") or 0),
            requested_volume=float(payload.get("requested_volume") or 0),
            executed_volume=float(payload.get("executed_volume") or 0),
            retcode=int(payload.get("retcode") or 0),
            raw_payload=dict(payload),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "success": self.status in {"filled", "partially_filled"},
            "status": self.status,
            "instruction_id": self.instruction_id,
            "transport": self.transport,
            "reason": self.reason,
            "error_message": self.reason,
            "order_id": self.order_id,
            "strategy_id": self.strategy_id,
            "account_id": self.account_id,
            "requested_price": self.requested_price,
            "executed_price": self.executed_price,
            "requested_volume": self.requested_volume,
            "executed_volume": self.executed_volume,
            "retcode": self.retcode,
            "occurred_at": self.occurred_at.isoformat(),
            "raw_payload": self.raw_payload,
        }
