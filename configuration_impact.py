"""Configuration dependency traversal for safe owner-scoped hot reloads."""

import json
from typing import Dict, List, Set, Tuple

from sqlite_storage import get_storage


DEPLOYED_STATUSES = {"active", "paused", "pending"}
DEPLOYMENT_MODES = {"paper", "live"}


class ConfigurationImpactService:
    def __init__(self, storage=None):
        self.storage = storage or get_storage()

    @staticmethod
    def _json(value) -> Dict:
        try:
            return json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _source_ids(payload: Dict) -> Set[str]:
        result = set()
        for item in payload.get("signal_sources") or []:
            params = item.get("params") or {}
            for value in (
                item.get("signal_source_id"), params.get("ai_signal_source_id"),
            ):
                if value:
                    result.add(str(value))
        return result

    def analyze(self, owner_user_id: int, entity_type: str, entity_id: str) -> Dict:
        owner_user_id = int(owner_user_id)
        entity_type = str(entity_type)
        entity_id = str(entity_id)
        strategy_rows = self.storage.fetchall(
            "SELECT user_id, strategy_id, config_json FROM user_strategy_configs"
        )
        strategies = {
            (int(row["user_id"]), str(row["strategy_id"])): self._json(row["config_json"])
            for row in strategy_rows
        }
        affected: Set[Tuple[int, str]] = set()

        if entity_type == "strategy":
            affected.add((owner_user_id, entity_id))
            for key, payload in strategies.items():
                if (
                    int(payload.get("source_owner_user_id") or 0) == owner_user_id
                    and str(payload.get("source_strategy_id") or "") == entity_id
                ):
                    affected.add(key)

        elif entity_type == "ai_signal_source":
            owner_strategy_ids = set()
            for key, payload in strategies.items():
                if key[0] == owner_user_id and entity_id in self._source_ids(payload):
                    affected.add(key)
                    owner_strategy_ids.add(key[1])
            runtime_ids = {
                f"{owner_user_id}:ai:{entity_id}",
                *(f"{owner_user_id}:{strategy_id}:{entity_id}" for strategy_id in owner_strategy_ids),
            }
            for key, payload in strategies.items():
                if key[0] == owner_user_id:
                    continue
                if (
                    int(payload.get("source_owner_user_id") or 0) == owner_user_id
                    and str(payload.get("source_strategy_id") or "") in owner_strategy_ids
                ):
                    affected.add(key)
                    continue
                for source in payload.get("signal_sources") or []:
                    shared_id = str((source.get("params") or {}).get("shared_runtime_id") or "")
                    if shared_id in runtime_ids:
                        affected.add(key)
                        break

        elif entity_type == "position_management":
            owner_strategy_ids = set()
            for key, payload in strategies.items():
                if key[0] == owner_user_id and str(payload.get("position_management_policy_id") or "") == entity_id:
                    affected.add(key)
                    owner_strategy_ids.add(key[1])
            policy_refs = self.storage.fetchall(
                "SELECT user_id, policy_id FROM position_management_policies "
                "WHERE source_owner_user_id = ? AND source_policy_id = ?",
                (owner_user_id, entity_id),
            )
            referenced_policy_ids = {
                (int(row["user_id"]), str(row["policy_id"])) for row in policy_refs
            }
            for key, payload in strategies.items():
                if (
                    int(payload.get("source_owner_user_id") or 0) == owner_user_id
                    and str(payload.get("source_strategy_id") or "") in owner_strategy_ids
                ) or (key[0], str(payload.get("position_management_policy_id") or "")) in referenced_policy_ids:
                    affected.add(key)
        else:
            raise ValueError("不支持的配置影响类型")

        deployments = self.storage.fetchall(
            """
            SELECT d.user_id, d.strategy_id, d.deployment_id, d.execution_mode,
                   d.status, d.account_id, a.account_name, u.username,
                   JSON_UNQUOTE(JSON_EXTRACT(s.config_json, '$.strategy_name')) AS strategy_name
            FROM strategy_deployments d
            JOIN trading_accounts a ON a.id = d.account_id
            JOIN users u ON u.id = d.user_id
            JOIN user_strategy_configs s
              ON s.user_id = d.user_id AND s.strategy_id = d.strategy_id
            WHERE d.execution_mode IN ('paper', 'live')
              AND d.status IN ('active', 'paused', 'pending')
            """
        )
        impacted_deployments: List[Dict] = [
            dict(row) for row in deployments
            if (int(row["user_id"]), str(row["strategy_id"])) in affected
        ]
        own = [row for row in impacted_deployments if int(row["user_id"]) == owner_user_id]
        external = [row for row in impacted_deployments if int(row["user_id"]) != owner_user_id]
        external_references = sorted({
            (user_id, strategies[(user_id, strategy_id)].get("strategy_name") or strategy_id)
            for user_id, strategy_id in affected if user_id != owner_user_id
        })
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "allowed": not external,
            "blocked_reason": "配置已被其他用户部署到模拟盘或实盘" if external else "",
            "requires_confirmation": bool(own or external_references),
            "affected_strategy_count": len(affected),
            "own_deployments": own,
            "external_deployments": external,
            "external_references": [
                {"user_id": item[0], "strategy_name": item[1]}
                for item in external_references
            ],
            "hot_reload_scope": "当前用户全部运行账户",
            "existing_positions": "继续使用开仓时的配置快照",
        }
