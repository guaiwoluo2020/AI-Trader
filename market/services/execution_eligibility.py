"""Ordered, serializable execution gate evaluation shared by Live and Paper."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class ExecutionGateResult:
    allowed: bool
    reason_code: str
    message: str = ""
    details: Dict = field(default_factory=dict)

    @classmethod
    def allow(cls, reason_code: str, message: str = "", **details):
        return cls(True, str(reason_code), str(message), dict(details))

    @classmethod
    def deny(cls, reason_code: str, message: str = "", **details):
        return cls(False, str(reason_code), str(message), dict(details))

    def to_dict(self) -> Dict:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ExecutionEligibilityResult:
    allowed: bool
    reason_code: str
    message: str
    details: Dict
    gate_trace: List[Dict]


@dataclass(frozen=True)
class ExecutionAuditOutcome:
    status: str
    reason_code: str
    message: str


def _value(source: Any, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def execution_audit_targets(signals: Iterable[Any]) -> List[Dict[str, str]]:
    """Return distinct plan/stage/direction identities visible in one snapshot."""
    targets: List[Dict[str, str]] = []
    seen = set()
    for signal in signals or []:
        direction = str(_value(signal, "action", "none") or "none").lower()
        if direction not in {"buy", "sell"} or not bool(
            _value(signal, "is_entry_trigger", True)
        ):
            continue
        target = {
            "plan_id": str(_value(signal, "trade_plan_id", "") or ""),
            "plan_stage": str(
                _value(signal, "trade_opportunity_stage", "") or "default"
            ),
            "direction": direction,
        }
        identity = tuple(target.values())
        if identity in seen:
            continue
        seen.add(identity)
        targets.append(target)
    return targets or [{"plan_id": "", "plan_stage": "default", "direction": "none"}]


def record_preflight_audits(
    repository: Any, *, user_id: int, account_id: int,
    deployment_id: str, strategy_id: str, tick_id: str,
    execution_mode: str, symbol: str, signals: Iterable[Any],
    result: ExecutionEligibilityResult,
) -> None:
    """Persist one preflight outcome for every visible plan-stage target."""
    for target in execution_audit_targets(signals):
        repository.record(
            user_id=int(user_id), account_id=int(account_id),
            deployment_id=str(deployment_id or ""),
            strategy_id=str(strategy_id or ""), tick_id=str(tick_id or ""),
            execution_mode=str(execution_mode or ""), symbol=str(symbol or ""),
            plan_id=target["plan_id"], plan_stage=target["plan_stage"],
            direction=target["direction"], status="blocked",
            reason_code=result.reason_code, message=result.message,
            gate_trace=result.gate_trace,
        )


def classify_execution_outcome(
    decision: Any,
    *,
    order_id: str = "",
    creation_result: Optional[Any] = None,
) -> ExecutionAuditOutcome:
    """Map Live/Paper execution outcomes to the same stable reason codes."""
    message = str(_value(decision, "decision_reason", "") or "")
    if creation_result is not None:
        created = bool(_value(creation_result, "created", False))
        result_reason = str(_value(creation_result, "reason_code", "") or "")
        result_message = str(_value(creation_result, "message", "") or message)
        if created:
            return ExecutionAuditOutcome("ordered", "eligible", result_message)
        if result_reason:
            return ExecutionAuditOutcome("blocked", result_reason, result_message)
    if order_id:
        return ExecutionAuditOutcome("ordered", "eligible", message)

    action = str(_value(decision, "action", "none") or "none").lower()
    status = str(_value(decision, "status", "") or "").lower()
    summary = dict(_value(decision, "signal_summary", {}) or {})
    position_check = dict(_value(decision, "position_check", {}) or {})
    risk_check = dict(_value(decision, "risk_check", {}) or {})

    if action == "none":
        guard = dict(summary.get("loss_streak_guard") or {})
        if "冷却" in message:
            reason_code = "decision_cooldown"
        elif guard and not guard.get("allowed", True):
            reason_code = "entry_guard"
        elif "没有新的入场触发" in message or "等待价格或信号变化" in message:
            reason_code = "no_new_trigger"
        else:
            reason_code = "no_direction"
        return ExecutionAuditOutcome("no_action", reason_code, message)

    if status == "rejected":
        staged = dict(risk_check.get("staged_execution") or {})
        staged_reason = str(staged.get("reason") or "")
        if staged and not staged.get("allowed", True):
            if "手数" in staged_reason and "为 0" in staged_reason:
                reason_code = "invalid_volume"
            elif "风险" in staged_reason:
                reason_code = "risk_limit"
            else:
                reason_code = "position_policy"
        elif position_check and not position_check.get("allowed", True):
            reason_code = "position_limit"
        elif risk_check and not risk_check.get("allowed", True):
            reason_code = "risk_limit"
        elif "消费" in message or "claim" in message.lower():
            reason_code = "claim_conflict"
        else:
            reason_code = "risk_limit"
        return ExecutionAuditOutcome("blocked", reason_code, message)

    return ExecutionAuditOutcome("blocked", "technical_failure", message)


class ExecutionEligibilityEvaluator:
    def evaluate(self, gates: Iterable[ExecutionGateResult]) -> ExecutionEligibilityResult:
        trace = []
        final = ExecutionGateResult.allow("eligible", "全部执行门禁通过")
        for gate in gates:
            final = gate
            trace.append(gate.to_dict())
            if not gate.allowed:
                break
        return ExecutionEligibilityResult(
            allowed=final.allowed,
            reason_code=final.reason_code,
            message=final.message,
            details=dict(final.details),
            gate_trace=trace,
        )

    @staticmethod
    def snapshot_missing(strategy_id: str, tick_id: str) -> ExecutionEligibilityResult:
        return ExecutionEligibilityEvaluator().evaluate([
            ExecutionGateResult.deny(
                "snapshot_missing", "共享 Tick 快照缺少当前策略，禁止重新生成信号",
                strategy_id=str(strategy_id), tick_id=str(tick_id),
            )
        ])

    @staticmethod
    def preflight(
        *, automation_enabled: bool, account_status: str,
        account_enabled: bool, trading_enabled: bool,
        auto_trading_enabled: bool, authorization_allowed: bool = True,
        authorization_message: str = "",
    ) -> ExecutionEligibilityResult:
        """Evaluate account-level switches before strategy decision creation."""
        account_active = (
            str(account_status or "").lower() == "active"
            and bool(account_enabled)
        )
        account_message = (
            "交易账户已停用或不处于活跃状态"
            if not account_active else "交易账户处于活跃状态"
        )
        trading_allowed = bool(trading_enabled) and bool(auto_trading_enabled)
        trading_message = (
            "账户交易或自动交易开关已关闭"
            if not trading_allowed else "账户交易开关已启用"
        )
        authorization_text = str(
            authorization_message
            or ("实盘交易授权校验未通过" if not authorization_allowed else "交易授权校验通过")
        )
        return ExecutionEligibilityEvaluator().evaluate([
            (
                ExecutionGateResult.allow("automation_enabled", "全局自动交易已启用")
                if automation_enabled else
                ExecutionGateResult.deny("automation_disabled", "全局自动交易开关已关闭")
            ),
            (
                ExecutionGateResult.allow("account_active", account_message)
                if account_active else
                ExecutionGateResult.deny("account_inactive", account_message)
            ),
            (
                ExecutionGateResult.allow("trading_enabled", trading_message)
                if trading_allowed else
                ExecutionGateResult.deny("trading_disabled", trading_message)
            ),
            (
                ExecutionGateResult.allow("authorization_allowed", authorization_text)
                if authorization_allowed else
                ExecutionGateResult.deny("authorization_blocked", authorization_text)
            ),
        ])
