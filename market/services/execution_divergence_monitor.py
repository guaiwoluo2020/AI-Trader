"""Classify per-plan execution differences across account deployments."""
from __future__ import annotations

from typing import Dict, List


class ExecutionDivergenceMonitor:
    TECHNICAL_REASONS = {"snapshot_missing", "claim_conflict", "technical_failure"}
    SHARED_DECISION_REASONS = {"no_direction", "no_new_trigger"}
    ACCOUNT_REASONS = {
        "risk_limit", "position_limit", "position_policy", "invalid_volume",
        "entry_guard", "decision_cooldown",
    }

    def build_matrix(
        self, deployments: List[Dict], audits: List[Dict],
        executions: List[Dict] = None,
    ) -> Dict:
        has_any_audit = bool(audits)
        latest = {}
        for audit in sorted(audits or [], key=lambda row: int(row.get("updated_at") or 0)):
            latest[str(audit.get("deployment_id") or "")] = dict(audit)
        latest_execution = {}
        for execution in sorted(
            executions or [], key=lambda row: int(row.get("updated_at") or 0)
        ):
            latest_execution[str(execution.get("deployment_id") or "")] = dict(execution)
        rows, findings = [], []
        for deployment in deployments or []:
            deployment_id = str(deployment.get("deployment_id") or "")
            audit = latest.get(deployment_id)
            execution = latest_execution.get(deployment_id)
            row = {**deployment, **(audit or {})}
            row["gate_status"] = str((audit or {}).get("status") or "not_evaluated")
            row["gate_reason_code"] = str(
                (audit or {}).get("reason_code") or "missing_audit"
            )
            if execution:
                row.update({
                    "execution_status": str(execution.get("status") or "unconsumed"),
                    "execution_reason_code": str(execution.get("reason_code") or ""),
                    "execution_reason": str(execution.get("reason") or ""),
                    "order_id": str(execution.get("order_id") or ""),
                    "execution_updated_at": int(execution.get("updated_at") or 0),
                })
            else:
                row.setdefault("execution_status", "unconsumed")
            if audit is None:
                row.update({"status": "not_evaluated", "reason_code": "missing_audit"})
                if has_any_audit:
                    findings.append({
                        "type": "missing_audit", "technical": True,
                        "deployment_id": deployment_id,
                        "message": "其他部署已评估该计划，但当前部署缺少执行门禁审计",
                    })
            elif str(audit.get("reason_code") or "") in self.TECHNICAL_REASONS:
                findings.append({
                    "type": str(audit.get("reason_code")), "technical": True,
                    "deployment_id": deployment_id,
                    "message": str(audit.get("message") or "技术链路阻断"),
                })
            rows.append(row)
        outcomes = {
            (str(row.get("gate_status") or ""), str(row.get("gate_reason_code") or ""))
            for row in rows if row.get("gate_reason_code") != "missing_audit"
        }
        if len(outcomes) > 1 and any(
            reason in self.SHARED_DECISION_REASONS for _, reason in outcomes
        ):
            findings.append({
                "type": "shared_decision_difference", "technical": True,
                "message": "同一共享 Tick 快照在不同部署产生了不同的信号决策结果",
            })
        if len(outcomes) > 1 and not any(item["technical"] for item in findings):
            findings.append({
                "type": "account_gate_difference", "technical": False,
                "message": "共享计划一致，但账户持仓或风险门禁结果不同",
            })
        return {
            "rows": rows,
            "findings": findings,
            "diverged": any(item["technical"] for item in findings),
        }
