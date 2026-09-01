"""Strategy configuration repository boundary.

The implementation remains behind the legacy module until all migration-only
helpers are moved. Consumers should import this module so the next extraction
does not touch business code.
"""

from mysql_repositories import StrategyConfigRepository

__all__ = ["StrategyConfigRepository"]
