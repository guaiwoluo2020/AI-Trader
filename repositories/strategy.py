"""Strategy-domain repository boundary.

The public types are centralized here while the SQL implementation is being
migrated out of the legacy repository module incrementally.
"""

from mysql_repositories import StrategyConfigRepository, StrategyDeploymentRepository

__all__ = ["StrategyConfigRepository", "StrategyDeploymentRepository"]
